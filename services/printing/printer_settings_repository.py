from __future__ import annotations

from dataclasses import dataclass

from services.printing.settings_service import LABEL_KEYS, RECEIPT_KEYS


@dataclass
class PrinterSettingsRepository:
    store_settings: object

    def load_receipt(self) -> dict[str, str]:
        return {k: str(self.store_settings.get(k, '') or '') for k in RECEIPT_KEYS}

    def load_label(self) -> dict[str, str]:
        return {k: str(self.store_settings.get(k, '') or '') for k in LABEL_KEYS}

    def save_receipt(self, payload: dict[str, str]) -> int:
        clean = {k: v for k, v in payload.items() if k in RECEIPT_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)

    def save_label(self, payload: dict[str, str]) -> int:
        clean = {k: v for k, v in payload.items() if k in LABEL_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)
