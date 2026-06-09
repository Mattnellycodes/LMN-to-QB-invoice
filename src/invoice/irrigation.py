"""Pair irrigation jobsites with their maintenance counterparts.

Irrigation work for an existing maintenance customer lives on a separate LMN
jobsite. VOTF bills both on a single invoice with the irrigation lines tagged
under the QBO "Irrigation" class.

Pairing runs two strategies in order:
  1. By shared QBO customer (authoritative): if an irrigation jobsite and a
     maintenance jobsite are mapped to the same `qbo_customer_id`, merge them
     regardless of their names.
  2. By name (fallback): for rollups the first pass didn't merge, strip a
     trailing ` - Irr.` suffix and match an irrigation jobsite to a
     maintenance jobsite with the same (case- and whitespace-insensitive)
     name. Anything still unmatched becomes its own standalone invoice.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

from src.calculations.allocation import JobsiteRollup, merge_rollups

logger = logging.getLogger(__name__)


# Matches LMN's irrigation-suffix variants, case-insensitive:
#   " - Irr.", "-Irr.", " - Irr", " - Irrigation", "-Irrigation", etc.
# Optional whitespace around the dash, optional period, optional "igation"
# expansion. The maintenance counterpart sheds the suffix entirely so paired
# names match by `_match_key` after stripping.
IRR_SUFFIX_RE = re.compile(r"\s*-\s*Irr(?:igation)?\.?\s*$", re.IGNORECASE)


def has_irr_suffix(name: Optional[str]) -> bool:
    """True if `name` ends with ` - Irr.` (case-insensitive)."""
    return bool(IRR_SUFFIX_RE.search(name or ""))


def strip_irr_suffix(name: Optional[str]) -> str:
    """Remove a trailing ` - Irr.` suffix; return the stripped name."""
    return IRR_SUFFIX_RE.sub("", name or "").strip()


def _match_key(name: str) -> str:
    return strip_irr_suffix(name).casefold().strip()


@dataclass
class RollupGroup:
    """One invoice's worth of rollups.

    Exactly one of `maintenance`/`irrigation` may be None. When both are set,
    the Irr rollup's lines merge onto the maintenance invoice tagged as the
    Irrigation QBO class.
    """

    maintenance: Optional[JobsiteRollup]
    irrigation: Optional[JobsiteRollup]


def pair_rollups(rollups: Iterable[JobsiteRollup]) -> list[RollupGroup]:
    """Group rollups into invoice-level bundles.

    Merges irrigation onto maintenance first by shared QBO customer, then by
    name for whatever the first pass left behind. With no `qbo_customer_id`
    set on any rollup, this is identical to the name-only pairing.
    """
    rollups_list = list(rollups)
    id_groups, remainder = _pair_by_qbo_customer_id(rollups_list)
    return id_groups + _pair_by_name(remainder)


def _pair_by_qbo_customer_id(
    rollups: list[JobsiteRollup],
) -> tuple[list[RollupGroup], list[JobsiteRollup]]:
    """Merge irrigation onto maintenance when they share a QBO customer.

    Returns (merged groups, leftover rollups). A customer group merges only
    when it has exactly one maintenance rollup plus one-or-more irrigation
    rollups; multiple irrigation rollups collapse into one via `merge_rollups`.
    Every other rollup — unmapped, no irrigation twin, or an ambiguous
    multi-maintenance customer — is returned as leftover for the name fallback.
    """
    by_customer: dict[str, list[JobsiteRollup]] = defaultdict(list)
    leftover: list[JobsiteRollup] = []
    for r in rollups:
        if r.qbo_customer_id:
            by_customer[r.qbo_customer_id].append(r)
        else:
            leftover.append(r)

    groups: list[RollupGroup] = []
    for customer_id, members in by_customer.items():
        maint = [m for m in members if not m.is_irrigation]
        irr = [m for m in members if m.is_irrigation]
        if len(maint) == 1 and irr:
            irrigation = (
                irr[0]
                if len(irr) == 1
                else merge_rollups(
                    irr,
                    primary_jobsite_id=irr[0].jobsite_id,
                    display_name=strip_irr_suffix(irr[0].customer_name),
                    is_irrigation=True,
                )
            )
            groups.append(RollupGroup(maintenance=maint[0], irrigation=irrigation))
            logger.info(
                "Merged %d irrigation jobsite(s) onto maintenance %r (id=%s) "
                "via QBO customer %s",
                len(irr),
                maint[0].customer_name,
                maint[0].jobsite_id,
                customer_id,
            )
        else:
            if len(maint) > 1 and irr:
                logger.warning(
                    "Ambiguous QBO customer %s: %d maintenance jobsites with "
                    "irrigation — falling back to name matching",
                    customer_id,
                    len(maint),
                )
            leftover.extend(members)

    return groups, leftover


def _pair_by_name(rollups: Iterable[JobsiteRollup]) -> list[RollupGroup]:
    """Group rollups by ` - Irr.`-stripped name (fallback for unmapped twins).

    Emits: one merged group per Irr rollup whose stripped name matches a
    maintenance rollup; one standalone group for each unmatched Irr rollup;
    one standalone group for each maintenance rollup that no Irr paired with.

    Ambiguity (two maint rollups share the same stripped name): log a warning
    and remove both from the index — affected Irr rollups fall through to
    standalone rather than merging onto the wrong customer.
    """
    maint_rollups: list[JobsiteRollup] = []
    irr_rollups: list[JobsiteRollup] = []
    for r in rollups:
        if r.is_irrigation:
            irr_rollups.append(r)
        else:
            maint_rollups.append(r)

    index: dict[str, JobsiteRollup] = {}
    ambiguous: set[str] = set()
    for r in maint_rollups:
        key = _match_key(r.customer_name)
        if key in index:
            ambiguous.add(key)
        else:
            index[key] = r
    for key in ambiguous:
        dup_names = [r.customer_name for r in maint_rollups if _match_key(r.customer_name) == key]
        logger.warning(
            "Ambiguous maintenance name %r (matches: %s) — Irr rollups will not merge",
            key,
            dup_names,
        )
        index.pop(key, None)

    used_maint_ids: set[str] = set()
    groups: list[RollupGroup] = []

    for irr in irr_rollups:
        key = _match_key(irr.customer_name)
        match = index.get(key)
        # Belt-and-suspenders: skip a maint rollup that was already consumed
        # by a previous Irr rollup with the same stripped name. Without this,
        # two Irr rollups whose stripped names collide would both pair with
        # (and double-bill) the same maintenance rollup.
        if match is not None and match.jobsite_id not in used_maint_ids:
            groups.append(RollupGroup(maintenance=match, irrigation=irr))
            used_maint_ids.add(match.jobsite_id)
            del index[key]
            logger.debug(
                "Paired irrigation %r (id=%s) with maintenance %r (id=%s)",
                irr.customer_name,
                irr.jobsite_id,
                match.customer_name,
                match.jobsite_id,
            )
        else:
            groups.append(RollupGroup(maintenance=None, irrigation=irr))
            logger.info(
                "Standalone irrigation jobsite %r (id=%s) — no matching "
                "maintenance jobsite in this upload",
                irr.customer_name,
                irr.jobsite_id,
            )

    for maint in maint_rollups:
        if maint.jobsite_id not in used_maint_ids:
            groups.append(RollupGroup(maintenance=maint, irrigation=None))

    return groups
