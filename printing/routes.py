"""Canonical Flask routes for the printer + receipt system.

All routes are registered through :func:`register_printing_routes` to follow
the same pattern as the other route modules in this app.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from printing import service as printing_service
from printing.models import (
    CompanyProfile,
    PrinterSettings,
    ReceiptLayoutSettings,
)
from printing.printer_detector import (
    get_printer_status,
    list_printers_with_status,
)
from printing.receipt_engine import VALID_TYPES, build_receipt_context
from shared_helpers import role_from_user, user_has_any_role

log = logging.getLogger(__name__)

ALLOWED_LOGO_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _require_admin():
    if not user_has_any_role(current_user, "Admin", "Operator", "Developer"):
        abort(403)


def _require_admin_write():
    if not user_has_any_role(current_user, "Admin", "Operator"):
        abort(403)


def _validated_receipt_type(receipt_type: str) -> str:
    if receipt_type not in VALID_TYPES:
        abort(400, description=f"Invalid receipt type: {receipt_type}")
    return receipt_type


def _safe_int(value, name: str = "id") -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        abort(400, description=f"Invalid {name}")
    if v <= 0:
        abort(400, description=f"Invalid {name}")
    return v


def _logo_dir() -> Path:
    base = Path(__file__).resolve().parent.parent
    target = base / "static" / "uploads" / "logos"
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def register_printing_routes(app, *, db, log_action=None):
    """Register all canonical routes on ``app``."""

    log_action = log_action or (lambda *a, **k: None)

    # ── PAGES ──────────────────────────────────────────────────────────────

    @app.route("/settings/printers")
    @login_required
    def page_printer_settings():
        _require_admin()
        try:
            return render_template(
                "settings/printer_settings.html",
                settings=PrinterSettings.load(),
            )
        except Exception:
            log.exception(
                "Printer settings page render failed user=%s",
                getattr(current_user, "username", "?"),
            )
            raise

    @app.route("/settings/receipt-layout")
    @login_required
    def page_receipt_layout():
        _require_admin()
        try:
            return render_template(
                "settings/receipt_layout.html",
                layout=ReceiptLayoutSettings.load(),
            )
        except Exception:
            log.exception(
                "Receipt layout page render failed user=%s",
                getattr(current_user, "username", "?"),
            )
            raise

    @app.route("/settings/company-intro")
    @login_required
    def page_company_intro():
        _require_admin()
        try:
            return render_template(
                "settings/company_intro.html",
                company=CompanyProfile.load(),
            )
        except Exception:
            log.exception(
                "Company intro page render failed user=%s",
                getattr(current_user, "username", "?"),
            )
            raise

    # ── PRINTER SETTINGS API ───────────────────────────────────────────────

    @app.route("/api/settings/printers/list", methods=["GET"])
    @login_required
    def api_printers_list():
        _require_admin()
        return jsonify(printing_service.list_available_printers())

    @app.route("/api/settings/printers/status", methods=["GET"])
    @login_required
    def api_printer_status():
        _require_admin()
        name = (request.args.get("name") or "").strip()
        if not name:
            saved = PrinterSettings.load()
            name = saved.get("printer_receipt_name") or ""
        if not name:
            return jsonify({
                "ok": False,
                "msg": "No printer selected",
                "status": {
                    "name": "",
                    "status": "unknown",
                    "can_print": False,
                    "message": "No printer selected",
                },
            })
        info = get_printer_status(name)
        return jsonify({"ok": True, "status": info})

    @app.route("/api/settings/printers/save", methods=["POST"])
    @login_required
    def api_printers_save():
        _require_admin_write()
        data = request.get_json(silent=True) or {}
        try:
            saved = PrinterSettings.save(data)
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        log_action(
            "Printer settings saved",
            target_type="printer_settings",
            metadata={"keys": sorted(list(data.keys()))[:10]},
        )
        log.info("printer settings saved count=%d user=%s", saved, getattr(current_user, "username", "?"))
        return jsonify({"ok": True, "saved": saved})

    @app.route("/api/settings/printers/test", methods=["POST"])
    @login_required
    def api_printers_test():
        _require_admin_write()
        kind = (request.get_json(silent=True) or {}).get("kind", "receipt")
        if kind == "label":
            result = printing_service.test_label_print()
        else:
            result = printing_service.test_receipt_print()
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.route("/api/settings/printers/cut", methods=["POST"])
    @login_required
    def api_printers_cut():
        _require_admin_write()
        result = printing_service.cut_printer_paper()
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    # ── RECEIPT LAYOUT API ─────────────────────────────────────────────────

    @app.route("/api/settings/receipt-layout", methods=["GET"])
    @login_required
    def api_layout_get():
        _require_admin()
        return jsonify({"ok": True, "layout": ReceiptLayoutSettings.load()})

    @app.route("/api/settings/receipt-layout/save", methods=["POST"])
    @login_required
    def api_layout_save():
        _require_admin_write()
        data = request.get_json(silent=True) or {}
        try:
            saved = ReceiptLayoutSettings.save(data)
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        log_action(
            "Receipt layout saved",
            target_type="receipt_layout",
            metadata={"keys": sorted(list(data.keys()))[:10]},
        )
        log.info("receipt layout saved count=%d", saved)
        return jsonify({"ok": True, "saved": saved})

    @app.route("/api/settings/receipt-layout/preview", methods=["POST"])
    @login_required
    def api_layout_preview():
        _require_admin()
        data = request.get_json(silent=True) or {}
        receipt_type = data.get("type", "billing")
        if receipt_type not in VALID_TYPES:
            return jsonify({"ok": False, "msg": "Invalid receipt type"}), 400
        # caller may pass a tentative layout (live preview before save)
        try:
            layout = ReceiptLayoutSettings.load()
            for key, value in (data.get("layout") or {}).items():
                if key in layout:
                    layout[key] = value
        except Exception:
            layout = ReceiptLayoutSettings.load()
        company = CompanyProfile.load()
        ctx = _sample_context(receipt_type)
        from printing.receipt_engine import render_receipt_html
        html = render_receipt_html(receipt_type, ctx, layout, company)
        return jsonify({"ok": True, "html": html, "type": receipt_type})

    @app.route("/api/settings/receipt-layout/reset", methods=["POST"])
    @login_required
    def api_layout_reset():
        _require_admin_write()
        deleted = ReceiptLayoutSettings.reset()
        log_action(
            "Receipt layout reset",
            target_type="receipt_layout",
            metadata={"deleted": deleted},
        )
        return jsonify({"ok": True, "deleted": deleted, "layout": ReceiptLayoutSettings.load()})

    # ── COMPANY INTRO API ──────────────────────────────────────────────────

    @app.route("/api/settings/company-intro", methods=["GET"])
    @login_required
    def api_company_get():
        _require_admin()
        return jsonify({"ok": True, "company": CompanyProfile.load()})

    @app.route("/api/settings/company-intro/save", methods=["POST"])
    @login_required
    def api_company_save():
        _require_admin_write()
        # Accept JSON or multipart for logo upload.
        if request.content_type and "multipart/form-data" in request.content_type:
            data = {k: v for k, v in request.form.items()}
            file = request.files.get("logo")
            if file and file.filename:
                ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
                if ext not in ALLOWED_LOGO_EXT:
                    return jsonify({"ok": False, "msg": f"Logo type .{ext} is not allowed"}), 400
                file.stream.seek(0, os.SEEK_END)
                size = file.stream.tell()
                file.stream.seek(0)
                if size > MAX_LOGO_BYTES:
                    return jsonify({"ok": False, "msg": "Logo file too large (max 2MB)"}), 400
                safe_name = secure_filename(file.filename) or f"logo.{ext}"
                target = _logo_dir() / safe_name
                file.save(str(target))
                data["company_logo_path"] = f"/static/uploads/logos/{safe_name}"
        else:
            data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"ok": False, "msg": "Invalid payload"}), 400
        saved = CompanyProfile.save(data)
        log_action(
            "Company profile saved",
            target_type="company_profile",
            metadata={"keys": sorted(list(data.keys()))[:10]},
        )
        log.info("company profile saved count=%d", saved)
        return jsonify({"ok": True, "saved": saved, "company": CompanyProfile.load()})

    # ── RECEIPT PREVIEW + PRINT API (canonical) ────────────────────────────

    def _preview(receipt_type: str, source_id):
        receipt_type = _validated_receipt_type(receipt_type)
        sid = _safe_int(source_id, "source_id")
        try:
            result = printing_service.preview_receipt(receipt_type, sid)
        except LookupError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        # If browser asked for HTML directly, serve the receipt page so users
        # can hit Ctrl+P or auto-print. JSON consumers get the raw payload.
        wants_html = (
            "text/html" in (request.headers.get("Accept") or "")
            or request.args.get("format") == "html"
        )
        if wants_html:
            from flask import Response
            return Response(result["html"], mimetype="text/html")
        return jsonify(result)

    @app.route("/api/receipts/billing/<int:source_id>/preview", methods=["GET"])
    @login_required
    def api_receipt_billing_preview(source_id):
        return _preview("billing", source_id)

    @app.route("/api/receipts/job/<int:source_id>/preview", methods=["GET"])
    @login_required
    def api_receipt_job_preview(source_id):
        return _preview("job", source_id)

    @app.route("/api/receipts/return/<int:source_id>/preview", methods=["GET"])
    @login_required
    def api_receipt_return_preview(source_id):
        return _preview("return", source_id)

    def _print(receipt_type: str, source_id):
        receipt_type = _validated_receipt_type(receipt_type)
        sid = _safe_int(source_id, "source_id")
        try:
            result = printing_service.print_receipt(receipt_type, sid)
        except LookupError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.route("/api/receipts/billing/<int:source_id>/print", methods=["POST"])
    @login_required
    def api_receipt_billing_print(source_id):
        return _print("billing", source_id)

    @app.route("/api/receipts/job/<int:source_id>/print", methods=["POST"])
    @login_required
    def api_receipt_job_print(source_id):
        return _print("job", source_id)

    @app.route("/api/receipts/return/<int:source_id>/print", methods=["POST"])
    @login_required
    def api_receipt_return_print(source_id):
        return _print("return", source_id)

    # ── BACKWARD COMPAT ────────────────────────────────────────────────────
    # Old vehicle-history button calls /repairs/<id>/receipt?auto_print=1.
    # Redirect into the canonical preview endpoint with HTML output.

    @app.route("/repairs/<int:source_id>/receipt", methods=["GET"])
    @login_required
    def legacy_repair_receipt(source_id):
        auto = (request.args.get("auto_print") or "0").lower() in ("1", "true", "yes")
        try:
            result = printing_service.preview_receipt("job", int(source_id))
        except LookupError as exc:
            abort(404, description=str(exc))
        from flask import Response
        if auto:
            # The receipt template auto-prints when ?auto_print=1 is passed.
            return redirect(
                url_for("api_receipt_job_preview", source_id=source_id)
                + "?format=html&auto_print=1"
            )
        return Response(result["html"], mimetype="text/html")

    # Allow auto_print pass-through on canonical preview when format=html
    @app.before_request
    def _propagate_auto_print():  # pragma: no cover - tiny helper
        return None


# ---------------------------------------------------------------------------
# sample context for the live preview (when no real records exist yet)
# ---------------------------------------------------------------------------

def _sample_context(receipt_type: str) -> dict:
    from decimal import Decimal

    if receipt_type == "billing":
        items = [
            {"name": "30000017 OIL SEAL KIT CT100 2008-7113",
             "qty": 8, "qty_str": "8",
             "price": Decimal("950.00"), "price_str": "950.00",
             "value": Decimal("7600.00"), "value_str": "7,600.00"},
            {"name": "NUT 17 KEY WHEEL A/F",
             "qty": 25, "qty_str": "25",
             "price": Decimal("75.00"), "price_str": "75.00",
             "value": Decimal("1875.00"), "value_str": "1,875.00"},
            {"name": "HANDLE TROTTLE KIT SPORT-YELLOW",
             "qty": 5, "qty_str": "5",
             "price": Decimal("450.00"), "price_str": "450.00",
             "value": Decimal("2250.00"), "value_str": "2,250.00"},
        ]
        return {
            "type": "billing",
            "type_title": "SALES RECEIPT",
            "doc_label": "Receipt No",
            "doc_number": "INV-PREVIEW-0001",
            "datetime": "2026 May 22 - 11:37",
            "cashier": "Sandaru",
            "customer_name": "Kamal Perera",
            "customer_phone": "077 123 4567",
            "vehicle": "",
            "items": items,
            "subtotal": Decimal("15125.00"),
            "discount": Decimal("8520.00"),
            "tax": Decimal("0"),
            "grand_total": Decimal("6605.00"),
            "paid": Decimal("10.00"),
            "balance": Decimal("6595.00"),
            "outstanding": Decimal("6595.00"),
            "payment_method": "Cash",
            "items_count": 38,
            "extra": {},
        }
    if receipt_type == "job":
        items = [
            {"name": "Engine oil 4L",
             "qty": 1, "qty_str": "1",
             "price": Decimal("4500.00"), "price_str": "4,500.00",
             "value": Decimal("4500.00"), "value_str": "4,500.00"},
            {"name": "Oil filter",
             "qty": 1, "qty_str": "1",
             "price": Decimal("1200.00"), "price_str": "1,200.00",
             "value": Decimal("1200.00"), "value_str": "1,200.00"},
            {"name": "Labour Charge",
             "qty": 1, "qty_str": "1",
             "price": Decimal("2500.00"), "price_str": "2,500.00",
             "value": Decimal("2500.00"), "value_str": "2,500.00"},
        ]
        return {
            "type": "job",
            "type_title": "JOB RECEIPT",
            "doc_label": "Job No",
            "doc_number": "JOB-PREVIEW-0001",
            "datetime": "2026 May 22 - 09:10",
            "cashier": "Mechanic A",
            "customer_name": "Nimal Silva",
            "customer_phone": "071 222 3344",
            "vehicle": "CTI-204888 / Toyota Axio",
            "items": items,
            "subtotal": Decimal("8200.00"),
            "discount": Decimal("0"),
            "tax": Decimal("0"),
            "grand_total": Decimal("8200.00"),
            "paid": Decimal("3000.00"),
            "balance": Decimal("5200.00"),
            "outstanding": Decimal("5200.00"),
            "payment_method": "",
            "items_count": 3,
            "extra": {
                "service_type": "Service",
                "issue_reported": "Engine oil change + filter",
                "diagnosis": "",
                "odometer_in": 65420,
            },
        }
    # return
    items = [
        {"name": "Headlamp bulb",
         "qty": 2, "qty_str": "2",
         "price": Decimal("450.00"), "price_str": "450.00",
         "value": Decimal("900.00"), "value_str": "900.00"},
    ]
    return {
        "type": "return",
        "type_title": "RETURN RECEIPT",
        "doc_label": "Return No",
        "doc_number": "RET-PREVIEW-0001",
        "datetime": "2026 May 22 - 14:05",
        "cashier": "Sandaru",
        "customer_name": "Walk-in",
        "customer_phone": "",
        "vehicle": "",
        "items": items,
        "subtotal": Decimal("900.00"),
        "discount": Decimal("0"),
        "tax": Decimal("0"),
        "grand_total": Decimal("900.00"),
        "paid": Decimal("900.00"),
        "balance": Decimal("0"),
        "outstanding": Decimal("0"),
        "payment_method": "Refund",
        "items_count": 2,
        "extra": {
            "original_invoice": "INV-20260520-0042",
            "reason": "Wrong fitment",
            "return_type": "refund",
            "refund_amount": Decimal("900.00"),
        },
    }
