from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedPrinter:
    """Normalized resolved printer descriptor used across receipt/label flows."""

    mode: str
    name: str
    connected: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'mode': self.mode,
            'name': self.name,
            'connected': self.connected,
            'reason': self.reason,
        }


@dataclass(frozen=True)
class PrinterStatus:
    """Computed status for a concrete printer target."""

    printer_name: str
    connected: bool
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'printer_name': self.printer_name,
            'connected': self.connected,
            'status': self.status,
            'reason': self.reason,
        }
