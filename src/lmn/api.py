"""LMN API client for fetching job-to-customer mappings."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import requests

from src.mapping.customer_mapping import CustomerMapping

logger = logging.getLogger(__name__)

LMN_API_URL = "https://accounting-api.golmn.com/qbdata/jobmatching"


def get_lmn_token() -> Optional[str]:
    """
    Get a valid LMN API bearer token.

    Uses the auth module to get a token from:
    1. Cached token from database
    2. Re-authentication with stored credentials
    3. LMN_API_TOKEN environment variable (fallback)
    """
    from src.lmn.auth import get_valid_token
    return get_valid_token()


def get_job_matching() -> List[Dict]:
    """
    Fetch job matching data from LMN API.

    Returns:
        List of job objects with JobsiteID, AccountingID, etc.

    Raises:
        ValueError: If LMN_API_TOKEN is not set
        requests.RequestException: If API request fails
    """
    token = get_lmn_token()
    if not token:
        raise ValueError("LMN_API_TOKEN environment variable not set")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.get(LMN_API_URL, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data.get("lmnitems", [])


def build_mapping_from_lmn(lmn_data: List[Dict]) -> Dict[str, CustomerMapping]:
    """
    Convert LMN API response to CustomerMapping dict.

    Args:
        lmn_data: List of job objects from LMN API

    Returns:
        Dict mapping JobsiteID (str) to CustomerMapping
    """
    mappings = {}

    for item in lmn_data:
        jobsite_id = str(item.get("JobsiteID", ""))
        accounting_id = str(item.get("AccountingID", ""))
        customer_name = item.get("CustomerName", "") or item.get("JobName", "")

        # Skip entries without valid IDs
        if not jobsite_id or not accounting_id:
            continue

        mappings[jobsite_id] = CustomerMapping(
            jobsite_id=jobsite_id,
            qbo_customer_id=accounting_id,
            qbo_display_name=customer_name,
            notes="From LMN API",
        )

    return mappings


def load_mapping_from_lmn_api() -> Dict[str, CustomerMapping]:
    """
    Fetch and build customer mappings from LMN API.

    Convenience function combining get_job_matching and build_mapping_from_lmn.

    Returns:
        Dict mapping JobsiteID to CustomerMapping
    """
    lmn_data = get_job_matching()
    return build_mapping_from_lmn(lmn_data)
