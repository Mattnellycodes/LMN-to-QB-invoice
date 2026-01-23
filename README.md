# LMN to QuickBooks Invoice Automation

Automates the creation of QuickBooks Online draft invoices from LMN (landscaping management software) timesheet exports.

## What It Does

Takes two CSV exports from LMN and creates draft invoices in QuickBooks Online:

```
LMN Time Data CSV  ─┐
                    ├──►  Python Script  ──►  QBO Draft Invoices
LMN Service Data CSV┘
```

Each invoice includes:
- **Labor line**: Billable hours (work time + allocated drive time) × hourly rate
- **Materials/services**: Items from the service data export
- **Direct payment fee**: Automatically calculated based on subtotal

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Preview Invoices (No QBO Connection Required)

```bash
python -m src.main --preview \
  --time-data path/to/time_data.csv \
  --service-data path/to/service_data.csv
```

### 3. Set Up QuickBooks Connection

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

#### OAuth Authentication

Run the interactive OAuth setup:

```bash
python -m src.qbo.auth setup
```

This will:
1. Open your browser to the QuickBooks authorization page
2. Prompt you to sign in and authorize the app
3. Capture the callback and exchange the code for tokens
4. Save tokens locally to `config/.qbo_tokens.json`

#### OAuth CLI Commands

```bash
# Interactive OAuth authorization
python -m src.qbo.auth setup

# Export tokens for Render deployment
python -m src.qbo.auth export

# Manually refresh access token
python -m src.qbo.auth refresh

# Clear stored tokens
python -m src.qbo.auth clear
```

#### Deploying to Render

Tokens are automatically stored in PostgreSQL on Render:

1. **Link a PostgreSQL database** to your Render service
2. **Set these environment variables** in Render dashboard:
   - `QBO_CLIENT_ID`
   - `QBO_CLIENT_SECRET`
   - `QBO_REDIRECT_URI`
   - `DATABASE_URL` (auto-set when you link a PostgreSQL database)

3. **Authorize once locally**, then run the app on Render:
   ```bash
   python -m src.qbo.auth setup
   ```
   This saves tokens to your local `config/.qbo_tokens.json`. When you deploy to Render, move the tokens to the Render database:
   ```bash
   python -m src.qbo.auth export
   ```

Tokens are now stored securely in the PostgreSQL database instead of environment variables. See [docs/QB_OAuth.md](docs/QB_OAuth.md) for full details.

### 4. Set Up Customer Mapping

Create a mapping between LMN JobsiteIDs and QBO CustomerIDs in `config/customer_mapping.csv`:

```csv
JobsiteID,QBO_CustomerID,QBO_DisplayName,Notes
5440055,123456,Zhenya Yoder,
5525262,789012,Karen Gilhousen,
```

Helper commands:

```bash
# Extract jobsites from LMN data
python -m src.mapping.build_mapping lmn-jobsites --input path/to/time_data.csv

# Export QBO customers for reference
python -m src.mapping.build_mapping qbo-customers
```

### 5. Create Invoices

```bash
# Dry run (shows what would be created)
python -m src.main --dry-run \
  --time-data path/to/time_data.csv \
  --service-data path/to/service_data.csv

# Create draft invoices in QBO
python -m src.main \
  --time-data path/to/time_data.csv \
  --service-data path/to/service_data.csv
```

## Command Reference

```bash
python -m src.main [OPTIONS]

Required:
  --time-data PATH      LMN Job History Time Data CSV
  --service-data PATH   LMN Job History Service Data CSV

Optional:
  --mapping PATH        Customer mapping CSV (default: config/customer_mapping.csv)
  --date YYYY-MM-DD     Invoice date (default: today)
  --preview             Show detailed invoice preview
  --dry-run             Show what would be created without calling QBO API
```

## LMN Export Requirements

### Time Data Export (Job History Time Data)

Required columns:
- `TimesheetID`, `JobsiteID`, `Jobsite`, `CustomerName`
- `TaskName`, `CostCode`, `Man Hours`, `Billable Rate`, `EndDate`

### Service Data Export (Job History Service Data)

Required columns:
- `TimesheetID`, `JobsiteID`, `Service_Activity`
- `Timesheet Qty`, `Invoice Type`, `Unit Price`, `Total Price`, `Invoiced`

## Business Logic

### Drive Time Allocation

Drive time (CostCode 900) is split equally among all jobsites in a timesheet:

```
Allocated Drive Time = Total Drive Hours ÷ Number of Unique Jobsites
```

### Billable Hours

```
Billable Hours = Work Hours (CostCode 200) + Allocated Drive Time
```

### Direct Payment Fee

| Subtotal | Fee |
|----------|-----|
| Under $1,000 | 10% of subtotal |
| $1,000 - $2,000 | $15 flat |
| Over $2,000 | $20 flat |

### Billable Line Items

Items from service data are included when:
- `Total Price > 0`, AND
- `Invoice Type` is not "Included"

## Development

### Run Tests

```bash
pytest tests/ -v
```

### Project Structure

```
src/
├── main.py                 # CLI entry point
├── parsing/                # CSV parsing
├── calculations/           # Time and drive allocation
├── invoice/                # Invoice building
├── qbo/                    # QuickBooks API
└── mapping/                # Customer mapping
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design documentation.

## Troubleshooting

### "No stored tokens found"

Run OAuth setup: `python -m src.qbo.auth setup`

### "JobsiteID not in mapping"

Add the missing JobsiteID to `config/customer_mapping.csv`

### QBO API Errors

- Check that your OAuth tokens are valid (they expire after ~100 days)
- Verify the QBO CustomerID exists in QuickBooks
- Check QBO API rate limits if processing many invoices
