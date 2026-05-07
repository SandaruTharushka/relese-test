"""Thin model facade for printer + receipt + company settings.

These three "models" are namespaced views over the existing `StoreSettings`
key-value table — no schema migration is required, existing customer / sale /
job / return data is never touched.

    CompanyProfile         keys: company_*    (also reads legacy store_* keys)
    PrinterSettings        keys: printer_*
    ReceiptLayoutSettings  keys: rcpt_layout_*

Each class exposes:
    .load()           -> dict of settings (includes defaults for missing keys)
    .save(data: dict) -> persists allowed keys, returns saved count
    .reset()          -> deletes its keys (back to defaults)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable

from models import StoreSettings, db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _coerce_str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# CompanyProfile
# ---------------------------------------------------------------------------

class CompanyProfile:
    """Company intro data shown on every receipt."""

    PREFIX = "company_"

    DEFAULTS: Dict[str, str] = {
        "company_name": "",
        "company_slogan": "",
        "company_address": "",
        "company_phone": "",
        "company_email": "",
        "company_website": "",
        "company_logo_path": "",
        "company_registration_number": "",
        "company_tax_number": "",
        "company_footer_text": "Thank you for business, Visit us again!",
        "company_receipt_note": "",
    }

    LEGACY_FALLBACKS: Dict[str, str] = {
        "company_name": "store_name",
        "company_address": "store_address",
        "company_phone": "store_phone",
        "company_email": "store_email",
        "company_registration_number": "store_reg",
        "company_footer_text": "receipt_footer",
    }

    @classmethod
    def load(cls) -> Dict[str, Any]:
        all_settings = StoreSettings.as_dict()
        out: Dict[str, Any] = {}
        for key, default in cls.DEFAULTS.items():
            value = all_settings.get(key, "")
            if not value:
                legacy_key = cls.LEGACY_FALLBACKS.get(key)
                if legacy_key:
                    value = all_settings.get(legacy_key, "") or default
                else:
                    value = default
            out[key] = value
        return out

    @classmethod
    def save(cls, data: Dict[str, Any]) -> int:
        clean: Dict[str, str] = {}
        for key in cls.DEFAULTS.keys():
            if key in data:
                clean[key] = _coerce_str(data[key], "").strip()
        if not clean:
            return 0
        StoreSettings.set_many(clean)
        db.session.commit()
        return len(clean)


# ---------------------------------------------------------------------------
# PrinterSettings
# ---------------------------------------------------------------------------

class PrinterSettings:
    """Persistent printer configuration."""

    PREFIX = "printer_"

    PAPER_WIDTHS = ("58mm", "80mm")
    CONNECTION_TYPES = ("usb", "network", "windows")
    PRINT_MODES = ("html", "escpos", "windows_raw")

    DEFAULTS: Dict[str, str] = {
        "printer_receipt_name": "",
        "printer_label_name": "",
        "printer_default_paper_width": "80mm",
        "printer_connection_type": "windows",
        "printer_print_mode": "html",
        "printer_auto_detect_enabled": "1",
        "printer_is_enabled": "1",
    }

    @classmethod
    def load(cls) -> Dict[str, Any]:
        raw = StoreSettings.as_dict(prefix=cls.PREFIX)
        out: Dict[str, Any] = {}
        for key, default in cls.DEFAULTS.items():
            out[key] = raw.get(key, default) or default
        out["printer_auto_detect_enabled"] = _coerce_bool(
            out["printer_auto_detect_enabled"], True
        )
        out["printer_is_enabled"] = _coerce_bool(out["printer_is_enabled"], True)
        return out

    @classmethod
    def save(cls, data: Dict[str, Any]) -> int:
        clean: Dict[str, str] = {}
        for key in cls.DEFAULTS.keys():
            if key not in data:
                continue
            value = data[key]
            if key == "printer_default_paper_width":
                value = str(value or "80mm").strip().lower()
                if value not in cls.PAPER_WIDTHS:
                    raise ValueError(
                        f"Invalid paper width: {value!r}. "
                        f"Use one of {cls.PAPER_WIDTHS}."
                    )
            elif key == "printer_connection_type":
                value = str(value or "windows").strip().lower()
                if value not in cls.CONNECTION_TYPES:
                    raise ValueError(
                        f"Invalid connection type: {value!r}. "
                        f"Use one of {cls.CONNECTION_TYPES}."
                    )
            elif key == "printer_print_mode":
                value = str(value or "html").strip().lower()
                if value not in cls.PRINT_MODES:
                    raise ValueError(
                        f"Invalid print mode: {value!r}. "
                        f"Use one of {cls.PRINT_MODES}."
                    )
            elif key in ("printer_auto_detect_enabled", "printer_is_enabled"):
                value = "1" if _coerce_bool(value, True) else "0"
            else:
                value = _coerce_str(value, "").strip()
            clean[key] = value
        if not clean:
            return 0
        StoreSettings.set_many(clean)
        db.session.commit()
        return len(clean)


# ---------------------------------------------------------------------------
# ReceiptLayoutSettings
# ---------------------------------------------------------------------------

class ReceiptLayoutSettings:
    """Layout knobs that drive the central receipt engine."""

    PREFIX = "rcpt_layout_"

    PAPER_WIDTHS = ("58mm", "80mm")
    ALIGNMENTS = ("left", "center", "right")
    DIVIDER_STYLES = ("dashed", "solid", "dotted", "double")
    ROW_STYLES = ("standard", "compact")
    TEMPLATE_STYLES = ("modern_garage", "compact", "classic")

    DEFAULTS: Dict[str, str] = {
        "rcpt_layout_name": "Default",
        "rcpt_layout_paper_width": "80mm",
        "rcpt_layout_font_size": "12",
        "rcpt_layout_font_family": "ui-monospace, 'Courier New', monospace",
        "rcpt_layout_show_logo": "1",
        "rcpt_layout_show_company_name": "1",
        "rcpt_layout_show_address": "1",
        "rcpt_layout_show_phone": "1",
        "rcpt_layout_show_email": "0",
        "rcpt_layout_show_customer": "1",
        "rcpt_layout_show_vehicle": "1",
        "rcpt_layout_show_cashier": "1",
        "rcpt_layout_show_barcode": "1",
        "rcpt_layout_show_footer": "1",
        "rcpt_layout_show_tax_info": "1",
        "rcpt_layout_show_payment_summary": "1",
        "rcpt_layout_header_alignment": "center",
        "rcpt_layout_footer_alignment": "center",
        "rcpt_layout_item_row_style": "standard",
        "rcpt_layout_divider_style": "dashed",
        "rcpt_layout_template_style": "modern_garage",
    }

    BOOL_KEYS = {
        "rcpt_layout_show_logo",
        "rcpt_layout_show_company_name",
        "rcpt_layout_show_address",
        "rcpt_layout_show_phone",
        "rcpt_layout_show_email",
        "rcpt_layout_show_customer",
        "rcpt_layout_show_vehicle",
        "rcpt_layout_show_cashier",
        "rcpt_layout_show_barcode",
        "rcpt_layout_show_footer",
        "rcpt_layout_show_tax_info",
        "rcpt_layout_show_payment_summary",
    }

    ENUM_KEYS = {
        "rcpt_layout_paper_width": PAPER_WIDTHS,
        "rcpt_layout_header_alignment": ALIGNMENTS,
        "rcpt_layout_footer_alignment": ALIGNMENTS,
        "rcpt_layout_item_row_style": ROW_STYLES,
        "rcpt_layout_divider_style": DIVIDER_STYLES,
        "rcpt_layout_template_style": TEMPLATE_STYLES,
    }

    @classmethod
    def load(cls) -> Dict[str, Any]:
        raw = StoreSettings.as_dict(prefix=cls.PREFIX)
        out: Dict[str, Any] = {}
        for key, default in cls.DEFAULTS.items():
            value = raw.get(key, default)
            if value in (None, ""):
                value = default
            if key in cls.BOOL_KEYS:
                out[key] = _coerce_bool(value, default == "1")
            elif key == "rcpt_layout_font_size":
                out[key] = _coerce_int(value, 12)
            else:
                out[key] = _coerce_str(value, default)
        return out

    @classmethod
    def save(cls, data: Dict[str, Any]) -> int:
        clean: Dict[str, str] = {}
        for key, default in cls.DEFAULTS.items():
            if key not in data:
                continue
            value = data[key]
            if key in cls.BOOL_KEYS:
                clean[key] = "1" if _coerce_bool(value, default == "1") else "0"
            elif key in cls.ENUM_KEYS:
                normalized = _coerce_str(value, default).strip().lower()
                allowed = cls.ENUM_KEYS[key]
                if normalized not in allowed:
                    raise ValueError(
                        f"Invalid value for {key}: {value!r}. "
                        f"Use one of {allowed}."
                    )
                clean[key] = normalized
            elif key == "rcpt_layout_font_size":
                size = _coerce_int(value, 12)
                if not 8 <= size <= 24:
                    raise ValueError("Font size must be between 8 and 24.")
                clean[key] = str(size)
            else:
                clean[key] = _coerce_str(value, default).strip()
        if not clean:
            return 0
        StoreSettings.set_many(clean)
        db.session.commit()
        return len(clean)

    @classmethod
    def reset(cls) -> int:
        keys = list(cls.DEFAULTS.keys())
        rows = StoreSettings.query.filter(StoreSettings.key.in_(keys)).all()
        for row in rows:
            db.session.delete(row)
        db.session.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# convenience
# ---------------------------------------------------------------------------

def all_settings_snapshot() -> Dict[str, Any]:
    """Return everything (used for receipt preview/render)."""
    return {
        "company": CompanyProfile.load(),
        "printer": PrinterSettings.load(),
        "layout": ReceiptLayoutSettings.load(),
    }
