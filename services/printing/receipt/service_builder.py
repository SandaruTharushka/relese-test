"""Service / Job receipt text builder.

Converts a RepairJob ORM object + payment snapshot + store settings +
service receipt layout settings into a formatted thermal receipt text
string ready for ESC/POS dispatch.

This replaces the old render_repair_receipt_text() in receipt_renderer.py
and the dead _build_service_receipt_text() in repair_routes.py.
The output is now fully governed by service receipt layout settings.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from services.printing.receipt.formatter import (
    center,
    clean_line,
    cpl_from_settings,
    money,
    pair,
    rule,
    safe_str,
    to_decimal,
    wrap_text,
)


def _as_bool(layout: dict[str, Any], key: str, default: bool = True) -> bool:
    raw = layout.get(key, 'true' if default else 'false')
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _extract(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            v = obj[name]
            if v is not None:
                return v
        if hasattr(obj, name):
            v = getattr(obj, name)
            if v is not None:
                return v
    return default


def _collect_part_lines(job: Any, width: int, currency: str) -> list[str]:
    """Gather lines for parts/materials from job object."""
    for source_name in ('parts', 'materials', 'items'):
        source = _extract(job, source_name, default=None)
        if not source:
            continue
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes, dict)):
            continue
        part_lines: list[str] = []
        for row in source:
            name = _extract(row, 'part_name', 'name', 'product_name', 'item_name', 'description', default='Item')
            qty = _extract(row, 'qty', 'quantity', default=1)
            total = _extract(row, 'total', 'line_total', 'subtotal', 'amount', 'price', default=0)
            from services.printing.receipt.formatter import qty_amount_line
            part_lines.extend(qty_amount_line(str(name), qty, total, width, currency))
        return part_lines
    return []


class ServiceReceiptBuilder:
    """Builds a thermal-printable service/job receipt from a RepairJob record.

    Uses service receipt layout settings (svc_rcpt_* keys) so every
    field is configurable — no more hardcoded widths or visibility flags.
    """

    def build(
        self,
        *,
        job: Any,
        payment_snapshot: dict[str, Any],
        store: dict[str, str],
        layout: dict[str, str],
        currency: str = 'LKR',
    ) -> str:
        """Return formatted service receipt text for thermal printing."""
        # Derive width from svc_rcpt_cpl (service layout key), fallback rcpt_cpl
        width_raw = layout.get('svc_rcpt_cpl') or layout.get('rcpt_cpl', '48')
        try:
            width = max(24, min(int(width_raw), 96))
        except (TypeError, ValueError):
            width = 48

        show = lambda key, default=True: _as_bool(layout, key, default)
        header_align = layout.get('svc_rcpt_header_align', 'center')

        def _hdr(text: str) -> str:
            return center(text, width) if header_align != 'left' else text

        # ── Payment figures ───────────────────────────────────────
        parts_total = to_decimal(payment_snapshot.get('parts_total', 0))
        labor_total = to_decimal(payment_snapshot.get('labor_total', payment_snapshot.get('labour_total', 0)))
        grand_total = to_decimal(payment_snapshot.get('grand_total', payment_snapshot.get('total_amount', 0)))
        paid_total = to_decimal(payment_snapshot.get('paid_total', payment_snapshot.get('final_paid_amount', 0)))
        balance_total = to_decimal(payment_snapshot.get('balance_total', payment_snapshot.get('remaining_balance', 0)))

        # ── Job fields ────────────────────────────────────────────
        job_number = safe_str(_extract(job, 'job_number', 'job_no', default=''), '-')
        status = safe_str(_extract(job, 'status', default=''), '-')
        received_at = _extract(job, 'received_date', 'created_at', 'check_in_date', default=None)
        created_date = received_at.strftime('%Y-%m-%d %H:%M') if getattr(received_at, 'strftime', None) else safe_str(received_at, '-')

        customer_name = safe_str(
            _extract(job, 'customer_name_snapshot', 'customer_name', default=None)
            or _extract(_extract(job, 'customer', default=None), 'full_name', 'name', default=None),
            '-',
        )
        customer_phone = safe_str(
            _extract(job, 'customer_phone_snapshot', 'customer_phone', default=None)
            or _extract(_extract(job, 'customer', default=None), 'phone', 'mobile', default=None),
            '-',
        )

        reg_no = safe_str(_extract(job, 'plate_number_snapshot', 'vehicle_reg_no', 'reg_no', 'plate_number', default=None), '-')
        vehicle_make = safe_str(_extract(job, 'vehicle_make_snapshot', 'vehicle_make', 'make', default=None), '')
        vehicle_model = safe_str(_extract(job, 'vehicle_model_snapshot', 'vehicle_model', 'model', default=None), '')
        vehicle_year = safe_str(_extract(job, 'vehicle_year_snapshot', 'vehicle_year', 'year', default=None), '')
        vehicle_text = ' '.join(x for x in [vehicle_year, vehicle_make, vehicle_model] if x and x != '-').strip() or '-'

        issue = safe_str(_extract(job, 'complaint', 'issue', 'issue_reported', 'problem_description', default=None), '-')
        diagnosis = safe_str(_extract(job, 'diagnosis', 'diagnosis_text', default=None), '-')
        technician = safe_str(
            _extract(job, 'technician_name', default=None)
            or _extract(_extract(job, 'technician', default=None), 'full_name', 'name', default=None),
            '-',
        )
        mileage = safe_str(_extract(job, 'mileage_in', 'odometer_in', 'mileage', default=None), '-')

        lines: list[str] = []

        # ── Store header ──────────────────────────────────────────
        if show('svc_rcpt_show_store_name'):
            lines.append(_hdr(store.get('store_name', 'Service Centre')))
        if show('svc_rcpt_show_phone') and store.get('store_phone'):
            lines.append(_hdr(store['store_phone']))
        if show('svc_rcpt_show_address') and store.get('store_address'):
            for addr_line in wrap_text(store['store_address'], width):
                lines.append(_hdr(addr_line))

        lines.append(rule(width, '='))
        lines.append(_hdr('SERVICE RECEIPT'))
        lines.append(rule(width))

        # ── Job info ──────────────────────────────────────────────
        if show('svc_rcpt_show_job_number'):
            lines.append(pair('Job No', job_number, width))
        if show('svc_rcpt_show_date'):
            lines.append(pair('Date', created_date, width))
        if show('svc_rcpt_show_status'):
            lines.append(pair('Status', status.replace('_', ' ').title(), width))

        lines.append(rule(width))

        # ── Customer / Vehicle ────────────────────────────────────
        if show('svc_rcpt_show_customer'):
            lines.append(pair('Customer', customer_name, width))
        if show('svc_rcpt_show_customer_phone'):
            lines.append(pair('Phone', customer_phone, width))
        if show('svc_rcpt_show_reg_no'):
            lines.append(pair('Reg No', reg_no, width))
        if show('svc_rcpt_show_vehicle'):
            lines.append(pair('Vehicle', clean_line(vehicle_text, width - 9), width))
        if show('svc_rcpt_show_mileage') and mileage != '-':
            lines.append(pair('Mileage', f'{mileage} km', width))
        if show('svc_rcpt_show_technician'):
            lines.append(pair('Technician', technician, width))

        lines.append(rule(width))

        # ── Issue / Diagnosis ─────────────────────────────────────
        if show('svc_rcpt_show_issue'):
            lines.append('Issue:')
            for il in wrap_text(issue, width):
                lines.append(f'  {il}')
        if show('svc_rcpt_show_diagnosis', False) and diagnosis != '-':
            lines.append('Diagnosis:')
            for dl in wrap_text(diagnosis, width):
                lines.append(f'  {dl}')

        lines.append(rule(width))

        # ── Parts ─────────────────────────────────────────────────
        if show('svc_rcpt_show_parts'):
            item_lines = _collect_part_lines(job, width, currency)
            if item_lines:
                lines.append('Parts / Materials:')
                lines.extend(item_lines)
                lines.append(rule(width))

        # ── Financials ────────────────────────────────────────────
        if show('svc_rcpt_show_parts_total') and parts_total:
            lines.append(pair('Parts', money(parts_total, currency), width))
        if show('svc_rcpt_show_labour'):
            lines.append(pair('Labour', money(labor_total, currency), width))
        lines.append(rule(width))
        if show('svc_rcpt_show_grand_total'):
            lines.append(pair('TOTAL', money(grand_total, currency), width))
        if show('svc_rcpt_show_paid'):
            lines.append(pair('PAID', money(paid_total, currency), width))
        if show('svc_rcpt_show_balance'):
            lines.append(pair('BALANCE', money(balance_total, currency), width))

        lines.append(rule(width, '='))

        # ── Warranty / Collection ────────────────────────────────
        if show('svc_rcpt_show_warranty', False):
            warranty_text = layout.get('svc_rcpt_warranty_text', 'Warranty: 30 days on parts and labour.')
            for wl in wrap_text(warranty_text, width):
                lines.append(center(wl, width))
        if show('svc_rcpt_show_collection_note', False):
            note = layout.get('svc_rcpt_collection_note', 'Please bring this receipt when collecting your vehicle.')
            for nl in wrap_text(note, width):
                lines.append(center(nl, width))

        # ── Footer ────────────────────────────────────────────────
        footer_text = layout.get('svc_rcpt_footer_text', 'Thank you for choosing us!')
        for fl in wrap_text(footer_text, width):
            lines.append(center(fl, width))

        lines.append('')
        lines.append('')
        text = '\n'.join(lines)
        # Append ESC/POS cut command
        return text + '\x1d\x56\x00'
