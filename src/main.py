"""Deprecated CLI entry point.

The LMN-to-QBO tool now runs exclusively as a Flask web app. Start the server
with `python app.py` and upload the LMN Job History PDF via the browser.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "The CLI has been removed. Run the web app instead:\n\n"
        "    python app.py\n\n"
        "Then open http://localhost:5000 and upload the LMN Job History PDF.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
