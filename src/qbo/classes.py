"""QuickBooks Online Class operations.

Looks up the invoice-line Classes used for per-line departmental tagging.
Class tracking per transaction line must already be enabled on the connected
company and both the Maintenance and Irrigation classes must exist before
invoicing.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import requests

from src.invoice.line_items import IRRIGATION_CLASS_NAME, MAINTENANCE_CLASS_NAME
from src.qbo.customers import get_api_base_url

logger = logging.getLogger(__name__)


__all__ = [
    "IRRIGATION_CLASS_NAME",
    "MAINTENANCE_CLASS_NAME",
    "ClassMappingError",
    "get_class_by_name",
    "get_required_class_refs",
]


class ClassMappingError(RuntimeError):
    """Raised when a required QBO Class is not present."""


def get_class_by_name(
    access_token: str,
    realm_id: str,
    name: str,
) -> Optional[Dict[str, str]]:
    """Return the QBO ClassRef for an exact class name, or None if missing.

    Result is `{"value": class_id, "name": class_name}` — shaped for direct
    use as a ClassRef on an invoice line.
    """
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"SELECT Id, Name FROM Class WHERE Name = '{safe_name}'"
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

    classes = response.json().get("QueryResponse", {}).get("Class", []) or []
    for qbo_class in classes:
        class_name = (qbo_class.get("Name") or "").strip()
        class_id = qbo_class.get("Id")
        if class_name and class_id:
            logger.debug("Resolved QBO Class %r -> id=%s", name, class_id)
            return {"value": class_id, "name": class_name}
    logger.warning("QBO Class %r not found on company %s", name, realm_id)
    return None


def get_required_class_refs(
    access_token: str,
    realm_id: str,
) -> Dict[str, Dict[str, str]]:
    """Fetch the Maintenance and Irrigation ClassRefs; fail loud if missing.

    Every invoice line needs a ClassRef; the Irrigation class is required
    even for uploads that have no Irr jobsites, because `create_draft_invoice`
    now always passes both.
    """
    refs: Dict[str, Dict[str, str]] = {}
    for name in (MAINTENANCE_CLASS_NAME, IRRIGATION_CLASS_NAME):
        ref = get_class_by_name(access_token, realm_id, name)
        if ref is None:
            raise ClassMappingError(
                f"QBO Class {name!r} not found. Create it in QuickBooks "
                "(Settings → All Lists → Classes) and retry."
            )
        refs[name] = ref
    return refs
