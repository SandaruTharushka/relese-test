"""Sales receipt text builder — v2.

Converts a Sale ORM object + store settings + layout settings into a
formatted thermal receipt text string ready for ESC/POS dispatch.

ESC/POS formatting tags (ESCPOS_*) are embedded inline; build_escpos_payload()
splits on them and injects the matching raw printer commands before encoding.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from services.printing.receipt.formatter import (
    CHAR_RULE_HEAVY,
    CHAR_RULE_LIGHT,
    ESCPOS_BOLD_OFF,
    ESCPOS_BOLD_ON,
    ESCPOS_DH_OFF,
    ESCPOS_DH_ON,
    ESCPOS_DW_OFF,
    ESCPOS_DW_ON,
    THERMAL_WIDTH_58MM,
    THERMAL_WIDTH_80MM,
    center,
    clean_line,
    cpl_from_settings,
    money,
    pair,
    qty_amount_line,
    rule,
    rule_heavy,
    to_decimal,
    wrap_text,
)


def _as_bool(layout: dict[str, Any], key: str, default: bool = True) -> bool:
    raw = layout.get(key, 'true' if default else 'false')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


class SalesReceiptBuilder:
    """Builds a thermal-printable text receipt from a Sale record."""

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
        """Return formatted receipt text for thermal printing."""
        width = cpl_from_settings(layout)
        show = lambda key, default=True: _as_bool(layout, key, default)

        lines: list[str] = []

        # ── Header ── (DOUBLE HEIGHT on thermal printer) ──────────────
        header_align = layout.get('rcpt_header_align', 'center')

        def _header(text: str) -> str:
            if header_align == 'left':
                return text
            return center(text, width)

        if show('rcpt_show_store_name'):
            store_name = store.get('store_name', 'Garage Management System')
            lines.append(ESCPOS_DH_ON)
            lines.append(_header(store_name))
            lines.append(ESCPOS_DH_OFF)
        if show('rcpt_show_branch') and store.get('store_branch'):
            lines.append(_header(store['store_branch']))
        if show('rcpt_show_address') and store.get('store_address'):
            for addr_line in wrap_text(store['store_address'], width):
                lines.append(_header(addr_line))
        if show('rcpt_show_phone') and store.get('store_phone'):
            lines.append(_header(store['store_phone']))
        if show('rcpt_show_email') and store.get('store_email'):
            lines.append(_header(store['store_email']))
        if show('rcpt_show_tax_number') and store.get('store_tax_number'):
            lines.append(_header(f'Tax No: {store["store_tax_number"]}'))

        lines.append(rule_heavy(width))

        # ── Invoice info ──────────────────────────────────────────────
        if show('rcpt_show_invoice') and getattr(sale, 'invoice_number', ''):
            lines.append(pair('Invoice', sale.invoice_number, width))
        if show('rcpt_show_datetime'):
            sale_dt = getattr(sale, 'sale_date', None) or datetime.utcnow()
            lines.append(pair('Date', sale_dt.strftime('%Y-%m-%d %H:%M'), width))
        if show('rcpt_show_cashier') and cashier_name:
            lines.append(pair('Cashier', cashier_name, width))
        if show('rcpt_show_customer') and customer_name:
            lines.append(pair('Customer', customer_name, width))
        if show('rcpt_show_customer_phone') and customer_phone:
            lines.append(pair('Phone', customer_phone, width))
        if show('rcpt_show_payment_method') and payment_method_label:
            lines.append(pair('Payment', payment_method_label, width))

        lines.append(rule(width))

        # ── Items ─────────────────────────────────────────────────────
        for item in getattr(sale, 'items', []) or []:
            product = getattr(item, 'product', None)
            name = product.name if product else 'Unknown Item'
            qty = item.quantity
            qty_display = int(qty) if qty == int(qty) else qty

            if show('rcpt_show_sku') and product and getattr(product, 'barcode', ''):
                lines.append(f'  SKU: {product.barcode}')

            if show('rcpt_show_qty') and show('rcpt_show_unit_price') and show('rcpt_show_line_total'):
                item_lines = qty_amount_line(name, qty_display, item.total, width, currency)
                lines.extend(item_lines)
                if show('rcpt_show_unit_price'):
                    lines.append(pair('  Unit price', money(item.price, currency), width))
                if show('rcpt_show_discount') and getattr(item, 'discount', 0):
                    lines.append(pair('  Discount', f'-{money(item.discount, currency)}', width))
            else:
                wrap = show('rcpt_wrap_product_names')
                if wrap:
                    for name_line in wrap_text(name, width):
                        lines.append(name_line)
                else:
                    lines.append(clean_line(name, width))
                if show('rcpt_show_qty'):
                    lines.append(pair('  Qty', str(qty_display), width))
                if show('rcpt_show_unit_price'):
                    lines.append(pair('  Price', money(item.price, currency), width))
                if show('rcpt_show_discount') and getattr(item, 'discount', 0):
                    lines.append(pair('  Discount', f'-{money(item.discount, currency)}', width))
                if show('rcpt_show_line_total'):
                    lines.append(pair('  Total', money(item.total, currency), width))

        lines.append(rule(width))

        # ── Totals ────────────────────────────────────────────────────
        subtotal    = to_decimal(getattr(sale, 'subtotal', 0))
        discount    = to_decimal(getattr(sale, 'discount', 0))
        tax         = to_decimal(getattr(sale, 'tax', 0))
        grand_total = to_decimal(getattr(sale, 'total_amount', 0))
        paid        = to_decimal(getattr(sale, 'tendered', 0))
        change      = to_decimal(getattr(sale, 'change_amount', 0))

        if show('rcpt_show_subtotal'):
            lines.append(pair('Subtotal', money(subtotal, currency), width))
        if show('rcpt_show_discount_total') and discount:
            lines.append(pair('Discount', f'-{money(discount, currency)}', width))
        if show('rcpt_show_tax') and tax:
            lines.append(pair('Tax', money(tax, currency), width))
        if show('rcpt_show_grand_total'):
            # ── Grand Total — DOUBLE WIDTH + BOLD on thermal printer ──
            lines.append(rule_heavy(width))
            lines.append(ESCPOS_DW_ON)
            lines.append(pair('TOTAL', money(grand_total, currency), width))
            lines.append(ESCPOS_DW_OFF)
        if show('rcpt_show_paid'):
            lines.append(pair('PAID', money(paid, currency), width))
        if show('rcpt_show_change'):
            lines.append(pair('CHANGE', money(change, currency), width))

        lines.append(rule_heavy(width))

        # ── Footer ────────────────────────────────────────────────────
        footer_align = layout.get('rcpt_footer_align', 'center')

        def _footer(text: str) -> str:
            if footer_align == 'left':
                return text
            return center(text, width)

        lines.append(_footer('* * * * *'))
        if show('rcpt_show_thankyou', True):
            footer_text = layout.get('rcpt_footer_text', 'Thank you for shopping!')
            for fline in wrap_text(footer_text, width):
                lines.append(_footer(fline))
        if show('rcpt_show_return_policy', False):
            lines.append(_footer('For returns, keep this receipt.'))
        if show('rcpt_show_powered_by', False):
            lines.append(_footer('Powered by Garage Management System'))

        lines.append('')
        lines.append('')
        return '\n'.join(lines)
