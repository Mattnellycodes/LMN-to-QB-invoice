CLAUDE.md - LMN to QuickBooks Invoice Automation
Project Overview
Automates the creation of QuickBooks Online draft invoices from a single LMN "Job History (All Details)" PDF. The tool parses the PDF, allocates shop/overhead hours (CostCode 900) across billable jobsites, builds one invoice per jobsite aggregated over multiple days, and posts drafts to QBO for manual review.
See docs/IMPLEMENTATION_PLAN.md for detailed technical specifications.
Code Design Philosophy
Modular Design

Each component should do one thing well
Keep functions small and focused (ideally < 30 lines)
Separate concerns: file parsing, time calculations, drive time allocation, QBO API
Use clear interfaces between modules

Simplicity First

Write code that reads like plain English
Prefer explicit over implicit
Avoid clever tricks - boring code is good code
If a function needs comments to explain what it does, refactor it

No Over-Engineering

Solve the problem at hand, not hypothetical future problems
Don't add abstraction layers until you need them
Avoid premature optimization
Skip design patterns unless they genuinely simplify the code

Domain Context
Key Concepts

JobsiteID: Unique identifier for a customer's job site — used to match to QBO customers. The shop itself uses jobsite `5613100W` (`*SHOP`).
CostCode 200: "Grounds Maintenance" — billable on-site work time
CostCode 900: "Unbillable/Overhead" — drive time AND load/unload time (both pooled together)
Foreman: The crew lead; the key that links shop overhead hours to the jobsites a crew visited that day

Drive Time Allocation Formula
Pool = all CostCode 900 tasks under *SHOP (drive time + land/load time),
summed per (work_date, foreman).
For each (work_date, foreman), allocate to each billable jobsite weighted by
that jobsite's billable work hours for that same (date, foreman):
    Allocated per Jobsite = Shop Hours(date, foreman)
                            * Work Hours(date, foreman, jobsite)
                            / Σ Work Hours(date, foreman, *)
Fallback: if Σ Work Hours is 0 for that (date, foreman), split equally.
A jobsite's total allocated drive across the reporting period is the sum of
its per-day shares.
Billable Hours Calculation
Billable Hours per Jobsite = Sum(CostCode 200 task hours) + Allocated Drive Time
Invoice Aggregation
One invoice per jobsite, collecting every (date, foreman) row and the jobsite's
allocated drive across all days covered by the uploaded PDF.
Included Items Allow-List
`config/included_items.txt` lists exact service names that are bundled in
customer package prices. When these names appear with `Total Price = $0`, they
are silently dropped from invoices. Any $0 item NOT on the list surfaces in the
zero-price modal with its source (date, foreman, notes) for manual review.
Direct Payment Fee
Applied to digital invoices based on subtotal:

Under $1,000 → 10% of subtotal
$1,000–$2,000 → $15 flat
Over $2,000 → $20 flat

Language & Tools
Primary Language: Python

Use Python unless another language is clearly better suited
Target Python 3.10+ for modern syntax features
Use type hints for function signatures

Dependencies

pypdfium2 - PDF text extraction (positional) for the LMN Job History report
intuit-oauth - QBO API OAuth2 authentication
requests - HTTP requests for API calls
flask - Web application framework
gunicorn - Production WSGI server
psycopg2-binary - PostgreSQL database connection
python-dotenv - Environment variable management
pytest - Testing framework
ruff - Code linting and formatting

Keep dependencies minimal. Document why each is needed.
Code Style
Naming

Use snake_case for functions and variables
Use PascalCase for classes
Use descriptive names: calculate_drive_time_allocation not calc_dt
Boolean variables should read as questions: is_billable, has_materials

File Organization
src/
    parsing/pdf_parser.py     # LMN Job History PDF -> ParsedReport (customers + tasks)
    calculations/allocation.py # Shop pool -> per-jobsite drive-time allocation (proportional)
    invoice/line_items.py     # Aggregated invoice building with dedupe + included filter
    qbo/                      # QuickBooks Online API integration
    lmn/                      # LMN API (auth + customer mapping)
    mapping/customer_mapping.py # Customer matching (JobsiteID -> QBO CustomerID)
    mapping/item_mapping.py   # QBO item/service name mapping
    db/                       # Invoice history, customer overrides, item overrides, LMN credentials
    logging_config.py         # Central logging setup (LOG_LEVEL env var, request-id filter)
    web_processing.py         # Web-facing entry: process_uploaded_pdf
    main.py                   # Deprecated stub (CLI removed — web app only)
app.py                        # Flask web application
config/
    customer_mapping.csv      # JobsiteID -> QBO customer mapping
    included_items.txt        # Exact-match allow-list for $0 bundled services
templates/
    base.html                 # Base template with styling
    index.html                # Home/connection status
    upload.html               # Single-PDF drag-and-drop upload
    mapping.html              # Customer mapping UI
    item_mapping.html         # QBO item/service mapping UI
    results.html              # Invoice preview + zero-price modal (with crew notes)
    invoice_results.html      # Invoice creation results
    oauth_success.html        # QBO OAuth callback success page
tests/                        # Flat layout, one file per module under test
docs/
    IMPLEMENTATION_PLAN.md
    QB_OAuth.md               # QuickBooks OAuth requirements and implementation
    LMN_API.md                # LMN API integration for customer mapping
    sample_data/              # Example LMN exports and QBO invoice PDFs
Sample Time Sheets/
    NewSampleData.pdf         # Canonical sample used by tests/test_pdf_parser.py
Imports

Group imports: standard library, third-party, local
Use absolute imports
Avoid wildcard imports (from x import *)

Git Workflow
Before Every Commit

Check README.md is up to date
Run tests if they exist
Ensure no debug code or print statements remain
Verify no credentials or API keys are committed

Commit Messages

Use present tense: "Add drive time calculation" not "Added..."
Keep first line under 50 characters
Reference issues if applicable

Testing

Write tests for calculation logic (time parsing, drive time allocation)
Use pytest
Test edge cases: overnight shifts, missing drive time, zero-dollar items
Use sample data from docs/sample_data/

Common Commands

# Start web application
python app.py

# Run tests
pytest

# Run linter
ruff check .

# Format code
ruff format .

# Web: single PDF upload
# Export "Job History (All Details)" from LMN for the target week,
# filtered to the T-Town group. Upload the PDF; the app parses customers,
# allocates shop hours, and builds one invoice per billable jobsite.
Environment Setup
Create a .env file (never commit this):
QBO_CLIENT_ID=your_client_id
QBO_CLIENT_SECRET=your_client_secret
QBO_REDIRECT_URI=https://lmn-to-qb-invoice.onrender.com/qbo/callback
QBO_ENVIRONMENT=sandbox  # Use "sandbox" for dev/test, "production" for real companies
FLASK_SECRET_KEY=your_secret_key  # For Flask session security (auto-generated if not set)
LMN_EMAIL=your_lmn_email          # LMN account email for API access
LMN_PASSWORD=your_lmn_password    # LMN account password

For production (Render):
DATABASE_URL=postgresql://...  (auto-set when you link a PostgreSQL database)
FLASK_SECRET_KEY=your_secret_key_for_production  # Required for persistent sessions
Tokens are stored in the database automatically after running `python -m src.qbo.auth setup`
Legacy: Token env vars (QBO_ACCESS_TOKEN, QBO_REFRESH_TOKEN, etc.) still supported but deprecated

LMN Authentication
The app authenticates against LMN's accounting API at https://accounting-api.golmn.com/token:
- Token sources (priority order):
  1. Cached token from database (if not expired)
  2. LMN_EMAIL + LMN_PASSWORD from .env
  3. LMN_API_TOKEN env var (bare token, legacy)
- Access tokens are cached (~10 hour lifetime) and auto-refreshed
- See docs/LMN_API.md for full endpoint details

Duplicate Detection
Keyed on (jobsite_id, work_date, foreman) triples. Each successfully created
QBO invoice records `date_foreman_pairs` (strings of `date|foreman`) into the
`invoice_history` table. On a new upload:
- For every pending invoice, the system queries prior rows for the same jobsite
  where any `date|foreman` pair overlaps the new invoice's pairs.
- Matches produce a warning banner on the results preview listing the
  conflicting (date, foreman) pairs, the prior QBO invoice number, and date.
- A checkbox lets the user skip overlapping jobsites before creating invoices.
- Database is optional — if `DATABASE_URL` is unset, duplicate detection is
  silently skipped.

QuickBooks OAuth
See docs/QB_OAuth.md for full OAuth implementation requirements and details.

OAuth CLI commands:
python -m src.qbo.auth setup    # Interactive authorization
python -m src.qbo.auth export   # Export tokens for Render
python -m src.qbo.auth refresh  # Manually refresh access token
python -m src.qbo.auth clear    # Clear stored tokens

Known Issues
- None currently tracked