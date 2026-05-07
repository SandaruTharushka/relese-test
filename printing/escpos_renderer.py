"""ESC/POS bytes renderer (delegates to receipt_engine)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from printing.receipt_engine import (
    render_receipt_escpos,
    render_receipt_text,
)


def render_escpos(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
) -> bytes:
    return render_receipt_escpos(
        receipt_type, context, layout_settings, company_profile
    )


def render_text(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
) -> str:
    return render_receipt_text(
        receipt_type, context, layout_settings, company_profile
    )
