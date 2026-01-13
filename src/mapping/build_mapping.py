"""Utility script to help build the customer mapping file."""

import argparse
from pathlib import Path

from src.parsing.lmn_parser import parse_time_data
from src.qbo.customers import export_customers_for_mapping
from src.mapping.customer_mapping import create_mapping_template


def extract_jobsites_from_lmn(time_data_path: str, output_path: str) -> None:
    """
    Extract unique jobsites from LMN time data and create a mapping template.

    The output CSV will have JobsiteID filled in but QBO_CustomerID blank
    for manual completion.
    """
    df = parse_time_data(time_data_path)

    # Get unique jobsite info
    jobsites = df.groupby("JobsiteID").agg({
        "Jobsite": "first",
        "CustomerName": "first",
    }).reset_index()

    print(f"Found {len(jobsites)} unique jobsites")

    # Create template
    create_mapping_template(jobsites["JobsiteID"].tolist(), output_path)

    # Also print the jobsites for reference
    print()
    print("Jobsites found:")
    for _, row in jobsites.iterrows():
        print(f"  {row['JobsiteID']}: {row['CustomerName']} - {row['Jobsite']}")


def export_qbo_customers(output_path: str) -> None:
    """Export all QBO customers to CSV for reference when building mapping."""
    print("Fetching customers from QuickBooks...")
    export_customers_for_mapping(output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Build customer mapping between LMN JobsiteID and QBO CustomerID"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Extract jobsites from LMN
    lmn_parser = subparsers.add_parser(
        "lmn-jobsites",
        help="Extract unique jobsites from LMN export",
    )
    lmn_parser.add_argument(
        "--input",
        required=True,
        help="Path to LMN time data CSV",
    )
    lmn_parser.add_argument(
        "--output",
        default="config/lmn_jobsites.csv",
        help="Output path for jobsite template (default: config/lmn_jobsites.csv)",
    )

    # Export QBO customers
    qbo_parser = subparsers.add_parser(
        "qbo-customers",
        help="Export QBO customers for mapping reference",
    )
    qbo_parser.add_argument(
        "--output",
        default="config/qbo_customers.csv",
        help="Output path for customer list (default: config/qbo_customers.csv)",
    )

    args = parser.parse_args()

    if args.command == "lmn-jobsites":
        extract_jobsites_from_lmn(args.input, args.output)
    elif args.command == "qbo-customers":
        export_qbo_customers(args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
