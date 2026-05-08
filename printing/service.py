"""High-level printing service used by routes."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from printing import escpos_renderer, html_renderer
from printing.models import (
    CompanyProfile,
    PrinterSettings,
    ReceiptLayoutSettings,
)
from printing.printer_detector import (
    get_printer_status,
    list_printers_with_status,
    validate_printer_ready,
)
from printing.receipt_engine import build_receipt_context, render_receipt_text
from printing.windows_spooler import send_raw

log = logging.getLogger(__name__)


def _printer_logger() -> logging.Logger:
    logger = logging.getLogger("printing.audit")
    if logger.handlers:
        return logger
    base = Path(__file__).resolve().parent.parent
    logs_dir = base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs_dir / "printer.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger


printer_log = _printer_logger()


# ---------------------------------------------------------------------------
# settings facade
# ---------------------------------------------------------------------------

def get_saved_printer_settings() -> Dict[str, Any]:
    return PrinterSettings.load()


def save_printer_settings(data: Dict[str, Any]) -> int:
    return PrinterSettings.save(data)


def list_available_printers() -> Dict[str, Any]:
    printers = list_printers_with_status()
    online = [p for p in printers if p.get("status") == "online"]
    log.info("printer list loaded count=%d online=%d", len(printers), len(online))
    printer_log.info("available_printers total=%d online=%d", len(printers), len(online))
    return {
        "ok": True,
        "printers": printers,
        "count": len(printers),
        "online_count": len(online),
        "warning": "No connected receipt printer detected" if not online else None,
    }


def validate_printer_exists(printer_name: Optional[str]) -> Dict[str, Any]:
    """Validate printer exists and provide detailed status."""
    if not printer_name:
        return {"ok": False, "msg": "No printer selected"}

    info = get_printer_status(printer_name)
    printer_log.info(
        "printer_validation printer=%s status=%s driver=%s connected=%s",
        printer_name,
        info.get("status"),
        info.get("driver_installed"),
        info.get("connected")
    )

    if info["status"] in ("offline", "unknown") and not info.get("driver_installed", False):
        return {"ok": False, "msg": info.get("message", "Printer not found")}

    # Return detailed status even if not ready, so UI can show helpful message
    return {"ok": True, "info": info}


# ---------------------------------------------------------------------------
# print + preview flow
# ---------------------------------------------------------------------------

def preview_receipt(receipt_type: str, source_id: int) -> Dict[str, Any]:
    """Build everything needed for a preview (HTML + text)."""
    ctx = build_receipt_context(receipt_type, source_id)
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    html = html_renderer.render_html(receipt_type, ctx, layout, company)
    text = render_receipt_text(receipt_type, ctx, layout, company)
    log.info(
        "preview generated type=%s source=%s width=%s",
        receipt_type, source_id, layout.get("rcpt_layout_paper_width"),
    )
    return {
        "ok": True,
        "type": receipt_type,
        "source_id": source_id,
        "doc_number": ctx["doc_number"],
        "html": html,
        "text": text,
    }


def print_receipt(receipt_type: str, source_id: int) -> Dict[str, Any]:
    """Full print pipeline with real connectivity validation."""
    settings = PrinterSettings.load()
    printer_name = settings.get("printer_receipt_name") or ""
    log.info(
        "print requested type=%s source=%s printer=%s mode=%s",
        receipt_type, source_id, printer_name, settings.get("printer_print_mode"),
    )
    if not settings.get("printer_is_enabled", True):
        return {"ok": False, "msg": "Printing is disabled in settings"}
    if not printer_name:
        return {"ok": False, "msg": "No receipt printer selected — open Settings → Printer Settings"}

    status = validate_printer_ready(printer_name)
    log.info("printer status=%s can_print=%s", status.get("status"), status.get("can_print"))
    printer_log.info(
        "print_attempt printer=%s status=%s can_print=%s port_validated=%s",
        printer_name,
        status.get("status"),
        status.get("can_print"),
        status.get("port_validated")
    )
    if not status.get("can_print"):
        msg = status.get("message") or f"Printer {printer_name!r} not ready"
        if status.get("connected") is False:
            msg = f"Printer {printer_name!r} is not physically connected. Please verify the USB/Network connection."
        elif status.get("status") == "offline":
            msg = f"Printer {printer_name!r} is offline. Check power and connections."
        return {
            "ok": False,
            "msg": msg,
            "status": status,
        }

    company = CompanyProfile.load()
    layout = ReceiptLayoutSettings.load()
    ctx = build_receipt_context(receipt_type, source_id)

    mode = settings.get("printer_print_mode", "windows_raw")
    if mode in ("html", "html_preview", "pdf_print"):
        # HTML mode: caller (the browser) opens preview URL with auto_print=1
        # and prints via the OS print dialog. Service-side dispatch is a no-op
        # but we still return success so the UI flow is uniform.
        log.info("print dispatched mode=html printer=%s", printer_name)
        return {
            "ok": True,
            "mode": mode,
            "msg": "Opened in browser print dialog",
            "preview_url": f"/api/receipts/{receipt_type}/{source_id}/preview?auto_print=1",
            "doc_number": ctx["doc_number"],
        }
    if mode in ("escpos", "windows_raw"):
        # Both raw modes use the canonical ESC/POS renderer so that every
        # layout setting (logo, auto-cut, font, paper width, Sinhala image
        # rendering) is applied consistently regardless of which mode is chosen.
        payload = escpos_renderer.render_escpos(receipt_type, ctx, layout, company)
        result = send_raw(printer_name, payload, doc_name=f"Receipt {ctx['doc_number']}")
        printer_log.info(
            "receipt_print type=%s source=%s printer=%s mode=%s ok=%s result=%s",
            receipt_type, source_id, printer_name, mode, result.get("ok"), result.get("msg"),
        )
        log.info("print dispatched mode=%s ok=%s bytes=%d", mode, result.get("ok"), len(payload))
        return {**result, "mode": mode, "doc_number": ctx["doc_number"]}
    return {"ok": False, "msg": f"Unsupported print mode: {mode!r}"}


def test_receipt_print() -> Dict[str, Any]:
    """Send a diagnostic receipt rendered by the canonical receipt engine."""
    settings = PrinterSettings.load()
    name = settings.get("printer_receipt_name") or ""
    if not name:
        printer_log.warning("test_print attempted with no printer selected")
        return {"ok": False, "msg": "No receipt printer selected"}
    status = validate_printer_ready(name)
    printer_log.info("test_receipt_print printer=%s status=%s can_print=%s", name, status.get("status"), status.get("can_print"))
    if not status.get("can_print"):
        msg = status.get("message") or "Printer not ready"
        if status.get("connected") is False:
            msg = f"Printer {name!r} is not connected. {msg}"
        return {
            "ok": False,
            "msg": msg,
            "status": status,
        }
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    sample_ctx = {
        "type": "billing",
        "type_title": "TEST RECEIPT",
        "doc_number": "TEST-PRINT",
        "doc_label": "Receipt No",
        "datetime": "Test Print",
        "cashier": "System",
        "customer_name": "Test Customer",
        "customer_phone": "000 000 0000",
        "vehicle": "",
        "items": [{
            "name": "Printer Diagnostic Item",
            "qty": 1,
            "qty_str": "1",
            "price_str": "0.00",
            "value_str": "0.00",
        }],
        "subtotal": 0,
        "discount": 0,
        "tax": 0,
        "grand_total": 0,
        "paid": 0,
        "balance": 0,
        "outstanding": 0,
        "payment_method": "test",
        "items_count": 1,
        "extra": {},
    }
    # Always use the canonical ESC/POS renderer so that every layout setting
    # (logo, auto-cut, paper width) is exercised in the test print too.
    sample = escpos_renderer.render_escpos("billing", sample_ctx, layout, company)
    renderer_used = "canonical_escpos_renderer"
    result = send_raw(name, sample, doc_name="Printer Test")
    out = {
        **result,
        "printer_name": name,
        "print_mode": settings.get("printer_print_mode", "windows_raw"),
        "renderer_used": renderer_used,
        "spooler_result": result.get("msg"),
    }
    printer_log.info("test_print kind=receipt printer=%s ok=%s result=%s", name, out.get("ok"), out.get("msg"))
    return out


def test_label_print() -> Dict[str, Any]:
    settings = PrinterSettings.load()
    name = settings.get("printer_label_name") or ""
    if not name:
        printer_log.warning("test_label_print attempted with no printer selected")
        return {"ok": False, "msg": "No label printer selected"}
    status = validate_printer_ready(name)
    printer_log.info("test_label_print printer=%s status=%s can_print=%s", name, status.get("status"), status.get("can_print"))
    if not status.get("can_print"):
        msg = status.get("message") or "Printer not ready"
        if status.get("connected") is False:
            msg = f"Printer {name!r} is not connected. {msg}"
        return {
            "ok": False,
            "msg": msg,
            "status": status,
        }
    payload = b"--- Label Test ---\nGarage POS\n\n\n"
    result = send_raw(name, payload, doc_name="Label Test")
    out = {
        **result,
        "printer_name": name,
        "print_mode": settings.get("printer_print_mode", "windows_raw"),
        "renderer_used": "raw_label_test_payload",
        "spooler_result": result.get("msg"),
    }
    printer_log.info("test_print kind=label printer=%s ok=%s result=%s", name, out.get("ok"), out.get("msg"))
    return out


def cut_printer_paper() -> Dict[str, Any]:
    """Send a manual cut command to the selected receipt printer."""
    settings = PrinterSettings.load()
    name = settings.get("printer_receipt_name") or ""
    if not name:
        return {"ok": False, "msg": "No receipt printer selected"}

    # Minimal ESC/POS cut command
    _ESC = b"\x1b"
    _GS = b"\x1d"
    _CUT = _GS + b"V" + b"\x00"
    _INIT = _ESC + b"@"

    payload = _INIT + b"\n\n\n" + _CUT
    result = send_raw(name, payload, doc_name="Manual Cut")
    printer_log.info("manual_cut printer=%s ok=%s", name, result.get("ok"))
    return result
