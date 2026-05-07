"""Canonical receipt service.

This is the single high-level facade used by the new
``/api/receipts/<billing|repair>/<id>/{preview,text,print}`` endpoints.
It wraps the existing :class:`services.printing.receipt.receipt_engine.ReceiptEngine`
so the same builder produces both:

* the thermal text dispatched to the printer;
* the modern HTML preview rendered in the browser.

That guarantees what the user sees in the preview is exactly what the
thermal printer will print.

All settings — store identity, layout toggles, paper width, footer text
— are loaded from :class:`models.StoreSettings` via ReceiptEngine.  No
hardcoded values live in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from services.receipt_renderer import (
    render_billing_preview_html,
    render_repair_preview_html,
)


ReceiptKind = Literal['billing', 'repair']


@dataclass
class ReceiptDocument:
    """Result of building a billing or repair receipt.

    Attributes:
        kind: ``'billing'`` or ``'repair'``.
        record_id: the underlying ``sale_id`` or ``job_id``.
        display_no: invoice number / job number for the user.
        receipt_text: tagged ESC/POS text dispatched to the thermal printer.
        receipt_plain: plain ASCII text (no control markers) for `text` API.
        html: modern HTML preview matching the printed content.
        layout: the layout dict loaded from StoreSettings (debug visibility).
        cpl: characters per line used by this receipt.
        paper_size: ``'58mm'`` / ``'80mm'`` / ``'dot_matrix'``.
        char_count: length of ``receipt_plain`` (for logs).
        debug: free-form diagnostic dict.
    """
    kind: ReceiptKind
    record_id: int
    display_no: str
    receipt_text: str
    receipt_plain: str
    html: str
    layout: dict[str, str]
    cpl: int
    paper_size: str
    char_count: int = 0
    debug: dict[str, Any] = field(default_factory=dict)


class ReceiptService:
    """Canonical builder for billing + repair receipts.

    Construct once per request with the live :class:`StoreSettings` so
    every build reflects the latest saved layout.  Internally delegates
    to :class:`ReceiptEngine`; we never re-implement business logic.
    """

    def __init__(self, store_settings: Any) -> None:
        self._ss = store_settings

    # ── Internal ──────────────────────────────────────────────────────────────

    def _engine(self):
        # Lazy import — keeps this module importable in tests that stub
        # out the printing subsystem.
        from services.printing.receipt.receipt_engine import ReceiptEngine
        return ReceiptEngine(self._ss)

    @staticmethod
    def _store_dict(ss: Any) -> dict[str, str]:
        return {
            'store_name':       str(ss.get('store_name', '') or ''),
            'store_branch':     str(ss.get('store_branch', '') or ''),
            'store_address':    str(ss.get('store_address', '') or ''),
            'store_phone':      str(ss.get('store_phone', '') or ''),
            'store_email':      str(ss.get('store_email', '') or ''),
            'store_tax_number': str(ss.get('store_tax_number', '') or ''),
        }

    # ── Billing ───────────────────────────────────────────────────────────────

    def build_billing(
        self,
        sale_id: int,
        *,
        actor_user_id: int | None = None,
        cashier_name: str = '',
    ) -> ReceiptDocument:
        from models import Sale  # local import — avoids circular import at startup

        engine = self._engine()
        result = engine.build_sales_receipt(
            sale_id=sale_id,
            actor_user_id=actor_user_id,
            cashier_name=cashier_name,
        )

        sale = Sale.query.filter_by(id=sale_id).first()
        invoice_no = (getattr(sale, 'invoice_number', '') or f'SALE-{sale_id}') if sale else f'SALE-{sale_id}'

        html = render_billing_preview_html(
            sale=sale,
            store=self._store_dict(self._ss),
            layout=result.layout,
            cashier_name=cashier_name,
            cpl=result.cpl,
            paper_size=result.paper_size,
        )

        return ReceiptDocument(
            kind='billing',
            record_id=sale_id,
            display_no=invoice_no,
            receipt_text=result.receipt_text,
            receipt_plain=result.receipt_plain,
            html=html,
            layout=result.layout,
            cpl=result.cpl,
            paper_size=result.paper_size,
            char_count=result.char_count,
            debug=result.debug,
        )

    # ── Repair ────────────────────────────────────────────────────────────────

    def build_repair(
        self,
        job_id: int,
        *,
        actor_user_id: int | None = None,
    ) -> ReceiptDocument:
        from models import RepairJob  # local import to avoid startup cycle

        engine = self._engine()
        result = engine.build_service_receipt(
            job_id=job_id,
            actor_user_id=actor_user_id,
        )

        job = RepairJob.query.filter_by(id=job_id).first()
        job_no = (getattr(job, 'job_number', '') or f'JOB-{job_id}') if job else f'JOB-{job_id}'

        html = render_repair_preview_html(
            job=job,
            store=self._store_dict(self._ss),
            layout=result.layout,
            cpl=result.cpl,
            paper_size=result.paper_size,
        )

        return ReceiptDocument(
            kind='repair',
            record_id=job_id,
            display_no=job_no,
            receipt_text=result.receipt_text,
            receipt_plain=result.receipt_plain,
            html=html,
            layout=result.layout,
            cpl=result.cpl,
            paper_size=result.paper_size,
            char_count=result.char_count,
            debug=result.debug,
        )
