from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


def _fmt_money(value: Any) -> str:
    return f"{float(value or 0):.2f}"


def _profile_visual(profile_type: str, profile: dict[str, Any], fallback_paper_width: int = 48) -> dict[str, Any]:
    cpl = int(profile.get('cpl') or fallback_paper_width or 48)
    paper_size = str(profile.get('paper_size') or ('80mm' if cpl > 32 else '58mm')).strip().lower()
    compact_mode = bool(profile.get('compact_mode')) or cpl <= 32 or paper_size == '58mm'
    print_mode = 'a4' if profile_type == 'dot_matrix' or paper_size.startswith('a4') else 'thermal'
    paper_width = '210mm' if print_mode == 'a4' else ('58mm' if compact_mode else '80mm')
    return {'cpl': cpl, 'compact_mode': compact_mode, 'print_mode': print_mode, 'paper_width': paper_width}


def build_receipt_context(*, sale: Any, store: dict[str, str], profile_type: str, profile: dict[str, Any],
                          cashier_name: str, customer_name: str, customer_phone: str,
                          payment_method_label: str = '', barcode_url: str = '', qr_url: str = '',
                          invoice_detail_url: str = '', fallback_paper_width: int = 48) -> dict[str, Any]:
    visual = _profile_visual(profile_type, profile, fallback_paper_width)
    show = lambda key, default=True: bool(profile.get(key, default))

    item_rows = []
    for item in getattr(sale, 'items', []) or []:
        qty_val = item.quantity
        qty_display = int(qty_val) if qty_val == int(qty_val) else qty_val
        item_rows.append({
            'name': item.product.name if item.product else 'Unknown Item',
            'qty': qty_display,
            'unit_price': _fmt_money(item.price),
            'discount': _fmt_money(item.discount),
            'line_total': _fmt_money(item.total),
            'imei': item.imei,
            'sku': getattr(item.product, 'barcode', '') if item.product else '',
        })

    payments = []
    for payment in getattr(sale, 'payments', []) or []:
        payments.append({
            'method': (payment.method or 'other').replace('_', ' ').title(),
            'amount': _fmt_money(payment.amount),
        })

    footer_lines: list[str] = []
    if show('show_thankyou', True):
        footer = str(profile.get('footer_text') or '').strip() or 'Thank you for shopping!'
        footer_lines.extend([line.strip() for line in footer.split('\n') if line.strip()])
    if show('show_return_policy', False):
        footer_lines.append('For exchanges/returns, keep this receipt safe.')

    return {
        'profile': profile,
        'store': store,
        'sale': sale,
        'item_rows': item_rows,
        'payments': payments,
        'cashier_name': cashier_name,
        'customer_name': customer_name,
        'customer_phone': customer_phone,
        'payment_method_label': payment_method_label,
        'barcode_url': barcode_url,
        'qr_url': qr_url,
        'invoice_detail_url': invoice_detail_url,
        'footer_lines': footer_lines,
        'profile_type': profile_type,
        'show': {
            'store_name': show('show_store_name', True),
            'branch': show('show_branch', True),
            'address': show('show_address', False),
            'phone': show('show_phone', True),
            'email': show('show_email', False),
            'invoice': show('show_invoice', True),
            'datetime': show('show_datetime', True),
            'cashier': show('show_cashier', True),
            'customer': show('show_customer', True),
            'customer_phone': show('show_customer_phone', False),
            'payment_method': show('show_payment_method', True),
            'qty': show('show_qty', True),
            'unit_price': show('show_unit_price', True),
            'discount': show('show_discount', True),
            'line_total': show('show_line_total', True),
            'sku': show('show_sku', False),
            'subtotal': show('show_subtotal', True),
            'discount_total': show('show_discount_total', True),
            'tax': show('show_tax', True),
            'grand_total': show('show_grand_total', True),
            'paid': show('show_paid', True),
            'change': show('show_change', True),
            'powered_by': show('show_powered_by', False),
        },
        'header_align': profile.get('header_align', 'center'),
        'footer_align': profile.get('footer_align', 'center'),
        'totals': {
            'subtotal': _fmt_money(getattr(sale, 'subtotal', 0)),
            'discount': _fmt_money(getattr(sale, 'discount', 0)),
            'tax': _fmt_money(getattr(sale, 'tax', 0)),
            'grand_total': _fmt_money(getattr(sale, 'total_amount', 0)),
            'paid': _fmt_money(getattr(sale, 'tendered', 0)),
            'change': _fmt_money(getattr(sale, 'change_amount', 0)),
            'you_saved': _fmt_money(getattr(sale, 'discount', 0)),
        },
        'sale_datetime': getattr(sale, 'sale_date', None) or datetime.utcnow(),
        **visual,
    }


THERMAL_WIDTH_80MM = 48
THERMAL_WIDTH_58MM = 32


def _safe_str(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _to_decimal(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _money(value: Any, currency: str = "LKR") -> str:
    amount = _to_decimal(value)
    return f"{currency} {amount:,.2f}"


def _clean_line(text: str, width: int) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)].rstrip() + "..."


def _wrap_text(text: str, width: int) -> list[str]:
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ["-"]

    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        if not current:
            current = word
            continue
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or ["-"]


def _center(text: str, width: int) -> str:
    text = _clean_line(text, width)
    return text.center(width)


def _rule(width: int, ch: str = "-") -> str:
    return ch * width


def _pair(left: str, right: str, width: int) -> str:
    left = _safe_str(left, "")
    right = _safe_str(right, "")
    if len(left) + len(right) + 1 <= width:
        return f"{left}{' ' * (width - len(left) - len(right))}{right}"

    available_left = max(1, width - len(right) - 1)
    left = _clean_line(left, available_left)
    spaces = max(1, width - len(left) - len(right))
    return f"{left}{' ' * spaces}{right}"


def _qty_amount_line(name: str, qty: Any, amount: Any, width: int, currency: str = "LKR") -> list[str]:
    amount_text = _money(amount, currency)
    left_text = f"{_safe_str(name, 'Item')} x{_safe_str(qty, '1')}"

    if len(left_text) + len(amount_text) + 1 <= width:
        return [_pair(left_text, amount_text, width)]

    wrapped = _wrap_text(left_text, max(8, width - len(amount_text) - 1))
    lines: list[str] = []
    for i, line in enumerate(wrapped):
        if i == len(wrapped) - 1:
            lines.append(_pair(line, amount_text, width))
        else:
            lines.append(line)
    return lines


def _extract_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            value = obj.get(name)
            if value is not None:
                return value
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _collect_part_lines(job: Any, width: int, currency: str) -> list[str]:
    sources = [
        _extract_attr(job, "parts", default=None),
        _extract_attr(job, "materials", default=None),
        _extract_attr(job, "items", default=None),
    ]

    part_lines: list[str] = []

    for source in sources:
        if not source:
            continue
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes, dict)):
            continue

        for row in source:
            name = _extract_attr(row, "part_name", "name", "product_name", "item_name", "description", default="Item")
            qty = _extract_attr(row, "qty", "quantity", default=1)
            total = _extract_attr(row, "total", "line_total", "subtotal", "amount", "price", default=0)
            part_lines.extend(_qty_amount_line(str(name), qty, total, width, currency))
        break

    return part_lines


# ─────────────────────────────────────────────────────────────────────────────
# Modern HTML preview renderers — used by the canonical
# /api/receipts/<billing|repair>/<id>/preview endpoints.
#
# These produce a self-contained HTML fragment that is dropped into a thermal
# receipt frame.  The same StoreSettings-driven layout dict that controls the
# printed text controls the visible HTML, so preview always matches print.
# ─────────────────────────────────────────────────────────────────────────────


def _layout_bool(layout: dict[str, str], key: str, default: bool = True) -> bool:
    raw = layout.get(key, 'true' if default else 'false')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _esc(value: Any) -> str:
    """HTML-escape — minimal, safe for receipt text."""
    s = '' if value is None else str(value)
    return (
        s.replace('&', '&amp;')
         .replace('<', '&lt;')
         .replace('>', '&gt;')
         .replace('"', '&quot;')
    )


_RECEIPT_CSS = """
<style>
  .gms-receipt{font-family:'JetBrains Mono','Fira Code',Menlo,Consolas,'Courier New',monospace;color:#0f172a;
    background:#ffffff;padding:18px 14px 22px;line-height:1.45;font-size:12.5px;}
  .gms-receipt.r-58mm{width:280px;}
  .gms-receipt.r-80mm{width:340px;}
  .gms-receipt .r-head{text-align:center;}
  .gms-receipt .r-store{font-size:16px;font-weight:800;letter-spacing:0.2px;}
  .gms-receipt .r-tag{font-size:11.5px;color:#475569;margin-top:1px;}
  .gms-receipt .r-title{margin-top:10px;text-align:center;font-weight:700;letter-spacing:1px;}
  .gms-receipt .r-meta{margin-top:8px;display:grid;grid-template-columns:auto 1fr;column-gap:8px;row-gap:2px;font-size:12px;}
  .gms-receipt .r-meta .k{color:#475569;}
  .gms-receipt .r-meta .v{text-align:right;}
  .gms-receipt .r-rule{border:none;border-top:1px dashed #cbd5e1;margin:10px 0;}
  .gms-receipt .r-section{font-size:11px;font-weight:700;letter-spacing:1px;color:#0f172a;margin:6px 0 4px;}
  .gms-receipt .r-items{width:100%;border-collapse:collapse;font-size:12px;}
  .gms-receipt .r-items th{text-align:left;padding:2px 0;font-weight:600;color:#475569;border-bottom:1px solid #e2e8f0;}
  .gms-receipt .r-items td{padding:3px 0;vertical-align:top;}
  .gms-receipt .r-items td.qty{text-align:center;width:34px;}
  .gms-receipt .r-items td.amt,
  .gms-receipt .r-items th.amt{text-align:right;width:84px;white-space:nowrap;}
  .gms-receipt .r-items .sub{display:block;font-size:11px;color:#64748b;}
  .gms-receipt .r-totals{margin-top:6px;}
  .gms-receipt .r-totals .row{display:flex;justify-content:space-between;font-size:12px;padding:2px 0;}
  .gms-receipt .r-totals .row.grand{font-weight:800;font-size:14px;border-top:2px solid #0f172a;
    border-bottom:2px solid #0f172a;padding:5px 0;margin-top:4px;}
  .gms-receipt .r-totals .row.muted{color:#64748b;}
  .gms-receipt .r-pay{margin-top:8px;}
  .gms-receipt .r-foot{margin-top:12px;text-align:center;font-size:12px;color:#0f172a;}
  .gms-receipt .r-foot .thanks{font-weight:700;}
  .gms-receipt .r-foot .barcode{font-family:'Libre Barcode 39',monospace;font-size:42px;line-height:1;letter-spacing:1px;margin-top:6px;}
  .gms-receipt .r-foot .barcode-no{font-size:11px;letter-spacing:2px;color:#475569;margin-top:2px;}
  .gms-receipt .r-issue{font-size:12px;}
</style>
"""


def _format_dt(value: Any) -> tuple[str, str]:
    if not value:
        return ('—', '')
    if hasattr(value, 'strftime'):
        return (value.strftime('%Y-%m-%d'), value.strftime('%I:%M %p').lstrip('0'))
    return (str(value), '')


def render_billing_preview_html(
    *,
    sale: Any,
    store: dict[str, str],
    layout: dict[str, str],
    cashier_name: str = '',
    cpl: int = 48,
    paper_size: str = '80mm',
    currency: str = 'LKR',
) -> str:
    """Render a modern HTML preview for a billing receipt.

    Visibility / fields mirror exactly the rcpt_* layout flags used by the
    thermal builder (services.printing.receipt.sales_builder), so the preview
    looks like the printed output.
    """
    show = lambda k, default=True: _layout_bool(layout, k, default)

    paper_class = 'r-58mm' if paper_size == '58mm' else 'r-80mm'

    # Extract sale fields safely.
    invoice_no = getattr(sale, 'invoice_number', '') or '—'
    sale_dt = getattr(sale, 'sale_date', None)
    date_str, time_str = _format_dt(sale_dt)

    customer_name = ''
    customer_phone = ''
    if sale is not None:
        rc = getattr(sale, 'retail_customer', None)
        wc = getattr(sale, 'wholesale_customer', None)
        if rc:
            customer_name = getattr(rc, 'name', '') or ''
            customer_phone = getattr(rc, 'phone', '') or ''
        elif wc:
            customer_name = getattr(wc, 'name', '') or ''
            customer_phone = getattr(wc, 'phone', '') or ''
        if not customer_name:
            customer_name = getattr(sale, 'customer_name_snapshot', '') or ''
        if not customer_phone:
            customer_phone = getattr(sale, 'customer_phone_snapshot', '') or ''

    payment_methods = []
    if sale is not None:
        for p in getattr(sale, 'payments', []) or []:
            label = (p.method or 'other').replace('_', ' ').title()
            try:
                amount = float(p.amount or 0)
            except (TypeError, ValueError):
                amount = 0.0
            payment_methods.append((label, amount))

    items = []
    if sale is not None:
        for it in getattr(sale, 'items', []) or []:
            product = getattr(it, 'product', None)
            items.append({
                'name':       getattr(product, 'name', None) or 'Item',
                'sku':        getattr(product, 'barcode', '') or '',
                'qty':        it.quantity,
                'unit_price': _fmt_money(it.price),
                'discount':   _fmt_money(getattr(it, 'discount', 0)),
                'line_total': _fmt_money(it.total),
                'imei':       getattr(it, 'imei', '') or '',
            })

    subtotal = _fmt_money(getattr(sale, 'subtotal', 0))
    discount = _fmt_money(getattr(sale, 'discount', 0))
    tax      = _fmt_money(getattr(sale, 'tax', 0))
    total    = _fmt_money(getattr(sale, 'total_amount', 0))
    paid     = _fmt_money(getattr(sale, 'tendered', 0))
    change   = _fmt_money(getattr(sale, 'change_amount', 0))

    # ── Header ───────────────────────────────────────────────────────────────
    head_parts = []
    if show('rcpt_show_store_name') and store.get('store_name'):
        head_parts.append(f'<div class="r-store">{_esc(store["store_name"])}</div>')
    if show('rcpt_show_branch') and store.get('store_branch'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_branch"])}</div>')
    if show('rcpt_show_address') and store.get('store_address'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_address"])}</div>')
    if show('rcpt_show_phone') and store.get('store_phone'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_phone"])}</div>')
    if show('rcpt_show_email') and store.get('store_email'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_email"])}</div>')
    if show('rcpt_show_tax_number') and store.get('store_tax_number'):
        head_parts.append(f'<div class="r-tag">Tax No: {_esc(store["store_tax_number"])}</div>')

    # ── Meta ─────────────────────────────────────────────────────────────────
    meta_rows: list[str] = []
    if show('rcpt_show_invoice'):
        meta_rows.append(f'<div class="k">Invoice</div><div class="v">{_esc(invoice_no)}</div>')
    if show('rcpt_show_datetime'):
        when = (date_str + (' ' + time_str if time_str else '')).strip() or '—'
        meta_rows.append(f'<div class="k">Date</div><div class="v">{_esc(when)}</div>')
    if show('rcpt_show_cashier') and cashier_name:
        meta_rows.append(f'<div class="k">Cashier</div><div class="v">{_esc(cashier_name)}</div>')
    if show('rcpt_show_customer') and customer_name:
        meta_rows.append(f'<div class="k">Customer</div><div class="v">{_esc(customer_name)}</div>')
    if show('rcpt_show_customer_phone') and customer_phone:
        meta_rows.append(f'<div class="k">Phone</div><div class="v">{_esc(customer_phone)}</div>')
    if show('rcpt_show_payment_method') and payment_methods:
        label = ', '.join(f'{m}' for m, _ in payment_methods)
        meta_rows.append(f'<div class="k">Payment</div><div class="v">{_esc(label)}</div>')

    # ── Items ────────────────────────────────────────────────────────────────
    item_rows: list[str] = []
    for it in items:
        sub_bits = []
        if show('rcpt_show_sku') and it['sku']:
            sub_bits.append(f'SKU {_esc(it["sku"])}')
        if it['imei']:
            sub_bits.append(f'IMEI {_esc(it["imei"])}')
        if show('rcpt_show_unit_price'):
            sub_bits.append(f'{currency} {_esc(it["unit_price"])} ea')
        if show('rcpt_show_discount') and it['discount'] not in ('0.00', '-0.00'):
            sub_bits.append(f'-{currency} {_esc(it["discount"])}')
        sub = f'<span class="sub">{ " · ".join(sub_bits) }</span>' if sub_bits else ''
        qty_cell = f'<td class="qty">{_esc(it["qty"])}</td>' if show('rcpt_show_qty') else ''
        amt_cell = (
            f'<td class="amt">{currency} {_esc(it["line_total"])}</td>'
            if show('rcpt_show_line_total')
            else '<td class="amt"></td>'
        )
        item_rows.append(
            f'<tr><td>{_esc(it["name"])}{sub}</td>{qty_cell}{amt_cell}</tr>'
        )
    qty_th = '<th class="qty">Qty</th>' if show('rcpt_show_qty') else ''
    items_tbl = (
        '<table class="r-items">'
        f'<thead><tr><th>Item</th>{qty_th}<th class="amt">Amount</th></tr></thead>'
        f'<tbody>{ "".join(item_rows) or "<tr><td colspan=3 class=sub>No items</td></tr>"}</tbody>'
        '</table>'
    )

    # ── Totals ───────────────────────────────────────────────────────────────
    totals: list[str] = []
    if show('rcpt_show_subtotal'):
        totals.append(f'<div class="row"><span>Subtotal</span><span>{currency} {_esc(subtotal)}</span></div>')
    if show('rcpt_show_discount_total') and discount not in ('0.00', '-0.00'):
        totals.append(f'<div class="row"><span>Discount</span><span>-{currency} {_esc(discount)}</span></div>')
    if show('rcpt_show_tax') and tax not in ('0.00', '-0.00'):
        totals.append(f'<div class="row"><span>Tax</span><span>{currency} {_esc(tax)}</span></div>')
    if show('rcpt_show_grand_total'):
        totals.append(f'<div class="row grand"><span>Grand Total</span><span>{currency} {_esc(total)}</span></div>')
    if show('rcpt_show_paid'):
        totals.append(f'<div class="row"><span>Paid</span><span>{currency} {_esc(paid)}</span></div>')
    if show('rcpt_show_change'):
        totals.append(f'<div class="row muted"><span>Balance / Change</span><span>{currency} {_esc(change)}</span></div>')

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_text = layout.get('rcpt_footer_text') or 'Thank you for your business!'
    enable_barcode = _layout_bool(layout, 'rcpt_enable_barcode', True)
    show_barcode_text = _layout_bool(layout, 'rcpt_show_barcode_text', True)

    footer_parts = []
    if show('rcpt_show_thankyou', True):
        footer_parts.append(f'<div class="thanks">{_esc(footer_text)}</div>')
    if show('rcpt_show_return_policy', False):
        footer_parts.append('<div class="muted">For exchanges/returns, keep this receipt.</div>')
    if enable_barcode and invoice_no and invoice_no != '—':
        footer_parts.append(f'<div class="barcode">*{_esc(invoice_no)}*</div>')
        if show_barcode_text:
            footer_parts.append(f'<div class="barcode-no">{_esc(invoice_no)}</div>')
    if show('rcpt_show_powered_by', False):
        footer_parts.append('<div class="muted">Powered by Garage Management System</div>')

    return (
        _RECEIPT_CSS
        + f'<div class="gms-receipt {paper_class}" data-cpl="{cpl}">'
        + '<div class="r-head">' + ''.join(head_parts) + '</div>'
        + '<div class="r-title">SALES INVOICE</div>'
        + ('<div class="r-meta">' + ''.join(meta_rows) + '</div>' if meta_rows else '')
        + '<hr class="r-rule">'
        + items_tbl
        + '<hr class="r-rule">'
        + ('<div class="r-totals">' + ''.join(totals) + '</div>' if totals else '')
        + '<div class="r-foot">' + ''.join(footer_parts) + '</div>'
        + '</div>'
    )


def render_repair_preview_html(
    *,
    job: Any,
    store: dict[str, str],
    layout: dict[str, str],
    cpl: int = 48,
    paper_size: str = '80mm',
    currency: str = 'LKR',
) -> str:
    """Render a modern HTML preview for a repair / service receipt.

    Mirrors svc_rcpt_* layout flags used by ServiceReceiptBuilder so the
    rendered HTML matches the thermal print output exactly.
    """
    show = lambda k, default=True: _layout_bool(layout, k, default)
    paper_class = 'r-58mm' if paper_size == '58mm' else 'r-80mm'

    if job is None:
        return _RECEIPT_CSS + '<div class="gms-receipt"><em>Job not found.</em></div>'

    # ── Job fields ────────────────────────────────────────────────────────────
    job_no = getattr(job, 'job_number', '') or '—'
    status = (getattr(job, 'status', '') or '').replace('_', ' ').title() or '—'

    received = (
        getattr(job, 'received_date', None)
        or getattr(job, 'created_at', None)
        or getattr(job, 'check_in_date', None)
    )
    date_str, time_str = _format_dt(received)

    customer_name = (
        getattr(job, 'customer_name_snapshot', None)
        or getattr(job, 'customer_name', None)
        or getattr(getattr(job, 'customer', None), 'name', None)
        or '—'
    )
    customer_phone = (
        getattr(job, 'customer_phone_snapshot', None)
        or getattr(job, 'customer_phone', None)
        or getattr(getattr(job, 'customer', None), 'phone', None)
        or ''
    )
    reg_no = (
        getattr(job, 'plate_number_snapshot', None)
        or getattr(job, 'vehicle_reg_no', None)
        or ''
    )
    vehicle_make  = getattr(job, 'vehicle_make_snapshot', None)  or getattr(job, 'vehicle_make', None)  or ''
    vehicle_model = getattr(job, 'vehicle_model_snapshot', None) or getattr(job, 'vehicle_model', None) or ''
    vehicle_year  = getattr(job, 'vehicle_year_snapshot', None)  or getattr(job, 'vehicle_year', None)  or ''
    vehicle_text = ' '.join(str(x) for x in [vehicle_year, vehicle_make, vehicle_model] if x).strip() or ''

    issue = (
        getattr(job, 'complaint', None)
        or getattr(job, 'issue_reported', None)
        or getattr(job, 'problem_description', None)
        or ''
    )
    technician = (
        getattr(job, 'technician_name', None)
        or getattr(getattr(job, 'technician', None), 'full_name', None)
        or getattr(getattr(job, 'technician', None), 'name', None)
        or ''
    )
    mileage = getattr(job, 'odometer_in', None) or getattr(job, 'mileage_in', None)

    parts = list(getattr(job, 'parts', None) or [])

    # Money
    def D(v: Any) -> Decimal:
        return _to_decimal(v)

    parts_total = sum((D(getattr(p, 'total', 0)) for p in parts), D(0))
    labour      = D(getattr(job, 'labour_charge', 0))
    grand_total = D(getattr(job, 'total_amount', 0))
    advance     = D(getattr(job, 'advance_paid', 0))

    # additional payments via RepairPayment relation
    additional = D(0)
    try:
        for pay in getattr(job, 'payments', None) or []:
            additional += D(getattr(pay, 'amount', 0))
    except Exception:
        additional = D(0)
    paid_total  = advance + additional
    balance     = max(D(0), grand_total - paid_total)

    # ── Header ───────────────────────────────────────────────────────────────
    head_parts = []
    if show('svc_rcpt_show_store_name', True) and store.get('store_name'):
        head_parts.append(f'<div class="r-store">{_esc(store["store_name"])}</div>')
    if store.get('store_branch'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_branch"])}</div>')
    if show('svc_rcpt_show_phone', True) and store.get('store_phone'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_phone"])}</div>')
    if show('svc_rcpt_show_address', False) and store.get('store_address'):
        head_parts.append(f'<div class="r-tag">{_esc(store["store_address"])}</div>')

    # ── Meta ─────────────────────────────────────────────────────────────────
    meta_rows: list[str] = []
    if show('svc_rcpt_show_job_number', True):
        meta_rows.append(f'<div class="k">Job No</div><div class="v">{_esc(job_no)}</div>')
    if show('svc_rcpt_show_status', True):
        meta_rows.append(f'<div class="k">Status</div><div class="v">{_esc(status)}</div>')
    if show('svc_rcpt_show_date', True):
        when = (date_str + (' ' + time_str if time_str else '')).strip() or '—'
        meta_rows.append(f'<div class="k">Date</div><div class="v">{_esc(when)}</div>')
    if show('svc_rcpt_show_customer', True) and customer_name:
        meta_rows.append(f'<div class="k">Customer</div><div class="v">{_esc(customer_name)}</div>')
    if show('svc_rcpt_show_customer_phone', True) and customer_phone:
        meta_rows.append(f'<div class="k">Phone</div><div class="v">{_esc(customer_phone)}</div>')
    if show('svc_rcpt_show_reg_no', True) and reg_no:
        meta_rows.append(f'<div class="k">Reg No</div><div class="v">{_esc(reg_no)}</div>')
    if show('svc_rcpt_show_vehicle', True) and vehicle_text:
        meta_rows.append(f'<div class="k">Vehicle</div><div class="v">{_esc(vehicle_text)}</div>')
    if show('svc_rcpt_show_mileage', False) and mileage:
        meta_rows.append(f'<div class="k">Mileage</div><div class="v">{_esc(mileage)} km</div>')
    if show('svc_rcpt_show_technician', True) and technician:
        meta_rows.append(f'<div class="k">Technician</div><div class="v">{_esc(technician)}</div>')

    # ── Issue ────────────────────────────────────────────────────────────────
    issue_block = ''
    if show('svc_rcpt_show_issue', True) and issue:
        issue_block = (
            '<div class="r-section">ISSUE / COMPLAINT</div>'
            f'<div class="r-issue">{_esc(issue)}</div>'
            '<hr class="r-rule">'
        )

    # ── Parts table ──────────────────────────────────────────────────────────
    parts_block = ''
    if show('svc_rcpt_show_parts', True):
        rows = []
        for p in parts:
            name = (
                getattr(p, 'part_name', None)
                or getattr(getattr(p, 'product', None), 'name', None)
                or 'Item'
            )
            qty = getattr(p, 'quantity', 1)
            line_total = _fmt_money(getattr(p, 'total', 0))
            rows.append(
                f'<tr><td>{_esc(name)}</td><td class="qty">{_esc(qty)}</td>'
                f'<td class="amt">{currency} {_esc(line_total)}</td></tr>'
            )
        body = ''.join(rows) or '<tr><td colspan=3 class="sub">No parts used</td></tr>'
        parts_block = (
            '<div class="r-section">PARTS / MATERIALS</div>'
            '<table class="r-items">'
            '<thead><tr><th>Item</th><th class="qty">Qty</th><th class="amt">Amount</th></tr></thead>'
            f'<tbody>{body}</tbody></table>'
        )

    # ── Totals ───────────────────────────────────────────────────────────────
    totals: list[str] = []
    if show('svc_rcpt_show_parts_total', True) and parts_total:
        totals.append(f'<div class="row"><span>Parts Total</span><span>{currency} {parts_total:,.2f}</span></div>')
    if show('svc_rcpt_show_labour', True) and labour:
        totals.append(f'<div class="row"><span>Labour</span><span>{currency} {labour:,.2f}</span></div>')
    if show('svc_rcpt_show_grand_total', True):
        totals.append(f'<div class="row grand"><span>Total</span><span>{currency} {grand_total:,.2f}</span></div>')
    if show('svc_rcpt_show_paid', True):
        totals.append(f'<div class="row"><span>Paid</span><span>{currency} {paid_total:,.2f}</span></div>')
    if show('svc_rcpt_show_balance', True):
        totals.append(f'<div class="row muted"><span>Balance Due</span><span>{currency} {balance:,.2f}</span></div>')

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_text = layout.get('svc_rcpt_footer_text') or 'Thank you for choosing us!'
    footer_parts = [f'<div class="thanks">{_esc(footer_text)}</div>']
    if show('svc_rcpt_show_warranty', False):
        warranty = layout.get('svc_rcpt_warranty_text') or 'Warranty: 30 days on parts and labour.'
        footer_parts.append(f'<div class="muted">{_esc(warranty)}</div>')
    if show('svc_rcpt_show_collection_note', False):
        note = layout.get('svc_rcpt_collection_note') or 'Please bring this receipt when collecting.'
        footer_parts.append(f'<div class="muted">{_esc(note)}</div>')
    # Barcode of job number
    if job_no and job_no != '—':
        footer_parts.append(f'<div class="barcode">*{_esc(job_no)}*</div>')
        footer_parts.append(f'<div class="barcode-no">{_esc(job_no)}</div>')

    return (
        _RECEIPT_CSS
        + f'<div class="gms-receipt {paper_class}" data-cpl="{cpl}">'
        + '<div class="r-head">' + ''.join(head_parts) + '</div>'
        + '<div class="r-title">SERVICE RECEIPT</div>'
        + ('<div class="r-meta">' + ''.join(meta_rows) + '</div>' if meta_rows else '')
        + '<hr class="r-rule">'
        + issue_block
        + parts_block
        + '<hr class="r-rule">'
        + ('<div class="r-totals">' + ''.join(totals) + '</div>' if totals else '')
        + '<div class="r-foot">' + ''.join(footer_parts) + '</div>'
        + '</div>'
    )
