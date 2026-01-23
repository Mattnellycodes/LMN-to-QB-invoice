# QuickBooks OAuth Implementation

## Reference Documentation

- OAuth/OpenID Discovery Doc: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-openid-discovery-doc

## API Call Frequency

Only on-demand during customer interactions with the app, with a monthly auth token refresh to prevent errors.

## Required Error Handling

The application must handle the following OAuth error types:

### a. Expired Access Tokens
- Access tokens expire after a short period (typically 1 hour)
- Implement automatic token refresh using the refresh token
- Retry the failed request after refreshing

### b. Expired Refresh Tokens
- Refresh tokens expire after ~100 days
- When expired, user must re-authenticate through the OAuth flow
- Display clear message directing user to run `python -m src.qbo.auth setup`

### c. Invalid Grant Errors
- Occurs when refresh token is revoked or invalid
- Occurs when authorization code is used more than once
- Clear stored tokens and prompt for re-authentication

### d. CSRF Errors
- Validate state parameter on OAuth callback
- Reject requests where state doesn't match the originally sent value
- Log potential CSRF attempts for security monitoring

## Error Handling & Support Requirements

1. **API Error Handling** - App handles API errors including syntax and validation errors
2. **Capture intuit_tid** - App captures the `intuit_tid` field from response headers for troubleshooting
3. **Error Logging** - App stores all error information in logs that can be shared for troubleshooting
4. **Customer Support Contact** - App provides a way for customers to contact support from within the app

## Payment Processing Requirements

All of the following are required:

1. **No Automated Authorization UI** - App doesn't automate any part of the merchant application authorization user interface
2. **No Intuit ID Storage** - App doesn't request and/or store user's Intuit ID
3. **Encrypted Access Tokens** - App encrypts access tokens before storing them
4. **Volatile Memory Storage** - App stores access tokens in volatile memory only

## App Configuration

```
Host domain: lmn-to-qb-invoice.onrender.com
Launch URL: https://lmn-to-qb-invoice.onrender.com
Redirect URI: https://lmn-to-qb-invoice.onrender.com/qbo/callback
```

## CLI Commands

All OAuth management is done via the `src.qbo.auth` module:

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

## Token Storage

### Local Development
Tokens are stored in `config/.qbo_tokens.json`:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "realm_id": "...",
  "expires_at": "2026-01-18T16:01:57.135213",
  "refresh_expires_at": "2026-04-28T15:01:57.135240"
}
```

### Production (Render)
Tokens are stored in **PostgreSQL database** automatically:

1. **Link a PostgreSQL database** to your Render service
2. Set `DATABASE_URL` environment variable (auto-set by Render when you link the database)
3. Tokens are saved to the database the first time you run `python -m src.qbo.auth setup`

The app uses this priority for token loading:
1. PostgreSQL database (if `DATABASE_URL` is set)
2. Environment variables (legacy support for older deployments)
3. Local JSON file (development only)

## Implementation Details

The OAuth implementation is in `src/qbo/auth.py` and provides:

- **Automatic token refresh**: Access tokens (1 hour expiry) are auto-refreshed when needed
- **CSRF protection**: State parameter validated on all callbacks
- **intuit_tid capture**: Logged for troubleshooting support requests
- **Custom exceptions**: `RefreshTokenExpired`, `InvalidGrant`, `CSRFError`
- **Flexible storage**: PostgreSQL (production), environment variables (legacy), or JSON file (local dev)
