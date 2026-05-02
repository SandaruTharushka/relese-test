"""Sales receipt text builder — v3.

Uses EscposLayoutEngine to produce a structured, configurable thermal receipt:

    ================================
            GARAGE MANAGEMENT
          Automobile & Electronics
              077 XXX XXXX
    --------------------------------
              SALES INVOICE
    --------------------------------
    Invoice No : INV-0001
    Date       : 2026-05-02 12:44
    Cashier    : Admin
    Customer   : Walk-in Customer
    --------------------------------
    ITEM              QTY   PRICE      TOTAL
    Engine Oil          1 2500.00    2500.00
    Brake Cable         2  850.00    1700.00
    --------------------------------
    Subtotal                       LKR 4,200.00
    Discount                          LKR 0.00
    Grand Total                    LKR 4,200.00
    PAID                           LKR 4,200.00
    CHANGE                            LKR 0.00
    ================================
          Thank you for your business!

All visible fields are controlled by rcpt_* layout settings loaded from
StoreSettings.  No hardcoded values in this file.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from services.escpos_layout_engine import (
    EscposLayoutEngine,
    _clip,
    _safe,
    _to_dec,
    _wrap,
)


def _as_bool(layout: dict[str, Any], key: str, default: bool = True) -> bool:
    raw = layout.get(key, 'true' if default else 'false')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


class SalesReceiptBuilder:
    """Builds a thermal-printable sales invoice receipt from a Sale record."""

    def build(
        self,
        *,
        sale: Any,
        store: dict[str, str],
        layout: dict[str, str],
        cashier_name: str = '',
        customer_name: str = '',
        customer_phone: str = '',
        payment_method_label: str = '',
        currency: str = 'LKR',
    ) -> str:
        # ── Width / settings ───────────────────────────────────────────────────
        raw_cpl = layout.get('rcpt_cpl', '48')
        try:
            width = max(24, min(int(raw_cpl), 96))
        except (TypeError, ValueError):
            width = 48

        show = lambda key, default=True: _as_bool(layout, key, default)
        header_align = layout.get('rcpt_header_align', 'center')
        footer_align = layout.get('rcpt_footer_align', 'center')

        eng = EscposLayoutEngine(width=width)

        # ── Store header block ─────────────────────────────────────────────────
        # Format: ==== first, then store info, then ---- separator
        eng.double_separator()

        if show('rcpt_show_store_name'):
            store_name = store.get('store_name') or 'Garage Management System'
            eng.double_height(store_name, align=header_align)
        if show('rcpt_show_branch') and store.get('store_branch'):
            if header_align == 'center':
                eng.center(store['store_branch'])
            else:
                eng.left(store['store_branch'])
        if show('rcpt_show_address') and store.get('store_address'):
            for addr_line in _wrap(store['store_address'], width):
                if header_align == 'center':
                    eng.center(addr_line)
                else:
                    eng.left(addr_line)
        if show('rcpt_show_phone') and store.get('store_phone'):
            if header_align == 'center':
                eng.center(store['store_phone'])
            else:
                eng.left(store['store_phone'])
        if show('rcpt_show_email') and store.get('store_email'):
            if header_align == 'center':
                eng.center(store['store_email'])
            else:
                eng.left(store['store_email'])
        if show('rcpt_show_tax_number') and store.get('store_tax_number'):
            if header_align == 'center':
                eng.center(f'Tax No: {store["store_tax_number"]}')
            else:
                eng.left(f'Tax No: {store["store_tax_number"]}')

        eng.separator()
        eng.center('SALES INVOICE')
        eng.separator()

        # ── Invoice info ───────────────────────────────────────────────────────
        if show('rcpt_show_invoice') and getattr(sale, 'invoice_number', ''):
            eng.kv_row('Invoice No', sale.invoice_number)
        if show('rcpt_show_datetime'):
            sale_dt = getattr(sale, 'sale_date', None) or datetime.utcnow()
            eng.kv_row('Date', sale_dt.strftime('%Y-%m-%d %H:%M'))
        if show('rcpt_show_cashier') and cashier_name:
            eng.kv_row('Cashier', cashier_name)
        if show('rcpt_show_customer') and customer_name:
            eng.kv_row('Customer', customer_name)
        if show('rcpt_show_customer_phone') and customer_phone:
            eng.kv_row('Phone', customer_phone)
        if show('rcpt_show_payment_method') and payment_method_label:
            eng.kv_row('Payment', payment_method_label)

        eng.separator()

        # ── Items ──────────────────────────────────────────────────────────────
        items = getattr(sale, 'items', []) or []
        if items:
            eng.three_col_header()

        for item in items:
            product = getattr(item, 'product', None)
            name = product.name if product else 'Unknown Item'
            qty = item.quantity
            try:
                qty_display = int(qty) if float(qty) == int(float(qty)) else qty
            except (TypeError, ValueError):
                qty_display = qty

            if show('rcpt_show_sku') and product and getattr(product, 'barcode', ''):
                eng.left(f'  SKU: {product.barcode}')

            if show('rcpt_show_qty') and show('rcpt_show_unit_price') and show('rcpt_show_line_total'):
                eng.three_col_item(
                    name, qty_display,
                    _to_dec(item.price),
                    _to_dec(item.total),
                )
                if show('rcpt_show_discount') and _to_dec(getattr(item, 'discount', 0)):
                    eng.indented_pair('Discount', f'-{_to_dec(item.discount):,.2f}')
            else:
                wrap_names = show('rcpt_wrap_product_names')
                if wrap_names:
                    for name_line in _wrap(name, width):
                        eng.left(name_line)
                else:
                    eng.left(_clip(name, width))
                if show('rcpt_show_qty'):
                    eng.two_col('  Qty', str(qty_display))
                if show('rcpt_show_unit_price'):
                    eng.two_col('  Price', f'{_to_dec(item.price):,.2f}')
                if show('rcpt_show_discount') and _to_dec(getattr(item, 'discount', 0)):
                    eng.two_col('  Discount', f'-{_to_dec(item.discount):,.2f}')
                if show('rcpt_show_line_total'):
                    eng.two_col('  Total', f'{_to_dec(item.total):,.2f}')

        eng.separator()

        # ── Totals ─────────────────────────────────────────────────────────────
        subtotal    = _to_dec(getattr(sale, 'subtotal', 0))
        discount    = _to_dec(getattr(sale, 'discount', 0))
        tax         = _to_dec(getattr(sale, 'tax', 0))
        grand_total = _to_dec(getattr(sale, 'total_amount', 0))
        paid        = _to_dec(getattr(sale, 'tendered', 0))
        change      = _to_dec(getattr(sale, 'change_amount', 0))

        if show('rcpt_show_subtotal'):
            eng.two_col('Subtotal', f'{currency} {subtotal:,.2f}')
        if show('rcpt_show_discount_total') and discount:
            eng.two_col('Discount', f'-{currency} {discount:,.2f}')
        if show('rcpt_show_tax') and tax:
            eng.two_col('Tax', f'{currency} {tax:,.2f}')
        if show('rcpt_show_grand_total'):
            eng.grand_total_line('Grand Total', f'{currency} {grand_total:,.2f}')
        if show('rcpt_show_paid'):
            eng.two_col('PAID', f'{currency} {paid:,.2f}')
        if show('rcpt_show_change'):
            eng.two_col('CHANGE', f'{currency} {change:,.2f}')

        eng.double_separator()

        # ── Footer ─────────────────────────────────────────────────────────────
        footer_text = layout.get('rcpt_footer_text', 'Thank you for your business!')
        if show('rcpt_show_thankyou', True):
            for fline in _wrap(footer_text, width):
                if footer_align == 'center':
                    eng.center(fline)
                else:
                    eng.left(fline)
        if show('rcpt_show_return_policy', False):
            for fline in _wrap('For returns / exchanges, keep this receipt.', width):
                if footer_align == 'center':
                    eng.center(fline)
                else:
                    eng.left(fline)
        if show('rcpt_show_powered_by', False):
            eng.center('Powered by Garage Management System')

        # Barcode text line (invoice number)
        enable_barcode = show('rcpt_enable_barcode', True)
        show_barcode_text = show('rcpt_show_barcode_text', True)
        invoice_no = getattr(sale, 'invoice_number', '') or ''
        if enable_barcode and show_barcode_text and invoice_no:
            eng.barcode_line(invoice_no)

        eng.blank(2)

        return eng.as_tagged_text()
