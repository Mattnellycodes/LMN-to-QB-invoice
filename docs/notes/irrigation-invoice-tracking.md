# Irrigation Client Invoice Tracking — Notes

*Captured 2026-06-11. Raw requirements — no system designed yet.*

## Problem

We need a way to track irrigation client invoices created in QuickBooks and catch
two failure modes:

1. **Missing invoices** — work was done but no invoice was ever created in QBO.
   These need to be caught so they can be created manually.
2. **Stalled invoices** — an invoice *was* created in QBO but never made it
   through the review-and-send step. Created ≠ done; every created invoice must
   be reviewed and sent.

## Sources to Check (in order)

### 1. Google Sheet — source of truth
- Maintained and kept up to date by **Josh**, who runs irrigation.
- This is the authoritative record of what work happened / what should be invoiced.
- Every check ultimately reconciles against this sheet.

### 2. LMN / timesheets — upstream data
- The next layer to verify when something doesn't line up.
- If LMN or timesheet data is inaccurate, it breaks the whole system downstream.
- Need a way to catch problems here, not just assume the data is right.

### 3. QuickBooks — output to reconcile
Two checks against QBO:
- **Coverage:** every invoice that *should* exist (per the Google Sheet) actually
  exists in QBO. Anything missing gets flagged for manual creation.
- **Completion:** every invoice that *does* exist in QBO has been reviewed and
  sent — none stuck in draft/created state.

## Summary of Required Checks

| # | Check | Compares | Catches |
|---|-------|----------|---------|
| 1 | Invoice coverage | Google Sheet vs QBO invoices | Work that never got invoiced |
| 2 | Review-and-send status | QBO invoice state | Created invoices never reviewed/sent |
| 3 | Upstream accuracy | LMN / timesheets vs Google Sheet | Bad source data that would break checks 1–2 |

## Proposed Solution — Client-Matching Program

A program that matches clients between two sheets:

- **Irrigation sheet** (Josh's source of truth)
- **Schedule master sheet**

Key design points:

- **Naming conventions will NOT match** between the two sheets. Expect manual
  matching up front — the program surfaces unmatched clients, a human resolves
  them once.
- **Persist the manual matches.** Once a pairing is confirmed, remember it so
  future runs only need to track *changes* between the two sheets over time
  (new clients, removed clients — not re-matching everything).
- **Scheduled routine run.** This runs on a cadence as part of the invoicing
  process, but it is a standalone tool — not built into the main
  LMN-to-QB-invoice program.

## Sheet Links

- **Schedule Master Sheet:**
  https://docs.google.com/spreadsheets/d/19CnRI2G-gOBJvCs6BFotJH_-n5FF06ebcUVopnPtqlo/edit?gid=0#gid=0
- **Irrigation Sheet:**
  https://docs.google.com/spreadsheets/d/15ElcgGzGVHRHb7gCl217HYh5AiwAb-5KTmC5e8oZa0g/edit?gid=18182495#gid=18182495

## Routine Spec — "Irrigation Only Clients" Tab

The Schedule Master sheet has an **"Irrigation Only Clients" tab**. The routine's
job is to populate that tab with every client that appears on the Irrigation
sheet but does NOT overlap with the Schedule Master client list.

Requirements:

- **Built as a Claude Code routine, run on the cloud** (scheduled cloud agent).
- **Access needs:**
  - Read: both sheets (Schedule Master + Irrigation), constant/standing access.
  - Write: Schedule Master sheet only — specifically the Irrigation Only
    Clients tab (checklist).
- **Feedback / memory loop:** clients that fail exact matching (typos, naming
  convention differences, anything else) need a feedback path where a human
  confirms the match once, and that match is **stored as a memory** so future
  runs match them automatically. The non-overlap list should only contain
  genuinely irrigation-only clients, not false positives from name mismatches.

## Status (2026-06-11): BUILT — see `tools/irrigation_match/`

Implemented in this repo as a standalone tool. Full docs in
`tools/irrigation_match/README.md`. Decisions made:

- Persisted matches live in a **"Client Match Memory" tab on the Schedule
  Master** (blank status = awaiting review; `confirmed` / `not_a_match` set by
  a human). Chosen over DB/repo file so Josh can do confirmations in-sheet.
- Matching: memory → exact → proposals (address match, street-style same-number
  match, name containment, ≥95% fuzzy). Different street numbers are never
  matched (Renner's sanity check: "150 Andesite" ≠ "170 Andesite").
- Sections honored: Past/Inactive and Maintenance Only rows excluded; Blowout
  Only kept and flagged.
- Live access: **Apps Script web app "Irrigation Match Bridge"** deployed
  under renner@intuitive-intel.com (no GCP — Renner declined a service
  account). Source in `tools/irrigation_match/apps_script/Code.gs`; env vars
  `APPS_SCRIPT_URL` + `APPS_SCRIPT_TOKEN` (in repo `.env`, gitignored).
  Note: live irrigation tab names have stray apostrophes ("Bozeman '26") —
  the bridge resolves tab names ignoring punctuation.
- Cloud routine env vars ARE supported in claude.ai/code environments
  (Settings → Environments), with the caveat that there is no encrypted
  secrets store yet — value visible to environment editors.
- FIRST LIVE WRITE DONE 2026-06-11: 229 irrigation rows → 51 matched (incl. 3
  seeded confirmations), 101 needs-review proposals appended to Client Match
  Memory, 77 irrigation-only. Idempotent re-run verified (0 new proposals).

Confirmed by Renner in-chat (2026-06-11) — seed these into Client Match Memory
as `confirmed` during the first live write (they score below the 95% bar and
won't be auto-proposed):
- Slamowotz → Slamowitz
- Stark, Jen and Jeremy → Stark, Jennifer and Jeremy
- Ulevitch → Ulevich

## Open Questions / Next Steps
- Renner: create the GCP service account and share both sheets (README has
  steps); then live dry-run → first write (incl. seeding the three confirmed
  matches above) → `/schedule` the routine.
- Decide the schedule cadence (weekly before invoicing?).
- Define how the Google Sheet maps to QBO invoices (customer, date range, job?).
- Define what "reviewed and sent" looks like in QBO so it's detectable.
