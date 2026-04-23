"""LMN API client for fetching job-to-customer mappings."""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import requests

from src.mapping.customer_mapping import CustomerMapping

logger = logging.getLogger(__name__)

LMN_API_URL = "https://accounting-api.golmn.com/qbdata/jobmatching"

# Retry policy for transient LMN failures (5xx / network errors).
# 4xx responses are not retried — those indicate client-side problems
# (auth, bad request) that will not self-heal.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 2.0)  # waits between attempts: 1→2, 2→3


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

    last_exception: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            logger.debug(
                "GET %s (attempt %d/%d)", LMN_API_URL, attempt, _MAX_ATTEMPTS
            )
            response = requests.get(LMN_API_URL, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            items = data.get("lmnitems", [])
            if attempt > 1:
                logger.warning(
                    "LMN job matching recovered on attempt %d/%d after "
                    "transient failure: %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    last_exception,
                )
            logger.info("LMN job matching returned %d items", len(items))
            return items
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is None or status < 500:
                raise
            last_exception = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exception = e

        if attempt < _MAX_ATTEMPTS:
            backoff = _BACKOFF_SECONDS[attempt - 1]
            logger.debug(
                "LMN request failed (%s); retrying in %.1fs",
                last_exception,
                backoff,
            )
            time.sleep(backoff)

    assert last_exception is not None
    raise last_exception


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
