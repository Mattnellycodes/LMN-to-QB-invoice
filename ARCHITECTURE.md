# Architecture

This document describes the technical architecture of the LMN to QuickBooks invoice automation system.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           INPUT LAYER                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Time Data CSV                    Service Data CSV                      │
│   ┌─────────────┐                  ┌─────────────┐                      │
│   │ TimesheetID │                  │ JobsiteID   │                      │
│   │ JobsiteID   │                  │ Service     │                      │
│   │ Man Hours   │                  │ Qty, Price  │                      │
│   │ CostCode    │                  │ Invoice Type│                      │
│   │ Rate        │                  └─────────────┘                      │
│   └─────────────┘                                                        │
│         │                                │                               │
└─────────┼────────────────────────────────┼───────────────────────────────┘
          │                                │
          ▼                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        PROCESSING LAYER                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│   │   lmn_parser    │    │    time_calc    │    │   line_items    │    │
│   │                 │    │                 │    │                 │    │
│   │ • Parse CSVs    │───►│ • Drive time    │───►│ • Build invoice │    │
│   │ • Validate      │    │   allocation    │    │ • Calculate fee │    │
│   │ • Clean data    │    │ • Work hours    │    │ • Format lines  │    │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘    │
│                                                          │              │
└──────────────────────────────────────────────────────────┼──────────────┘
                                                           │
                                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         OUTPUT LAYER                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│   │ customer_mapping│    │    invoices     │    │  QuickBooks     │    │
│   │                 │    │                 │    │  Online API     │    │
│   │ JobsiteID ──────│───►│ • Create draft  │───►│                 │    │
│   │   → QBO ID      │    │ • Format JSON   │    │ Draft Invoices  │    │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### `src/parsing/lmn_parser.py`

Handles CSV input parsing and validation.

```python
parse_time_data(csv_path) -> DataFrame
parse_service_data(csv_path) -> DataFrame
validate_columns(df, required) -> List[str]
filter_billable_services(df) -> DataFrame
```

**Key decisions:**
- Returns pandas DataFrames for easy manipulation
- Validates required columns upfront, fails fast on missing data
- Cleans numeric columns (removes `$`, converts to float)
- Normalizes JobsiteID/TimesheetID to strings for consistent matching

### `src/calculations/time_calc.py`

Core business logic for hours calculation.

```python
calculate_drive_time_allocation(time_df) -> Dict[str, Dict[str, float]]
calculate_work_hours_by_jobsite(time_df) -> Dict[str, float]
calculate_billable_hours(time_df) -> List[JobsiteHours]
```

**Data structures:**

```python
@dataclass
class JobsiteHours:
    jobsite_id: str
    jobsite_name: str
    customer_name: str
    work_hours: float           # CostCode 200 hours
    allocated_drive_time: float # Share of CostCode 900
    total_billable_hours: float # work + drive
    billable_rate: float
    dates: List[str]
```

**Algorithm for drive time allocation:**

```
For each TimesheetID:
    1. Sum all hours where CostCode contains "900" (drive time)
    2. Count unique JobsiteIDs in this timesheet
    3. Divide total drive time by jobsite count
    4. Assign equal share to each jobsite
```

### `src/invoice/line_items.py`

Builds invoice data structures from calculated hours and services.

```python
calculate_direct_payment_fee(subtotal) -> float
build_invoice(jobsite_hours, service_df, invoice_date) -> InvoiceData
build_all_invoices(jobsite_hours_list, service_df, invoice_date) -> List[InvoiceData]
```

**Data structures:**

```python
@dataclass
class LineItem:
    description: str
    quantity: float
    rate: float
    amount: float

@dataclass
class InvoiceData:
    jobsite_id: str
    jobsite_name: str
    customer_name: str
    invoice_date: str
    line_items: List[LineItem]
    subtotal: float
    direct_payment_fee: float
    total: float
```

**Invoice assembly order:**
1. Labor line (hours × rate)
2. Service/material lines from service data
3. Direct payment fee line

### `src/qbo/auth.py`

OAuth 2.0 token management for QuickBooks API.

```python
get_authorization_url() -> str
exchange_code_for_tokens(auth_code, realm_id) -> dict
refresh_access_token() -> dict
get_valid_access_token() -> Tuple[str, str]
```

**Token storage:** JSON file at `config/.qbo_tokens.json` (gitignored)

**Token lifecycle:**
- Access tokens expire in 1 hour
- Refresh tokens expire in ~100 days
- `get_valid_access_token()` auto-refreshes if within 5 minutes of expiry

### `src/qbo/invoices.py`

Creates draft invoices via QBO API.

```python
create_draft_invoice(invoice_data, qbo_customer_id, item_ref, terms) -> InvoiceResult
build_qbo_line_item(item, line_num, item_ref) -> Dict
```

**QBO API payload structure:**

```json
{
  "CustomerRef": {"value": "customer_id"},
  "TxnDate": "2026-01-12",
  "DueDate": "2026-01-27",
  "Line": [
    {
      "DetailType": "SalesItemLineDetail",
      "Amount": 493.75,
      "Description": "Skilled Garden Hourly Labor 1/05",
      "SalesItemLineDetail": {
        "Qty": 6.25,
        "UnitPrice": 79.00
      }
    }
  ],
  "PrivateNote": "Created from LMN export. JobsiteID: 5440055"
}
```

### `src/mapping/customer_mapping.py`

Manages the JobsiteID → QBO CustomerID relationship.

```python
load_customer_mapping(path) -> Dict[str, CustomerMapping]
get_qbo_customer_id(jobsite_id, mappings) -> Optional[str]
find_unmapped_jobsites(jobsite_ids, mappings) -> List[str]
```

**Why explicit mapping?**
- LMN and QBO have no shared identifier
- Customer names may differ between systems
- Explicit CSV is auditable and manually editable
- No fuzzy matching surprises

## Data Flow

### 1. Parsing Phase

```
Time CSV ──► parse_time_data() ──► DataFrame with:
                                   - Cleaned numeric columns
                                   - String JobsiteID/TimesheetID
                                   - Validated required columns

Service CSV ──► parse_service_data() ──► DataFrame with:
                                         - Cleaned price columns
                                         - String JobsiteID
```

### 2. Calculation Phase

```
Time DataFrame ──► calculate_billable_hours() ──► List[JobsiteHours]
                   │
                   ├── calculate_drive_time_allocation()
                   │   └── Groups by TimesheetID
                   │   └── Splits drive time equally
                   │
                   ├── calculate_work_hours_by_jobsite()
                   │   └── Sums CostCode 200 hours
                   │
                   └── get_jobsite_metadata()
                       └── Extracts names, rates, dates
```

### 3. Invoice Building Phase

```
JobsiteHours + Service DataFrame ──► build_all_invoices() ──► List[InvoiceData]
                                     │
                                     ├── Create labor LineItem
                                     ├── Extract service LineItems
                                     ├── Calculate subtotal
                                     ├── Calculate fee
                                     └── Add fee LineItem
```

### 4. QBO Creation Phase

```
InvoiceData + Mapping ──► create_draft_invoice() ──► InvoiceResult
                         │
                         ├── Look up QBO CustomerID
                         ├── Build QBO API payload
                         ├── POST to QBO API
                         └── Return success/error
```

## Error Handling Strategy

| Error Type | Behavior |
|------------|----------|
| Missing CSV columns | Fail fast with clear error message |
| Unmapped JobsiteID | Skip invoice, log warning, continue |
| QBO API error | Log error, continue with other invoices |
| Invalid time data | Skip entry, log warning |
| Token expired | Auto-refresh and retry |

**Design principle:** Never fail silently. Log everything, summarize at end.

## Testing Strategy

### Unit Tests (`tests/`)

- `test_time_calc.py`: Drive time allocation, work hours calculation
- `test_line_items.py`: Fee calculation, date formatting

### Integration Testing

```bash
# Preview mode tests full pipeline without QBO
python -m src.main --preview --time-data sample.csv --service-data sample.csv

# Dry run tests everything except actual API calls
python -m src.main --dry-run --time-data sample.csv --service-data sample.csv
```

## Configuration

### Environment Variables (`.env`)

```
QBO_CLIENT_ID=...
QBO_CLIENT_SECRET=...
QBO_REDIRECT_URI=http://localhost:8000/callback
QBO_COMPANY_ID=...
```

### Customer Mapping (`config/customer_mapping.csv`)

```csv
JobsiteID,QBO_CustomerID,QBO_DisplayName,Notes
5440055,123456,Zhenya Yoder,Primary residence
```

## Extension Points

### Adding New Fee Tiers

Edit `calculate_direct_payment_fee()` in `src/invoice/line_items.py`

### Supporting Additional Line Item Types

1. Add extraction logic in `line_items.py`
2. Ensure proper ordering in `build_invoice()`

### Custom Invoice Formatting

Modify `format_labor_description()` and line item construction in `line_items.py`

### Alternative Output Formats

The `InvoiceData` dataclass can be serialized to:
- JSON for API integrations
- CSV for spreadsheet review
- PDF via template rendering
