"""Main entry point for LMN to QuickBooks invoice automation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.parsing.lmn_parser import parse_time_data, parse_service_data
from src.calculations.time_calc import calculate_billable_hours
from src.invoice.line_items import build_all_invoices, InvoiceData
from src.mapping.customer_mapping import (
    load_customer_mapping,
    get_qbo_customer_id,
    find_unmapped_jobsites,
)
from src.mapping.interactive_mapping import (
    build_unmapped_context,
    prompt_interactive_mapping,
)
from src.qbo.invoices import create_draft_invoice, InvoiceResult


def process_lmn_exports(
    time_data_path: str,
    service_data_path: str,
    mapping_path: Optional[str] = None,
    invoice_date: Optional[str] = None,
    dry_run: bool = False,
) -> Dict:
    """
    Main processing function: LMN CSVs -> QBO draft invoices.

    Args:
        time_data_path: Path to LMN Job History Time Data CSV
        service_data_path: Path to LMN Job History Service Data CSV
        mapping_path: Path to customer mapping CSV (optional, uses default)
        invoice_date: Invoice date in YYYY-MM-DD format (optional, uses today)
        dry_run: If True, don't create invoices in QBO, just show what would be created

    Returns:
        Summary dict with created, skipped, and errored invoices
    """
    print("LMN to QuickBooks Invoice Automation")
    print("=" * 50)
    print()

    # Parse input files
    print(f"Loading time data: {time_data_path}")
    time_df = parse_time_data(time_data_path)
    print(f"  Found {len(time_df)} time entries")

    print(f"Loading service data: {service_data_path}")
    service_df = parse_service_data(service_data_path)
    print(f"  Found {len(service_df)} service entries")

    # Calculate billable hours
    print()
    print("Calculating billable hours...")
    jobsite_hours = calculate_billable_hours(time_df)
    print(f"  Found {len(jobsite_hours)} unique jobsites")

    # Load customer mapping
    print()
    print("Loading customer mapping...")
    mappings = load_customer_mapping(mapping_path)
    print(f"  Loaded {len(mappings)} mappings")

    # Check for unmapped jobsites and prompt for mapping
    jobsite_ids = [jh.jobsite_id for jh in jobsite_hours]
    unmapped = find_unmapped_jobsites(jobsite_ids, mappings)
    if unmapped:
        unmapped_context = build_unmapped_context(jobsite_hours, unmapped)
        mappings = prompt_interactive_mapping(
            unmapped_context=unmapped_context,
            existing_mappings=mappings,
            mapping_path=mapping_path,
        )

    # Build invoice data
    print()
    print("Building invoices...")
    invoices = build_all_invoices(jobsite_hours, service_df, invoice_date)
    print(f"  Built {len(invoices)} invoices with billable amounts")

    # Create invoices in QBO
    print()
    if dry_run:
        print("DRY RUN - Would create these invoices:")
    else:
        print("Creating draft invoices in QuickBooks...")
    print()

    results = {
        "created": [],
        "skipped": [],
        "errors": [],
    }

    for invoice in invoices:
        qbo_customer_id = get_qbo_customer_id(invoice.jobsite_id, mappings)

        if not qbo_customer_id:
            print(f"  ⚠ {invoice.customer_name} (JobsiteID: {invoice.jobsite_id}) - SKIPPED (not in mapping)")
            results["skipped"].append(invoice)
            continue

        if dry_run:
            print(f"  → {invoice.customer_name} - ${invoice.total:.2f} ({len(invoice.line_items)} line items)")
            results["created"].append(invoice)
        else:
            result = create_draft_invoice(invoice, qbo_customer_id)

            if result.success:
                print(f"  ✓ {result.customer_name} - ${result.total:.2f} - Invoice #{result.invoice_number}")
                results["created"].append(result)
            else:
                print(f"  ✗ {result.customer_name} - ERROR: {result.error}")
                results["errors"].append(result)

    # Summary
    print()
    print("=" * 50)
    print("Summary:")
    print(f"  Created: {len(results['created'])} draft invoices")
    print(f"  Skipped: {len(results['skipped'])} (unmapped customers)")
    print(f"  Errors:  {len(results['errors'])}")

    if not dry_run and results["created"]:
        print()
        print("Next step: Open QuickBooks to review and send draft invoices.")

    return results


def preview_invoices(
    time_data_path: str,
    service_data_path: str,
    invoice_date: Optional[str] = None,
) -> List[InvoiceData]:
    """
    Preview invoices without QBO interaction.

    Useful for verifying calculations before creating invoices.
    """
    time_df = parse_time_data(time_data_path)
    service_df = parse_service_data(service_data_path)
    jobsite_hours = calculate_billable_hours(time_df)
    invoices = build_all_invoices(jobsite_hours, service_df, invoice_date)

    print(f"Preview: {len(invoices)} invoices")
    print()

    for inv in invoices:
        print(f"{inv.customer_name} ({inv.jobsite_id})")
        print(f"  Date: {inv.invoice_date}")
        for item in inv.line_items:
            print(f"    {item.description}: {item.quantity} x ${item.rate:.2f} = ${item.amount:.2f}")
        print(f"  Subtotal: ${inv.subtotal:.2f}")
        print(f"  Fee: ${inv.direct_payment_fee:.2f}")
        print(f"  Total: ${inv.total:.2f}")
        print()

    return invoices


def main():
    # TODO: Production version will have drag-and-drop UI for adding files
    time_data = "docs/sample_data/time_data_sample.csv"
    service_data = "docs/sample_data/service_data_sample.csv"

    if not Path(time_data).exists():
        print(f"ERROR: Time data file not found: {time_data}")
        return 1

    if not Path(service_data).exists():
        print(f"ERROR: Service data file not found: {service_data}")
        return 1

    process_lmn_exports(time_data, service_data)

    return 0


if __name__ == "__main__":
    exit(main())
