"""Flask web application for LMN to QuickBooks invoice automation."""

from __future__ import annotations

import io
import logging
import os
import secrets
import time
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
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

from src import results_store
from src.logging_config import configure_logging

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)


# =============================================================================
# Server-side result storage
# Full processing/invoice result dicts exceed the ~4 KB Flask cookie cap and
# were silently dropped. The session now carries only a UUID pointing to a
# filesystem-backed JSON blob. See src/results_store.py.
# =============================================================================


def _get_processing_result(default=None):
    """Load the current processing result from the server-side store."""
    return results_store.load(session.get("results_key")) or default


def _set_processing_result(result):
    """Persist the processing result, creating or updating its store entry."""
    key = session.get("results_key")
    if key:
        results_store.update(key, result)
    else:
        session["results_key"] = results_store.save(result)


def _clear_processing_result():
    results_store.delete(session.pop("results_key", None))


def _get_invoice_result():
    return results_store.load(session.get("invoice_results_key"))


def _set_invoice_result(result):
    key = session.get("invoice_results_key")
    if key:
        results_store.update(key, result)
    else:
        session["invoice_results_key"] = results_store.save(result)

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

# Initialize database tables (idempotent - uses CREATE TABLE IF NOT EXISTS)
try:
    from src.db.connection import init_db
    init_db()
except Exception as e:
    logger.warning(f"Database initialization skipped: {e}")


# =============================================================================
# Request Hooks - Request ID, access log, QBO credentials
# =============================================================================


@app.before_request
def assign_request_id():
    """Assign a short request ID and start timer for access log."""
    g.request_id = secrets.token_hex(4)
    g.request_start = time.monotonic()


@app.after_request
def log_request(response):
    """One-line access log per request: method, path, status, duration."""
    start = getattr(g, "request_start", None)
    duration_ms = int((time.monotonic() - start) * 1000) if start else -1
    logger.info(
        "%s %s -> %d (%dms)",
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response


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
        except Exception:
            # Log the error so we can debug - this was causing silent failures
            logger.exception("Error loading QBO credentials from session")


def require_qbo_auth(f):
    """Decorator to require QBO authentication for a route."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        from src.qbo.context import has_qbo_credentials

        if not has_qbo_credentials():
            # Return JSON error for AJAX requests
            if (
                request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return jsonify(
                    {"error": "Not connected to QuickBooks. Please reconnect."}
                ), 401
            flash("Please connect to QuickBooks first.", "warning")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated_function


@app.context_processor
def inject_connection_status():
    """Expose QBO and LMN connection status to every template for the header badges."""
    from src.qbo.context import has_qbo_credentials

    qbo_connected = bool(has_qbo_credentials())

    lmn_connected = False
    try:
        from src.db.lmn_credentials import get_cached_token

        lmn_connected = get_cached_token() is not None
    except Exception:
        lmn_connected = False

    return {"qbo_connected": qbo_connected, "lmn_connected": lmn_connected}


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
    from src.qbo.context import has_qbo_credentials

    # Clear any lingering invoice_type from older sessions
    session.pop("invoice_type", None)

    is_connected = has_qbo_credentials()
    realm_id = session.get("qbo_tokens", {}).get("realm_id") if is_connected else None

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
        flash(
            "Invalid state parameter - possible security issue. Please try again.",
            "error",
        )
        return redirect(url_for("index"))

    try:
        tokens = exchange_code_for_tokens(auth_code, realm_id)

        # Store tokens in session (not database)
        session["qbo_tokens"] = tokens

        # Set in request context for immediate use
        set_qbo_credentials(tokens["access_token"], tokens["realm_id"])

        flash(
            f"Successfully connected to QuickBooks! Company ID: {realm_id}", "success"
        )

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
# File Upload & Processing
# =============================================================================

ALLOWED_EXTENSIONS = {".pdf"}


def is_allowed_file(filename: str) -> bool:
    """Check if file has an allowed extension."""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


@app.route("/upload")
@require_qbo_auth
def upload():
    """Page with drag-and-drop UI for uploading the LMN Job History PDF."""
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
@require_qbo_auth
def upload_post():
    """Process the uploaded LMN Job History PDF."""
    from src.web_processing import process_uploaded_pdf, ProcessingError

    pdf_file = request.files.get("pdf_file")

    if not pdf_file or not pdf_file.filename:
        flash("Please upload the LMN Job History PDF.", "error")
        return redirect(url_for("upload"))

    if not is_allowed_file(pdf_file.filename):
        flash("Upload must be a .pdf file.", "error")
        return redirect(url_for("upload"))

    try:
        content = io.BytesIO(pdf_file.read())
        result = process_uploaded_pdf(pdf_file.filename, content)

        _clear_processing_result()
        _set_processing_result(result)

        if result.get("unmapped_jobsites"):
            return redirect(url_for("mapping"))
        return redirect(url_for("results"))

    except ProcessingError as e:
        flash(f"Error processing PDF: {e}", "error")
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
    result = _get_processing_result()
    if not result:
        logger.warning(
            "GET /mapping hit with no stored result (key=%s)",
            session.get("results_key"),
        )
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
        return jsonify(
            {
                "customers": [
                    {"id": c.get("Id"), "name": c.get("DisplayName")}
                    for c in customers[:10]
                ]
            }
        )
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

        result = _get_processing_result(default={})

        unmapped = result.get("unmapped_jobsites", [])
        result["unmapped_jobsites"] = [
            j for j in unmapped if j["jobsite_id"] != jobsite_id
        ]

        for inv in result.get("invoices", []):
            if inv["jobsite_id"] == jobsite_id:
                inv["qbo_customer_id"] = qbo_customer_id
                inv["qbo_display_name"] = qbo_display_name
                break

        _set_processing_result(result)

        return jsonify({"success": True})
    except Exception as e:
        logger.exception("Error saving customer mapping")
        return jsonify({"error": str(e)}), 500


@app.route("/mapping/skip", methods=["POST"])
@require_qbo_auth
def mapping_skip():
    """Skip remaining unmapped jobsites and proceed to results."""
    result = _get_processing_result(default={})
    result["skipped_jobsites"] = result.get("unmapped_jobsites", [])
    result["unmapped_jobsites"] = []
    _set_processing_result(result)
    return jsonify({"success": True, "redirect": url_for("results")})


# =============================================================================
# Item Mapping (LMN line descriptions → QBO Product/Service)
# =============================================================================


@app.route("/item-mapping")
@require_qbo_auth
def item_mapping():
    """List LMN line descriptions currently using the Other fallback.

    User-initiated (not a hard gate on /results). Every line already has a
    valid ItemRef via the fallback; this page lets the user upgrade any to
    a dedicated QBO Product/Service for cleaner reporting.
    """
    result = _get_processing_result()
    if not result:
        logger.warning(
            "GET /item-mapping hit with no stored result (key=%s)",
            session.get("results_key"),
        )
        flash("No data to map. Please upload files first.", "warning")
        return redirect(url_for("upload"))

    fallback_names = result.get("fallback_lookup_names", [])
    fallback_error = result.get("fallback_error")
    total_unique_items = len(result.get("item_refs", {}))

    return render_template(
        "item_mapping.html",
        fallback_lookup_names=fallback_names,
        fallback_error=fallback_error,
        total_unique_items=total_unique_items,
    )


@app.route("/item-mapping/search", methods=["POST"])
@require_qbo_auth
def item_mapping_search():
    """Search QBO Product/Service items by name (AJAX endpoint)."""
    from src.qbo.context import get_qbo_credentials
    from src.qbo.items import search_items_by_name

    json_data = request.json
    if not json_data:
        return jsonify({"error": "Request must be JSON"}), 400
    query = json_data.get("query", "")
    if len(query) < 2:
        return jsonify({"items": []})

    try:
        access_token, realm_id = get_qbo_credentials()
        items = search_items_by_name(access_token, realm_id, query, limit=20)
        return jsonify({"items": items})
    except Exception as e:
        logger.exception("Error searching QBO items")
        return jsonify({"error": str(e)}), 500


@app.route("/item-mapping/save", methods=["POST"])
@require_qbo_auth
def item_mapping_save():
    """Persist an item mapping override and update session state."""
    from src.db.item_overrides import save_item_override

    data = request.json or {}
    lmn_name = (data.get("lmn_item_name") or "").strip()
    qbo_item_id = (data.get("qbo_item_id") or "").strip()
    qbo_item_name = (data.get("qbo_item_name") or "").strip()

    if not lmn_name or not qbo_item_id:
        return jsonify({"error": "Missing lmn_item_name or qbo_item_id"}), 400

    try:
        save_item_override(lmn_name, qbo_item_id, qbo_item_name)

        result = _get_processing_result(default={})

        item_refs = result.get("item_refs", {})
        item_refs[lmn_name] = {"value": qbo_item_id, "name": qbo_item_name}
        result["item_refs"] = item_refs

        fallback_names = [
            n for n in result.get("fallback_lookup_names", []) if n != lmn_name
        ]
        result["fallback_lookup_names"] = fallback_names

        for inv in result.get("invoices", []):
            for line in inv.get("line_items", []):
                if (line.get("item_lookup_name") or "") == lmn_name:
                    line["qbo_item_name"] = qbo_item_name
                    line["uses_fallback"] = False

        summary = result.get("summary", {})
        summary["fallback_items"] = len(fallback_names)
        result["summary"] = summary

        _set_processing_result(result)

        return jsonify({"success": True, "remaining": len(fallback_names)})
    except Exception as e:
        logger.exception("Error saving item mapping")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Results & Invoice Creation
# =============================================================================


@app.route("/results")
@require_qbo_auth
def results():
    """Show processing results and invoice preview."""
    key = session.get("results_key")
    result = results_store.load(key)
    if not result:
        if key:
            logger.warning(
                "GET /results: results_key=%s present but no result loaded "
                "(file missing, expired, or unreadable — see prior warnings)",
                key,
            )
        else:
            logger.warning(
                "GET /results: no results_key in session — user landed here "
                "without uploading or session cookie was lost"
            )
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

    # Filter zero-price items to only mapped invoices
    mapped_jobsite_ids = {inv["jobsite_id"] for inv in mapped_invoices}
    zero_price_items = [
        item
        for item in result.get("zero_price_items", [])
        if item["jobsite_id"] in mapped_jobsite_ids
    ]

    # Build display result with only mapped invoices
    display_result = {
        "invoices": mapped_invoices,
        "skipped_jobsites": result.get("skipped_jobsites", []),
        "duplicates": duplicates,
        "duplicate_jobsite_ids": duplicate_jobsite_ids,
        "zero_price_items": zero_price_items,
        "total_amount": sum(inv["total"] for inv in mapped_invoices),
        "lmn_mapping_count": lmn_mapping_count,
        "fallback_lookup_names": result.get("fallback_lookup_names", []),
        "fallback_error": result.get("fallback_error"),
        "shop_missing": result.get("shop_missing", False),
        "summary": {
            "total_jobsites": len(all_invoices),
            "mapped_jobsites": len(mapped_invoices),
            "unmapped_jobsites": len(result.get("unmapped_jobsites", [])),
            "total_line_items": sum(len(inv["line_items"]) for inv in mapped_invoices),
            "fallback_items": len(result.get("fallback_lookup_names", [])),
        },
    }

    return render_template("results.html", result=display_result)


@app.route("/update-zero-price-items", methods=["POST"])
@require_qbo_auth
def update_zero_price_items():
    """Receive user-entered prices for zero-price items and update invoices."""
    from src.invoice.line_items import calculate_direct_payment_fee

    result = _get_processing_result()
    if not result:
        logger.warning(
            "POST /update-zero-price-items hit with no stored result (key=%s)",
            session.get("results_key"),
        )
        flash("No data to display. Please upload files first.", "warning")
        return redirect(url_for("upload"))

    zero_price_items = result.get("zero_price_items", [])
    if not zero_price_items:
        return redirect(url_for("results"))

    # Parse and validate submitted prices
    from src.invoice.line_items import strip_unit_marker

    new_line_items_by_jobsite = {}
    for item in zero_price_items:
        idx = item["index"]
        rate_str = request.form.get(f"rate_{idx}", "").strip()
        qty_str = request.form.get(f"quantity_{idx}", "").strip()
        desc = request.form.get(f"description_{idx}", "").strip()

        if not rate_str or float(rate_str) <= 0:
            flash("All items must have a price greater than $0.", "error")
            return redirect(url_for("results"))

        rate = float(rate_str)
        quantity = float(qty_str) if qty_str else item["quantity"]
        description = desc or item["description"]
        amount = round(rate * quantity, 2)

        jobsite_id = item["jobsite_id"]
        if jobsite_id not in new_line_items_by_jobsite:
            new_line_items_by_jobsite[jobsite_id] = []
        new_line_items_by_jobsite[jobsite_id].append(
            {
                "description": description,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "item_lookup_name": strip_unit_marker(description),
                "qbo_item_name": None,
                "uses_fallback": False,
            }
        )

    # Update invoices in session
    fee_description = "Direct Payment Fee (Subtract if paying by USPS check)"
    for inv in result["invoices"]:
        jobsite_id = inv["jobsite_id"]
        if jobsite_id not in new_line_items_by_jobsite:
            continue

        # Strip existing fee line item
        inv["line_items"] = [
            li for li in inv["line_items"] if li["description"] != fee_description
        ]

        # Add user-priced items
        inv["line_items"].extend(new_line_items_by_jobsite[jobsite_id])

        # Recalculate totals
        inv["subtotal"] = round(sum(li["amount"] for li in inv["line_items"]), 2)
        inv["direct_payment_fee"] = calculate_direct_payment_fee(inv["subtotal"])
        inv["line_items"].append(
            {
                "description": fee_description,
                "quantity": 1,
                "rate": inv["direct_payment_fee"],
                "amount": inv["direct_payment_fee"],
                "item_lookup_name": "Direct Payment Fee",
                "qbo_item_name": None,
                "uses_fallback": False,
            }
        )
        inv["total"] = round(inv["subtotal"] + inv["direct_payment_fee"], 2)

    # Clear zero-price items so modal doesn't reappear
    result["zero_price_items"] = []

    # Re-resolve item refs now that new line descriptions are in play.
    from src.web_processing import _resolve_line_items

    item_refs, fallback_names, fallback_error = _resolve_line_items(
        result["invoices"]
    )
    result["item_refs"] = item_refs
    result["fallback_lookup_names"] = fallback_names
    result["fallback_error"] = fallback_error
    summary = result.get("summary", {})
    summary["fallback_items"] = len(fallback_names)
    result["summary"] = summary

    _set_processing_result(result)

    return redirect(url_for("results"))


@app.route("/create-invoices", methods=["POST"])
@require_qbo_auth
def create_invoices():
    """Create draft invoices in QuickBooks."""
    from src.web_processing import create_qbo_invoices

    result = _get_processing_result()
    if not result or not result.get("invoices"):
        logger.warning(
            "POST /create-invoices: missing or empty result (key=%s)",
            session.get("results_key"),
        )
        flash("No invoices to create.", "warning")
        return redirect(url_for("results"))

    # Block if the Other fallback item couldn't be resolved — QBO requires
    # an ItemRef on every line, so submitting now would cause API rejections.
    fallback_error = result.get("fallback_error")
    if fallback_error:
        flash(fallback_error, "error")
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
            inv
            for inv in mapped_invoices
            if inv["jobsite_id"] not in duplicate_jobsite_ids
        ]
        if not mapped_invoices:
            flash("No invoices to create after skipping duplicates.", "info")
            return redirect(url_for("results"))

    item_refs = result.get("item_refs") or {}

    try:
        invoice_results = create_qbo_invoices(mapped_invoices, item_refs)
        _set_invoice_result(invoice_results)
        return redirect(url_for("invoice_results"))
    except Exception as e:
        flash(f"Error creating invoices: {e}", "error")
        return redirect(url_for("results"))


@app.route("/invoice-results")
@require_qbo_auth
def invoice_results():
    """Show results of invoice creation."""
    results = _get_invoice_result()
    if not results:
        logger.warning(
            "GET /invoice-results: no stored invoice result (key=%s)",
            session.get("invoice_results_key"),
        )
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
