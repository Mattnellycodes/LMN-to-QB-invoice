"""QuickBooks Online customer operations."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import requests

from src.qbo.context import get_qbo_credentials


def get_api_base_url() -> str:
    """Get the QBO API base URL based on environment setting."""
    environment = os.getenv("QBO_ENVIRONMENT", "production")
    if environment == "sandbox":
        return "https://sandbox-quickbooks.api.intuit.com/v3/company"
    return "https://quickbooks.api.intuit.com/v3/company"


def get_all_customers() -> List[Dict]:
    """
    Fetch all customers from QuickBooks Online.

    Returns list of customer objects with Id, DisplayName, etc.
    """
    access_token, realm_id = get_qbo_credentials()

    customers = []
    start_position = 1
    max_results = 1000

    while True:
        query = f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {max_results}"
        url = f"{get_api_base_url()}/{realm_id}/query"

        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={"query": query},
        )
        response.raise_for_status()

        data = response.json()
        query_response = data.get("QueryResponse", {})
        batch = query_response.get("Customer", [])

        if not batch:
            break

        customers.extend(batch)
        start_position += len(batch)

        if len(batch) < max_results:
            break

    return customers


def get_customer_by_id(customer_id: str) -> Optional[Dict]:
    """Fetch a single customer by QBO ID."""
    access_token, realm_id = get_qbo_credentials()

    url = f"{get_api_base_url()}/{realm_id}/customer/{customer_id}"

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return response.json().get("Customer")


def search_customers_by_name(name: str) -> List[Dict]:
    """Search for customers by display name (partial match)."""
    access_token, realm_id = get_qbo_credentials()

    safe_name = name.replace("'", "\\'")
    query = f"SELECT * FROM Customer WHERE DisplayName LIKE '%{safe_name}%'"

    url = f"{get_api_base_url()}/{realm_id}/query"

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"query": query},
    )
    response.raise_for_status()

    data = response.json()
    return data.get("QueryResponse", {}).get("Customer", [])


def export_customers_for_mapping(output_path: str) -> None:
    """
    Export all QBO customers to a CSV for mapping reference.

    Creates a file with QBO_CustomerID, DisplayName that can be used
    to manually build the JobsiteID mapping.
    """
    import csv

    customers = get_all_customers()

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["QBO_CustomerID", "DisplayName", "Email"])
        writer.writeheader()
        for customer in customers:
            writer.writerow(
                {
                    "QBO_CustomerID": customer.get("Id"),
                    "DisplayName": customer.get("DisplayName"),
                    "Email": customer.get("PrimaryEmailAddr", {}).get("Address", ""),
                }
            )

    print(f"Exported {len(customers)} customers to {output_path}")
