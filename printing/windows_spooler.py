"""Windows print dispatch.

Uses ``win32print`` directly — never ``shell=True`` and never ``subprocess``
calls into ``rundll32`` / ``mshta`` / ``print.exe``. On non-Windows hosts the
functions return a structured "unsupported" error so the service layer can
surface a clean message.
"""
from __future__ import annotations

import logging
import platform
import traceback
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
        payload = payload.encode("utf-8", errors="replace")

    last_error = None
    last_traceback = ""
    for attempt in range(1, 4):
        handle = None
        try:
            handle = win32print.OpenPrinter(printer_name)
            job_id = win32print.StartDocPrinter(handle, 1, (doc_name, None, "RAW"))
            win32print.StartPagePrinter(handle)
            bytes_written = win32print.WritePrinter(handle, payload)
            win32print.EndPagePrinter(handle)
            win32print.EndDocPrinter(handle)
            log.info("Raw print dispatched printer=%s bytes=%d written=%d doc=%s attempt=%d", printer_name, len(payload), bytes_written, doc_name, attempt)
            return {"ok": True, "msg": f"Sent {bytes_written} bytes to {printer_name}", "job_id": job_id, "bytes_written": bytes_written, "attempt": attempt}
        except Exception as exc:
            last_error = exc
            last_traceback = traceback.format_exc()
            log.exception("Raw print failed for %s on attempt=%d", printer_name, attempt)
        finally:
            if handle is not None:
                try:
                    win32print.ClosePrinter(handle)
                except Exception:
                    pass
    return {
        "ok": False,
        "msg": f"Print failed after retries: {last_error}",
        "exception": repr(last_error),
        "traceback": last_traceback,
    }
