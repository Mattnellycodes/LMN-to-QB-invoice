# Irrigation-Only Client Routine

Standalone tool that populates the **Irrigation Only Clients** tab on the
Schedule Master Google Sheet with every client that appears on Josh's
Irrigation sheet but not on the Schedule Master `Clients` tab. Part of the
invoicing workflow, deliberately not wired into the main LMN-to-QB app.

Spec and background: `docs/notes/irrigation-invoice-tracking.md`.

## How it works

1. Reads the Schedule Master `Clients` tab and the irrigation client tabs
   (`Bozeman 26`, `Big sky 26`, `Remote clients 26`). Rows under
   "Past/Inactive" or "Maintenance Only" markers are excluded; rows under
   "Blowout Only" are kept and flagged.
2. Matches each irrigation client against the schedule list:
   - **Memory first**: confirmed rows in the `Client Match Memory` tab always
     win (see feedback loop below).
   - **Exact**: normalized names identical (case/punctuation ignored,
     "Margaret Dowling" = "Dowling, Margaret").
   - **Proposals** (never auto-accepted): same address on both sheets;
     street-style names sharing a street number ("13 Wicki Up" / "13 Wickiup
     Trail"); name containment ("Welles" / "Welles, Jeffrey"); or ≥95% name
     similarity. Names with *different* numbers are never matched — "150
     Andesite" ≠ "170 Andesite".
3. Rewrites the **Irrigation Only Clients** tab and appends new proposals to
   the **Client Match Memory** tab.

## The feedback loop (how matches are remembered)

`Client Match Memory` tab columns:

| Irrigation Name | Schedule Client | Status | Notes | Updated At |
|---|---|---|---|---|

The routine appends proposed matches with a **blank Status**. A human fills in
Status:

- `confirmed` — same client; future runs treat it as matched automatically.
- `not_a_match` — different clients; future runs list it as irrigation-only.
- blank — still shows on the output tab as "Needs Review".

To map a client the matcher missed entirely, add a row by hand with
`confirmed`. If a confirmed client later disappears from the `Clients` tab,
they resurface as irrigation-only (that's the point — it catches changes).

⚠️ **Irrigation Only Clients is machine-written.** It is cleared and rewritten
on every run. Never annotate it by hand — all human input belongs in
`Client Match Memory`.

## Setup (one-time — DONE 2026-06-11)

Live access goes through an **Apps Script web app** ("Irrigation Match
Bridge") deployed under renner@intuitive-intel.com — no GCP project or service
account. The script source is `apps_script/Code.gs`; the deployed copy has the
real shared token instead of the placeholder.

- The web app executes as the deploying account (which has access to both
  sheets) with access "Anyone"; every request must carry the token.
- The tool needs two env vars: `APPS_SCRIPT_URL` (the `/exec` deployment URL)
  and `APPS_SCRIPT_TOKEN`. Locally they live in the repo `.env`
  (gitignored); for the cloud routine they go in the environment settings.
- To change the bridge code: edit in script.google.com (project "Irrigation
  Match Bridge"), then Deploy → Manage deployments → edit → New version.
  Keep `apps_script/Code.gs` in sync.

```bash
pip install -r tools/irrigation_match/requirements.txt
```

## Running

```bash
# Report only, no writes
python -m tools.irrigation_match.main --dry-run

# Full run: rewrites Irrigation Only Clients, appends memory proposals
python -m tools.irrigation_match.main

# Offline against local .xlsx exports (schedule_master.xlsx + irrigation.xlsx)
python -m tools.irrigation_match.main --snapshot tools/irrigation_match/sample_data
```

Snapshots in `sample_data/` are gitignored (client PII). Refresh them by
exporting both Google Sheets as .xlsx.

## Scheduling

Runs as a Claude Code cloud routine (`/schedule`). The routine's environment
needs `APPS_SCRIPT_URL` and `APPS_SCRIPT_TOKEN` set as environment variables
(claude.ai/code → Settings → Environments). Note: cloud environments have no
dedicated secrets store yet — values are visible to anyone who can edit the
environment. The prompt should: install requirements, run
`python -m tools.irrigation_match.main`, and report the summary plus any new
"Needs Review" proposals.

## Tests

```bash
pytest tests/test_irrigation_match.py
```

First live run (2026-06-11): 229 irrigation rows → 51 matched, 101 needs
review, 77 irrigation-only. Three sub-95% typo matches (Slamowotz→Slamowitz,
Stark Jen→Jennifer, Ulevitch→Ulevich) were confirmed by Renner and seeded into
Client Match Memory.
