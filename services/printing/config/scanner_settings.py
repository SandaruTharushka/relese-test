"""Scanner / barcode input settings repository."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.printing.domain.constants import SCANNER_DEFAULTS, SCANNER_KEYS


@dataclass
class ScannerSettingsRepository:
    """Loads and saves barcode scanner input configuration.

    Strictly limited to scanner input behaviour — does NOT own
    label printer config (that belongs to LabelPrinterSettingsRepository).
    """

    store_settings: Any

    def load(self) -> dict[str, str]:
        return {
            k: str(self.store_settings.get(k, SCANNER_DEFAULTS.get(k, '')) or SCANNER_DEFAULTS.get(k, ''))
            for k in SCANNER_KEYS
        }

    def save(self, payload: dict[str, Any]) -> int:
        clean = {k: v for k, v in payload.items() if k in SCANNER_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)

    def cleanup_legacy_overlap(self) -> None:
        """Remove legacy shadow keys that duplicated label config inside scanner namespace."""
        legacy_shadow = 'label_printer_selection'
        if self.store_settings.get(legacy_shadow, ''):
            self.store_settings.set(legacy_shadow, '')
