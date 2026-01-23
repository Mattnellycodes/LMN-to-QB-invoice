"""Flask web application for LMN to QuickBooks invoice automation."""

from __future__ import annotations

import io
import os
import secrets
from datetime import datetime

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Flask secret key - required for session security
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    import logging
    logging.warning(
        "FLASK_SECRET_KEY not set - using random key. "
        "Sessions will be lost on restart. Set FLASK_SECRET_KEY in production."
    )
    _secret_key = secrets.token_hex(32)
app.secret_key = _secret_key

# Initialize database on startup (if DATABASE_URL is set)
if os.getenv("DATABASE_URL"):
    from src.qbo.database import init_db
    init_db()


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
    """Landing page with QuickBooks connection status."""
    # Check if we have valid tokens
    is_connected = False
    realm_id = None

    try:
        from src.qbo.auth import load_tokens
        tokens = load_tokens()
        if tokens and tokens.get("access_token") and tokens.get("realm_id"):
            is_connected = True
            realm_id = tokens.get("realm_id")
    except Exception:
        pass

    return render_template(
        "index.html",
        is_connected=is_connected,
        realm_id=realm_id,
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
    from src.qbo.auth import exchange_code_for_tokens, CSRFError, InvalidGrant

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
        flash(f"Successfully connected to QuickBooks! Company ID: {realm_id}", "success")

        # Store token info for display
        session["qbo_connected"] = True
        session["qbo_realm_id"] = realm_id
        session["qbo_expires_at"] = tokens.get("expires_at", "")

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
    from src.qbo.auth import clear_tokens

    clear_tokens()
    session.pop("qbo_connected", None)
    session.pop("qbo_realm_id", None)
    session.pop("qbo_expires_at", None)
    flash("Disconnected from QuickBooks.", "info")
    return redirect(url_for("index"))


@app.route("/auth/status")
def auth_status():
    """Check current authentication status (JSON endpoint)."""
    from src.qbo.auth import load_tokens
    from src.qbo.database import is_database_configured

    tokens = load_tokens()

    if not tokens:
        return jsonify({
            "connected": False,
            "message": "No QuickBooks connection",
            "database_configured": is_database_configured(),
        })

    # Parse expiration times
    try:
        expires_at_str = tokens.get("expires_at", "")
        refresh_expires_at_str = tokens.get("refresh_expires_at", "")

        now = datetime.utcnow()
        access_valid = False
        refresh_valid = False

        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            access_valid = now < expires_at

        if refresh_expires_at_str:
            refresh_expires_at = datetime.fromisoformat(refresh_expires_at_str)
            refresh_valid = now < refresh_expires_at

        return jsonify({
            "connected": True,
            "realm_id": tokens.get("realm_id"),
            "access_token_valid": access_valid,
            "access_token_expires": expires_at_str,
            "refresh_token_valid": refresh_valid,
            "refresh_token_expires": refresh_expires_at_str,
            "database_configured": is_database_configured(),
        })
    except Exception as e:
        return jsonify({
            "connected": True,
            "realm_id": tokens.get("realm_id"),
            "error": f"Could not parse token expiry: {e}",
            "database_configured": is_database_configured(),
        })


# =============================================================================
# CSV Upload & Processing
# =============================================================================


@app.route("/upload")
def upload():
    """Page with drag-and-drop UI for uploading CSVs."""
    # Check if connected to QuickBooks
    is_connected = False
    try:
        from src.qbo.auth import load_tokens
        tokens = load_tokens()
        is_connected = bool(tokens and tokens.get("access_token"))
    except Exception:
        pass

    if not is_connected:
        flash("Please connect to QuickBooks first.", "warning")
        return redirect(url_for("index"))

    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_post():
    """Process uploaded CSV files."""
    from src.web_processing import process_csv_files, ProcessingError

    time_file = request.files.get("time_data")
    service_file = request.files.get("service_data")

    if not time_file or not service_file:
        flash("Please upload both Time Data and Service Data CSV files.", "error")
        return redirect(url_for("upload"))

    if not time_file.filename.endswith(".csv") or not service_file.filename.endswith(".csv"):
        flash("Both files must be CSV files.", "error")
        return redirect(url_for("upload"))

    try:
        # Read file contents
        time_data = io.StringIO(time_file.read().decode("utf-8"))
        service_data = io.StringIO(service_file.read().decode("utf-8"))

        # Process the CSVs
        result = process_csv_files(time_data, service_data)

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
        flash(f"Unexpected error: {e}", "error")
        return redirect(url_for("upload"))


# =============================================================================
# Customer Mapping
# =============================================================================


@app.route("/mapping")
def mapping():
    """Show unmapped jobsites and allow mapping to QBO customers."""
    result = session.get("processing_result")
    if not result:
        flash("No data to map. Please upload CSV files first.", "warning")
        return redirect(url_for("upload"))

    unmapped = result.get("unmapped_jobsites", [])
    if not unmapped:
        return redirect(url_for("results"))

    return render_template("mapping.html", unmapped_jobsites=unmapped)


@app.route("/mapping/search", methods=["POST"])
def mapping_search():
    """Search QBO customers by name (AJAX endpoint)."""
    from src.qbo.customers import search_customers_by_name

    query = request.json.get("query", "")
    if len(query) < 2:
        return jsonify({"customers": []})

    try:
        customers = search_customers_by_name(query)
        return jsonify({
            "customers": [
                {"id": c.get("Id"), "name": c.get("DisplayName")}
                for c in customers[:10]  # Limit to 10 results
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mapping/save", methods=["POST"])
def mapping_save():
    """Save a single customer mapping."""
    from src.mapping.customer_mapping import (
        CustomerMapping,
        load_customer_mapping,
        save_customer_mapping,
    )

    data = request.json
    jobsite_id = data.get("jobsite_id")
    qbo_customer_id = data.get("qbo_customer_id")
    qbo_display_name = data.get("qbo_display_name", "")

    if not jobsite_id or not qbo_customer_id:
        return jsonify({"error": "Missing jobsite_id or qbo_customer_id"}), 400

    try:
        mappings = load_customer_mapping()
        mappings[jobsite_id] = CustomerMapping(
            jobsite_id=jobsite_id,
            qbo_customer_id=qbo_customer_id,
            qbo_display_name=qbo_display_name,
        )
        save_customer_mapping(mappings)

        # Update session data to remove this jobsite from unmapped
        result = session.get("processing_result", {})
        unmapped = result.get("unmapped_jobsites", [])
        result["unmapped_jobsites"] = [j for j in unmapped if j["jobsite_id"] != jobsite_id]
        session["processing_result"] = result

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mapping/skip", methods=["POST"])
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
def results():
    """Show processing results and invoice preview."""
    result = session.get("processing_result")
    if not result:
        flash("No data to display. Please upload CSV files first.", "warning")
        return redirect(url_for("upload"))

    return render_template("results.html", result=result)


@app.route("/create-invoices", methods=["POST"])
def create_invoices():
    """Create draft invoices in QuickBooks."""
    from src.web_processing import create_qbo_invoices

    result = session.get("processing_result")
    if not result or not result.get("invoices"):
        flash("No invoices to create.", "warning")
        return redirect(url_for("results"))

    try:
        invoice_results = create_qbo_invoices(result["invoices"])
        session["invoice_results"] = invoice_results
        return redirect(url_for("invoice_results"))
    except Exception as e:
        flash(f"Error creating invoices: {e}", "error")
        return redirect(url_for("results"))


@app.route("/invoice-results")
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
    # For local development only - never runs in production (gunicorn used instead)
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
