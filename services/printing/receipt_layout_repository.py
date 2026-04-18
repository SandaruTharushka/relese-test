from __future__ import annotations

from dataclasses import dataclass

from services.printing.settings_service import RECEIPT_LAYOUT_DEFAULTS, RECEIPT_LAYOUT_KEYS


@dataclass
class ReceiptLayoutRepository:
    store_settings: object

    def load(self) -> dict[str, str]:
        return {
            k: str(self.store_settings.get(k, RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
            for k in RECEIPT_LAYOUT_KEYS
        }

    def save(self, payload: dict[str, str]) -> int:
        clean = {k: v for k, v in payload.items() if k in RECEIPT_LAYOUT_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)
