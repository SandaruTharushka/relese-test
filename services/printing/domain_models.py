"""Canonical domain model dataclasses for the printing subsystem.

All four frozen dataclasses live here and are the single source of truth.
Other modules import from here; backward-compat re-exports are in models.py.
"""
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


@dataclass(frozen=True)
class ReceiptPrintOutcome:
    """Result of a receipt print execution attempt."""

    ok: bool
    target: str
    copies: int
    resolved: ResolvedPrinter
    error_code: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'ok': self.ok,
            'target': self.target,
            'copies': self.copies,
            'resolved': self.resolved.to_dict(),
            'error_code': self.error_code,
            'error': self.error,
        }


@dataclass(frozen=True)
class LabelPrintOutcome:
    """Result of a label print execution attempt."""

    ok: bool
    code: str
    message: str
    resolved_size_mm: dict[str, float] | None = None
