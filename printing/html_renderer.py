"""HTML render helpers (Jinja delegates to printing/receipt.html)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from printing.receipt_engine import render_receipt_html


def render_html(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
    *,
    auto_print: bool = False,
) -> str:
    return render_receipt_html(
        receipt_type,
        context,
        layout_settings,
        company_profile,
        auto_print=auto_print,
    )
