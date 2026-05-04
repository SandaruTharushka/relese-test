"""Sales receipt text builder — v4 clean invoice style.

Clean professional layout (no separator lines):

        GARAGE MANAGEMENT
      Automobile & Electronics
          077 XXX XXXX

          SALES INVOICE

Date: 2026-05-04          Time: 9:17 am
Inv No: INV-0001          User: Admin
Customer: Walk-in Customer

R5-F-4-36311028 - VALVE KIT CALIBER
        1.00 x 3,030.00      3,030.00

ENGINE OIL FILTER
        2.00 x 750.00        1,500.00

Subtotal              LKR 4,530.00
Total Discount            LKR 0.00
Grand Total           LKR 4,530.00
Amount Received       LKR 5,000.00
Balance                 LKR 470.00

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
    """Builds a clean invoice-style thermal receipt from a Sale record."""

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

        # ── Store header block (centered, no separators) ───────────────────────
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
            line = f'Tax No: {store["store_tax_number"]}'
            if header_align == 'center':
                eng.center(line)
            else:
                eng.left(line)

        eng.blank()
        eng.center('SALES INVOICE')
        eng.blank()

        # ── Invoice meta: Date/Time same row, Inv No/User same row ───────────
        sale_dt = getattr(sale, 'sale_date', None) or datetime.utcnow()
        date_str = f'Date: {sale_dt.strftime("%Y-%m-%d")}'
        time_str = f'Time: {sale_dt.strftime("%I:%M %p").lstrip("0")}'

        if show('rcpt_show_datetime'):
            if show('rcpt_show_cashier') and cashier_name:
                eng.left_right(date_str, time_str)
                inv_str = f'Inv No: {_safe(getattr(sale, "invoice_number", ""), "-")}'
                user_str = f'User: {cashier_name}'
                if show('rcpt_show_invoice'):
                    eng.left_right(inv_str, user_str)
                else:
                    eng.left(f'User: {cashier_name}')
            else:
                eng.left_right(date_str, time_str)
                if show('rcpt_show_invoice') and getattr(sale, 'invoice_number', ''):
                    eng.left(f'Inv No: {sale.invoice_number}')
        else:
            if show('rcpt_show_invoice') and getattr(sale, 'invoice_number', ''):
                if show('rcpt_show_cashier') and cashier_name:
                    inv_str = f'Inv No: {sale.invoice_number}'
                    user_str = f'User: {cashier_name}'
                    eng.left_right(inv_str, user_str)
                else:
                    eng.left(f'Inv No: {sale.invoice_number}')
            elif show('rcpt_show_cashier') and cashier_name:
                eng.left(f'User: {cashier_name}')

        if show('rcpt_show_customer') and customer_name:
            eng.left(f'Customer: {customer_name}')
        if show('rcpt_show_customer_phone') and customer_phone:
            eng.left(f'Phone: {customer_phone}')
        if show('rcpt_show_payment_method') and payment_method_label:
            eng.left(f'Payment: {payment_method_label}')

        eng.blank()

        # ── Items ──────────────────────────────────────────────────────────────
        items = getattr(sale, 'items', []) or []

        for item in items:
            product = getattr(item, 'product', None)
            name = product.name if product else 'Unknown Item'
            sku = getattr(product, 'barcode', '') if product else ''
            qty = _to_dec(item.quantity)
            price = _to_dec(item.price)
            total = _to_dec(item.total)

            # Line 1: SKU - Name  (or just Name)
            if show('rcpt_show_sku') and sku:
                header_line = _clip(f'{sku} - {name}', width)
            else:
                header_line = _clip(name, width)
            eng.left(header_line)

            # Line 2: indented "qty x price" left, total right
            try:
                qty_f = float(qty)
                qty_str = f'{qty_f:.2f}'
            except (TypeError, ValueError):
                qty_str = str(qty)

            if show('rcpt_show_qty') and show('rcpt_show_unit_price') and show('rcpt_show_line_total'):
                eng.item_detail(qty_str, price, total)
            elif show('rcpt_show_qty') and show('rcpt_show_unit_price'):
                eng.left(f'        {qty_str} x {price:,.2f}')
            elif show('rcpt_show_line_total'):
                eng.right(f'{total:,.2f}')

            if show('rcpt_show_discount') and _to_dec(getattr(item, 'discount', 0)):
                disc = _to_dec(item.discount)
                eng.left(f'        Discount: -{disc:,.2f}')

            eng.blank()

        # ── Totals (right-aligned, no separator) ───────────────────────────────
        subtotal    = _to_dec(getattr(sale, 'subtotal', 0))
        discount    = _to_dec(getattr(sale, 'discount', 0))
        tax         = _to_dec(getattr(sale, 'tax', 0))
        grand_total = _to_dec(getattr(sale, 'total_amount', 0))
        paid        = _to_dec(getattr(sale, 'tendered', 0))
        change      = _to_dec(getattr(sale, 'change_amount', 0))

        if show('rcpt_show_subtotal'):
            eng.money_row('Subtotal', f'{currency} {subtotal:,.2f}')
        if show('rcpt_show_discount_total') and discount:
            eng.money_row('Total Discount', f'{currency} {discount:,.2f}')
        if show('rcpt_show_tax') and tax:
            eng.money_row('Tax', f'{currency} {tax:,.2f}')
        if show('rcpt_show_grand_total'):
            eng.two_col_bold('Grand Total', f'{currency} {grand_total:,.2f}')
        if show('rcpt_show_paid'):
            eng.money_row('Amount Received', f'{currency} {paid:,.2f}')
        if show('rcpt_show_change'):
            eng.money_row('Balance', f'{currency} {change:,.2f}')

        eng.blank()

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

        enable_barcode = show('rcpt_enable_barcode', True)
        show_barcode_text = show('rcpt_show_barcode_text', True)
        invoice_no = getattr(sale, 'invoice_number', '') or ''
        if enable_barcode and show_barcode_text and invoice_no:
            eng.barcode_line(invoice_no)

        eng.blank(2)

        return eng.as_tagged_text()
