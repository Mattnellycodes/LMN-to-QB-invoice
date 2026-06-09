"""Drive-time allocation from the LMN *SHOP pool across billable jobsites.

Policy (confirmed by user):
  - Shop pool = all tasks under the *SHOP jobsite (CostCode 900 — Land Time
    and Drive Time), keyed by (work_date, foreman).
  - For each (date, foreman) in the shop pool, split hours proportionally
    to each billable jobsite's work hours that day:
        share(jobsite) = shop_hours * work_hours(jobsite) / Σ work_hours
    Fallback: if Σ work_hours is 0 (degenerate), split equally.
  - Invoices aggregate across multiple days: one invoice per jobsite
    collects every (date, foreman) row plus that jobsite's share of shop
    hours from each day its foremen appeared.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from src.parsing.pdf_parser import (
    SHOP_JOBSITE_ID,
    LineItem,
    ParsedReport,
    Task,
    lmn_date_sort_key,
    parse_money,
)

logger = logging.getLogger(__name__)


BILLABLE_COST_CODE = "200"  # Maintenance cost code; kept for tests' use, no longer used as a filter.
IRRIGATION_COST_CODE = "100"  # Installation; classifies a rollup as irrigation work.

# Distinctive cores of LMN's irrigation labor rate-row descriptions, matched
# case-insensitively as substrings so town suffixes ("-Bzn", "-Bozeman") and
# trailing qualifiers ("hourly") are tolerated. A jobsite whose Rates table
# carries any of these is irrigation work even when CostCode 100 isn't set.
IRRIGATION_RATE_MARKERS = (
    "irrigation service call",
    "irrigation technician",
    "winterization",
)

NO_SHOP_ALLOCATION_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "no_shop_allocation.txt"
)


def load_excluded_jobsites(path: Path = NO_SHOP_ALLOCATION_PATH) -> frozenset[str]:
    """Load JobsiteIDs that should not receive any shop-pool allocation."""
    if not path.exists():
        return frozenset()
    ids: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ids.add(line)
    return frozenset(ids)


@dataclass
class AllocationRow:
    """One (date, foreman) contribution to a jobsite's allocated drive time."""

    date: str
    foreman: str
    shared_jobsites: list[str]
    shop_hours: float
    share: float


@dataclass
class JobsiteRollup:
    """Everything needed to build one jobsite's invoice."""

    jobsite_id: str
    customer_name: str
    # (date, foreman) -> billable work hours
    work_by_date_foreman: dict[tuple[str, str], float] = field(default_factory=dict)
    allocated_drive_hours: float = 0.0
    allocation_breakdown: list[AllocationRow] = field(default_factory=list)
    # All service rows from this jobsite's tasks, in task order.
    # Each item is augmented with source_context for zero-price notes.
    services: list[dict] = field(default_factory=list)
    hourly_rate: float = 0.0
    # LMN rate-row description (e.g. "Maintenance Skilled Hourly Labor - TOWN").
    # Used as the QBO item lookup key for the labor line; separate from the
    # customer-facing invoice description.
    hourly_rate_name: str = ""
    # Crew notes recorded on billable tasks for this jobsite, de-duplicated
    # on (date, foreman, notes) and preserved in first-seen order. Shown to
    # the reviewer on the invoice preview; not pushed to QBO.
    task_notes: list[dict] = field(default_factory=list)
    # True if any of this rollup's tasks is irrigation work — either CostCode
    # 100 (Installation) or an irrigation labor rate name (see
    # IRRIGATION_RATE_MARKERS). Drives invoice-side class tagging without
    # leaking QBO class-name strings into the allocation layer.
    is_irrigation: bool = False
    # QBO customer this jobsite maps to, stamped after allocation from the
    # customer mapping (see web_processing). Used to merge an irrigation
    # jobsite onto its maintenance twin even when their names don't align.
    # None until stamped, or when the jobsite is unmapped.
    qbo_customer_id: str | None = None
    # When this rollup is a synthetic merge of several real rollups (see
    # `merge_rollups`), `member_rollups` lists the originals so the invoice
    # builder can emit one InvoiceSource per contributing jobsite.
    # Empty for non-bundled rollups.
    member_rollups: list["JobsiteRollup"] = field(default_factory=list)

    @property
    def work_hours(self) -> float:
        return sum(self.work_by_date_foreman.values())

    @property
    def total_billable_hours(self) -> float:
        return self.work_hours + self.allocated_drive_hours

    @property
    def work_dates(self) -> list[str]:
        return sorted(
            {date for date, _ in self.work_by_date_foreman}, key=lmn_date_sort_key
        )

    @property
    def foremen(self) -> list[str]:
        return sorted({foreman for _, foreman in self.work_by_date_foreman})


@dataclass
class AllocationResult:
    rollups: dict[str, JobsiteRollup]  # jobsite_id -> rollup (excludes *SHOP)
    shop_pool: dict[tuple[str, str], float]  # (date, foreman) -> total hours


def build_shop_pool(tasks: list[Task]) -> dict[tuple[str, str], float]:
    """Sum CostCode 900 task hours under *SHOP, keyed by (date, foreman)."""
    pool: dict[tuple[str, str], float] = defaultdict(float)
    for t in tasks:
        if t.jobsite_id != SHOP_JOBSITE_ID:
            continue
        if not t.date or not t.foreman:
            continue
        pool[(t.date, t.foreman)] += t.task_man_hrs
    return dict(pool)


def _is_irrigation_for_task(task: Task) -> bool:
    """True if a task is irrigation work, by cost code or its labor rate name."""
    if task.cost_code_num == IRRIGATION_COST_CODE:
        return True
    for rate_row in task.rates:
        description = rate_row.description.casefold()
        if any(marker in description for marker in IRRIGATION_RATE_MARKERS):
            return True
    return False


def compute(
    report: ParsedReport,
    excluded_from_shop: frozenset[str] = frozenset(),
) -> AllocationResult:
    """Roll up the parsed report into per-jobsite invoice inputs.

    Jobsite IDs in `excluded_from_shop` still get their own rollup (with real
    work hours and services) but are skipped during shop-pool allocation, so
    the remaining billable jobsites for that (date, foreman) absorb the pool.
    """
    shop_pool = build_shop_pool(report.tasks)
    rollups: dict[str, JobsiteRollup] = {}

    # Pass 1: accumulate billable work hours, services, and hourly rate.
    # Every non-*SHOP task contributes — cost code only determines whether
    # the resulting rollup is classified as irrigation (CC 100) or not.
    for task in report.tasks:
        if task.jobsite_id == SHOP_JOBSITE_ID:
            continue

        rollup = rollups.get(task.jobsite_id)
        if rollup is None:
            rollup = JobsiteRollup(
                jobsite_id=task.jobsite_id,
                customer_name=task.customer_name,
            )
            rollups[task.jobsite_id] = rollup

        # Any task can flag the jobsite as irrigation; the irrigation rate may
        # appear on a later task than the first one seen for this jobsite.
        if not rollup.is_irrigation:
            rollup.is_irrigation = _is_irrigation_for_task(task)

        if task.date and task.foreman:
            key = (task.date, task.foreman)
            rollup.work_by_date_foreman[key] = (
                rollup.work_by_date_foreman.get(key, 0.0) + task.task_man_hrs
            )

        for service in task.services:
            rollup.services.append(
                _service_to_dict(service, task)
            )

        if task.notes:
            entry = {
                "date": task.date,
                "foreman": task.foreman,
                "notes": task.notes,
            }
            if entry not in rollup.task_notes:
                rollup.task_notes.append(entry)

        if rollup.hourly_rate == 0.0:
            for rate_row in task.rates:
                rate_val = parse_money(rate_row.rate)
                if rate_val > 0:
                    rollup.hourly_rate = rate_val
                    rollup.hourly_rate_name = rate_row.description
                    break

    # Pass 2: allocate shop hours, weighted by each jobsite's billable work
    # hours for that (date, foreman). Fallback to equal split if total work
    # hours are zero.
    jobsites_by_day_foreman: dict[tuple[str, str], set[str]] = defaultdict(set)
    for jobsite_id, rollup in rollups.items():
        if jobsite_id in excluded_from_shop:
            continue
        for (date, foreman) in rollup.work_by_date_foreman:
            jobsites_by_day_foreman[(date, foreman)].add(jobsite_id)

    for (date, foreman), jobsites in jobsites_by_day_foreman.items():
        shop_hours = shop_pool.get((date, foreman), 0.0)
        if shop_hours <= 0 or not jobsites:
            continue
        weights = {
            jid: rollups[jid].work_by_date_foreman.get((date, foreman), 0.0)
            for jid in jobsites
        }
        total_weight = sum(weights.values())
        shared = sorted(jobsites)
        for jobsite_id in jobsites:
            if total_weight > 0:
                share = shop_hours * weights[jobsite_id] / total_weight
            else:
                share = shop_hours / len(jobsites)
            logger.debug(
                "Allocating: date=%s foreman=%s jobsite=%s shop_hrs=%.2f "
                "weight=%.2f total_weight=%.2f share=%.2f",
                date,
                foreman,
                jobsite_id,
                shop_hours,
                weights[jobsite_id],
                total_weight,
                share,
            )
            rollup = rollups[jobsite_id]
            rollup.allocated_drive_hours += share
            rollup.allocation_breakdown.append(
                AllocationRow(
                    date=date,
                    foreman=foreman,
                    shared_jobsites=shared,
                    shop_hours=shop_hours,
                    share=share,
                )
            )

    unallocated = [
        (date, foreman)
        for (date, foreman), hrs in shop_pool.items()
        if hrs > 0 and (date, foreman) not in jobsites_by_day_foreman
    ]
    if unallocated:
        logger.warning(
            "Shop pool entries with no matching billable jobsite foreman: %d",
            len(unallocated),
        )

    return AllocationResult(rollups=rollups, shop_pool=shop_pool)


def merge_rollups(
    members: list[JobsiteRollup],
    *,
    primary_jobsite_id: str,
    display_name: str,
    is_irrigation: bool,
) -> JobsiteRollup:
    """Merge member rollups into one synthetic rollup.

    Sums work_by_date_foreman and allocated_drive_hours, concatenates
    services and task_notes (de-duped), takes the first non-zero
    hourly_rate / hourly_rate_name encountered. The originals are kept in
    `member_rollups` so the invoice builder can emit one InvoiceSource per
    contributing jobsite.
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


def _service_to_dict(service: LineItem, task: Task) -> dict:
    """Flatten a service line item with source context for zero-price notes."""
    return {
        "description": service.description,
        "act_qty": service.act_qty,
        "est_cost": service.est_cost,
        "inv_qty": service.inv_qty,
        "rate": service.rate,
        "total_price": service.total_price,
        "source_context": {
            "date": task.date,
            "foreman": task.foreman,
            "notes": task.notes,
        },
    }
