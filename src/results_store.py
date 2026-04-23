"""Server-side JSON store for upload/preview/invoice results.

The Flask client-side session is a signed cookie capped near 4 KB. The upload
result dict (hundreds of QBO items, invoice line items, duplicate records) does
not fit. Without this store the browser silently drops the oversized cookie and
the next request sees an empty session — manifesting as the "No data to
display" redirect after a seemingly successful upload.

Usage:
    key = results_store.save(result_dict)
    session["results_key"] = key
    ...
    result = results_store.load(session.get("results_key"))
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_SUBDIR = "lmn_results"
_DEFAULT_MAX_AGE_SECONDS = 6 * 60 * 60  # 6 hours


def _store_dir() -> Path:
    path = Path(tempfile.gettempdir()) / _SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path_for(key: str) -> Path:
    # Reject anything that isn't a plain UUID-looking token to avoid path traversal.
    if not key or not all(c.isalnum() or c == "-" for c in key):
        raise ValueError(f"invalid results key: {key!r}")
    return _store_dir() / f"{key}.json"


def save(result: dict) -> str:
    """Write result to a new file; return its UUID key. Prunes stale files."""
    _cleanup_stale()
    key = uuid.uuid4().hex
    path = _path_for(key)
    payload = json.dumps(result)
    path.write_text(payload, encoding="utf-8")
    logger.info("Results saved: key=%s bytes=%d", key, len(payload))
    return key


def update(key: str, result: dict) -> None:
    """Overwrite an existing key's result."""
    path = _path_for(key)
    payload = json.dumps(result)
    path.write_text(payload, encoding="utf-8")
    logger.debug("Results updated: key=%s bytes=%d", key, len(payload))


def load(key: str | None) -> dict | None:
    """Return the stored dict, or None if key is missing, unreadable, or corrupt."""
    if not key:
        return None
    try:
        path = _path_for(key)
    except ValueError:
        logger.warning("Results load rejected invalid key: %r", key)
        return None
    if not path.exists():
        logger.warning("Results missing for key=%s (file not found)", key)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Results unreadable for key=%s: %s", key, e)
        return None


def delete(key: str | None) -> None:
    if not key:
        return
    try:
        path = _path_for(key)
    except ValueError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to delete results key=%s: %s", key, e)


def _cleanup_stale(max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS) -> None:
    now = time.time()
    try:
        for entry in _store_dir().iterdir():
            if not entry.is_file():
                continue
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                continue
            if age > max_age_seconds:
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        return
