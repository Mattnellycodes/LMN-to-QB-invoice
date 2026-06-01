"""QuickBooks Online sales-term lookup helper.

Term IDs are stable per company file but vary across companies, so we resolve
them by name on first use and cache the result for the life of the process.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from src.qbo.context import get_qbo_credentials
from src.qbo.customers import get_api_base_url

logger = logging.getLogger(__name__)


_TERM_CACHE: dict[str, str] = {}


def get_term_id_by_name(name: str) -> Optional[str]:
    """Return the QBO Term Id whose Name matches `name` (case-insensitive)."""
    if not name:
        return None

    access_token, realm_id = get_qbo_credentials()
    cache_key = f"{realm_id}:{name.lower()}"
    if cache_key in _TERM_CACHE:
        return _TERM_CACHE[cache_key]

    query = "SELECT * FROM Term"
    url = f"{get_api_base_url()}/{realm_id}/query"

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={"query": query},
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning("QBO Term query failed: %s", e)
        return None

    terms = response.json().get("QueryResponse", {}).get("Term", [])
    target = name.lower()
    for term in terms:
        if str(term.get("Name", "")).lower() == target:
            term_id = str(term.get("Id"))
            _TERM_CACHE[cache_key] = term_id
            return term_id

    logger.warning(
        "QBO Term not found by name: %r (available: %s)",
        name,
        [t.get("Name") for t in terms],
    )
    return None
