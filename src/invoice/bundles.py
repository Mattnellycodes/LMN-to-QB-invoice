"""Hardcoded multi-jobsite bundles.

Some customers split their work across many LMN jobsites (e.g., a single
HOA with one maintenance jobsite and several irrigation zones). When that
happens we want a single QBO invoice covering every jobsite, not one
invoice per LMN jobsite.

Each `JobsiteBundle` matches rollups by `customer_name` regex and merges
its members into at most two synthetic rollups (one per cost-code class)
that share the bundle's `primary_jobsite_id` — the jobsite whose existing
LMN→QBO mapping decides which QBO customer the merged invoice goes to.

`apply_bundles` returns `(remaining_rollups, pre_built_groups)`:
- `remaining_rollups`: rollups not claimed by any bundle. Pass these to
  `pair_rollups` for the usual suffix-based merging.
- `pre_built_groups`: one `RollupGroup` per bundle that had at least one
  member. These bypass `pair_rollups` since their pair structure is fixed
  by the bundle definition.

Each synthetic rollup carries `member_rollups` listing the originals so
the invoice builder can emit one `InvoiceSource` per contributing jobsite
(preserves duplicate detection, zero-price-item lookup, and per-jobsite
invoice_history rows).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from src.calculations.allocation import JobsiteRollup
from src.invoice.irrigation import RollupGroup

logger = logging.getLogger(__name__)


@dataclass
class JobsiteBundle:
    name: str  # internal label for logs
    name_pattern: re.Pattern  # matched against rollup.customer_name
    primary_jobsite_id: str  # this jobsite's QBO mapping drives the merged invoice
    display_name: str  # customer-facing name on the merged invoice


HARDCODED_BUNDLES: list[JobsiteBundle] = [
    JobsiteBundle(
        name="Cannery HOA",
        # Matches "Cannery HOA", "Cannery HOA - A118 - Irr.", etc. Word
        # boundary keeps "Cannery HOA Plaza" out unless that's in the name.
        name_pattern=re.compile(r"^\s*cannery\s+hoa\b", re.IGNORECASE),
        # Already maps to "Cannery District HOA" QBO customer.
        primary_jobsite_id="5923663W",
        display_name="Cannery District HOA",
    ),
]


def _find_bundle(rollup: JobsiteRollup) -> JobsiteBundle | None:
    """Return the first bundle whose pattern matches `rollup.customer_name`."""
    for b in HARDCODED_BUNDLES:
        if b.name_pattern.search(rollup.customer_name or ""):
            return b
    return None


def _merge_rollups(
    members: list[JobsiteRollup],
    *,
    primary_jobsite_id: str,
    display_name: str,
    is_irrigation: bool,
) -> JobsiteRollup:
    """Merge member rollups into one synthetic rollup.

    Sums work_by_date_foreman and allocated_drive_hours, concatenates
    services and task_notes (de-duped), takes the first non-zero
    hourly_rate / hourly_rate_name encountered.
    """
    merged = JobsiteRollup(
        jobsite_id=primary_jobsite_id,
        customer_name=display_name,
        is_irrigation=is_irrigation,
    )
    for r in members:
        for key, hours in r.work_by_date_foreman.items():
            merged.work_by_date_foreman[key] = (
                merged.work_by_date_foreman.get(key, 0.0) + hours
            )
        merged.allocated_drive_hours += r.allocated_drive_hours
        merged.services.extend(r.services)
        if merged.hourly_rate == 0.0 and r.hourly_rate > 0:
            merged.hourly_rate = r.hourly_rate
            merged.hourly_rate_name = r.hourly_rate_name
        for note in r.task_notes:
            if note not in merged.task_notes:
                merged.task_notes.append(note)
    merged.member_rollups = list(members)
    return merged


def apply_bundles(
    rollups: Iterable[JobsiteRollup],
) -> tuple[list[JobsiteRollup], list[RollupGroup]]:
    """Split rollups into (non-bundled, pre-built bundle groups).

    Non-bundled rollups go through `pair_rollups` as usual. Bundle groups
    are emitted directly with the bundle's chosen Maintenance / Irrigation
    structure.
    """
    rollups_list = list(rollups)
    bundled: dict[str, list[JobsiteRollup]] = {}
    remaining: list[JobsiteRollup] = []

    for r in rollups_list:
        bundle = _find_bundle(r)
        if bundle is None:
            remaining.append(r)
        else:
            bundled.setdefault(bundle.name, []).append(r)

    pre_built_groups: list[RollupGroup] = []
    for bundle in HARDCODED_BUNDLES:
        members = bundled.get(bundle.name) or []
        if not members:
            continue
        maint_members = [m for m in members if not m.is_irrigation]
        irr_members = [m for m in members if m.is_irrigation]

        maint_synth = (
            _merge_rollups(
                maint_members,
                primary_jobsite_id=bundle.primary_jobsite_id,
                display_name=bundle.display_name,
                is_irrigation=False,
            )
            if maint_members
            else None
        )
        irr_synth = (
            _merge_rollups(
                irr_members,
                primary_jobsite_id=bundle.primary_jobsite_id,
                display_name=bundle.display_name,
                is_irrigation=True,
            )
            if irr_members
            else None
        )

        if maint_synth is None and irr_synth is None:
            continue

        pre_built_groups.append(
            RollupGroup(maintenance=maint_synth, irrigation=irr_synth)
        )
        logger.info(
            "Bundle %r: merged %d maint + %d irr member(s) into one invoice "
            "(primary=%s display=%r)",
            bundle.name,
            len(maint_members),
            len(irr_members),
            bundle.primary_jobsite_id,
            bundle.display_name,
        )

    return remaining, pre_built_groups
