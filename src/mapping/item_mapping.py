"""Resolve LMN item lookup names to QBO Product/Service ItemRefs.

Resolution order per lookup name:
  1. Exact case-insensitive match in the QBO item cache.
  2. DB override (user-confirmed mapping from `item_mapping_overrides`).
  3. The user-created `FALLBACK_ITEM_NAME` catch-all ItemRef.

Every call returns a usable ItemRef — there is no "unresolved" state; the
fallback is always available (or the caller should not be here at all,
which is enforced upstream by surfacing `ItemMappingError`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set, Tuple


def resolve_item_ref(
    lookup_name: str,
    item_cache: Mapping[str, Dict[str, str]],
    db_overrides: Mapping[str, Dict[str, str]],
    fallback_ref: Dict[str, str],
) -> Tuple[Dict[str, str], bool]:
    """Return `(ItemRef, is_fallback)` for a single lookup name."""
    name = (lookup_name or "").strip()
    if name:
        exact = item_cache.get(name.lower())
        if exact is not None:
            return exact, False
        override = db_overrides.get(name)
        if override is not None:
            return override, False
    return fallback_ref, True


def build_item_refs(
    invoices: List[Mapping[str, Any]],
    item_cache: Mapping[str, Dict[str, str]],
    db_overrides: Mapping[str, Dict[str, str]],
    fallback_ref: Dict[str, str],
) -> Tuple[Dict[str, Dict[str, str]], Set[str]]:
    """Resolve every unique `item_lookup_name` across all invoices.

    Returns `(refs_by_lookup_name, fallback_lookup_names)`:
      - `refs_by_lookup_name`: `{item_lookup_name: ItemRef}` — consumed at
        QBO submit time.
      - `fallback_lookup_names`: the deduped set of lookup names that got
        the fallback ItemRef. Used to render the "upgrade mappings" banner
        on the results page.
    """
    refs: Dict[str, Dict[str, str]] = {}
    fallback_names: Set[str] = set()

    for invoice in invoices:
        for line in invoice.get("line_items", []):
            lookup = (line.get("item_lookup_name") or "").strip()
            if not lookup or lookup in refs:
                continue
            ref, is_fallback = resolve_item_ref(
                lookup, item_cache, db_overrides, fallback_ref
            )
            refs[lookup] = ref
            if is_fallback:
                fallback_names.add(lookup)

    return refs, fallback_names
