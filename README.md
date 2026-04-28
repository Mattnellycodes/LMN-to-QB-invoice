# LMN to QuickBooks Invoice Automation

Automates the creation of QuickBooks Online draft invoices from one or more LMN
"Job History (All Details)" PDFs.

## What It Does

```
LMN Job History PDF(s)  ──►  Flask Web App  ──►  QBO Draft Invoices
```

For the reporting period covered by the uploaded PDF batch, the app:

1. Parses all tasks (customer work and *SHOP overhead).
2. Pools CostCode 900 hours under `*SHOP` keyed by `(date, foreman)`.
3. For each `(date, foreman)`, splits the pool across the billable jobsites
   that foreman worked that day, weighted by each jobsite's billable work
   hours (equal split as fallback when all work hours are zero).
4. Aggregates per-jobsite work plus allocated drive into one invoice per
   jobsite, covering all dates in the uploaded PDF batch.
5. Adds service/material line items (deduped by description) with
   `Total Price > $0`; items whose exact name is on the included-items
   allow-list with `$0` price are silently dropped; other `$0` items surface
   in a modal with the crew's notes.
6. Calculates the direct-payment fee and creates draft invoices in QBO.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up QuickBooks Connection

Copy `.env.example` to `.env` and add your QBO credentials:

```bash
cp .env.example .env
```

Required environment variables:

```
QBO_CLIENT_ID=your_client_id
QBO_CLIENT_SECRET=your_client_secret
QBO_REDIRECT_URI=https://lmn-to-qb-invoice.onrender.com/qbo/callback
```

Then authorize once:

```bash
python -m src.qbo.auth setup
```

### 3. Run the Web App

```bash
python app.py
```

Open <http://localhost:5000>.

### 4. Upload the PDF(s)

From LMN, export **Job History (All Details)** for the target week, filtered
to the T-Town group.

1. Go to the Upload page.
2. Drop or select one or more PDFs.
3. The app previews invoices, shows any unmapped jobsites, and any $0 items
   that need a price.
4. Click "Create Draft Invoices in QuickBooks".

The app rejects exact duplicate PDF files and overlapping parsed tasks across
different PDFs before building invoices, preventing the same visit from being
counted twice in a multi-PDF batch.

### 5. Customer Mapping

JobsiteID → QBO Customer mappings come from two sources:

- `config/customer_mapping.csv` — static overrides.
- LMN API — fetched automatically if `LMN_EMAIL` / `LMN_PASSWORD` are set or
  credentials are saved via the home page.

Unmapped jobsites are surfaced in the UI so you can match them before
creating invoices.

### 6. Included-Items Allow-List

`config/included_items.txt` lists exact service names bundled in customer
package prices. When these names appear on the PDF with `Total Price = $0`,
they are silently dropped from the invoice (matching is case-sensitive).
Any other `$0` item is shown in the zero-price modal with its date, foreman,
and crew notes so you can decide whether to price it or ignore it.

## Business Logic

### Drive-Time Allocation

Shop pool = total `CostCode 900` (Land Time + Drive Time) under `*SHOP`.
Per-day, per-foreman, hours are split proportionally by each jobsite's
billable work hours that day (equal split as fallback when all work hours
are zero):

```
Allocated Drive Time per Jobsite = Shop Hours(date, foreman)
                                  × Work Hours(date, foreman, jobsite)
                                  / Σ Work Hours(date, foreman, *)
```

### Billable Hours

```
Billable Hours per Jobsite = sum(CostCode 200 hours) + Allocated Drive Time
                             (across all dates in the uploaded PDF batch)
```

### Direct Payment Fee

| Subtotal | Fee |
|----------|-----|
| Under $1,000 | 10% of subtotal |
| $1,000 - $2,000 | $15 flat |
| Over $2,000 | $20 flat |

### Duplicate Detection

Successful QBO invoices record `(jobsite_id, date, foreman)` triples in the
`invoice_history` table. A new upload that overlaps any prior triple triggers
a warning banner on the preview; you can opt to skip overlapping jobsites.

## Development

### Run Tests

```bash
pytest -v
```

### Lint

```bash
ruff check src tests
```

### Project Structure

```
src/
├── parsing/pdf_parser.py     # LMN Job History PDF parser (pypdfium2-based)
├── calculations/allocation.py # Shop pool + per-jobsite allocation
├── invoice/line_items.py     # Aggregated invoice building
├── qbo/                      # QuickBooks Online API integration
├── lmn/                      # LMN API (customer mapping)
├── mapping/                  # JobsiteID -> QBO CustomerID
└── db/                       # Invoice history, overrides, LMN credentials
app.py                        # Flask web application
config/
├── customer_mapping.csv      # Manual jobsite mapping overrides
└── included_items.txt        # Bundled-item allow-list ($0 auto-drop)
```

See [CLAUDE.md](CLAUDE.md) for further detail.

## Troubleshooting

### "*SHOP jobsite (5613100W) not found in PDF"

The uploaded PDF doesn't include the `*SHOP` jobsite. Re-run the LMN export
without filtering out the shop job — the app needs it for drive-time
allocation.

### "Invalid grant error" during customer mapping

OAuth tokens expired or were revoked:

```bash
python -m src.qbo.auth setup
```

### `$0` item keeps appearing in the zero-price modal

Add its exact service name (including capitalization and trailing tags like
`(VT)`) to `config/included_items.txt`.
