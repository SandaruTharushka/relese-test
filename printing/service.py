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
    log.info("printer list loaded count=%d", len(printers))
    return {
        "ok": True,
        "printers": printers,
        "count": len(printers),
    }


def validate_printer_exists(printer_name: Optional[str]) -> Dict[str, Any]:
    if not printer_name:
        return {"ok": False, "msg": "No printer selected"}
    info = get_printer_status(printer_name)
    if info["status"] in ("offline", "unknown") and not info.get("driver_installed", False):
        return {"ok": False, "msg": info.get("message", "Printer not found")}
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
    """Full print pipeline."""
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
    if not status.get("can_print"):
        return {
            "ok": False,
            "msg": status.get("message") or f"Printer {printer_name!r} not ready",
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
        if mode == "escpos":
            payload = escpos_renderer.render_escpos(receipt_type, ctx, layout, company)
        else:
            payload = render_receipt_text(receipt_type, ctx, layout, company).encode(
                "cp437", errors="replace"
            )
        result = send_raw(printer_name, payload, doc_name=f"Receipt {ctx['doc_number']}")
        printer_log.info("receipt_print type=%s source=%s printer=%s mode=%s ok=%s result=%s", receipt_type, source_id, printer_name, mode, result.get("ok"), result.get("msg"))
        log.info("print dispatched mode=%s ok=%s", mode, result.get("ok"))
        return {**result, "mode": mode, "doc_number": ctx["doc_number"]}
    return {"ok": False, "msg": f"Unsupported print mode: {mode!r}"}


def test_receipt_print() -> Dict[str, Any]:
    """Send a tiny diagnostic receipt to the configured printer."""
    settings = PrinterSettings.load()
    name = settings.get("printer_receipt_name") or ""
    if not name:
        return {"ok": False, "msg": "No receipt printer selected"}
    status = validate_printer_ready(name)
    if not status.get("can_print"):
        return {
            "ok": False,
            "msg": status.get("message") or "Printer not ready",
            "status": status,
        }
    sample = (
        b"\x1b@"
        b"\x1ba\x01"
        b"--- Test Receipt ---\n"
        b"Garage POS\n"
        b"Printer OK\n\n\n"
        b"\x1dV\x00"
    )
    result = send_raw(name, sample, doc_name="Printer Test")
    out = {
        **result,
        "printer_name": name,
        "print_mode": settings.get("printer_print_mode", "windows_raw"),
        "renderer_used": "raw_escpos_test_payload",
        "spooler_result": result.get("msg"),
    }
    printer_log.info("test_print kind=receipt printer=%s ok=%s result=%s", name, out.get("ok"), out.get("msg"))
    return out


def test_label_print() -> Dict[str, Any]:
    settings = PrinterSettings.load()
    name = settings.get("printer_label_name") or ""
    if not name:
        return {"ok": False, "msg": "No label printer selected"}
    status = validate_printer_ready(name)
    if not status.get("can_print"):
        return {
            "ok": False,
            "msg": status.get("message") or "Printer not ready",
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
