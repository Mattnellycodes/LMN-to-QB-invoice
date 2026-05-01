"""Flask web application for LMN to QuickBooks invoice automation."""

from __future__ import annotations

import logging
import os
import secrets
import threading
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


def _active_zero_price_items(result):
    """Zero-price items that belong to mapped invoices — the list actually rendered in the modal.

    Shared by GET /results (renders the modal) and POST /update-zero-price-items
    (validates submitted prices) so both agree on which item indexes to expect.

    Merged (maint + Irr) invoices contribute $0 rows from both source jobsites,
    so match on any source jobsite_id — not just the primary.
    """
    mapped_source_ids: set[str] = set()
    for inv in result.get("invoices", []):
        if not inv.get("qbo_customer_id"):
            continue
        sources = inv.get("sources") or []
        if sources:
            for src in sources:
                mapped_source_ids.add(str(src.get("jobsite_id", "")))
        else:
            mapped_source_ids.add(str(inv.get("jobsite_id", "")))
    return [
        item
        for item in result.get("zero_price_items", [])
        if str(item.get("jobsite_id", "")) in mapped_source_ids
    ]


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
except Exception:
    logger.exception(
        "Database initialization failed — app will run without DB features"
    )


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
    """Process uploaded LMN Job History PDF(s)."""
    from src.web_processing import ProcessingError, UploadedPdf, process_uploaded_pdfs

    pdf_files = [
        file for file in request.files.getlist("pdf_file") if file and file.filename
    ]

    if not pdf_files:
        flash("Please upload at least one LMN Job History PDF.", "error")
        return redirect(url_for("upload"))

    for pdf_file in pdf_files:
        if not is_allowed_file(pdf_file.filename):
            flash(f"{pdf_file.filename} must be a .pdf file.", "error")
            return redirect(url_for("upload"))

    try:
        uploads = [
            UploadedPdf(filename=pdf_file.filename, content=pdf_file.read())
            for pdf_file in pdf_files
        ]
        result = process_uploaded_pdfs(uploads)

        _clear_processing_result()
        _set_processing_result(result)

        logger.info(
            "POST /upload committed: files=%d invoices=%d unmapped=%d zero_price=%d",
            len(uploads),
            len(result.get("invoices", [])),
            len(result.get("unmapped_jobsites", [])),
            len(result.get("zero_price_items", [])),
        )

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

    zero_price_items = _active_zero_price_items(result)

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

    zero_price_items = _active_zero_price_items(result)
    if not zero_price_items:
        return redirect(url_for("results"))

    submitted_keys = sorted(
        k
        for k in request.form.keys()
        if k.startswith(("rate_", "quantity_", "description_"))
    )
    logger.info(
        "POST /update-zero-price-items: %d active items, form keys=%s",
        len(zero_price_items),
        submitted_keys,
    )

    # Parse and validate submitted prices
    from src.invoice.line_items import (
        FEE_DESCRIPTION,
        FEE_ITEM_LOOKUP_NAME,
        MAINTENANCE_CLASS_NAME,
        strip_unit_marker,
    )

    # Map every source jobsite_id -> the primary jobsite_id of its containing
    # invoice, so Irr-side submitted items route onto the merged invoice.
    source_to_primary: dict[str, str] = {}
    for inv in result["invoices"]:
        primary = str(inv["jobsite_id"])
        for src in inv.get("sources") or []:
            source_to_primary[str(src["jobsite_id"])] = primary
        # Safety net: invoices without a sources list route by their own id.
        source_to_primary.setdefault(primary, primary)

    new_line_items_by_primary: dict[str, list[dict]] = {}
    for item in zero_price_items:
        idx = item["index"]
        rate_str = request.form.get(f"rate_{idx}", "").strip()
        qty_str = request.form.get(f"quantity_{idx}", "").strip()
        desc = request.form.get(f"description_{idx}", "").strip()

        if not rate_str or float(rate_str) <= 0:
            logger.warning(
                "Zero-price validation failed: item index=%s jobsite=%s rate_str=%r",
                idx,
                item.get("jobsite_id"),
                rate_str,
            )
            flash("All items must have a price greater than $0.", "error")
            return redirect(url_for("results"))

        rate = float(rate_str)
        quantity = float(qty_str) if qty_str else item["quantity"]
        description = desc or item["description"]
        amount = round(rate * quantity, 2)

        source_id = str(item["jobsite_id"])
        primary_id = source_to_primary.get(source_id, source_id)
        class_name = item.get("class_name") or MAINTENANCE_CLASS_NAME
        new_line_items_by_primary.setdefault(primary_id, []).append(
            {
                "description": description,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "item_lookup_name": strip_unit_marker(description),
                "class_name": class_name,
                "qbo_item_name": None,
                "uses_fallback": False,
            }
        )

    # Update invoices in session
    for inv in result["invoices"]:
        primary_id = str(inv["jobsite_id"])
        if primary_id not in new_line_items_by_primary:
            continue

        # Strip existing fee line item
        inv["line_items"] = [
            li for li in inv["line_items"] if li["description"] != FEE_DESCRIPTION
        ]

        # Add user-priced items
        inv["line_items"].extend(new_line_items_by_primary[primary_id])

        # Recalculate totals
        inv["subtotal"] = round(sum(li["amount"] for li in inv["line_items"]), 2)
        inv["direct_payment_fee"] = calculate_direct_payment_fee(inv["subtotal"])
        if inv["direct_payment_fee"] > 0:
            inv["line_items"].append(
                {
                    "description": FEE_DESCRIPTION,
                    "quantity": 1,
                    "rate": inv["direct_payment_fee"],
                    "amount": inv["direct_payment_fee"],
                    "item_lookup_name": FEE_ITEM_LOOKUP_NAME,
                    "class_name": MAINTENANCE_CLASS_NAME,
                    "qbo_item_name": None,
                    "uses_fallback": False,
                }
            )
        inv["total"] = round(inv["subtotal"] + inv["direct_payment_fee"], 2)

    # Clear zero-price items so modal doesn't reappear
    result["zero_price_items"] = []

    # Re-resolve item refs now that new line descriptions are in play.
    from src.web_processing import _resolve_line_items

    item_refs, fallback_names, fallback_error = _resolve_line_items(result["invoices"])
    result["item_refs"] = item_refs
    result["fallback_lookup_names"] = fallback_names
    result["fallback_error"] = fallback_error
    summary = result.get("summary", {})
    summary["fallback_items"] = len(fallback_names)
    result["summary"] = summary

    _set_processing_result(result)
    logger.info(
        "POST /update-zero-price-items committed: %d item(s) priced across %d invoice(s)",
        sum(len(v) for v in new_line_items_by_primary.values()),
        len(new_line_items_by_primary),
    )

    return redirect(url_for("results"))


def _run_invoice_creation(
    flask_app,
    progress_key,
    invoice_results_key,
    invoices,
    item_refs,
    qbo_tokens,
):
    """Background worker: create invoices in QBO and write progress to disk.

    Runs in a daemon thread spawned by /create-invoices. Uses a fresh app
    context so request-scoped helpers (set_qbo_credentials, DB writes) work.
    """
    from src.qbo.context import set_qbo_credentials
    from src.web_processing import create_qbo_invoices

    total = len(invoices)
    with flask_app.app_context():
        set_qbo_credentials(qbo_tokens["access_token"], qbo_tokens["realm_id"])

        def on_progress(completed, total_, last):
            results_store.save_progress(
                progress_key,
                {
                    "status": "running",
                    "completed": completed,
                    "total": total_,
                    "current": last.get("customer_name"),
                },
            )

        try:
            invoice_results = create_qbo_invoices(
                invoices, item_refs, progress_callback=on_progress
            )
            results_store.update(invoice_results_key, invoice_results)
            success_count = sum(1 for r in invoice_results if r.get("success"))
            logger.info(
                "Background invoice creation finished: created=%d failed=%d",
                success_count,
                len(invoice_results) - success_count,
            )
            results_store.save_progress(
                progress_key,
                {
                    "status": "done",
                    "completed": len(invoice_results),
                    "total": total,
                    "current": None,
                },
            )
        except Exception as e:
            logger.exception("Background invoice creation failed")
            results_store.save_progress(
                progress_key,
                {
                    "status": "error",
                    "error": str(e),
                    "completed": 0,
                    "total": total,
                    "current": None,
                },
            )


@app.route("/create-invoices", methods=["POST"])
@require_qbo_auth
def create_invoices():
    """Kick off invoice creation in a background thread; render loading page."""
    result = _get_processing_result()
    results_key = session.get("results_key")
    if not result or not result.get("invoices"):
        logger.warning(
            "POST /create-invoices: missing or empty result (key=%s)",
            results_key,
        )
        flash("No invoices to create.", "warning")
        return redirect(url_for("results"))

    # If a previous run for this upload is still in flight, just re-render
    # the loading page instead of spawning a second thread.
    existing = results_store.load_progress(results_key)
    if existing and existing.get("status") == "running":
        return render_template("creating_invoices.html", total=existing.get("total", 0))

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

    logger.info(
        "POST /create-invoices: attempting %d invoice(s) (skip_duplicates=%s)",
        len(mapped_invoices),
        bool(request.form.get("skip_duplicates")),
    )

    # Reserve a key for the eventual invoice-results blob so the worker thread
    # can write to it directly (it can't touch session).
    invoice_results_key = results_store.save([])
    session["invoice_results_key"] = invoice_results_key

    qbo_tokens = session.get("qbo_tokens") or {}

    # Initialize progress sidecar before spawning so the loading page sees
    # status=running on its first poll even if the thread hasn't yielded yet.
    results_store.save_progress(
        results_key,
        {
            "status": "running",
            "completed": 0,
            "total": len(mapped_invoices),
            "current": None,
        },
    )

    threading.Thread(
        target=_run_invoice_creation,
        args=(
            app,
            results_key,
            invoice_results_key,
            mapped_invoices,
            item_refs,
            qbo_tokens,
        ),
        daemon=True,
    ).start()

    return render_template("creating_invoices.html", total=len(mapped_invoices))


@app.route("/create-invoices/progress")
def create_invoices_progress():
    """Return JSON status of the in-flight invoice-creation thread."""
    results_key = session.get("results_key")
    progress = results_store.load_progress(results_key)
    if progress is None:
        return jsonify({"error": "no progress"}), 404
    if progress.get("status") == "done":
        progress = dict(progress)
        progress["redirect"] = url_for("invoice_results")
    return jsonify(progress)


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
# Admin: delete every QBO invoice created on a target date (broken-run recovery)
# =============================================================================


def _build_cleanup_preview(target_date: str) -> dict:
    """Build the list of invoices to delete for `target_date` (YYYY-MM-DD).

    Combines two sources so a partial-write on the broken run still gets cleaned
    up: every `invoice_history` row for the date, plus any QBO invoice whose
    MetaData.CreateTime falls on the date but is missing from `invoice_history`.
    Both sources go into a single flat list — every invoice will be deleted.
    """
    from src.db.invoice_history import get_invoices_created_on
    from src.qbo.invoices import query_invoices_created_since

    rows = get_invoices_created_on(target_date)
    history_targets = [
        {
            "source": "invoice_history",
            "qbo_invoice_id": str(r["qbo_invoice_id"]),
            "qbo_invoice_number": r["qbo_invoice_number"],
            "label": r["jobsite_id"],
            "total_amount": r["total_amount"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    orphan_targets: list[dict] = []
    cross_check_error: str | None = None
    try:
        iso_start = f"{target_date}T00:00:00Z"
        qbo_invoices = query_invoices_created_since(iso_start)
        known_ids = {str(r["qbo_invoice_id"]) for r in rows}
        for inv in qbo_invoices:
            inv_id = str(inv.get("Id"))
            if inv_id in known_ids:
                continue
            create_time = (inv.get("MetaData") or {}).get("CreateTime") or ""
            # Filter to this date only — the QBO query is lower-bounded but
            # may include later days if the user runs cleanup days after.
            if not create_time.startswith(target_date):
                continue
            orphan_targets.append(
                {
                    "source": "qbo_orphan",
                    "qbo_invoice_id": inv_id,
                    "qbo_invoice_number": inv.get("DocNumber"),
                    "label": (inv.get("CustomerRef") or {}).get("name")
                    or (inv.get("CustomerRef") or {}).get("value"),
                    "total_amount": float(inv.get("TotalAmt") or 0),
                    "created_at": create_time,
                }
            )
    except Exception as e:
        logger.exception("QBO orphan cross-check failed")
        cross_check_error = str(e)

    all_targets = history_targets + orphan_targets
    all_targets.sort(key=lambda t: t.get("created_at") or "")

    return {
        "target_date": target_date,
        "all_targets": all_targets,
        "history_count": len(history_targets),
        "orphan_count": len(orphan_targets),
        "cross_check_error": cross_check_error,
        "total_amount_sum": round(sum(t["total_amount"] or 0 for t in all_targets), 2),
    }


@app.route("/admin/cleanup-recent-duplicates")
@require_qbo_auth
def admin_cleanup_preview():
    """Preview every invoice created on the target date (default: today)."""
    target_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    preview = _build_cleanup_preview(target_date)

    confirm_token = secrets.token_urlsafe(16)
    session["cleanup_confirm_token"] = confirm_token
    session["cleanup_target_date"] = target_date

    return render_template(
        "cleanup_duplicates.html",
        preview=preview,
        confirm_token=confirm_token,
        executed=False,
    )


def _run_cleanup_deletion(
    flask_app,
    progress_key: str,
    results_key: str,
    target_date: str,
    qbo_tokens: dict,
):
    """Background worker: delete every invoice in the rebuilt preview.

    Mirrors `_run_invoice_creation` — fresh app context, set credentials from
    the passed token snapshot, write progress to disk after every delete, prune
    invoice_history per-row so a mid-run crash leaves consistent state.
    """
    from src.db.invoice_history import delete_history_by_invoice_ids
    from src.qbo.context import set_qbo_credentials
    from src.qbo.invoices import delete_invoice

    with flask_app.app_context():
        set_qbo_credentials(qbo_tokens["access_token"], qbo_tokens["realm_id"])

        try:
            preview = _build_cleanup_preview(target_date)
            targets = preview["all_targets"]
            total = len(targets)

            results_store.save_progress(
                progress_key,
                {
                    "status": "running",
                    "completed": 0,
                    "total": total,
                    "current": None,
                    "deleted_count": 0,
                    "failed_count": 0,
                },
            )

            deleted: list[dict] = []
            failed: list[dict] = []
            for i, target in enumerate(targets, start=1):
                invoice_id = target["qbo_invoice_id"]
                label = target.get("label") or invoice_id
                results_store.save_progress(
                    progress_key,
                    {
                        "status": "running",
                        "completed": i - 1,
                        "total": total,
                        "current": f"{label} (#{target.get('qbo_invoice_number') or invoice_id})",
                        "deleted_count": len(deleted),
                        "failed_count": len(failed),
                    },
                )
                result = delete_invoice(invoice_id)
                record = {
                    "source": target["source"],
                    "label": target["label"],
                    "qbo_invoice_id": invoice_id,
                    "qbo_invoice_number": target["qbo_invoice_number"],
                    "total_amount": target["total_amount"],
                }
                if result.success:
                    deleted.append(record)
                    if target["source"] == "invoice_history":
                        try:
                            delete_history_by_invoice_ids([invoice_id])
                        except Exception:
                            logger.exception(
                                "Per-row invoice_history prune failed for id=%s",
                                invoice_id,
                            )
                else:
                    record["error"] = result.error
                    failed.append(record)

                results_store.save_progress(
                    progress_key,
                    {
                        "status": "running",
                        "completed": i,
                        "total": total,
                        "current": None,
                        "deleted_count": len(deleted),
                        "failed_count": len(failed),
                    },
                )

            results_store.update(
                results_key,
                {
                    "preview": preview,
                    "deleted": deleted,
                    "failed": failed,
                    "pruned": sum(
                        1 for d in deleted if d["source"] == "invoice_history"
                    ),
                },
            )
            results_store.save_progress(
                progress_key,
                {
                    "status": "done",
                    "completed": total,
                    "total": total,
                    "current": None,
                    "deleted_count": len(deleted),
                    "failed_count": len(failed),
                },
            )
            logger.info(
                "Cleanup deletion finished: deleted=%d failed=%d",
                len(deleted),
                len(failed),
            )
        except Exception as e:
            logger.exception("Background cleanup deletion failed")
            results_store.save_progress(
                progress_key,
                {
                    "status": "error",
                    "error": str(e),
                    "completed": 0,
                    "total": 0,
                    "current": None,
                    "deleted_count": 0,
                    "failed_count": 0,
                },
            )


def _cleanup_progress_key(target_date: str) -> str:
    """Stable per-day progress key — survives session loss after a worker restart."""
    return f"cleanup-{target_date}"


@app.route("/admin/cleanup-recent-duplicates", methods=["POST"])
@require_qbo_auth
def admin_cleanup_execute():
    """Spawn the deletion worker; render the polling progress page."""
    submitted_token = request.form.get("confirm_token")
    expected_token = session.pop("cleanup_confirm_token", None)
    target_date = session.pop("cleanup_target_date", None) or datetime.now().strftime(
        "%Y-%m-%d"
    )
    if not submitted_token or submitted_token != expected_token:
        flash("Cleanup confirmation expired or invalid — re-open the preview.", "error")
        return redirect(url_for("admin_cleanup_preview", date=target_date))

    progress_key = _cleanup_progress_key(target_date)

    # If a previous run is still in flight, just re-render the polling page.
    existing = results_store.load_progress(progress_key)
    if existing and existing.get("status") == "running":
        session["cleanup_results_key"] = session.get("cleanup_results_key") or ""
        return render_template(
            "cleanup_progress.html",
            target_date=target_date,
            total=existing.get("total", 0),
        )

    # Reserve a results blob the worker writes the final deleted/failed lists to.
    results_key = results_store.save({"deleted": [], "failed": [], "preview": None})
    session["cleanup_results_key"] = results_key

    qbo_tokens = session.get("qbo_tokens") or {}

    # Pre-write initial progress so the loading page sees status=running on
    # its first poll even if the thread hasn't yielded yet.
    results_store.save_progress(
        progress_key,
        {
            "status": "running",
            "completed": 0,
            "total": 0,  # actual total populated by the worker after preview rebuild
            "current": None,
            "deleted_count": 0,
            "failed_count": 0,
        },
    )

    threading.Thread(
        target=_run_cleanup_deletion,
        args=(app, progress_key, results_key, target_date, qbo_tokens),
        daemon=True,
    ).start()

    return render_template(
        "cleanup_progress.html",
        target_date=target_date,
        total=0,
    )


@app.route("/admin/cleanup-recent-duplicates/progress")
@require_qbo_auth
def admin_cleanup_progress():
    """JSON status of the in-flight cleanup deletion thread."""
    target_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    progress = results_store.load_progress(_cleanup_progress_key(target_date))
    if progress is None:
        return jsonify({"error": "no progress"}), 404
    if progress.get("status") == "done":
        progress = dict(progress)
        progress["redirect"] = url_for(
            "admin_cleanup_results", date=target_date
        )
    return jsonify(progress)


@app.route("/admin/cleanup-recent-duplicates/results")
@require_qbo_auth
def admin_cleanup_results():
    """Render the final deleted/failed report after the worker completes."""
    target_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    results_key = session.get("cleanup_results_key")
    final = results_store.load(results_key) if results_key else None
    if not final:
        flash("No cleanup results to display — re-open the preview.", "warning")
        return redirect(url_for("admin_cleanup_preview", date=target_date))

    return render_template(
        "cleanup_duplicates.html",
        preview=final.get("preview") or _build_cleanup_preview(target_date),
        executed=True,
        deleted=final.get("deleted", []),
        failed=final.get("failed", []),
        pruned=final.get("pruned", 0),
    )


# =============================================================================
# Admin: clear invoice_history (for use after a manual QBO wipe)
# =============================================================================


def _invoice_history_count() -> int:
    from src.db.connection import db_cursor

    with db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM invoice_history")
        row = cursor.fetchone()
        return int(row[0]) if row else 0


@app.route("/admin/clear-invoice-history")
@require_qbo_auth
def admin_clear_history_preview():
    """Confirm-page for wiping invoice_history.

    The idempotency check at /create-invoices treats any matching row in
    invoice_history as a "skip" — so stale rows (pointing at QBO invoices
    that have since been deleted) cause silent skip-creates on the next run.
    Clearing the table after a manual QBO wipe restores a clean slate.
    """
    try:
        count = _invoice_history_count()
    except Exception as e:
        logger.exception("Failed to count invoice_history rows")
        flash(f"Could not read invoice_history: {e}", "error")
        return redirect(url_for("index"))

    confirm_token = secrets.token_urlsafe(16)
    session["clear_history_confirm_token"] = confirm_token

    return render_template(
        "clear_invoice_history.html",
        row_count=count,
        confirm_token=confirm_token,
        executed=False,
        deleted=0,
    )


@app.route("/admin/clear-invoice-history", methods=["POST"])
@require_qbo_auth
def admin_clear_history_execute():
    """Truncate invoice_history after a confirmation token check."""
    from src.db.connection import db_cursor

    submitted_token = request.form.get("confirm_token")
    expected_token = session.pop("clear_history_confirm_token", None)
    if not submitted_token or submitted_token != expected_token:
        flash(
            "Confirmation expired or invalid — re-open the preview.",
            "error",
        )
        return redirect(url_for("admin_clear_history_preview"))

    try:
        with db_cursor() as cursor:
            cursor.execute("DELETE FROM invoice_history")
            deleted = int(cursor.rowcount or 0)
    except Exception as e:
        logger.exception("Failed to clear invoice_history")
        flash(f"Failed to clear invoice_history: {e}", "error")
        return redirect(url_for("admin_clear_history_preview"))

    logger.warning("invoice_history cleared via /admin: deleted=%d rows", deleted)

    return render_template(
        "clear_invoice_history.html",
        row_count=0,
        confirm_token=None,
        executed=True,
        deleted=deleted,
    )


# =============================================================================
# Run Server
# =============================================================================


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
