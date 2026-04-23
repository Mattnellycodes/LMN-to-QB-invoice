"""Drive-time allocation from the LMN *SHOP pool across billable jobsites.

Policy (confirmed by user):
  - Shop pool = all tasks under the *SHOP jobsite (CostCode 900 — Land Time
    and Drive Time), keyed by (work_date, foreman).
  - For each (date, foreman) in the shop pool, split hours EQUALLY across
    the unique billable jobsites that same foreman worked that day.
  - Invoices aggregate across multiple days: one invoice per jobsite
    collects every (date, foreman) row plus that jobsite's share of shop
    hours from each day its foremen appeared.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from src.parsing.pdf_parser import (
    SHOP_JOBSITE_ID,
    LineItem,
    ParsedReport,
    Task,
    parse_money,
)

logger = logging.getLogger(__name__)


BILLABLE_COST_CODE = "200"


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

    @property
    def work_hours(self) -> float:
        return sum(self.work_by_date_foreman.values())

    @property
    def total_billable_hours(self) -> float:
        return self.work_hours + self.allocated_drive_hours

    @property
    def work_dates(self) -> list[str]:
        return sorted({date for date, _ in self.work_by_date_foreman})

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


def compute(report: ParsedReport) -> AllocationResult:
    """Roll up the parsed report into per-jobsite invoice inputs."""
    shop_pool = build_shop_pool(report.tasks)
    rollups: dict[str, JobsiteRollup] = {}

    # Pass 1: accumulate billable work hours, services, and hourly rate.
    for task in report.tasks:
        if task.jobsite_id == SHOP_JOBSITE_ID:
            continue
        if task.cost_code_num != BILLABLE_COST_CODE:
            continue

        rollup = rollups.get(task.jobsite_id)
        if rollup is None:
            rollup = JobsiteRollup(
                jobsite_id=task.jobsite_id,
                customer_name=task.customer_name,
            )
            rollups[task.jobsite_id] = rollup

        if task.date and task.foreman:
            key = (task.date, task.foreman)
            rollup.work_by_date_foreman[key] = (
                rollup.work_by_date_foreman.get(key, 0.0) + task.task_man_hrs
            )

        for service in task.services:
            rollup.services.append(
                _service_to_dict(service, task)
            )

        if rollup.hourly_rate == 0.0:
            for rate_row in task.rates:
                rate_val = parse_money(rate_row.rate)
                if rate_val > 0:
                    rollup.hourly_rate = rate_val
                    rollup.hourly_rate_name = rate_row.description
                    break

    # Pass 2: allocate shop hours.
    # For each (date, foreman), find unique billable jobsites where that
    # foreman worked that day; split shop hours equally.
    jobsites_by_day_foreman: dict[tuple[str, str], set[str]] = defaultdict(set)
    for jobsite_id, rollup in rollups.items():
        for (date, foreman) in rollup.work_by_date_foreman:
            jobsites_by_day_foreman[(date, foreman)].add(jobsite_id)

    for (date, foreman), jobsites in jobsites_by_day_foreman.items():
        shop_hours = shop_pool.get((date, foreman), 0.0)
        if shop_hours <= 0 or not jobsites:
            continue
        share = shop_hours / len(jobsites)
        shared = sorted(jobsites)
        logger.debug(
            "Allocating: date=%s foreman=%s shop_hrs=%.2f jobsites=%d share=%.2f",
            date,
            foreman,
            shop_hours,
            len(jobsites),
            share,
        )
        for jobsite_id in jobsites:
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
