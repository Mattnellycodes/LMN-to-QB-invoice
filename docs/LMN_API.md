# LMN API Investigation

## Endpoint Details

**URL:** `https://accounting-api.golmn.com/qbdata/jobmatching`

**Server:** Microsoft-IIS/10.0 (ASP.NET)

**Allowed Methods:** GET, POST

**Content-Type:** application/json; charset=utf-8

## Authentication

**Type:** Bearer Token (OAuth 2.0)

The API returns HTTP 401 with header:
```
WWW-Authenticate: Bearer
```

Unauthenticated requests receive:
```json
{"Message":"Authorization has been denied for this request."}
```

## Expected Response Format

Based on the EXECUTOR_STATUS.md specification:
```json
{
  "lmnitems": [
    {"lmnaccountid": "...", "accountingid": "..."}
  ]
}
```

This maps LMN account IDs (JobsiteID) to QuickBooks customer IDs.

## How to Obtain Bearer Token

**No public API documentation exists.** The bearer token must be obtained through:

1. **LMN Account Portal** - Log into https://my.golmn.com and check for API settings or developer options
2. **LMN Support** - Contact support@golmn.com or call (888) 347-9864 to request API access
3. **Accounting Integration Settings** - Check https://accounting.golmn.com for integration configuration

## Suggested Implementation

Once bearer token is obtained, requests should be made as:

```python
import requests

headers = {
    "Authorization": f"Bearer {LMN_API_TOKEN}",
    "Content-Type": "application/json"
}

response = requests.get(
    "https://accounting-api.golmn.com/qbdata/jobmatching",
    headers=headers
)
```

## Environment Variables to Add

Add to `.env`:
```
LMN_API_TOKEN=your_bearer_token_here
```

Add to `.env.example`:
```
# LMN API (for automatic customer mapping)
LMN_API_TOKEN=your_lmn_api_bearer_token
```

## Next Steps

1. User must obtain bearer token from LMN (via support or account settings)
2. Once token is available, test connectivity with GET request
3. Verify response format matches expected structure
4. Implement LMN API client module in `src/lmn/`

## Investigation Date

2026-01-30
