"""Interactive CLI for mapping LMN jobsites to QuickBooks customers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Union
from pathlib import Path

from src.calculations.time_calc import JobsiteHours
from src.mapping.customer_mapping import (
    CustomerMapping,
    save_customer_mapping,
    DEFAULT_MAPPING_FILE,
)
from src.qbo.customers import search_customers_by_name


@dataclass
class UnmappedJobsite:
    """Context for an unmapped jobsite to show the user."""

    jobsite_id: str
    jobsite_name: str
    lmn_customer_name: str
    estimated_amount: float


def build_unmapped_context(
    jobsite_hours: List[JobsiteHours],
    unmapped_ids: List[str],
) -> List[UnmappedJobsite]:
    """
    Build context objects for unmapped jobsites.

    Args:
        jobsite_hours: List of JobsiteHours from time calculations
        unmapped_ids: List of jobsite IDs that don't have mappings

    Returns:
        List of UnmappedJobsite with context for display
    """
    # Build lookup from jobsite_hours
    hours_by_id = {jh.jobsite_id: jh for jh in jobsite_hours}

    unmapped = []
    for jobsite_id in unmapped_ids:
        jh = hours_by_id.get(jobsite_id)
        if jh:
            estimated = jh.total_billable_hours * jh.billable_rate
            unmapped.append(
                UnmappedJobsite(
                    jobsite_id=jobsite_id,
                    jobsite_name=jh.jobsite_name,
                    lmn_customer_name=jh.customer_name,
                    estimated_amount=estimated,
                )
            )

    return unmapped


def display_search_results(matches: List[Dict]) -> None:
    """Display QBO search results in a numbered list."""
    for i, customer in enumerate(matches, 1):
        customer_id = customer.get("Id", "?")
        display_name = customer.get("DisplayName", "Unknown")
        print(f"  [{i}] {display_name} (QBO ID: {customer_id})")


def get_user_selection(max_index: int) -> Optional[int]:
    """
    Prompt user to select from numbered list.

    Returns:
        Index (0-based) if user selected a number
        -1 if user wants to re-search
        None if user wants to skip
    """
    while True:
        prompt = f"Select [1-{max_index}], 'r' to search again, or 's' to skip: "
        response = input(prompt).strip().lower()

        if response == "s":
            return None
        if response == "r":
            return -1

        try:
            num = int(response)
            if 1 <= num <= max_index:
                return num - 1  # Convert to 0-based index
            print(f"  Please enter a number between 1 and {max_index}")
        except ValueError:
            print("  Invalid input. Enter a number, 'r', or 's'")


def search_and_select_customer(search_term: str) -> Optional[Dict]:
    """
    Search QBO for customers matching search_term and let user select.

    Args:
        search_term: Customer name to search for

    Returns:
        Customer dict with 'Id' and 'DisplayName' if selected
        None if user skipped or no matches
    """
    print(f"\nSearching QuickBooks for \"{search_term}\"...")

    try:
        matches = search_customers_by_name(search_term)
    except Exception as e:
        print(f"\n  ERROR: Could not search QuickBooks: {e}")
        return None

    if not matches:
        print("\n  No customers found matching that name.")
        return None

    print()
    display_search_results(matches)
    print()

    selection = get_user_selection(len(matches))

    if selection is None:
        return None  # User skipped
    if selection == -1:
        return {"_research": True}  # Signal to search again

    return matches[selection]


def prompt_single_jobsite_mapping(
    unmapped: UnmappedJobsite,
    index: int,
    total: int,
) -> Optional[CustomerMapping]:
    """
    Interactive prompt for mapping a single unmapped jobsite.

    Args:
        unmapped: The unmapped jobsite context
        index: Current index (1-based) for display
        total: Total number of unmapped jobsites

    Returns:
        CustomerMapping if user completed mapping
        None if user chose to skip
    """
    print()
    print("-" * 50)
    print(f"Jobsite {index} of {total}")
    print("-" * 50)
    print(f"  LMN Jobsite ID:    {unmapped.jobsite_id}")
    print(f"  LMN Jobsite Name:  {unmapped.jobsite_name}")
    print(f"  LMN Customer Name: {unmapped.lmn_customer_name}")
    print(f"  Est. Invoice:      ${unmapped.estimated_amount:.2f}")
    print()

    while True:
        search_term = input("Enter QuickBooks customer name to search (or 's' to skip): ").strip()

        if search_term.lower() == "s":
            print(f"\n  Skipped: {unmapped.jobsite_id}")
            return None

        if not search_term:
            print("  Please enter a customer name to search")
            continue

        result = search_and_select_customer(search_term)

        if result is None:
            # No matches or error - let user try again
            continue

        if result.get("_research"):
            # User wants to search again
            continue

        # User selected a customer
        customer_id = result.get("Id")
        display_name = result.get("DisplayName")

        mapping = CustomerMapping(
            jobsite_id=unmapped.jobsite_id,
            qbo_customer_id=customer_id,
            qbo_display_name=display_name,
            notes="",
        )

        print(f"\n  Mapped: JobsiteID {unmapped.jobsite_id} -> QBO CustomerID {customer_id} ({display_name})")
        return mapping


def prompt_interactive_mapping(
    unmapped_context: List[UnmappedJobsite],
    existing_mappings: Dict[str, CustomerMapping],
    mapping_path: Optional[Union[str, Path]] = None,
) -> Dict[str, CustomerMapping]:
    """
    Main interactive loop for mapping unmapped jobsites.

    Prompts user to map each unmapped jobsite to a QBO customer.
    Saves mappings incrementally after each successful mapping.

    Args:
        unmapped_context: List of unmapped jobsites with context
        existing_mappings: Current mappings dict (will be updated)
        mapping_path: Path to mapping CSV file

    Returns:
        Updated mappings dict with any new mappings added
    """
    if not unmapped_context:
        return existing_mappings

    path = Path(mapping_path) if mapping_path else DEFAULT_MAPPING_FILE

    print()
    print("=" * 50)
    print(f"UNMAPPED JOBSITES FOUND: {len(unmapped_context)}")
    print("=" * 50)
    print()
    print("The following jobsites from LMN do not have QuickBooks customer mappings.")
    print("You can map them now, or skip to map them later.")

    mapped_count = 0
    skipped = []

    for i, unmapped in enumerate(unmapped_context, 1):
        result = prompt_single_jobsite_mapping(unmapped, i, len(unmapped_context))

        if result:
            # Add to mappings and save immediately
            existing_mappings[unmapped.jobsite_id] = result
            save_customer_mapping(existing_mappings, path)
            mapped_count += 1
        else:
            skipped.append(unmapped.jobsite_id)

    # Print summary
    print()
    print("=" * 50)
    print("MAPPING COMPLETE")
    print("=" * 50)
    print(f"  Mapped:  {mapped_count} jobsites")
    print(f"  Skipped: {len(skipped)} jobsites")
    if skipped:
        for jid in skipped:
            print(f"    - {jid}")
    print()

    if mapped_count > 0:
        print(f"Mappings saved to: {path}")
        print()

    print("Continuing with invoice creation...")

    return existing_mappings
