from __future__ import annotations

from dataclasses import dataclass

from services.printing.settings_service import SCANNER_DEFAULTS, SCANNER_KEYS


@dataclass
class BarcodeSettingsRepository:
    store_settings: object

    def load(self) -> dict[str, str]:
        return {k: str(self.store_settings.get(k, SCANNER_DEFAULTS.get(k, '')) or SCANNER_DEFAULTS.get(k, '')) for k in SCANNER_KEYS}

    def save(self, payload: dict[str, str]) -> int:
        clean = {k: v for k, v in payload.items() if k in SCANNER_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)
