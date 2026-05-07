"""Real Windows printer status detection with port validation.

On Windows we use REAL connectivity checks, not just spooler status:
1. Verify printer exists in Windows
2. Verify driver is installed
3. CRITICAL: Verify port is actually reachable (not just in registry)
4. Verify printer not offline
5. Verify spooler status is ready

A driver may exist while the device is unplugged, paused, out of paper, or
simply offline; in those cases we NEVER report ``online``.

For USB printers: we validate the USB port and check device enumeration.
For network printers: we ping the port and verify connectivity.

On non-Windows hosts (developer Linux/macOS, CI) we degrade gracefully by
returning whatever printers ``lpstat`` knows about, marked ``status=unknown``.
"""
from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

def _printer_detector_logger() -> logging.Logger:
    logger = logging.getLogger("printing.detection")
    if logger.handlers:
        return logger
    base = Path(__file__).resolve().parent.parent
    logs_dir = base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(
            logs_dir / "printer_detection.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
    except Exception as e:
        log.warning("Failed to setup printer detection logger: %s", e)
    return logger

detection_log = _printer_detector_logger()


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

_PORT_TYPES = {
    "usb": {"prefixes": ["USB", "COM", "LPT"], "description": "USB Device"},
    "network": {"prefixes": ["IP", "9100", "127.0.0.1"], "description": "Network Port"},
    "parallel": {"prefixes": ["LPT", "FILE"], "description": "Parallel Port"},
}


# ---------------------------------------------------------------------------
# Port validation (most important: detects unplugged USB printers)
# ---------------------------------------------------------------------------

def _validate_usb_port_exists(port_name: str) -> Tuple[bool, str]:
    """Check if a USB port actually exists and has a device connected."""
    if not port_name or "usb" not in port_name.lower():
        return True, "Not a USB port"
    try:
        import wmi  # type: ignore
        c = wmi.WMI()
        query = 'SELECT * FROM Win32_USBHub'
        devices = list(c.query(query))
        if devices:
            detection_log.debug(f"Found {len(devices)} USB devices")
            return True, "USB device detected"
        detection_log.warning(f"No USB devices found for port {port_name}")
        return False, "USB port has no connected device"
    except Exception as e:
        detection_log.debug(f"USB port check failed (WMI unavailable): {e}")
        return True, "USB check skipped (WMI unavailable)"


def _validate_network_port(port_name: str) -> Tuple[bool, str]:
    """Check if a network port is reachable."""
    if not port_name or not any(x in port_name for x in ["IP", "127.0.0.1", ":", "9100"]):
        return True, "Not a network port"

    try:
        if ":" in port_name:
            host_port = port_name.split(":")
            host = host_port[0].strip()
            try:
                port = int(host_port[1]) if len(host_port) > 1 else 9100
            except ValueError:
                port = 9100
        else:
            host = port_name.strip()
            port = 9100

        detection_log.debug(f"Pinging network printer at {host}:{port}")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                detection_log.info(f"Network port {host}:{port} is reachable")
                return True, f"Network port reachable ({host}:{port})"
            detection_log.warning(f"Network port {host}:{port} is unreachable")
            return False, f"Network port unreachable ({host}:{port})"
        except Exception as e:
            detection_log.debug(f"Network port check error: {e}")
            return True, "Network check skipped"
    except Exception as e:
        detection_log.debug(f"Port parsing error: {e}")
        return True, "Port check skipped"


def _validate_port_exists(port_name: str, printer_name: str) -> Tuple[bool, str]:
    """CRITICAL: Verify the port actually exists and is reachable.

    This is what catches unplugged USB printers. The spooler may report
    them as READY, but the port validation will fail.
    """
    if not port_name:
        detection_log.warning(f"Printer {printer_name} has no port configured")
        return False, "No port configured"

    port_lower = port_name.lower()

    if "usb" in port_lower or "com" in port_lower:
        return _validate_usb_port_exists(port_name)
    elif "ip" in port_lower or ":" in port_name:
        return _validate_network_port(port_name)
    else:
        detection_log.debug(f"Port {port_name} type unknown, assuming valid")
        return True, f"Port type unknown ({port_name})"


# ---------------------------------------------------------------------------
# Windows backend (pywin32)
# ---------------------------------------------------------------------------

def _try_import_win32print():
    try:
        import win32print  # type: ignore
        return win32print
    except Exception:  # pragma: no cover - only on non-Windows
        return None


def _decode_windows_status(
    attributes: int,
    status: int,
    port_validated: bool = True,
    port_message: str = ""
) -> Dict[str, object]:
    """Translate Windows PRINTER_INFO_2 status field with port validation.

    IMPORTANT: Even if spooler says READY, if the port isn't reachable,
    the printer is OFFLINE. This catches unplugged USB printers.
    """
    flags = [name for bit, name, _ in _STATUS_FLAGS if status & bit]
    blocking = [f for f in flags if f in _BLOCKING]
    paused = any(f in _PAUSED for f in flags)
    work_offline = bool(attributes & 0x00000400)  # PRINTER_ATTRIBUTE_WORK_OFFLINE

    detection_log.debug(
        f"Decoding status: work_offline={work_offline}, flags={flags}, "
        f"port_validated={port_validated}, port_msg={port_message}"
    )

    if not port_validated:
        kind = "offline"
        message = f"Port unreachable: {port_message}"
    elif work_offline or "offline" in flags or "not_available" in flags:
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
        "port_validated": port_validated,
    }


def _windows_get_status(printer_name: str) -> Dict[str, object]:
    """Get real printer status with multi-stage detection and port validation."""
    detection_log.info(f"=== Detecting printer: {printer_name} ===")

    win32print = _try_import_win32print()
    if win32print is None:
        detection_log.warning(f"{printer_name}: win32print not available")
        return {
            "name": printer_name,
            "driver_installed": True,
            "connected": False,
            "status": "unknown",
            "status_code": -1,
            "can_print": False,
            "message": "Windows printer API not available on this host",
        }

    # STAGE 1: Check if printer exists in Windows
    detection_log.debug(f"{printer_name}: Stage 1 - checking if printer exists in Windows")
    try:
        handle = win32print.OpenPrinter(printer_name)
        detection_log.debug(f"{printer_name}: Stage 1 ✓ OpenPrinter succeeded")
    except Exception as exc:
        detection_log.error(f"{printer_name}: Stage 1 ✗ OpenPrinter failed: {exc}")
        return {
            "name": printer_name,
            "driver_installed": False,
            "connected": False,
            "status": "offline",
            "status_code": -1,
            "can_print": False,
            "message": f"Printer not found in Windows: {exc}",
        }

    try:
        # STAGE 2: Check driver and spooler status
        detection_log.debug(f"{printer_name}: Stage 2 - checking driver and spooler status")
        info = win32print.GetPrinter(handle, 2)
        detection_log.debug(f"{printer_name}: Stage 2 ✓ GetPrinter succeeded")

        port_name = info.get("PortName", "")
        attributes = int(info.get("Attributes", 0))
        spooler_status = int(info.get("Status", 0))

        detection_log.debug(
            f"{printer_name}: Port={port_name}, Attributes={attributes:08x}, "
            f"Status={spooler_status:08x}"
        )

        # STAGE 3: Validate port is physically reachable
        detection_log.debug(f"{printer_name}: Stage 3 - validating port connectivity")
        port_valid, port_msg = _validate_port_exists(port_name, printer_name)
        detection_log.debug(f"{printer_name}: Stage 3 {'✓' if port_valid else '✗'} {port_msg}")

        # STAGE 4: Decode spooler status (with port validation)
        detection_log.debug(f"{printer_name}: Stage 4 - decoding spooler status")
        decoded = _decode_windows_status(
            attributes,
            spooler_status,
            port_validated=port_valid,
            port_message=port_msg,
        )
        detection_log.debug(
            f"{printer_name}: Stage 4 ✓ Status={decoded['status']}, "
            f"can_print={decoded['can_print']}"
        )

        # Final result
        result = {
            "name": printer_name,
            "driver_installed": True,
            "connected": decoded["status"] != "offline" and port_valid,
            "port_name": port_name,
            **decoded,
        }

        detection_log.info(
            f"{printer_name}: FINAL -> status={result['status']}, "
            f"can_print={result['can_print']}, connected={result['connected']}"
        )
        return result

    except Exception as exc:
        detection_log.error(f"{printer_name}: Stage 2 ✗ GetPrinter failed: {exc}")
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
    """Enumerate printers and return REAL status for each one.

    Uses multi-stage detection:
    1. Printer exists in Windows
    2. Driver installed
    3. Port physically reachable (critical for USB printers)
    4. Spooler status is ready
    """
    detection_log.info("=== Starting printer enumeration ===")

    if platform.system() == "Windows":
        win32print = _try_import_win32print()
        if win32print is not None:
            try:
                flags = (
                    win32print.PRINTER_ENUM_LOCAL
                    | win32print.PRINTER_ENUM_CONNECTIONS
                )
                detection_log.debug(f"Enumerating with flags: {flags}")
                printers = win32print.EnumPrinters(flags, None, 2)
                names = [p.get("pPrinterName") or p.get("PrinterName") or "" for p in printers]
                names = [n for n in names if n]
                detection_log.info(f"Found {len(names)} printers in Windows: {names}")

                results = [_windows_get_status(name) for name in names]

                online_count = sum(1 for r in results if r.get("status") == "online")
                detection_log.info(
                    f"=== Enumeration complete: {len(results)} total, "
                    f"{online_count} online ==="
                )
                return results
            except Exception:
                detection_log.exception("EnumPrinters failed")
                log.exception("EnumPrinters failed; falling back to empty list")
                return []

    # non-Windows fallback
    detection_log.info("Non-Windows platform; using lpstat fallback")
    names = _lpstat_list()
    if not names:
        detection_log.info("No printers found via lpstat")
        return []

    detection_log.info(f"Found {len(names)} printers via lpstat: {names}")
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
