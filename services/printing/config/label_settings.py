"""Label printer settings repository — canonical source for label printer config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.printing.domain.constants import LABEL_PRINTER_DEFAULTS, LABEL_PRINTER_KEYS


@dataclass
class LabelPrinterSettingsRepository:
    """Loads and saves label printer hardware + layout settings."""

    store_settings: Any

    def load(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for k in LABEL_PRINTER_KEYS:
            val = self.store_settings.get(k, None)
            result[k] = str(val) if val is not None else str(LABEL_PRINTER_DEFAULTS.get(k, ''))
        return result

    def save(self, payload: dict[str, Any]) -> int:
        clean = {k: v for k, v in payload.items() if k in LABEL_PRINTER_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)

    def as_bool(self, key: str) -> bool:
        val = self.store_settings.get(key, None)
        raw = val if val is not None else LABEL_PRINTER_DEFAULTS.get(key, 'false')
        return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}

    def as_float(self, key: str) -> float:
        val = self.store_settings.get(key, None)
        raw = val if val is not None else LABEL_PRINTER_DEFAULTS.get(key, '0')
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(LABEL_PRINTER_DEFAULTS.get(key, '0') or '0')

    def as_int(self, key: str) -> int:
        val = self.store_settings.get(key, None)
        raw = val if val is not None else LABEL_PRINTER_DEFAULTS.get(key, '0')
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(LABEL_PRINTER_DEFAULTS.get(key, '0') or '0')
