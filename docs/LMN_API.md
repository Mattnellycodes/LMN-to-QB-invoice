# LMN Accounting API

## Endpoint

**URL:** `https://accounting-api.golmn.com/qbdata/jobmatching`

**Method:** GET

**Server:** Microsoft-IIS/10.0 (ASP.NET)

## Authentication

**Token endpoint:** `POST https://accounting-api.golmn.com/token`

Simple password grant with no client_id or scopes required:

```
POST /token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=<email>&password=<password>
```

**Token response:**
```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 35999,
  "Username": "...",
  "AccountID": "7473",
  ...
}
```

Tokens expire in ~10 hours. Use the access token as a Bearer token for API calls.

**Credential sources (priority order):**
1. Cached token from database (if not expired)
2. Re-authenticate with DB-stored credentials
3. `LMN_EMAIL` + `LMN_PASSWORD` environment variables
4. `LMN_API_TOKEN` environment variable (bare token, legacy)

## Response Format

The `/qbdata/jobmatching` endpoint returns three keys:

```json
{
  "lmnitems": [...],
  "qbitems": [...],
  "settings": {...}
}
```

### `lmnitems` (used for customer mapping)

Array of LMN jobsite objects. Each item:

| Field | Type | Description |
|-------|------|-------------|
| `JobsiteID` | int | LMN jobsite identifier (matches CSV export) |
| `AccountingID` | string | QuickBooks customer ID |
| `CustomerName` | string | Customer display name |
| `JobName` | string | Job/property name |
| `JobShortName` | string | Abbreviated job name |
| `JobAddress` | string | Property address |
| `CustomerAddress` | string | Customer billing address |
| `isActive` | bool | Whether the jobsite is active |
| `CreatedDate` | string | ISO datetime |
| `LMNAccountID` | int | LMN account ID |
| `ExternalID` | string | External reference ID |

### `qbitems` (not used)

Array of full QuickBooks customer objects. Available but not consumed by this app -- we query QBO directly via our own OAuth connection.

### `settings` (not used)

LMN's own QuickBooks connection settings. Not consumed by this app.

## Example Request

```python
import requests

# Authenticate
token_resp = requests.post(
    "https://accounting-api.golmn.com/token",
    data="grant_type=password&username=<email>&password=<password>",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
token = token_resp.json()["access_token"]

# Fetch job matching data
resp = requests.get(
    "https://accounting-api.golmn.com/qbdata/jobmatching",
    headers={"Authorization": f"Bearer {token}"},
)
lmn_items = resp.json()["lmnitems"]
```
