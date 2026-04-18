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


def render_repair_receipt_text(
    job: Any,
    store_name: str,
    store_phone: str = "",
    store_address: str = "",
    payment_snapshot: dict[str, Any] | None = None,
    width: int = THERMAL_WIDTH_80MM,
    currency: str = "LKR",
    footer_message: str = "Thank you. Please visit again.",
) -> str:
    payment_snapshot = payment_snapshot or {}

    job_number = _safe_str(_extract_attr(job, "job_number", "job_no", default="-"))
    status = _safe_str(_extract_attr(job, "status", default="-"))
    created_at = _extract_attr(job, "created_at", "received_date", "check_in_date", "date_created", default=None)
    created_date = created_at.strftime("%Y-%m-%d %H:%M") if getattr(created_at, "strftime", None) else _safe_str(created_at, "-")

    customer_name = _safe_str(
        _extract_attr(job, "customer_name_snapshot", "customer_name", default=None)
        or _extract_attr(_extract_attr(job, "customer", default=None), "full_name", "name", default=None),
        "-"
    )
    customer_phone = _safe_str(
        _extract_attr(job, "customer_phone_snapshot", "customer_phone", default=None)
        or _extract_attr(_extract_attr(job, "customer", default=None), "phone", "mobile", "telephone", default=None),
        "-"
    )

    reg_no = _safe_str(
        _extract_attr(job, "plate_number_snapshot", "vehicle_reg_no", "reg_no", "registration_number", "plate_number", default=None),
        "-"
    )

    make = _safe_str(_extract_attr(job, "vehicle_make_snapshot", "vehicle_make", "make", default=None), "")
    model = _safe_str(_extract_attr(job, "vehicle_model_snapshot", "vehicle_model", "model", default=None), "")
    year = _safe_str(_extract_attr(job, "vehicle_year_snapshot", "vehicle_year", "year", default=None), "")

    vehicle_text = " ".join(x for x in [year, make, model] if x and x != "-").strip() or "-"

    issue = _safe_str(_extract_attr(job, "complaint", "issue", "issue_reported", "problem_description", default=None), "-")
    technician = _safe_str(
        _extract_attr(job, "technician_name", default=None)
        or _extract_attr(_extract_attr(job, "technician", default=None), "full_name", "name", default=None),
        "-"
    )
    mileage = _safe_str(_extract_attr(job, "mileage_in", "odometer_in", "mileage", default=None), "-")

    parts_total = _to_decimal(_extract_attr(job, "parts_total", default=payment_snapshot.get("parts_total", 0)))
    labor_total = _to_decimal(_extract_attr(job, "labor_total", "labour_total", "labour_charge", default=payment_snapshot.get("labor_total", 0)))
    grand_total = _to_decimal(
        _extract_attr(
            job,
            "grand_total",
            "total_amount",
            "total",
            default=payment_snapshot.get("grand_total", payment_snapshot.get("total", 0)),
        )
    )
    paid_total = _to_decimal(payment_snapshot.get("paid_total", payment_snapshot.get("paid", 0)))
    balance_total = _to_decimal(
        payment_snapshot.get("balance_total", payment_snapshot.get("balance", grand_total - paid_total))
    )

    lines: list[str] = []

    lines.append(_center(store_name or "Service Center", width))
    if store_phone:
        lines.append(_center(store_phone, width))
    if store_address:
        for addr_line in _wrap_text(store_address, width):
            lines.append(_center(addr_line, width))

    lines.append(_rule(width))
    lines.append(_pair("Service Receipt", "", width))
    lines.append(_pair("Job No", job_number, width))
    lines.append(_pair("Date", created_date, width))
    lines.append(_pair("Status", status.title(), width))
    lines.append(_rule(width))

    lines.append(_pair("Customer", customer_name, width))
    lines.append(_pair("Phone", customer_phone, width))
    lines.append(_pair("Reg No", reg_no, width))
    lines.append(_pair("Vehicle", _clean_line(vehicle_text, width - 9), width))
    lines.append(_pair("Mileage", mileage, width))
    lines.append(_pair("Technician", technician, width))
    lines.append(_rule(width))

    lines.append("Issue")
    lines.extend(_wrap_text(issue, width))
    lines.append(_rule(width))

    item_lines = _collect_part_lines(job, width, currency)
    if item_lines:
        lines.append("Parts / Materials")
        lines.extend(item_lines)
        lines.append(_rule(width))

    lines.append(_pair("Parts Total", _money(parts_total, currency), width))
    lines.append(_pair("Labour", _money(labor_total, currency), width))
    lines.append(_rule(width))
    lines.append(_pair("TOTAL", _money(grand_total, currency), width))
    lines.append(_pair("PAID", _money(paid_total, currency), width))
    lines.append(_pair("BALANCE", _money(balance_total, currency), width))
    lines.append(_rule(width))

    for msg_line in _wrap_text(footer_message, width):
        lines.append(_center(msg_line, width))

    lines.append("")
    lines.append("")
    text = "\n".join(lines)
    return text + "\x1d\x56\x00"
