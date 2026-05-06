"""Backward-compat re-exports — all canonical types live in domain_models.py."""
from __future__ import annotations

from services.printing.domain_models import (  # noqa: F401
    LabelPrintOutcome,
    PrinterStatus,
    ReceiptPrintOutcome,
    ResolvedPrinter,
)

__all__ = ['ResolvedPrinter', 'PrinterStatus', 'ReceiptPrintOutcome', 'LabelPrintOutcome']
