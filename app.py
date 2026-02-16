"""Flask web application for LMN to QuickBooks invoice automation."""

from __future__ import annotations

import io
import os
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from dotenv import load_dotenv

load_dotenv()

import logging
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Flask secret key - required for session security
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    logger.warning(
        "FLASK_SECRET_KEY not set - using random key. "
        "Sessions will be lost on restart. Set FLASK_SECRET_KEY in production."
    )
    _secret_key = secrets.token_hex(32)
app.secret_key = _secret_key


# =============================================================================
# Request Hooks - Load QBO credentials into request context
# =============================================================================


@app.before_request
def load_qbo_credentials():
    """Load QBO tokens from session into request context (g object)."""
    from src.qbo.context import set_qbo_credentials
    from src.qbo.auth import get_valid_tokens, InvalidGrant, RefreshTokenExpired

    tokens = session.get("qbo_tokens")
    if tokens:
        try:
            # Validate and refresh if needed
            valid_tokens = get_valid_tokens(tokens)
            # Update session if tokens were refreshed
            if valid_tokens != tokens:
                session["qbo_tokens"] = valid_tokens
            # Set in request context
            set_qbo_credentials(valid_tokens["access_token"], valid_tokens["realm_id"])
        except (InvalidGrant, RefreshTokenExpired):
            # Token invalid/expired - clear and require re-auth
            session.pop("qbo_tokens", None)
        except Exception as e:
            # Log the error so we can debug - this was causing silent failures
            logger.exception("Error loading QBO credentials from session")


def require_qbo_auth(f):
    """Decorator to require QBO authentication for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from src.qbo.context import has_qbo_credentials
        if not has_qbo_credentials():
            # Return JSON error for AJAX requests
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": "Not connected to QuickBooks. Please reconnect."}), 401
            flash("Please connect to QuickBooks first.", "warning")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# Health Check
# =============================================================================


@app.route("/health")
def health():
    """Health check endpoint for Render."""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


# =============================================================================
# Landing Page
# =============================================================================


@app.route("/")
def index():
    """Landing page with QuickBooks and LMN connection status."""
    from src.qbo.context import has_qbo_credentials

    is_connected = has_qbo_credentials()
    realm_id = session.get("qbo_tokens", {}).get("realm_id") if is_connected else None

    # Check LMN status
    lmn_connected = False
    lmn_using_env = False
    try:
        from src.db.lmn_credentials import has_lmn_credentials
        from src.lmn.auth import get_valid_token

        if has_lmn_credentials():
            token = get_valid_token()
            lmn_connected = token is not None
        else:
            import os
            if os.getenv("LMN_API_TOKEN"):
                lmn_connected = True
                lmn_using_env = True
    except Exception:
        # Database not available - check env var
        import os
        if os.getenv("LMN_API_TOKEN"):
            lmn_connected = True
            lmn_using_env = True

    return render_template(
        "index.html",
        is_connected=is_connected,
        realm_id=realm_id,
        lmn_connected=lmn_connected,
        lmn_using_env=lmn_using_env,
    )


# =============================================================================
# OAuth Flow
# =============================================================================


@app.route("/qbo/authorize")
def qbo_authorize():
    """Start OAuth flow - redirect to QuickBooks authorization."""
    from src.qbo.auth import get_authorization_url

    # Generate CSRF state token and store in session
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state

    auth_url = get_authorization_url(state)
    return redirect(auth_url)


@app.route("/qbo/callback")
def qbo_callback():
    """Handle OAuth callback from QuickBooks."""
    from src.qbo.auth import exchange_code_for_tokens, InvalidGrant
    from src.qbo.context import set_qbo_credentials

    # Check for errors from QuickBooks
    error = request.args.get("error")
    if error:
        error_description = request.args.get("error_description", "Unknown error")
        flash(f"QuickBooks authorization failed: {error_description}", "error")
        return redirect(url_for("index"))

    # Get authorization code and realm ID
    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")
    callback_state = request.args.get("state")

    if not auth_code or not realm_id:
        flash("Missing authorization code or realm ID from QuickBooks.", "error")
        return redirect(url_for("index"))

    # Validate CSRF state
    expected_state = session.pop("oauth_state", None)
    if not expected_state or callback_state != expected_state:
        flash("Invalid state parameter - possible security issue. Please try again.", "error")
        return redirect(url_for("index"))

    try:
        tokens = exchange_code_for_tokens(auth_code, realm_id)

        # Store tokens in session (not database)
        session["qbo_tokens"] = tokens

        # Set in request context for immediate use
        set_qbo_credentials(tokens["access_token"], tokens["realm_id"])

        flash(f"Successfully connected to QuickBooks! Company ID: {realm_id}", "success")

        return render_template(
            "oauth_success.html",
            realm_id=realm_id,
            expires_at=tokens.get("expires_at"),
            refresh_expires_at=tokens.get("refresh_expires_at"),
        )

    except InvalidGrant as e:
        flash(f"Authorization code was invalid or already used: {e}", "error")
        return redirect(url_for("index"))
    except Exception as e:
        flash(f"Failed to complete authorization: {e}", "error")
        return redirect(url_for("index"))


@app.route("/qbo/disconnect")
def qbo_disconnect():
    """Clear stored tokens and disconnect from QuickBooks."""
    # Clear session tokens
    session.pop("qbo_tokens", None)

    # Clear local token file (if exists)
    try:
        from src.qbo.auth import clear_stored_tokens
        clear_stored_tokens()
    except Exception:
        pass  # Ignore errors - file may not exist

    flash("Disconnected from QuickBooks.", "info")
    return redirect(url_for("index"))


@app.route("/auth/status")
def auth_status():
    """Check current authentication status (JSON endpoint)."""
    from src.qbo.auth import get_token_status
    from src.qbo.context import has_qbo_credentials

    tokens = session.get("qbo_tokens")
    status = get_token_status(tokens)
    status["session_based"] = True
    status["has_request_credentials"] = has_qbo_credentials()

    return jsonify(status)


# =============================================================================
# LMN Authentication
# =============================================================================


@app.route("/lmn/status")
def lmn_status():
    """Check LMN connection status (JSON endpoint)."""
    from src.lmn.auth import get_valid_token

    try:
        from src.db.lmn_credentials import has_lmn_credentials, get_cached_token

        has_credentials = has_lmn_credentials()
        cached_token = get_cached_token()

        # If we have a cached token, we're connected
        if cached_token:
            return jsonify({
                "connected": True,
                "has_credentials": True,
                "token_cached": True,
            })

        # If we have credentials but no cached token, try to get one
        if has_credentials:
            token = get_valid_token()
            return jsonify({
                "connected": token is not None,
                "has_credentials": True,
                "token_cached": token is not None,
            })

        # Check for env var fallback
        import os
        env_token = os.getenv("LMN_API_TOKEN")
        if env_token:
            return jsonify({
                "connected": True,
                "has_credentials": False,
                "token_cached": False,
                "using_env_var": True,
            })

        return jsonify({
            "connected": False,
            "has_credentials": False,
            "token_cached": False,
        })

    except Exception as e:
        # Database not available
        import os
        env_token = os.getenv("LMN_API_TOKEN")
        return jsonify({
            "connected": env_token is not None,
            "has_credentials": False,
            "token_cached": False,
            "using_env_var": env_token is not None,
            "db_error": str(e),
        })


@app.route("/lmn/connect", methods=["POST"])
def lmn_connect():
    """Save LMN credentials and test authentication."""
    from src.lmn.auth import authenticate, LMNAuthError

    data = request.json
    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    try:
        # Test authentication first
        token, expires_at = authenticate(username, password)

        # Save credentials and token to database
        from src.db.lmn_credentials import save_lmn_credentials, save_lmn_token
        save_lmn_credentials(username, password)
        save_lmn_token(token, expires_at)

        return jsonify({
            "success": True,
            "message": "Connected to LMN successfully",
            "expires_at": expires_at.isoformat(),
        })

    except LMNAuthError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        logger.exception("Error connecting to LMN")
        return jsonify({"error": f"Failed to save credentials: {e}"}), 500


@app.route("/lmn/disconnect", methods=["POST"])
def lmn_disconnect():
    """Delete stored LMN credentials."""
    try:
        from src.db.lmn_credentials import delete_lmn_credentials
        delete_lmn_credentials()
        return jsonify({"success": True, "message": "Disconnected from LMN"})
    except Exception as e:
        logger.exception("Error disconnecting from LMN")
        return jsonify({"error": str(e)}), 500


@app.route("/lmn/test", methods=["POST"])
def lmn_test():
    """Test LMN API access and return mapping count."""
    from src.lmn.api import load_mapping_from_lmn_api

    try:
        mappings = load_mapping_from_lmn_api()
        return jsonify({
            "success": True,
            "mapping_count": len(mappings),
            "message": f"Successfully loaded {len(mappings)} customer mappings from LMN",
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        logger.exception("Error testing LMN API")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# File Upload & Processing
# =============================================================================

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


def is_allowed_file(filename: str) -> bool:
    """Check if file has an allowed extension."""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


@app.route("/upload")
@require_qbo_auth
def upload():
    """Page with drag-and-drop UI for uploading files."""
    return render_template("upload.html")


@app.route("/upload/detect", methods=["POST"])
@require_qbo_auth
def upload_detect():
    """AJAX endpoint for live file type detection preview."""
    from src.web_processing import detect_uploaded_files

    files = request.files.getlist("files")

    if not files:
        return jsonify({"files": [], "valid": False, "error": "No files uploaded"})

    # Prepare files for detection
    file_list = []
    for f in files:
        if f.filename and is_allowed_file(f.filename):
            content = io.BytesIO(f.read())
            file_list.append((f.filename, content))

    if not file_list:
        return jsonify({
            "files": [],
            "valid": False,
            "error": "No valid files. Please upload .xlsx, .xls, or .csv files.",
        })

    result = detect_uploaded_files(file_list)
    return jsonify(result)


@app.route("/upload", methods=["POST"])
@require_qbo_auth
def upload_post():
    """Process uploaded files (CSV or Excel)."""
    from src.web_processing import process_uploaded_files, ProcessingError

    files = request.files.getlist("files")

    if not files or not any(f.filename for f in files):
        flash("Please upload Time Data and Service Data files.", "error")
        return redirect(url_for("upload"))

    # Filter to allowed files and prepare for processing
    file_list = []
    for f in files:
        if f.filename and is_allowed_file(f.filename):
            content = io.BytesIO(f.read())
            file_list.append((f.filename, content))

    if len(file_list) < 2:
        flash("Please upload both Time Data and Service Data files (.xlsx, .xls, or .csv).", "error")
        return redirect(url_for("upload"))

    try:
        result = process_uploaded_files(file_list)

        # Store result in session for next steps
        session["processing_result"] = result

        # Check for unmapped jobsites
        if result.get("unmapped_jobsites"):
            return redirect(url_for("mapping"))

        # All mapped - go to results
        return redirect(url_for("results"))

    except ProcessingError as e:
        flash(f"Error processing files: {e}", "error")
        return redirect(url_for("upload"))
    except Exception as e:
        logger.exception("Unexpected error processing files")
        flash(f"Unexpected error: {e}", "error")
        return redirect(url_for("upload"))


# =============================================================================
# Customer Mapping
# =============================================================================


@app.route("/mapping")
@require_qbo_auth
def mapping():
    """Show unmapped jobsites and allow mapping to QBO customers."""
    result = session.get("processing_result")
    if not result:
        flash("No data to map. Please upload files first.", "warning")
        return redirect(url_for("upload"))

    unmapped = result.get("unmapped_jobsites", [])
    if not unmapped:
        return redirect(url_for("results"))

    return render_template("mapping.html", unmapped_jobsites=unmapped)


@app.route("/mapping/search", methods=["POST"])
@require_qbo_auth
def mapping_search():
    """Search QBO customers by name (AJAX endpoint)."""
    from src.qbo.customers import search_customers_by_name

    json_data = request.json
    if not json_data:
        return jsonify({"error": "Request must be JSON"}), 400
    query = json_data.get("query", "")
    if len(query) < 2:
        return jsonify({"customers": []})

    try:
        customers = search_customers_by_name(query)
        return jsonify({
            "customers": [
                {"id": c.get("Id"), "name": c.get("DisplayName")}
                for c in customers[:10]
            ]
        })
    except Exception as e:
        logger.exception("Error searching QBO customers")
        return jsonify({"error": str(e)}), 500


@app.route("/mapping/save", methods=["POST"])
@require_qbo_auth
def mapping_save():
    """Save a single customer mapping override to database."""
    from src.mapping.customer_mapping import CustomerMapping
    from src.db.customer_overrides import save_customer_override

    data = request.json
    jobsite_id = data.get("jobsite_id")
    qbo_customer_id = data.get("qbo_customer_id")
    qbo_display_name = data.get("qbo_display_name", "")

    if not jobsite_id or not qbo_customer_id:
        return jsonify({"error": "Missing jobsite_id or qbo_customer_id"}), 400

    try:
        mapping = CustomerMapping(
            jobsite_id=jobsite_id,
            qbo_customer_id=qbo_customer_id,
            qbo_display_name=qbo_display_name,
        )
        save_customer_override(mapping)

        # Update session data
        result = session.get("processing_result", {})

        # Remove from unmapped list
        unmapped = result.get("unmapped_jobsites", [])
        result["unmapped_jobsites"] = [j for j in unmapped if j["jobsite_id"] != jobsite_id]

        # Add qbo_customer_id and qbo_display_name to the invoice for this jobsite
        for inv in result.get("invoices", []):
            if inv["jobsite_id"] == jobsite_id:
                inv["qbo_customer_id"] = qbo_customer_id
                inv["qbo_display_name"] = qbo_display_name
                break

        session["processing_result"] = result

        return jsonify({"success": True})
    except Exception as e:
        logger.exception("Error saving customer mapping")
        return jsonify({"error": str(e)}), 500


@app.route("/mapping/skip", methods=["POST"])
@require_qbo_auth
def mapping_skip():
    """Skip remaining unmapped jobsites and proceed to results."""
    result = session.get("processing_result", {})
    result["skipped_jobsites"] = result.get("unmapped_jobsites", [])
    result["unmapped_jobsites"] = []
    session["processing_result"] = result
    return jsonify({"success": True, "redirect": url_for("results")})


# =============================================================================
# Results & Invoice Creation
# =============================================================================


@app.route("/results")
@require_qbo_auth
def results():
    """Show processing results and invoice preview."""
    result = session.get("processing_result")
    if not result:
        flash("No data to display. Please upload files first.", "warning")
        return redirect(url_for("upload"))

    # Filter to only mapped invoices (those with qbo_customer_id)
    all_invoices = result.get("invoices", [])
    mapped_invoices = [inv for inv in all_invoices if inv.get("qbo_customer_id")]

    # Get duplicates info
    duplicates = result.get("duplicates", [])
    duplicate_jobsite_ids = list(set(d["jobsite_id"] for d in duplicates))

    # Check LMN status for display
    lmn_mapping_count = result.get("lmn_mapping_count", 0)

    # Build display result with only mapped invoices
    display_result = {
        "invoices": mapped_invoices,
        "skipped_jobsites": result.get("skipped_jobsites", []),
        "duplicates": duplicates,
        "duplicate_jobsite_ids": duplicate_jobsite_ids,
        "total_amount": sum(inv["total"] for inv in mapped_invoices),
        "lmn_mapping_count": lmn_mapping_count,
        "summary": {
            "total_jobsites": len(all_invoices),
            "mapped_jobsites": len(mapped_invoices),
            "unmapped_jobsites": len(result.get("unmapped_jobsites", [])),
            "total_line_items": sum(len(inv["line_items"]) for inv in mapped_invoices),
        },
    }

    return render_template("results.html", result=display_result)


@app.route("/create-invoices", methods=["POST"])
@require_qbo_auth
def create_invoices():
    """Create draft invoices in QuickBooks."""
    from src.web_processing import create_qbo_invoices

    result = session.get("processing_result")
    if not result or not result.get("invoices"):
        flash("No invoices to create.", "warning")
        return redirect(url_for("results"))

    # Filter to only mapped invoices (those with qbo_customer_id)
    mapped_invoices = [inv for inv in result["invoices"] if inv.get("qbo_customer_id")]
    if not mapped_invoices:
        flash("No mapped invoices to create.", "warning")
        return redirect(url_for("results"))

    # Handle skip_duplicates option
    if request.form.get("skip_duplicates"):
        duplicates = result.get("duplicates", [])
        duplicate_jobsite_ids = set(d["jobsite_id"] for d in duplicates)
        mapped_invoices = [
            inv for inv in mapped_invoices
            if inv["jobsite_id"] not in duplicate_jobsite_ids
        ]
        if not mapped_invoices:
            flash("No invoices to create after skipping duplicates.", "info")
            return redirect(url_for("results"))

    try:
        invoice_results = create_qbo_invoices(mapped_invoices)
        session["invoice_results"] = invoice_results
        return redirect(url_for("invoice_results"))
    except Exception as e:
        flash(f"Error creating invoices: {e}", "error")
        return redirect(url_for("results"))


@app.route("/invoice-results")
@require_qbo_auth
def invoice_results():
    """Show results of invoice creation."""
    results = session.get("invoice_results")
    if not results:
        flash("No invoice results to display.", "warning")
        return redirect(url_for("index"))

    return render_template("invoice_results.html", results=results)


# =============================================================================
# Run Server
# =============================================================================


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
