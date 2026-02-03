# LMN to QuickBooks Invoice Automation

Automates the creation of QuickBooks Online draft invoices from LMN (landscaping management software) timesheet exports.

## What It Does

Takes two exports from LMN (CSV or Excel) and creates draft invoices in QuickBooks Online:

```
LMN Time Data (.csv, .xlsx, .xls)  ─┐
                                      ├──►  Python Script  ──►  QBO Draft Invoices
LMN Service Data (.csv, .xlsx, .xls)┘
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

Note: The web interface is now the recommended way to upload and process files. It supports CSV, .xlsx, and .xls files with automatic detection.

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

### 4. Upload Files (Web Interface)

Once connected to QuickBooks, use the web interface to upload your LMN exports:

1. **Go to Upload Page** - Click "Upload Files" from the home page
2. **Drop or Browse Files** - Use the drag-and-drop zone to select your Time Data and Service Data files
3. **Supported Formats** - The app accepts .csv, .xlsx, and .xls files
4. **Auto-Detection** - Files are automatically detected based on filename and content:
   - Files with "time" in the name → Time Data
   - Files with "service" in the name → Service Data
   - If filename is ambiguous, the app checks the file contents
5. **Live Preview** - As you select files, the interface shows detection results with colored badges

For detailed instructions, see the [Google Docs guide](https://docs.google.com/document/d/1J_hYbSsxYORKLG77RrbUNZY6MmxMTLKOCaqxqx5VHog/edit?usp=sharing).

### 5. Set Up Customer Mapping

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

### 6. Review and Create Invoices

After uploading and mapping jobsites (if needed):

1. **Review** - The app shows a preview of all draft invoices
2. **Adjust Mapping** - Map any new jobsites to QuickBooks customers
3. **Create** - Click "Create Invoices" to create draft invoices in QuickBooks

If using the CLI instead of the web interface:

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

The app accepts exports in **CSV, .xlsx, or .xls formats**. Files are auto-detected based on filename and content.

### Time Data Export (Job History Time Data)

Required columns:
- `TimesheetID`, `JobsiteID`, `Jobsite`, `CustomerName`
- `TaskName`, `CostCode`, `Man Hours`, `Billable Rate`, `EndDate`

File naming: Include "time" in the filename for best detection (e.g., `time_data.csv`, `TimeData.xlsx`)

### Service Data Export (Job History Service Data)

Required columns:
- `TimesheetID`, `JobsiteID`, `Service_Activity`
- `Timesheet Qty`, `Invoice Type`, `Unit Price`, `Total Price`, `Invoiced`, `EndDate`

File naming: Include "service" in the filename for best detection (e.g., `service_data.csv`, `ServiceData.xlsx`)

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
├── parsing/                # File parsing (CSV/Excel)
├── calculations/           # Time and drive allocation
├── invoice/                # Invoice building
├── qbo/                    # QuickBooks API
└── mapping/                # Customer mapping
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design documentation.

## Troubleshooting

### "Invalid grant" error during customer mapping / QuickBooks search

When searching for QuickBooks customers during mapping (web UI or CLI), you may see:
```
ERROR: Invalid grant error during refresh: {"error":"invalid_grant","error_description":"Incorrect or invalid refresh token"}
```

This means your OAuth tokens have expired or been revoked. Re-authorize:
```bash
python -m src.qbo.auth setup
```

Note: `--preview` mode still triggers QBO searches during interactive mapping. Workaround: skip mapping with 's' or pre-populate `config/customer_mapping.csv` manually.


### "No stored tokens found"

Run OAuth setup: `python -m src.qbo.auth setup`

### "JobsiteID not in mapping"

Add the missing JobsiteID to `config/customer_mapping.csv`

### QBO API Errors

- Check that your OAuth tokens are valid (they expire after ~100 days)
- Verify the QBO CustomerID exists in QuickBooks
- Check QBO API rate limits if processing many invoices
