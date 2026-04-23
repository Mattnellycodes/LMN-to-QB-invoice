"""Central logging configuration.

Applied once at app startup. Every module uses `logger = logging.getLogger(__name__)`
and inherits this config. Level is controlled by LOG_LEVEL env var (default INFO).

Format includes a request_id field that is populated from Flask's `g` when
inside a request context, otherwise '-'. This lets you filter a single upload's
logs by its short hex ID.
"""

from __future__ import annotations

import logging
import os
import sys


class RequestIdFilter(logging.Filter):
    """Inject request_id from Flask's g into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from flask import g, has_request_context

            if has_request_context():
                record.request_id = getattr(g, "request_id", "-")
            else:
                record.request_id = "-"
        except Exception:
            record.request_id = "-"
        return True


_CONFIGURED = False


def configure_logging() -> None:
    """Install root logging config. Safe to call more than once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.INFO)

    _CONFIGURED = True
