"""Populate the Schedule Master "Irrigation Only Clients" tab.

Reads the irrigation client tabs and the Schedule Master client list, matches
them (see matching.py), then:
- rewrites the Irrigation Only Clients tab with every irrigation client that
  has no schedule match (definite or awaiting review), and
- appends new uncertain matches to the Client Match Memory tab for a human
  to confirm or reject.

Run live:      python -m tools.irrigation_match.main
Dry run:       python -m tools.irrigation_match.main --dry-run
Offline:       python -m tools.irrigation_match.main --snapshot <dir with .xlsx>
"""

from __future__ import annotations

import argparse
import datetime
import sys

from . import config
from .extract import extract_irrigation_clients, extract_schedule_clients
from .matching import MatchResult, normalize, reconcile
from .sheets_io import AppsScriptBackend, SnapshotBackend


def load_memory(rows: list[list[object]]) -> dict[str, tuple[str, str]]:
    """Memory tab rows -> {normalized irrigation name: (schedule client, status)}."""
    memory = {}
    for row in rows[1:]:
        cells = [str(c).strip() if c is not None else "" for c in row] + [""] * 3
        irrigation_name, schedule_client, status = cells[0], cells[1], cells[2].lower()
        if irrigation_name:
            memory[normalize(irrigation_name)] = (schedule_client, status)
    return memory


def build_output_rows(result: MatchResult, today: str) -> list[list[str]]:
    rows = [list(config.OUTPUT_HEADER)]
    for client, reason in sorted(
        result.irrigation_only, key=lambda e: e[0].name.lower()
    ):
        rows.append(
            [
                client.name,
                client.source_tab,
                client.section,
                "Irrigation Only",
                "",
                today,
            ]
        )
    for client, candidate, _ in sorted(
        result.needs_review, key=lambda e: e[0].name.lower()
    ):
        rows.append(
            [
                client.name,
                client.source_tab,
                client.section,
                "Needs Review",
                candidate,
                today,
            ]
        )
    return rows


def build_memory_proposals(
    result: MatchResult, memory: dict[str, tuple[str, str]], today: str
) -> list[list[str]]:
    """Memory rows for newly proposed matches not already awaiting review."""
    return [
        [client.name, candidate, "", reason, today]
        for client, candidate, reason in result.needs_review
        if normalize(client.name) not in memory
    ]


def print_report(result: MatchResult, proposals: list[list[str]]) -> None:
    print(f"matched (no action): {len(result.matched)}")
    print(f"irrigation-only: {len(result.irrigation_only)}")
    print(f"needs review: {len(result.needs_review)} ({len(proposals)} new proposals)")
    for client, candidate, reason in result.needs_review:
        print(f"  review: {client.name!r} -> {candidate!r} [{reason}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report only, write nothing"
    )
    parser.add_argument(
        "--snapshot", metavar="DIR", help="read local .xlsx snapshots from DIR"
    )
    parser.add_argument("--threshold", type=float, default=config.FUZZY_THRESHOLD)
    args = parser.parse_args(argv)

    if args.snapshot:
        backend = SnapshotBackend(
            f"{args.snapshot}/schedule_master.xlsx", f"{args.snapshot}/irrigation.xlsx"
        )
    else:
        backend = AppsScriptBackend()

    schedule = extract_schedule_clients(
        backend.read_tab("schedule", config.SCHEDULE_CLIENTS_TAB),
        config.SCHEDULE_CLIENTS_HEADER,
    )
    irrigation = []
    for tab, name_label in config.IRRIGATION_TABS.items():
        irrigation.extend(
            extract_irrigation_clients(
                backend.read_tab("irrigation", tab), name_label, tab
            )
        )
    if not schedule or not irrigation:
        print(
            "error: empty client list from one of the sheets; aborting", file=sys.stderr
        )
        return 1

    memory_rows = backend.read_tab("schedule", config.MEMORY_TAB)
    memory = load_memory(memory_rows)
    result = reconcile(irrigation, schedule, memory, args.threshold)

    today = datetime.date.today().isoformat()
    output_rows = build_output_rows(result, today)
    proposals = build_memory_proposals(result, memory, today)
    print_report(result, proposals)

    if args.dry_run:
        print("dry run: no sheet writes")
        return 0
    backend.replace_tab("schedule", config.OUTPUT_TAB, output_rows)
    if not memory_rows:
        backend.replace_tab("schedule", config.MEMORY_TAB, [list(config.MEMORY_HEADER)])
    backend.append_rows("schedule", config.MEMORY_TAB, proposals)
    backend.flush()
    print(
        f"wrote {len(output_rows) - 1} rows to '{config.OUTPUT_TAB}', "
        f"appended {len(proposals)} proposals to '{config.MEMORY_TAB}'"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
