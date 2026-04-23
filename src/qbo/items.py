"""QuickBooks Online Product/Service item operations.

Provides a paginated bulk fetch of every QBO Item (used to resolve invoice
line ItemRefs without one API call per line), an AJAX-friendly name search
for the mapping UI, and the fallback-item lookup for lines that don't match
any known QBO product.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import requests

from src.qbo.customers import get_api_base_url

logger = logging.getLogger(__name__)


FALLBACK_ITEM_NAME = "Other"
"""QBO Product/Service used when a line's `item_lookup_name` doesn't match
any known QBO item and has no user-confirmed override. Must exist in the
target QBO company before running invoicing."""

PAGE_SIZE = 1000
SEARCH_LIMIT_DEFAULT = 20


class ItemMappingError(RuntimeError):
    """Raised when a required QBO item (e.g. the fallback) is not present."""


def fetch_all_items(access_token: str, realm_id: str) -> Dict[str, Dict[str, str]]:
    """Return every active QBO Item keyed by lowercased-trimmed name.

    Values are QBO ItemRef-shaped: `{"value": item_id, "name": item_name}`.
    Paginates through QBO's 1000-row page limit until a short page is seen.
    """
    cache: Dict[str, Dict[str, str]] = {}
    url = f"{get_api_base_url()}/{realm_id}/query"
    start_position = 1

    while True:
        query = (
            f"SELECT Id, Name FROM Item "
            f"STARTPOSITION {start_position} MAXRESULTS {PAGE_SIZE}"
        )
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params={"query": query},
        )
        response.raise_for_status()

        items = response.json().get("QueryResponse", {}).get("Item", []) or []
        for item in items:
            name = (item.get("Name") or "").strip()
            item_id = item.get("Id")
            if not name or not item_id:
                continue
            cache[name.lower()] = {"value": item_id, "name": name}

        if len(items) < PAGE_SIZE:
            break
        start_position += PAGE_SIZE

    logger.debug("Loaded %d QBO items into cache", len(cache))
    return cache


def get_fallback_item_ref(item_cache: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Return the ItemRef for the fallback QBO product.

    Raises ItemMappingError with a user-actionable message if the fallback
    item isn't present in the cache.
    """
    ref = item_cache.get(FALLBACK_ITEM_NAME.strip().lower())
    if ref is None:
        logger.error("QBO fallback item '%s' not found in cache", FALLBACK_ITEM_NAME)
        raise ItemMappingError(
            f"QBO Product/Service named '{FALLBACK_ITEM_NAME}' is required "
            "as a catch-all for unmapped invoice lines. Create it in QBO "
            "(Sales → Products and Services → New) before creating invoices."
        )
    return ref


def search_items_by_name(
    access_token: str,
    realm_id: str,
    query_fragment: str,
    limit: int = SEARCH_LIMIT_DEFAULT,
) -> List[Dict[str, str]]:
    """Case-insensitive substring search over QBO item names.

    Returns `[{"id": item_id, "name": item_name}, ...]` for the AJAX mapping
    UI. Escapes single quotes to avoid breaking the QBO query syntax.
    """
    fragment = (query_fragment or "").strip()
    if not fragment:
        return []

    safe_fragment = fragment.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"SELECT Id, Name FROM Item "
        f"WHERE Name LIKE '%{safe_fragment}%' "
        f"MAXRESULTS {max(1, int(limit))}"
    )
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

    items = response.json().get("QueryResponse", {}).get("Item", []) or []
    results: List[Dict[str, str]] = []
    for item in items:
        name = (item.get("Name") or "").strip()
        item_id = item.get("Id")
        if name and item_id:
            results.append({"id": item_id, "name": name})
    logger.info("QBO item search: query=%r matched=%d", fragment, len(results))
    return results
