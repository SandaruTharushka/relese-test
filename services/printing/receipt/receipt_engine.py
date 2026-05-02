"""Canonical receipt engine — single source of truth for all receipt builds.

All print routes and preview routes MUST use this engine.
No route is allowed to build receipt text manually.

Exposes:
  ReceiptEngine.build_sales_receipt(sale_id, actor_user_id, cashier_name)
  ReceiptEngine.build_service_receipt(job_id, actor_user_id)
  ReceiptEngine.build_return_receipt(sale_id, actor_user_id, cashier_name)
  ReceiptEngine.build_payment_receipt(job_id, actor_user_id, payment_id)
  ReceiptEngine.build_test_receipt(kind)
  ReceiptEngine.get_debug_layout()

All return ReceiptResult.

ReceiptResult fields:
  receipt_text  — tagged text with ESC/POS inline markers (for dispatch)
  receipt_plain — clean plain text (MODE A) stripped of all markers
  html_preview  — JSON-encoded preview metadata (layout + show flags)
  layout        — exact layout dict loaded from StoreSettings
  paper_size    — '58mm' / '80mm' / 'dot_matrix'
  cpl           — characters per line used
  char_count    — length of receipt_plain (for logging)
  debug         — diagnostic dict (no sensitive customer data)

Debug log format (emitted inside each build method):
  [RECEIPT] type=<type> id=<id> width=<cpl> layout=DB chars=<n>
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from services.printing.domain.constants import (
    RECEIPT_LAYOUT_DEFAULTS,
    RECEIPT_LAYOUT_KEYS,
    RECEIPT_PRINTER_DEFAULTS,
    RECEIPT_PRINTER_KEYS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)
from services.printing.receipt.sales_builder import SalesReceiptBuilder
from services.printing.receipt.service_builder import ServiceReceiptBuilder
from services.escpos_layout_engine import _strip_tags

_log = logging.getLogger(__name__)


@dataclass
class ReceiptResult:
    receipt_text: str        # tagged text with inline ESC/POS markers (for dispatch)
    html_preview: str        # JSON metadata for live preview
    layout: dict[str, str]
    paper_size: str
    cpl: int
    receipt_plain: str = ''  # plain-text version (MODE A) — no control chars
    char_count: int = 0      # len(receipt_plain) for logging
    debug: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.receipt_plain:
            self.receipt_plain = _strip_tags(self.receipt_text)
        if not self.char_count:
            self.char_count = len(self.receipt_plain)


class ReceiptEngine:
    """Single canonical engine for building all receipt types.

    Constructed once per request with the current StoreSettings so
    every print always reads the latest saved values — no caching.
    """

    def __init__(self, store_settings: Any) -> None:
        self._ss = store_settings

    # ── Settings loaders ──────────────────────────────────────────────────────

    def _load_store(self) -> dict[str, str]:
        return {
            'store_name':       str(self._ss.get('store_name', '') or ''),
            'store_branch':     str(self._ss.get('store_branch', '') or ''),
            'store_address':    str(self._ss.get('store_address', '') or ''),
            'store_phone':      str(self._ss.get('store_phone', '') or ''),
            'store_email':      str(self._ss.get('store_email', '') or ''),
            'store_tax_number': str(self._ss.get('store_tax_number', '') or ''),
        }

    def _load_sales_layout(self) -> dict[str, str]:
        return {
            k: str(
                self._ss.get(k, RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                or RECEIPT_LAYOUT_DEFAULTS.get(k, '')
            )
            for k in RECEIPT_LAYOUT_KEYS
        }

    def _load_service_layout(self) -> dict[str, str]:
        return {
            k: str(
                self._ss.get(k, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                or SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, '')
            )
            for k in SERVICE_RECEIPT_LAYOUT_KEYS
        }

    def _load_printer_settings(self) -> dict[str, str]:
        return {
            k: str(
                self._ss.get(k, RECEIPT_PRINTER_DEFAULTS.get(k, ''))
                or RECEIPT_PRINTER_DEFAULTS.get(k, '')
            )
            for k in RECEIPT_PRINTER_KEYS
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _cpl(layout: dict[str, str], key: str = 'rcpt_cpl') -> int:
        raw = layout.get(key, '48')
        try:
            return max(24, min(int(raw), 96))
        except (TypeError, ValueError):
            return 48

    @staticmethod
    def _paper_size(cpl: int) -> str:
        if cpl <= 32:
            return '58mm'
        if cpl <= 48:
            return '80mm'
        return 'dot_matrix'

    @staticmethod
    def _enabled_fields(layout: dict[str, str]) -> list[str]:
        return [
            k for k, v in layout.items()
            if k.startswith(('rcpt_show_', 'svc_rcpt_show_'))
            and str(v).strip().lower() in {'1', 'true', 'yes', 'on'}
        ]

    # ── Public API ────────────────────────────────────────────────────────────

    def build_sales_receipt(
        self,
        sale_id: int,
        actor_user_id: int | None = None,
        cashier_name: str = '',
    ) -> ReceiptResult:
        """Build a sales invoice receipt for the given sale_id."""
        from models import Sale

        sale = Sale.query.filter_by(id=sale_id).first()
        if not sale:
            raise ValueError(f'Sale {sale_id} not found')

        layout = self._load_sales_layout()
        store  = self._load_store()
        cpl    = self._cpl(layout, 'rcpt_cpl')
        paper_size = self._paper_size(cpl)

        customer_name = customer_phone = ''
        if sale.customer:
            customer_name  = getattr(sale.customer, 'full_name', '') or ''
            customer_phone = getattr(sale.customer, 'phone', '') or ''

        payments = getattr(sale, 'payments', []) or []
        payment_methods = list({p.method for p in payments if p.method})
        payment_label = ', '.join(
            m.replace('_', ' ').title() for m in payment_methods
        ) if payment_methods else ''

        receipt_text = SalesReceiptBuilder().build(
            sale=sale,
            store=store,
            layout=layout,
            cashier_name=cashier_name,
            customer_name=customer_name,
            customer_phone=customer_phone,
            payment_method_label=payment_label,
        )

        result = ReceiptResult(
            receipt_text=receipt_text,
            html_preview=self._preview_meta(layout=layout, cpl=cpl, paper_size=paper_size),
            layout=layout,
            paper_size=paper_size,
            cpl=cpl,
            debug={
                'layout_source': 'StoreSettings',
                'layout_type': layout.get('rcpt_layout_type', 'thermal'),
                'rcpt_cpl': cpl,
                'paper_size': paper_size,
                'enabled_fields': self._enabled_fields(layout),
                'sale_id': sale_id,
                'actor_user_id': actor_user_id,
            },
        )
        _log.info(
            '[RECEIPT] type=sales_invoice sale_id=%s width=%s layout=DB chars=%s paper=%s',
            sale_id, cpl, result.char_count, paper_size,
        )
        return result

    def build_service_receipt(
        self,
        job_id: int,
        actor_user_id: int | None = None,
    ) -> ReceiptResult:
        """Build a service/job receipt using service receipt layout settings."""
        from models import RepairJob, RepairPayment, money_to_decimal

        job = RepairJob.query.filter_by(id=job_id).first()
        if not job:
            raise ValueError(f'Job {job_id} not found')

        payments = (
            RepairPayment.query
            .filter_by(job_id=job_id)
            .order_by(RepairPayment.payment_date.asc())
            .all()
        )
        total_amount    = money_to_decimal(job.total_amount)
        advance_paid    = money_to_decimal(job.advance_paid)
        additional_paid = sum((money_to_decimal(p.amount) for p in payments), money_to_decimal(0))
        final_paid      = advance_paid + additional_paid
        remaining       = max(money_to_decimal(0), total_amount - final_paid)
        parts_total     = sum(money_to_decimal(p.total) for p in (job.parts or []))

        payment_snapshot = {
            'parts_total':  Decimal(str(parts_total or 0)),
            'labor_total':  Decimal(str(money_to_decimal(job.labour_charge) or 0)),
            'grand_total':  Decimal(str(total_amount)),
            'paid_total':   Decimal(str(final_paid)),
            'balance_total': Decimal(str(remaining)),
        }

        layout     = self._load_service_layout()
        store      = self._load_store()
        cpl        = self._cpl(layout, 'svc_rcpt_cpl')
        paper_size = self._paper_size(cpl)

        receipt_text = ServiceReceiptBuilder().build(
            job=job,
            payment_snapshot=payment_snapshot,
            store=store,
            layout=layout,
        )

        result = ReceiptResult(
            receipt_text=receipt_text,
            html_preview=self._preview_meta(layout=layout, cpl=cpl, paper_size=paper_size, kind='service'),
            layout=layout,
            paper_size=paper_size,
            cpl=cpl,
            debug={
                'layout_source': 'StoreSettings',
                'layout_type': 'service',
                'svc_rcpt_cpl': cpl,
                'paper_size': paper_size,
                'enabled_fields': self._enabled_fields(layout),
                'job_id': job_id,
                'actor_user_id': actor_user_id,
            },
        )
        _log.info(
            '[RECEIPT] type=service_job job_id=%s width=%s layout=DB chars=%s paper=%s',
            job_id, cpl, result.char_count, paper_size,
        )
        return result

    def build_return_receipt(
        self,
        sale_id: int,
        actor_user_id: int | None = None,
        cashier_name: str = '',
    ) -> ReceiptResult:
        """Build a return/refund receipt for the given sale_id.

        Uses the sales receipt layout but substitutes 'RETURN RECEIPT' as the
        title.  The underlying sale data remains unmodified.
        """
        from models import Sale

        sale = Sale.query.filter_by(id=sale_id).first()
        if not sale:
            raise ValueError(f'Sale {sale_id} not found for return receipt')

        layout     = self._load_sales_layout()
        store      = self._load_store()
        cpl        = self._cpl(layout, 'rcpt_cpl')
        paper_size = self._paper_size(cpl)

        customer_name = customer_phone = ''
        if sale.customer:
            customer_name  = getattr(sale.customer, 'full_name', '') or ''
            customer_phone = getattr(sale.customer, 'phone', '') or ''

        # Delegate to SalesReceiptBuilder; patch footer text to indicate return
        return_layout = dict(layout)
        return_layout['rcpt_footer_text'] = layout.get(
            'rcpt_footer_text', 'Thank you for your business!'
        )
        receipt_text = SalesReceiptBuilder().build(
            sale=sale,
            store=store,
            layout=return_layout,
            cashier_name=cashier_name,
            customer_name=customer_name,
            customer_phone=customer_phone,
            payment_method_label='Return / Refund',
        )
        # Inject RETURN RECEIPT header by replacing SALES INVOICE title line
        receipt_text = receipt_text.replace('SALES INVOICE', 'RETURN RECEIPT', 1)

        result = ReceiptResult(
            receipt_text=receipt_text,
            html_preview=self._preview_meta(layout=layout, cpl=cpl, paper_size=paper_size, kind='return'),
            layout=layout,
            paper_size=paper_size,
            cpl=cpl,
            debug={
                'layout_source': 'StoreSettings',
                'layout_type': 'return',
                'rcpt_cpl': cpl,
                'paper_size': paper_size,
                'sale_id': sale_id,
                'actor_user_id': actor_user_id,
            },
        )
        _log.info(
            '[RECEIPT] type=return_receipt sale_id=%s width=%s layout=DB chars=%s paper=%s',
            sale_id, cpl, result.char_count, paper_size,
        )
        return result

    def build_payment_receipt(
        self,
        job_id: int,
        actor_user_id: int | None = None,
        payment_id: int | None = None,
    ) -> ReceiptResult:
        """Build a payment receipt for a single payment on a service job.

        Delegates to build_service_receipt; the service receipt already
        contains full financial breakdown (PAID / BALANCE).
        """
        result = self.build_service_receipt(job_id=job_id, actor_user_id=actor_user_id)
        # Inject PAYMENT RECEIPT header by replacing SERVICE RECEIPT title
        patched_text = result.receipt_text.replace('SERVICE RECEIPT', 'PAYMENT RECEIPT', 1)
        patched_plain = _strip_tags(patched_text)
        _log.info(
            '[RECEIPT] type=payment_receipt job_id=%s payment_id=%s width=%s chars=%s',
            job_id, payment_id, result.cpl, len(patched_plain),
        )
        return ReceiptResult(
            receipt_text=patched_text,
            receipt_plain=patched_plain,
            html_preview=result.html_preview,
            layout=result.layout,
            paper_size=result.paper_size,
            cpl=result.cpl,
            char_count=len(patched_plain),
            debug={**result.debug, 'type': 'payment_receipt', 'payment_id': payment_id},
        )

    def build_test_receipt(
        self, kind: Literal['sales', 'service'] = 'sales'
    ) -> ReceiptResult:
        """Build a test receipt using real layout settings from StoreSettings."""
        store = self._load_store()
        if not store['store_name']:
            store['store_name'] = 'Your Store Name'

        if kind == 'service':
            layout     = self._load_service_layout()
            cpl        = self._cpl(layout, 'svc_rcpt_cpl')
            paper_size = self._paper_size(cpl)

            receipt_text = ServiceReceiptBuilder().build(
                job=_MockJob(),
                payment_snapshot={
                    'parts_total':   Decimal('500.00'),
                    'labor_total':   Decimal('1500.00'),
                    'grand_total':   Decimal('2000.00'),
                    'paid_total':    Decimal('1000.00'),
                    'balance_total': Decimal('1000.00'),
                },
                store=store,
                layout=layout,
            )
            layout_type = 'service'
        else:
            layout     = self._load_sales_layout()
            cpl        = self._cpl(layout, 'rcpt_cpl')
            paper_size = self._paper_size(cpl)

            receipt_text = SalesReceiptBuilder().build(
                sale=_MockSale(),
                store=store,
                layout=layout,
                cashier_name='Test Cashier',
                customer_name='Test Customer',
                customer_phone='',
                payment_method_label='Cash',
            )
            layout_type = layout.get('rcpt_layout_type', 'thermal')

        result = ReceiptResult(
            receipt_text=receipt_text,
            html_preview=self._preview_meta(layout=layout, cpl=cpl, paper_size=paper_size, kind=kind),
            layout=layout,
            paper_size=paper_size,
            cpl=cpl,
            debug={
                'layout_source': 'StoreSettings',
                'layout_type': layout_type,
                'cpl': cpl,
                'paper_size': paper_size,
                'enabled_fields': self._enabled_fields(layout),
                'kind': kind,
                'test': True,
            },
        )
        _log.info(
            '[RECEIPT] type=test_%s width=%s layout=DB chars=%s paper=%s',
            kind, cpl, result.char_count, paper_size,
        )
        return result

    def get_debug_layout(self) -> dict[str, Any]:
        """Return full layout diagnostic — used by GET /api/printing/receipt/debug-layout."""
        sales_layout    = self._load_sales_layout()
        service_layout  = self._load_service_layout()
        printer_settings = self._load_printer_settings()

        sales_cpl = self._cpl(sales_layout, 'rcpt_cpl')
        svc_cpl   = self._cpl(service_layout, 'svc_rcpt_cpl')

        return {
            'sales_layout': sales_layout,
            'service_layout': service_layout,
            'printer_settings': printer_settings,
            'derived': {
                'sales_cpl':           sales_cpl,
                'sales_paper_size':    self._paper_size(sales_cpl),
                'service_cpl':         svc_cpl,
                'service_paper_size':  self._paper_size(svc_cpl),
                'sales_enabled_fields':   self._enabled_fields(sales_layout),
                'service_enabled_fields': self._enabled_fields(service_layout),
            },
            'routes_using_engine': True,
            'engine': 'EscposLayoutEngine v2',
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _preview_meta(
        layout: dict[str, str],
        cpl: int,
        paper_size: str,
        kind: str = 'sales',
    ) -> str:
        show: dict[str, bool] = {}
        for k, v in layout.items():
            if k.startswith(('rcpt_show_', 'svc_rcpt_show_')):
                short = k.replace('rcpt_show_', '').replace('svc_rcpt_show_', 'svc_')
                show[short] = str(v).strip().lower() in {'1', 'true', 'yes', 'on'}
        return json.dumps({
            'kind': kind,
            'layout_type': layout.get('rcpt_layout_type', layout.get('svc_rcpt_header_align', 'thermal')),
            'cpl': cpl,
            'paper_size': paper_size,
            'show': show,
            'footer_text': layout.get('rcpt_footer_text', layout.get('svc_rcpt_footer_text', '')),
            'header_align': layout.get('rcpt_header_align', layout.get('svc_rcpt_header_align', 'center')),
        })


# ── Mock objects for test receipts ────────────────────────────────────────────

class _MockProduct:
    def __init__(self, name: str, barcode: str = '') -> None:
        self.name = name
        self.barcode = barcode


class _MockItem:
    def __init__(
        self, name: str, qty: int, price: Decimal, total: Decimal,
        discount: Decimal = Decimal('0'),
    ) -> None:
        self.quantity = qty
        self.price    = price
        self.total    = total
        self.discount = discount
        self.imei     = None
        self.product  = _MockProduct(name)


class _MockPayment:
    def __init__(self, method: str, amount: Decimal) -> None:
        self.method = method
        self.amount = amount


class _MockSale:
    invoice_number = 'TEST-0001'
    sale_date      = datetime.now()
    subtotal       = Decimal('2500.00')
    discount       = Decimal('0.00')
    tax            = Decimal('0.00')
    total_amount   = Decimal('2500.00')
    tendered       = Decimal('3000.00')
    change_amount  = Decimal('500.00')
    customer       = None
    items = [
        _MockItem('Engine Oil Filter', 2, Decimal('750.00'), Decimal('1500.00')),
        _MockItem('Air Filter',        1, Decimal('1000.00'), Decimal('1000.00')),
    ]
    payments = [_MockPayment('cash', Decimal('3000.00'))]


class _MockPart:
    def __init__(self, name: str, qty: int, total: Decimal) -> None:
        self.part_name = name
        self.quantity  = qty
        self.total     = total


class _MockJob:
    job_number               = 'JOB-TEST-001'
    status                   = 'ready'
    received_date            = datetime.now()
    customer_name_snapshot   = 'Test Customer'
    customer_phone_snapshot  = '0779876543'
    plate_number_snapshot    = 'TEST-1234'
    vehicle_make_snapshot    = 'Toyota'
    vehicle_model_snapshot   = 'Corolla'
    vehicle_year_snapshot    = '2020'
    complaint                = 'Engine check light on'
    issue_reported           = 'Engine check light on'
    diagnosis                = None
    odometer_in              = 45000
    labour_charge            = Decimal('1500.00')
    total_amount             = Decimal('2000.00')
    advance_paid             = Decimal('0.00')
    technician               = None
    technician_name          = 'Test Technician'
    customer                 = None
    parts                    = [_MockPart('Oil Filter', 1, Decimal('500.00'))]
    payments: list           = []
