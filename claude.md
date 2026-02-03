CLAUDE.md - LMN to QuickBooks Invoice Automation
Project Overview
Automates the creation of QuickBooks Online invoices from LMN timesheet exports for a landscaping company. The tool reads CSV exports from LMN, calculates billable hours (including drive time allocation), extracts materials/services, and creates draft invoices in QBO for manual review before sending.
See docs/IMPLEMENTATION_PLAN.md for detailed technical specifications.
Code Design Philosophy
Modular Design

Each component should do one thing well
Keep functions small and focused (ideally < 30 lines)
Separate concerns: CSV parsing, time calculations, drive time allocation, QBO API
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

TimesheetID: Groups all work from a single day/shift together
JobsiteID: Unique identifier for a customer's job site - used to match to QBO customers
CostCode 200: "Grounds Maintenance" - billable work time
CostCode 900: "Unbillable/Overhead" - drive time, load/unload time
General Maintenance: The TaskName for billable on-site work

Drive Time Allocation Formula
Allocated Drive Time per Job = Total Drive Time (CostCode 900) ÷ Number of Unique JobsiteIDs
All jobs in a timesheet share drive time equally.
Billable Hours Calculation
Billable Hours = General Maintenance Hours + Allocated Drive Time
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

pandas - CSV parsing and data manipulation
openpyxl - Excel file reading/writing (.xlsx, .xls support)
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
    parsing/              # LMN CSV/Excel parsing and validation (supports .csv, .xlsx, .xls)
    calculations/         # Time calculations, drive time allocation
    invoice/              # Invoice line item building, fee calculation
    qbo/                  # QuickBooks Online API integration
    mapping/              # Customer matching (JobsiteID → QBO CustomerID)
    web_processing.py     # High-level functions for web interface (file upload/detection)
    main.py               # CLI entry point
app.py                    # Flask web application
config/
    customer_mapping.csv   # JobsiteID to QBO customer mapping
templates/
    base.html             # Base template with styling
    index.html            # Home/connection status
    upload.html           # File upload with drag-and-drop and live detection
    mapping.html          # Customer mapping UI
    results.html          # Invoice preview
    invoice_results.html  # Invoice creation results
tests/
    # Mirror src/ structure, includes Excel fixtures
docs/
    IMPLEMENTATION_PLAN.md
    QB_OAuth.md           # QuickBooks OAuth requirements and implementation
    LMN_API.md            # LMN API integration for customer mapping
    sample_data/          # Example LMN exports and QBO invoice PDFs
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

# CLI: Preview invoices (no QBO connection)
python -m src.main --preview --time-data data/time.csv --service-data data/service.csv

# CLI: Create draft invoices in QBO
python -m src.main --time-data data/time.csv --service-data data/service.csv

# Web: File detection works with .csv, .xlsx, and .xls files
# Filename-based detection: include 'time' or 'service' keywords
# Fallback: Column-based detection if filename is ambiguous
Environment Setup
Create a .env file (never commit this):
QBO_CLIENT_ID=your_client_id
QBO_CLIENT_SECRET=your_client_secret
QBO_REDIRECT_URI=https://lmn-to-qb-invoice.onrender.com/qbo/callback
QBO_ENVIRONMENT=sandbox  # Use "sandbox" for dev/test, "production" for real companies

For production (Render):
DATABASE_URL=postgresql://...  (auto-set when you link a PostgreSQL database)
LMN_API_TOKEN=your_lmn_bearer_token  # For automatic customer mapping from LMN API
Tokens are stored in the database automatically after running `python -m src.qbo.auth setup`
Legacy: Token env vars (QBO_ACCESS_TOKEN, QBO_REFRESH_TOKEN, etc.) still supported but deprecated

QuickBooks OAuth
See docs/QB_OAuth.md for full OAuth implementation requirements and details.

OAuth CLI commands:
python -m src.qbo.auth setup    # Interactive authorization
python -m src.qbo.auth export   # Export tokens for Render
python -m src.qbo.auth refresh  # Manually refresh access token
python -m src.qbo.auth clear    # Clear stored tokens

Known Issues
- None currently tracked