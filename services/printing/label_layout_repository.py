from __future__ import annotations

from dataclasses import dataclass

from services.printing.settings_service import LABEL_DEFAULTS, LABEL_KEYS


@dataclass
class LabelLayoutRepository:
    store_settings: object

    def load(self) -> dict[str, str]:
        return {k: str(self.store_settings.get(k, LABEL_DEFAULTS.get(k, '')) or LABEL_DEFAULTS.get(k, '')) for k in LABEL_KEYS}

    def save(self, payload: dict[str, str]) -> int:
        clean = {k: v for k, v in payload.items() if k in LABEL_KEYS}
        self.store_settings.set_many(clean)
        return len(clean)
