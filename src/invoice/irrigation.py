"""Pair irrigation jobsites with their maintenance counterparts.

LMN jobsites ending in ` - Irr.` are irrigation work for an existing
maintenance customer. VOTF bills both on a single invoice with the
irrigation lines tagged under the QBO "Irrigation" class.

Pairing rule: strip the suffix from an Irr jobsite's display name and look
for a maintenance jobsite in the same upload with a matching (case- and
whitespace-insensitive) name. If found, the two rollups merge onto one
invoice. If not found, the Irr rollup becomes its own standalone invoice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from src.calculations.allocation import JobsiteRollup

logger = logging.getLogger(__name__)


# Matches the literal " - Irr." suffix, case-insensitive, with optional
# surrounding whitespace on either side of the dash and after the period.
IRR_SUFFIX_RE = re.compile(r"\s*-\s*Irr\.\s*$", re.IGNORECASE)


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
        if has_irr_suffix(r.customer_name):
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
        if match is not None:
            groups.append(RollupGroup(maintenance=match, irrigation=irr))
            used_maint_ids.add(match.jobsite_id)
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
