"""Real Windows printer status detection.

On Windows we use the pywin32 ``win32print`` API to query the actual spooler
status — driver presence is *not* enough. A driver may exist while the device
is unplugged, paused, out of paper, or simply offline; in those cases we never
report ``online``.

On non-Windows hosts (developer Linux/macOS, CI) we degrade gracefully by
returning whatever printers ``lpstat`` knows about, marked ``status=unknown``.
That keeps the app usable for development without lying about readiness.
"""
from __future__ import annotations

import logging
import platform
import subprocess
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# Subset of Windows PRINTER_STATUS_* flags we care about. Mirrored here so we
# don't need pywin32 to be importable on dev machines.
_STATUS_FLAGS = [
    (0x00000001, "paused", "Printer is paused"),
    (0x00000002, "error", "Printer error"),
    (0x00000004, "deleting", "Printer is being deleted"),
    (0x00000008, "paper_jam", "Paper jam"),
    (0x00000010, "paper_out", "Paper out"),
    (0x00000020, "manual_feed", "Manual feed required"),
    (0x00000040, "paper_problem", "Paper problem"),
    (0x00000080, "offline", "Printer is offline"),
    (0x00000100, "io_active", "I/O active"),
    (0x00000200, "busy", "Busy"),
    (0x00000400, "printing", "Printing"),
    (0x00000800, "output_bin_full", "Output bin full"),
    (0x00001000, "not_available", "Printer not available"),
    (0x00002000, "waiting", "Waiting"),
    (0x00004000, "processing", "Processing"),
    (0x00008000, "initializing", "Initializing"),
    (0x00010000, "warming_up", "Warming up"),
    (0x00020000, "toner_low", "Toner low"),
    (0x00040000, "no_toner", "No toner"),
    (0x00080000, "page_punt", "Page punt"),
    (0x00100000, "user_intervention", "User intervention required"),
    (0x00200000, "out_of_memory", "Out of memory"),
    (0x00400000, "door_open", "Printer door open"),
    (0x00800000, "server_unknown", "Server unknown"),
    (0x01000000, "power_save", "Power save mode"),
]

_BLOCKING = {
    "offline",
    "error",
    "paper_out",
    "paper_jam",
    "paper_problem",
    "not_available",
    "out_of_memory",
    "door_open",
    "user_intervention",
    "no_toner",
    "deleting",
    "server_unknown",
}

_PAUSED = {"paused"}


# ---------------------------------------------------------------------------
# Windows backend (pywin32)
# ---------------------------------------------------------------------------

def _try_import_win32print():
    try:
        import win32print  # type: ignore
        return win32print
    except Exception:  # pragma: no cover - only on non-Windows
        return None


def _decode_windows_status(attributes: int, status: int) -> Dict[str, object]:
    """Translate a raw Windows ``PRINTER_INFO_2`` status field."""
    flags = [name for bit, name, _ in _STATUS_FLAGS if status & bit]
    blocking = [f for f in flags if f in _BLOCKING]
    paused = any(f in _PAUSED for f in flags)
    work_offline = bool(attributes & 0x00000400)  # PRINTER_ATTRIBUTE_WORK_OFFLINE

    if work_offline or "offline" in flags or "not_available" in flags:
        kind = "offline"
        message = "Printer driver exists but printer is offline or not connected"
    elif paused:
        kind = "paused"
        message = "Printer is paused"
    elif blocking:
        kind = "error"
        message = next(msg for bit, name, msg in _STATUS_FLAGS if name == blocking[0])
    elif status == 0:
        kind = "online"
        message = "Ready"
    else:
        kind = "online"
        message = ", ".join(
            msg for bit, name, msg in _STATUS_FLAGS if status & bit
        ) or "Ready"
    can_print = kind == "online"
    return {
        "status": kind,
        "status_code": int(status),
        "flags": flags,
        "can_print": can_print,
        "message": message,
    }


def _windows_get_status(printer_name: str) -> Dict[str, object]:
    win32print = _try_import_win32print()
    if win32print is None:
        return {
            "name": printer_name,
            "driver_installed": True,
            "connected": False,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": "Windows printer API not available on this host",
        }
    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception as exc:
        log.info("OpenPrinter failed for %s: %s", printer_name, exc)
        return {
            "name": printer_name,
            "driver_installed": False,
            "connected": False,
            "status": "offline",
            "status_code": -1,
            "can_print": False,
            "message": f"Cannot open printer: {exc}",
        }
    try:
        info = win32print.GetPrinter(handle, 2)
    except Exception as exc:
        log.info("GetPrinter failed for %s: %s", printer_name, exc)
        return {
            "name": printer_name,
            "driver_installed": True,
            "connected": False,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": f"Cannot read printer status: {exc}",
        }
    finally:
        try:
            win32print.ClosePrinter(handle)
        except Exception:
            pass

    decoded = _decode_windows_status(
        int(info.get("Attributes", 0)),
        int(info.get("Status", 0)),
    )
    return {
        "name": printer_name,
        "driver_installed": True,
        "connected": decoded["status"] != "offline",
        **decoded,
    }


# ---------------------------------------------------------------------------
# CUPS / lpstat backend (Linux/macOS dev hosts)
# ---------------------------------------------------------------------------

def _lpstat_list() -> List[str]:
    try:
        out = subprocess.run(
            ["lpstat", "-p"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    names: List[str] = []
    for line in (out.stdout or "").splitlines():
        if line.startswith("printer "):
            parts = line.split()
            if len(parts) >= 2:
                names.append(parts[1])
    return names


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_printers_with_status() -> List[Dict[str, object]]:
    """Enumerate printers and return real status for each one."""
    if platform.system() == "Windows":
        win32print = _try_import_win32print()
        if win32print is not None:
            try:
                flags = (
                    win32print.PRINTER_ENUM_LOCAL
                    | win32print.PRINTER_ENUM_CONNECTIONS
                )
                printers = win32print.EnumPrinters(flags, None, 2)
                names = [p.get("pPrinterName") or p.get("PrinterName") or "" for p in printers]
                names = [n for n in names if n]
                return [_windows_get_status(name) for name in names]
            except Exception:
                log.exception("EnumPrinters failed; falling back to empty list")
                return []
    # non-Windows fallback
    names = _lpstat_list()
    if not names:
        return []
    return [
        {
            "name": name,
            "driver_installed": True,
            "connected": True,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": "Status detection unavailable (non-Windows host)",
        }
        for name in names
    ]


def get_printer_status(printer_name: Optional[str]) -> Dict[str, object]:
    """Return real status for a single named printer.

    Always returns the canonical dict shape so callers can render uniformly.
    """
    if not printer_name:
        return {
            "name": "",
            "driver_installed": False,
            "connected": False,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": "No printer selected",
        }
    if platform.system() == "Windows" and _try_import_win32print() is not None:
        return _windows_get_status(printer_name)
    # dev fallback
    available = _lpstat_list()
    if printer_name in available:
        return {
            "name": printer_name,
            "driver_installed": True,
            "connected": True,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": "Status detection unavailable (non-Windows host)",
        }
    return {
        "name": printer_name,
        "driver_installed": False,
        "connected": False,
        "status": "offline",
        "status_code": -1,
        "can_print": False,
        "message": f"Printer {printer_name!r} not found on this system",
    }


def is_printer_really_online(printer_name: Optional[str]) -> bool:
    return get_printer_status(printer_name).get("status") == "online"


def validate_printer_ready(printer_name: Optional[str]) -> Dict[str, object]:
    """Return the status dict; caller checks ``can_print``.

    Convenience function so service layer can do:
        info = validate_printer_ready(name)
        if not info["can_print"]: return error(info["message"])
    """
    return get_printer_status(printer_name)
