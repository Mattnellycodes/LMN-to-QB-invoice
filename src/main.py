"""Main entry point for LMN to QuickBooks invoice automation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional

from src.parsing.lmn_parser import parse_time_data, parse_service_data
from src.calculations.time_calc import calculate_billable_hours
from src.invoice.line_items import build_all_invoices, InvoiceData
from src.mapping.customer_mapping import (
    load_customer_mapping,
    load_mapping_from_lmn_api,
    get_qbo_customer_id,
    find_unmapped_jobsites,
)
from src.mapping.interactive_mapping import (
    build_unmapped_context,
    prompt_interactive_mapping,
)
from src.qbo.invoices import create_draft_invoice


def check_already_invoiced(jobsite_hours: List) -> Dict[str, List[Dict]]:
    """
    Check which timesheets have already been invoiced.

    Returns:
        {jobsite_id: [{"timesheet_id": ..., "qbo_invoice_number": ...}]}
    """
    try:
        from src.db.invoice_history import find_already_invoiced_timesheets

        # Collect all timesheet IDs
        all_timesheet_ids = []
        for jh in jobsite_hours:
            all_timesheet_ids.extend(jh.timesheet_ids)

        if not all_timesheet_ids:
            return {}

        already_invoiced = find_already_invoiced_timesheets(all_timesheet_ids)

        # Group by jobsite
        result = {}
        for jh in jobsite_hours:
            matches = [
                inv for inv in already_invoiced
                if inv["timesheet_id"] in jh.timesheet_ids
            ]
            if matches:
                result[jh.jobsite_id] = matches

        return result
    except Exception:
        # Database not available - skip overlap check
        return {}


def process_lmn_exports(
    time_data_path: str,
    service_data_path: str,
    mapping_path: Optional[str] = None,
    invoice_date: Optional[str] = None,
    dry_run: bool = False,
    use_csv_mapping: bool = False,
    skip_processed: bool = False,
) -> Dict:
    """
    Main processing function: LMN CSVs -> QBO draft invoices.

    Args:
        time_data_path: Path to LMN Job History Time Data CSV
        service_data_path: Path to LMN Job History Service Data CSV
        mapping_path: Path to customer mapping CSV (when use_csv_mapping=True)
        invoice_date: Invoice date in YYYY-MM-DD format (optional, uses today)
        dry_run: If True, don't create invoices in QBO, just show what would be created
        use_csv_mapping: If True, use CSV mapping; if False (default), use LMN API
        skip_processed: If True, skip timesheets that were already invoiced

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

    # Check for already-invoiced timesheets
    already_invoiced = check_already_invoiced(jobsite_hours)
    if already_invoiced:
        print()
        print("WARNING: Some timesheets have already been invoiced:")
        print("-" * 70)
        for jobsite_id, invoiced_list in already_invoiced.items():
            jh = next((j for j in jobsite_hours if j.jobsite_id == jobsite_id), None)
            customer = jh.customer_name if jh else "Unknown"
            for inv in invoiced_list:
                print(f"  {customer} - Timesheet {inv['timesheet_id']} -> Invoice #{inv['qbo_invoice_number']}")
        print("-" * 70)

        if skip_processed:
            print("  Skipping already-invoiced timesheets (--skip-processed)")
            # Filter out jobsites where ALL timesheets are already invoiced
            invoiced_jobsite_ids = set(already_invoiced.keys())
            jobsite_hours = [
                jh for jh in jobsite_hours
                if jh.jobsite_id not in invoiced_jobsite_ids
            ]
            print(f"  Remaining jobsites: {len(jobsite_hours)}")
        else:
            print("  Use --skip-processed to automatically skip already-invoiced timesheets")
        print()

    # Load customer mapping
    print()
    if use_csv_mapping:
        print("Loading customer mapping from CSV...")
        mappings = load_customer_mapping(mapping_path)
    else:
        print("Loading customer mapping from LMN API (with DB overrides)...")
        mappings = load_mapping_from_lmn_api(use_db_overrides=True, csv_override_path=mapping_path)
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
            preview_mode=dry_run,
        )

    # Build invoice data
    print()
    print("Building invoices...")
    invoices = build_all_invoices(jobsite_hours, service_df, invoice_date)
    print(f"  Built {len(invoices)} invoices with billable amounts")

    # Display mapping verification
    print()
    print("Customer Mapping Verification:")
    print("-" * 70)
    print(f"{'LMN Customer':<25} {'QBO Customer':<25} {'JobsiteID':<15}")
    print("-" * 70)
    for invoice in invoices:
        mapping = mappings.get(str(invoice.jobsite_id))
        qbo_name = mapping.qbo_display_name if mapping else "(NOT MAPPED)"
        lmn_name = invoice.customer_name[:24] if len(invoice.customer_name) > 24 else invoice.customer_name
        qbo_display = qbo_name[:24] if len(qbo_name) > 24 else qbo_name
        print(f"{lmn_name:<25} {qbo_display:<25} {invoice.jobsite_id:<15}")
    print("-" * 70)
    print()

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
    parser = argparse.ArgumentParser(
        description="Create QuickBooks invoices from LMN timesheet exports"
    )
    parser.add_argument(
        "--time-data",
        required=True,
        help="Path to LMN Job History Time Data CSV",
    )
    parser.add_argument(
        "--service-data",
        required=True,
        help="Path to LMN Job History Service Data CSV",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview invoices without creating them in QBO",
    )
    parser.add_argument(
        "--use-csv-mapping",
        action="store_true",
        help="Use CSV file for customer mapping instead of LMN API",
    )
    parser.add_argument(
        "--mapping-path",
        help="Path to customer mapping CSV (only used with --use-csv-mapping)",
    )
    parser.add_argument(
        "--invoice-date",
        help="Invoice date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database tables and exit",
    )
    parser.add_argument(
        "--skip-processed",
        action="store_true",
        help="Skip timesheets that have already been invoiced",
    )

    args = parser.parse_args()

    # Handle database initialization
    if args.init_db:
        from src.db.connection import init_db
        init_db()
        return 0

    # Validate input files
    if not Path(args.time_data).exists():
        print(f"ERROR: Time data file not found: {args.time_data}")
        return 1

    if not Path(args.service_data).exists():
        print(f"ERROR: Service data file not found: {args.service_data}")
        return 1

    process_lmn_exports(
        time_data_path=args.time_data,
        service_data_path=args.service_data,
        mapping_path=args.mapping_path,
        invoice_date=args.invoice_date,
        dry_run=args.preview,
        use_csv_mapping=args.use_csv_mapping,
        skip_processed=args.skip_processed,
    )

    return 0


if __name__ == "__main__":
    exit(main())
