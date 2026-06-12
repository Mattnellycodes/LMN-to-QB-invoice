"""Name and address matching between the Irrigation sheet and the Schedule Master.

Matching tiers (applied in order per irrigation client):
1. Memory: a human-confirmed mapping in the Client Match Memory tab always wins.
2. Exact: normalized names are identical.
3. Proposal (written to the memory tab for human confirmation, never
   auto-accepted), produced by any of:
   - address match: same street number and street on both sheets
   - street-style name match: names share the same street number and nearly
     the same street text ("13 Wicki Up" / "13 Wickiup Trail")
   - containment: one name's words all appear in the other's
     ("Welles" / "Welles, Jeffrey")
   - fuzzy: similarity >= FUZZY_THRESHOLD (default 0.95)
   Variant suffixes are stripped to a base name for proposals:
   "Keating - Main House" is also tried as "Keating".

Guard: two names that contain different numbers (street numbers, unit numbers)
are never matched or proposed, regardless of similarity. "150 Andesite" and
"170 Andesite" are different properties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_PUNCT = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
# Trailing city/state/zip noise on addresses: ", Big Sky, Montana 59716"
_ADDRESS_TAIL = re.compile(
    r",.*$|\b(big sky|bozeman|belgrade|livingston|montana|mt)\b.*$|\b\d{5}\b.*$"
)


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    cleaned = _PUNCT.sub(" ", name.lower())
    return _WHITESPACE.sub(" ", cleaned).strip()


def name_forms(name: str) -> list[str]:
    """Comparison forms of a name: normalized, comma-flipped, sorted tokens."""
    forms = [normalize(name)]
    if "," in name:
        last, _, first = name.partition(",")
        forms.append(normalize(f"{first} {last}"))
    forms.append(" ".join(sorted(forms[0].split())))
    return list(dict.fromkeys(f for f in forms if f))


def numbers_of(name: str) -> frozenset[str]:
    return frozenset(_DIGITS.findall(name))


def numbers_conflict(a: str, b: str) -> bool:
    """True when both names carry numbers and the numbers differ at all."""
    na, nb = numbers_of(a), numbers_of(b)
    return bool(na or nb) and na != nb


def name_similarity(a: str, b: str) -> float:
    """Best SequenceMatcher ratio across comparison forms; 0.0 on number conflict."""
    if numbers_conflict(a, b):
        return 0.0
    return max(
        SequenceMatcher(None, fa, fb).ratio()
        for fa in name_forms(a)
        for fb in name_forms(b)
    )


def base_name(name: str) -> str:
    """Name with a variant suffix removed: 'Keating - Main House' -> 'Keating'.

    Splits on ' - ' or ':' only; plain hyphens inside words are untouched.
    """
    base = re.split(r"\s+-\s+|:", name)[0].strip()
    return base or name


_FILLER_TOKENS = {"and", "&"}


def tokens_contained(a: str, b: str) -> bool:
    """True when the shorter name's words all appear in the longer name's.

    Requires the shorter side to carry a word of 4+ letters so initials and
    very short names ("KC", "Kim") can't ride along.
    """
    if numbers_conflict(a, b):
        return False
    ta = set(normalize(a).split()) - _FILLER_TOKENS
    tb = set(normalize(b).split()) - _FILLER_TOKENS
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return (
        bool(small)
        and small != large
        and small <= large
        and any(len(t) >= 4 for t in small)
    )


def street_style_match(a: str, b: str) -> bool:
    """True for names that share a street number and nearly the same street text.

    Catches "13 Wicki Up" vs "13 Wickiup Trail". The shared number is the
    safety anchor; the remaining text is compared with spaces squashed.
    """
    na, nb = numbers_of(a), numbers_of(b)
    if not na or na != nb:
        return False
    sa = _DIGITS.sub("", normalize(a)).replace(" ", "")
    sb = _DIGITS.sub("", normalize(b)).replace(" ", "")
    if not sa or not sb:
        return False
    return (
        sa.startswith(sb)
        or sb.startswith(sa)
        or SequenceMatcher(None, sa, sb).ratio() >= 0.8
    )


def normalize_address(address: str) -> str:
    """Leading street part of an address: '150 Andesite, Big Sky, MT' -> '150 andesite'."""
    street = _ADDRESS_TAIL.sub("", address.lower()).strip()
    return normalize(street)


def addresses_match(a: str, b: str) -> bool:
    """Same street number and street, ignoring formatting and city/state tails."""
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb or numbers_conflict(na, nb):
        return False
    if na == nb:
        return True
    # Same number(s) plus one street string containing the other
    # ("150 andesite" vs "150 andesite rd").
    return bool(numbers_of(na)) and (na.startswith(nb) or nb.startswith(na))


@dataclass
class ScheduleClient:
    name: str
    address: str = ""


@dataclass
class IrrigationClient:
    name: str
    address: str = ""
    source_tab: str = ""
    section: str = "Active"  # Active | Blowout Only


@dataclass
class MatchResult:
    matched: list[tuple[IrrigationClient, str, str]] = field(default_factory=list)
    irrigation_only: list[tuple[IrrigationClient, str]] = field(default_factory=list)
    needs_review: list[tuple[IrrigationClient, str, str]] = field(default_factory=list)


def best_candidate(
    client: IrrigationClient, schedule: list[ScheduleClient], threshold: float
) -> tuple[str, str] | None:
    """Best schedule-side proposal for a client, or None.

    Returns (schedule name, reason). Rule priority: address match, then
    street-style name match, then containment, then fuzzy >= `threshold`.
    The client's base name (variant suffix stripped) is tried alongside the
    full name.
    """
    names = list(dict.fromkeys([client.name, base_name(client.name)]))
    best: tuple[int, float, str, str] | None = None
    for sched in schedule:
        candidate = _judge(client, names, sched, threshold)
        if candidate and (best is None or candidate[:2] > best[:2]):
            best = candidate
    if best:
        return best[2], best[3]
    return None


def _judge(
    client: IrrigationClient,
    names: list[str],
    sched: ScheduleClient,
    threshold: float,
) -> tuple[int, float, str, str] | None:
    """(priority, score, schedule name, reason) for one schedule client, or None."""
    if (
        client.address
        and sched.address
        and addresses_match(client.address, sched.address)
    ):
        return 4, 1.0, sched.name, f"address match: {sched.address.strip()}"
    if any(street_style_match(n, sched.name) for n in names):
        return 3, 1.0, sched.name, "street-style name match"
    if any(tokens_contained(n, sched.name) for n in names):
        return 2, 1.0, sched.name, "name contained in schedule name"
    score = max(name_similarity(n, sched.name) for n in names)
    if score >= threshold:
        return 1, score, sched.name, f"name {score:.0%} similar"
    return None


def reconcile(
    irrigation: list[IrrigationClient],
    schedule: list[ScheduleClient],
    memory: dict[str, tuple[str, str]],
    threshold: float,
) -> MatchResult:
    """Classify each irrigation client as matched, irrigation-only, or needs-review.

    `memory` maps normalized irrigation name -> (schedule client, status), from
    the Client Match Memory tab.
    """
    schedule_by_norm = {normalize(s.name): s.name for s in schedule}
    result = MatchResult()
    for client in irrigation:
        key = normalize(client.name)
        if key in memory:
            schedule_name, status = memory[key]
            _apply_memory(result, client, schedule_name, status, schedule_by_norm)
            continue
        if key in schedule_by_norm:
            result.matched.append((client, schedule_by_norm[key], "exact"))
            continue
        proposal = best_candidate(client, schedule, threshold)
        if proposal:
            result.needs_review.append((client, proposal[0], proposal[1]))
        else:
            result.irrigation_only.append((client, "no match on schedule master"))
    return result


def _apply_memory(
    result: MatchResult,
    client: IrrigationClient,
    schedule_name: str,
    status: str,
    schedule_by_norm: dict[str, str],
) -> None:
    if status == "not_a_match":
        result.irrigation_only.append((client, "confirmed not a schedule client"))
    elif status == "confirmed":
        if normalize(schedule_name) in schedule_by_norm:
            result.matched.append((client, schedule_name, "memory"))
        else:
            result.irrigation_only.append(
                (client, f"mapped to '{schedule_name}', no longer on schedule master")
            )
    else:  # blank status: proposal still awaiting human review
        result.needs_review.append(
            (client, schedule_name, "awaiting review in memory tab")
        )
