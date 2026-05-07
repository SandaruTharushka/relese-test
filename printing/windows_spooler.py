"""Windows print dispatch.

Uses ``win32print`` directly — never ``shell=True`` and never ``subprocess``
calls into ``rundll32`` / ``mshta`` / ``print.exe``. On non-Windows hosts the
functions return a structured "unsupported" error so the service layer can
surface a clean message.
"""
from __future__ import annotations

import logging
import platform
from typing import Dict, Union

log = logging.getLogger(__name__)


def _try_import_win32print():
    try:
        import win32print  # type: ignore
        return win32print
    except Exception:
        return None


def _unsupported(reason: str) -> Dict[str, object]:
    return {"ok": False, "msg": reason}


def send_raw(printer_name: str, payload: Union[bytes, str], *, doc_name: str = "Receipt") -> Dict[str, object]:
    """Send a raw byte stream (ESC/POS or RAW Windows data) to a named printer."""
    if not printer_name:
        return _unsupported("No printer selected")
    if platform.system() != "Windows":
        return _unsupported("Raw printing is only available on Windows")
    win32print = _try_import_win32print()
    if win32print is None:
        return _unsupported("pywin32 not available; raw printing disabled")
    if isinstance(payload, str):
        payload = payload.encode("cp437", errors="replace")
    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception as exc:
        log.warning("OpenPrinter failed for %s: %s", printer_name, exc)
        return _unsupported(f"Cannot open printer: {exc}")
    try:
        try:
            win32print.StartDocPrinter(handle, 1, (doc_name, None, "RAW"))
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, payload)
            win32print.EndPagePrinter(handle)
            win32print.EndDocPrinter(handle)
        except Exception as exc:
            log.exception("Raw print failed for %s", printer_name)
            return _unsupported(f"Print failed: {exc}")
    finally:
        try:
            win32print.ClosePrinter(handle)
        except Exception:
            pass
    log.info("Raw print dispatched printer=%s bytes=%d doc=%s", printer_name, len(payload), doc_name)
    return {"ok": True, "msg": f"Sent {len(payload)} bytes to {printer_name}"}
