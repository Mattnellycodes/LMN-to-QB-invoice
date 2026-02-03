"""Build invoice line items from LMN data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

import pandas as pd

from src.calculations.time_calc import JobsiteHours


@dataclass
class LineItem:
    """A single invoice line item."""

    description: str
    quantity: float
    rate: float
    amount: float


@dataclass
class InvoiceData:
    """Complete invoice data for a single jobsite."""

    jobsite_id: str
    jobsite_name: str
    customer_name: str
    invoice_date: str
    line_items: list[LineItem] = field(default_factory=list)
    subtotal: float = 0
    direct_payment_fee: float = 0
    total: float = 0
    timesheet_ids: list[str] = field(default_factory=list)
    work_dates: list[str] = field(default_factory=list)


def calculate_direct_payment_fee(subtotal: float) -> float:
    """
    Calculate direct payment fee based on subtotal.

    Fee tiers:
    - Under $1,000 -> 10% of subtotal
    - $1,000 - $2,000 -> $15 flat
    - Over $2,000 -> $20 flat
    """
    if subtotal < 1000:
        return round(subtotal * 0.10, 2)
    elif subtotal <= 2000:
        return 15.00
    else:
        return 20.00


def format_labor_description(dates: list[str], task_summary: str = "") -> str:
    """
    Format the labor line item description.

    Format: "Skilled Garden Hourly Labor [date(s)] - [task summary]"
    """
    if not dates:
        date_str = ""
    elif len(dates) == 1:
        # Single date - format as MM/DD
        date_str = format_date_short(dates[0])
    else:
        # Multiple dates - show range
        date_str = f"{format_date_short(dates[0])}-{format_date_short(dates[-1])}"

    base = f"Skilled Garden Hourly Labor {date_str}".strip()

    if task_summary:
        return f"{base}- {task_summary}"
    return base


def format_date_short(date_str: str) -> str:
    """Convert date string to MM/DD format."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%-m/%d")
    except (ValueError, TypeError):
        return date_str


def extract_service_line_items(
    service_df: pd.DataFrame, jobsite_id: str
) -> list[LineItem]:
    """
    Extract billable service/material line items for a jobsite.

    Filters to: Total Price > 0 AND Invoice Type != 'Included'
    """
    jobsite_services = service_df[service_df["JobsiteID"] == jobsite_id]

    # Filter to billable items
    billable = jobsite_services[
        (jobsite_services["Total Price"] > 0)
        & (jobsite_services["Invoice Type"].str.lower() != "included")
    ]

    line_items = []
    for _, row in billable.iterrows():
        line_items.append(
            LineItem(
                description=row["Service_Activity"],
                quantity=row["Timesheet Qty"],
                rate=row["Unit Price"],
                amount=row["Total Price"],
            )
        )

    return line_items


def build_invoice(
    jobsite_hours: JobsiteHours,
    service_df: pd.DataFrame,
    invoice_date: Optional[str] = None,
) -> InvoiceData:
    """
    Build complete invoice data for a jobsite.

    Combines labor hours with materials/services and adds direct payment fee.
    """
    if invoice_date is None:
        invoice_date = datetime.now().strftime("%Y-%m-%d")

    invoice = InvoiceData(
        jobsite_id=jobsite_hours.jobsite_id,
        jobsite_name=jobsite_hours.jobsite_name,
        customer_name=jobsite_hours.customer_name,
        invoice_date=invoice_date,
        timesheet_ids=jobsite_hours.timesheet_ids or [],
        work_dates=jobsite_hours.dates or [],
    )

    # Add labor line item (if there are billable hours)
    if jobsite_hours.total_billable_hours > 0:
        labor_amount = round(
            jobsite_hours.total_billable_hours * jobsite_hours.billable_rate, 2
        )
        labor_item = LineItem(
            description=format_labor_description(jobsite_hours.dates),
            quantity=jobsite_hours.total_billable_hours,
            rate=jobsite_hours.billable_rate,
            amount=labor_amount,
        )
        invoice.line_items.append(labor_item)

    # Add service/material line items
    service_items = extract_service_line_items(service_df, jobsite_hours.jobsite_id)
    invoice.line_items.extend(service_items)

    # Calculate totals
    invoice.subtotal = round(sum(item.amount for item in invoice.line_items), 2)
    invoice.direct_payment_fee = calculate_direct_payment_fee(invoice.subtotal)
    invoice.total = round(invoice.subtotal + invoice.direct_payment_fee, 2)

    # Add fee as line item
    if invoice.direct_payment_fee > 0:
        fee_item = LineItem(
            description="Please subtract if paying by USPS check",
            quantity=1,
            rate=invoice.direct_payment_fee,
            amount=invoice.direct_payment_fee,
        )
        invoice.line_items.append(fee_item)

    return invoice


def build_all_invoices(
    jobsite_hours_list: List[JobsiteHours],
    service_df: pd.DataFrame,
    invoice_date: Optional[str] = None,
) -> List[InvoiceData]:
    """Build invoices for all jobsites."""
    invoices = []

    for jobsite_hours in jobsite_hours_list:
        invoice = build_invoice(jobsite_hours, service_df, invoice_date)

        # Skip invoices with no line items (nothing to bill)
        if invoice.subtotal > 0:
            invoices.append(invoice)

    return invoices
