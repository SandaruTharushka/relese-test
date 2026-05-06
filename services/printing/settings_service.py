"""Print settings service — backward-compat facade.

All KEYS and DEFAULTS now sourced from services.printing.domain.constants
which is the single source of truth.

The PrintSettingsService class is retained for backward compatibility with
printer_routes.py, domain_service.py, and existing callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Single source of truth: all keys and defaults ────────────────────────────
from services.printing.domain.constants import (
    ALL_DEFAULTS,
    LABEL_PRINTER_DEFAULTS as LABEL_DEFAULTS,
    LABEL_PRINTER_KEYS as LABEL_KEYS,
    RECEIPT_LAYOUT_DEFAULTS,
    RECEIPT_LAYOUT_KEYS,
    RECEIPT_PRINTER_DEFAULTS as RECEIPT_DEFAULTS,
    RECEIPT_PRINTER_KEYS as RECEIPT_KEYS,
    SCANNER_DEFAULTS,
    SCANNER_KEYS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)

# ── Backward-compat re-exports (old imports still work) ───────────────────────
__all__ = [
    'RECEIPT_KEYS', 'RECEIPT_DEFAULTS',
    'RECEIPT_LAYOUT_KEYS', 'RECEIPT_LAYOUT_DEFAULTS',
    'SERVICE_RECEIPT_LAYOUT_KEYS', 'SERVICE_RECEIPT_LAYOUT_DEFAULTS',
    'LABEL_KEYS', 'LABEL_DEFAULTS',
    'SCANNER_KEYS', 'SCANNER_DEFAULTS',
    'ALL_DEFAULTS',
    'PrintSettingsService',
]


@dataclass
class PrintSettingsService:
    """Backward-compatible settings facade used by domain_service.py and printer_routes.py."""

    store_settings: Any

    def load(self, keys: list[str]) -> dict[str, str]:
        """Generic loader — None-check pattern, never `val or default`."""
        result: dict[str, str] = {}
        for k in keys:
            val = self.store_settings.get(k, None)
            result[k] = str(val) if val is not None else str(ALL_DEFAULTS.get(k, ''))
        return result

    def save(self, payload: dict[str, Any]) -> int:
        self.store_settings.set_many(payload)
        return len(payload)

    def load_receipt(self) -> dict[str, str]:
        return self.load(RECEIPT_KEYS)

    def load_label(self) -> dict[str, str]:
        return self.load(LABEL_KEYS)

    def load_scanner(self) -> dict[str, str]:
        return self.load(SCANNER_KEYS)

    def load_receipt_layout(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k in RECEIPT_LAYOUT_KEYS:
            val = self.store_settings.get(k, None)
            result[k] = str(val) if val is not None else str(RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
        return result

    def load_service_receipt_layout(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k in SERVICE_RECEIPT_LAYOUT_KEYS:
            val = self.store_settings.get(k, None)
            result[k] = str(val) if val is not None else str(SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
        return result

    def cleanup_legacy_overlap(self) -> None:
        """Remove legacy label_printer_selection key that was incorrectly owned by scanner settings."""
        if self.store_settings.get('label_printer_selection', ''):
            self.store_settings.set('label_printer_selection', '')
