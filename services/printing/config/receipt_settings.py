"""Receipt printer settings repository — canonical source for receipt printer config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.printing.domain.constants import (
    RECEIPT_LAYOUT_DEFAULTS,
    RECEIPT_LAYOUT_KEYS,
    RECEIPT_PRINTER_DEFAULTS,
    RECEIPT_PRINTER_KEYS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)


@dataclass
class ReceiptPrinterSettingsRepository:
    """Loads and saves receipt printer hardware settings."""

    store_settings: Any

    def load(self) -> dict[str, str]:
        return {
            k: str(self.store_settings.get(k, RECEIPT_PRINTER_DEFAULTS.get(k, '')) or RECEIPT_PRINTER_DEFAULTS.get(k, ''))
            for k in RECEIPT_PRINTER_KEYS
        }

    def save(self, payload: dict[str, Any]) -> int:
        clean = {k: v for k, v in payload.items() if k in RECEIPT_PRINTER_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)


@dataclass
class ReceiptLayoutSettingsRepository:
    """Loads and saves sales receipt layout/display settings."""

    store_settings: Any

    def load(self) -> dict[str, str]:
        return {
            k: str(self.store_settings.get(k, RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
            for k in RECEIPT_LAYOUT_KEYS
        }

    def save(self, payload: dict[str, Any]) -> int:
        clean = {k: v for k, v in payload.items() if k in RECEIPT_LAYOUT_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)

    def paper_width_chars(self) -> int:
        """Derive canonical paper width (characters per line) from rcpt_cpl."""
        raw = self.store_settings.get('rcpt_cpl', RECEIPT_LAYOUT_DEFAULTS['rcpt_cpl'])
        try:
            cpl = int(raw)
        except (TypeError, ValueError):
            cpl = 48
        return max(24, min(cpl, 96))


@dataclass
class ServiceReceiptLayoutSettingsRepository:
    """Loads and saves service/job receipt layout settings (NEW configurable domain)."""

    store_settings: Any

    def load(self) -> dict[str, str]:
        return {
            k: str(self.store_settings.get(k, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
            for k in SERVICE_RECEIPT_LAYOUT_KEYS
        }

    def save(self, payload: dict[str, Any]) -> int:
        clean = {k: v for k, v in payload.items() if k in SERVICE_RECEIPT_LAYOUT_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)

    def paper_width_chars(self) -> int:
        """Derive paper width from svc_rcpt_cpl setting."""
        raw = self.store_settings.get('svc_rcpt_cpl', SERVICE_RECEIPT_LAYOUT_DEFAULTS['svc_rcpt_cpl'])
        try:
            cpl = int(raw)
        except (TypeError, ValueError):
            cpl = 48
        return max(24, min(cpl, 96))

    def as_bool(self, key: str) -> bool:
        """Load a single boolean-valued setting."""
        raw = self.store_settings.get(key, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(key, 'false'))
        return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}
