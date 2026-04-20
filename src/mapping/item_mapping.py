"""Resolve LMN item lookup names to QBO Product/Service ItemRefs.

Resolution rounds per lookup name:
  1. Exact case-insensitive match in the QBO item cache.
  2. Normalized match — canonicalize both LMN and QBO names (strip trailing
     bracketed unit like ``[ea]``, trailing parenthetical like `` (maint)``,
     and a trailing ``, <unit>`` token drawn from a small allow-list) and
     compare. Canonical keys with multiple candidate QBO items are dropped
     so ambiguous names fall through instead of matching arbitrarily.
  3. DB override (user-confirmed mapping from ``item_mapping_overrides``).
  4. The user-created ``FALLBACK_ITEM_NAME`` catch-all ItemRef.

Every call returns a usable ItemRef — there is no "unresolved" state; the
fallback is always available (or the caller should not be here at all,
which is enforced upstream by surfacing ``ItemMappingError``).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Set, Tuple


# Tokens that appear as the trailing ``, <unit>`` segment of both LMN and
# QBO item names per the shared naming convention
# (Material, Type, Size, Units). Matched case-insensitively.
UNIT_TOKENS: frozenset[str] = frozenset({
    "ea",
    "ft",
    "yd",
    "sf",
    "lb",
    "ton",
    "hr",
    "gal",
    "daily",
    "day",
    "$",
})

_BRACKET_TAIL_RE = re.compile(r"\s*\[[^\]]*\]\s*$")
_PAREN_TAIL_RE = re.compile(r"\s*\([^)]*\)\s*$")
# Captures the last comma-separated token as a potential unit suffix.
_COMMA_TAIL_RE = re.compile(r",\s*([^,]+?)\s*$")


def canonicalize_item_name(name: str) -> str:
    """Strip trailing unit/variant markers so LMN and QBO names align.

    Applies iteratively so ``"Foo [ea] (maint)"`` → ``"Foo"`` and
    ``"Widget, ea (maint)"`` → ``"Widget"``. Only strips a trailing
    ``, <token>`` when ``<token>`` is in the known ``UNIT_TOKENS`` set
    (case-insensitive) — a name like ``"Fertilizer, bagged"`` stays intact
    because ``bagged`` is not a unit.
    """
    if not name:
        return ""
    s = name.strip()
    changed = True
    while changed and s:
        changed = False

        stripped = _BRACKET_TAIL_RE.sub("", s).strip()
        if stripped != s:
            s = stripped
            changed = True
            continue

        stripped = _PAREN_TAIL_RE.sub("", s).strip()
        if stripped != s:
            s = stripped
            changed = True
            continue

        match = _COMMA_TAIL_RE.search(s)
        if match and match.group(1).strip().lower() in UNIT_TOKENS:
            s = s[: match.start()].strip()
            changed = True
    return s


def build_normalized_cache(
    item_cache: Mapping[str, Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    """Index the QBO item cache by canonicalized lowercased name.

    Canonical keys that more than one QBO item maps to are **dropped
    entirely** — ambiguous lookups fall through to later rounds rather than
    matching arbitrarily. Exact (Round 1) matches still resolve via the
    original ``item_cache`` regardless of collisions here.
    """
    per_canon: Dict[str, List[Dict[str, str]]] = {}
    for ref in item_cache.values():
        canon = canonicalize_item_name(ref.get("name", "")).lower()
        if not canon:
            continue
        per_canon.setdefault(canon, []).append(ref)
    return {canon: refs[0] for canon, refs in per_canon.items() if len(refs) == 1}


def resolve_item_ref(
    lookup_name: str,
    item_cache: Mapping[str, Dict[str, str]],
    normalized_cache: Mapping[str, Dict[str, str]],
    db_overrides: Mapping[str, Dict[str, str]],
    fallback_ref: Dict[str, str],
) -> Tuple[Dict[str, str], bool]:
    """Return ``(ItemRef, is_fallback)`` after running the four rounds."""
    name = (lookup_name or "").strip()
    if name:
        exact = item_cache.get(name.lower())
        if exact is not None:
            return exact, False

        canon = canonicalize_item_name(name).lower()
        if canon:
            normalized = normalized_cache.get(canon)
            if normalized is not None:
                return normalized, False

        override = db_overrides.get(name)
        if override is not None:
            return override, False
    return fallback_ref, True


def build_item_refs(
    invoices: List[Mapping[str, Any]],
    item_cache: Mapping[str, Dict[str, str]],
    normalized_cache: Mapping[str, Dict[str, str]],
    db_overrides: Mapping[str, Dict[str, str]],
    fallback_ref: Dict[str, str],
) -> Tuple[Dict[str, Dict[str, str]], Set[str]]:
    """Resolve every unique ``item_lookup_name`` across all invoices.

    Returns ``(refs_by_lookup_name, fallback_lookup_names)``:
      - ``refs_by_lookup_name``: ``{item_lookup_name: ItemRef}`` — consumed
        at QBO submit time.
      - ``fallback_lookup_names``: the deduped set of lookup names that got
        the fallback ItemRef. Used to render the "upgrade mappings" banner
        on the results page.
    """
    refs: Dict[str, Dict[str, str]] = {}
    fallback_names: Set[str] = set()

    for invoice in invoices:
        for line in invoice.get("line_items", []):
            lookup = (line.get("item_lookup_name") or "").strip()
            if not lookup or lookup in refs:
                continue
            ref, is_fallback = resolve_item_ref(
                lookup, item_cache, normalized_cache, db_overrides, fallback_ref
            )
            refs[lookup] = ref
            if is_fallback:
                fallback_names.add(lookup)

    return refs, fallback_names
