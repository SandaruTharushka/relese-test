"""Service / Job receipt text builder — v3 clean invoice style.

Clean professional layout (no separator lines):

        GARAGE MANAGEMENT
      Automobile & Electronics
          077 XXX XXXX

          SERVICE RECEIPT

Date: 2026-05-02          Time: 12:44 pm
Job No: JOB-20260502-0001  Status: Ready

Customer: Sandaru
Phone: 0788140255
Reg No: ABC-1234
Vehicle: 2014 Bajaj Platina
Technician: Tech Name

ISSUE
  Engine check light on

LABOUR
  Labour Charge         LKR 1,500.00

PARTS / MATERIALS
  Oil Filter x1           LKR 500.00

Parts Total           LKR   500.00
Total                 LKR 2,000.00
Paid                  LKR 1,000.00
Balance               LKR 1,000.00

    Thank you for choosing us!

All visible fields are controlled by svc_rcpt_* layout settings loaded from
StoreSettings.  No hardcoded values in this file.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from services.escpos_layout_engine import EscposLayoutEngine, _clip, _safe, _to_dec, _wrap


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
    """Gather formatted part lines from job.parts / .materials / .items."""
    for source_name in ('parts', 'materials', 'items'):
        source = _extract(job, source_name, default=None)
        if not source:
            continue
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes, dict)):
            continue
        lines: list[str] = []
        for row in source:
            name = _extract(
                row, 'part_name', 'name', 'product_name', 'item_name', 'description',
                default='Item',
            )
            qty = _extract(row, 'qty', 'quantity', default=1)
            total = _extract(
                row, 'total', 'line_total', 'subtotal', 'amount', 'price', default=0,
            )
            try:
                qty_val = float(qty or 0)
                qty_str = str(int(qty_val)) if qty_val == int(qty_val) else str(qty_val)
            except (TypeError, ValueError):
                qty_str = str(qty or '1')
            total_dec = _to_dec(total)
            amount_str = f'{currency} {total_dec:,.2f}'
            label = f'{_safe(name, "Item")} x{qty_str}'
            avail = width - 2
            if len(label) + len(amount_str) + 1 <= avail:
                spaces = avail - len(label) - len(amount_str)
                lines.append(label + ' ' * spaces + amount_str)
            else:
                max_lbl = max(1, avail - len(amount_str) - 1)
                lbl = label[:max_lbl]
                spaces = max(1, avail - len(lbl) - len(amount_str))
                lines.append(lbl + ' ' * spaces + amount_str)
        return lines
    return []


class ServiceReceiptBuilder:
    """Builds a clean invoice-style service/job receipt from a RepairJob record.

    Uses svc_rcpt_* layout settings so every field is configurable.
    Output is a text string with embedded ESC/POS inline tags; pass to
    build_escpos_payload() for final byte payload, or strip tags for preview.
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
        # ── Width / settings ───────────────────────────────────────────────────
        width_raw = layout.get('svc_rcpt_cpl') or layout.get('rcpt_cpl', '48')
        try:
            width = max(24, min(int(width_raw), 96))
        except (TypeError, ValueError):
            width = 48

        show = lambda key, default=True: _as_bool(layout, key, default)
        header_align = layout.get('svc_rcpt_header_align', 'center')

        eng = EscposLayoutEngine(width=width)

        # ── Payment figures ────────────────────────────────────────────────────
        parts_total   = _to_dec(payment_snapshot.get('parts_total', 0))
        labor_total   = _to_dec(payment_snapshot.get('labor_total', payment_snapshot.get('labour_total', 0)))
        grand_total   = _to_dec(payment_snapshot.get('grand_total', payment_snapshot.get('total_amount', 0)))
        paid_total    = _to_dec(payment_snapshot.get('paid_total', payment_snapshot.get('final_paid_amount', 0)))
        balance_total = _to_dec(payment_snapshot.get('balance_total', payment_snapshot.get('remaining_balance', 0)))

        # ── Job fields ─────────────────────────────────────────────────────────
        job_number   = _safe(_extract(job, 'job_number', 'job_no', default=''), '-')
        status       = _safe(_extract(job, 'status', default=''), '-')
        received_at  = _extract(job, 'received_date', 'created_at', 'check_in_date', default=None)
        created_date = (
            received_at.strftime('%Y-%m-%d')
            if getattr(received_at, 'strftime', None)
            else _safe(received_at, '-')
        )
        created_time = (
            received_at.strftime('%I:%M %p').lstrip('0')
            if getattr(received_at, 'strftime', None)
            else ''
        )
        customer_name = _safe(
            _extract(job, 'customer_name_snapshot', 'customer_name', default=None)
            or _extract(_extract(job, 'customer', default=None), 'full_name', 'name', default=None),
            '-',
        )
        customer_phone = _safe(
            _extract(job, 'customer_phone_snapshot', 'customer_phone', default=None)
            or _extract(_extract(job, 'customer', default=None), 'phone', 'mobile', default=None),
            '-',
        )
        reg_no = _safe(
            _extract(job, 'plate_number_snapshot', 'vehicle_reg_no', 'reg_no', 'plate_number', default=None), '-',
        )
        vehicle_make  = _safe(_extract(job, 'vehicle_make_snapshot', 'vehicle_make', 'make', default=None), '')
        vehicle_model = _safe(_extract(job, 'vehicle_model_snapshot', 'vehicle_model', 'model', default=None), '')
        vehicle_year  = _safe(_extract(job, 'vehicle_year_snapshot', 'vehicle_year', 'year', default=None), '')
        vehicle_text  = ' '.join(
            x for x in [vehicle_year, vehicle_make, vehicle_model] if x and x != '-'
        ).strip() or '-'

        issue     = _safe(_extract(job, 'complaint', 'issue', 'issue_reported', 'problem_description', default=None), '-')
        diagnosis = _safe(_extract(job, 'diagnosis', 'diagnosis_text', default=None), '-')
        technician = _safe(
            _extract(job, 'technician_name', default=None)
            or _extract(_extract(job, 'technician', default=None), 'full_name', 'name', default=None),
            '-',
        )
        mileage = _safe(_extract(job, 'mileage_in', 'odometer_in', 'mileage', default=None), '-')

        # ── Store header block (centered, no separators) ───────────────────────
        if show('svc_rcpt_show_store_name') and store.get('store_name'):
            eng.double_height(store['store_name'], align=header_align)
        if store.get('store_branch'):
            if header_align == 'center':
                eng.center(store['store_branch'])
            else:
                eng.left(store['store_branch'])
        if show('svc_rcpt_show_phone') and store.get('store_phone'):
            if header_align == 'center':
                eng.center(store['store_phone'])
            else:
                eng.left(store['store_phone'])
        if show('svc_rcpt_show_address') and store.get('store_address'):
            for addr_line in _wrap(store['store_address'], width):
                if header_align == 'center':
                    eng.center(addr_line)
                else:
                    eng.left(addr_line)

        eng.blank()
        eng.center('SERVICE RECEIPT')
        eng.blank()

        # ── Job meta: Date/Time same row, Job No/Status same row ─────────────
        if show('svc_rcpt_show_date'):
            date_str = f'Date: {created_date}'
            time_str = f'Time: {created_time}' if created_time else ''
            if time_str:
                eng.left_right(date_str, time_str)
            else:
                eng.left(date_str)

        if show('svc_rcpt_show_job_number') and show('svc_rcpt_show_status'):
            job_str = f'Job No: {job_number}'
            status_str = f'Status: {status.replace("_", " ").title()}'
            eng.left_right(job_str, status_str)
        elif show('svc_rcpt_show_job_number'):
            eng.left(f'Job No: {job_number}')
        elif show('svc_rcpt_show_status'):
            eng.left(f'Status: {status.replace("_", " ").title()}')

        eng.blank()

        # ── Customer / Vehicle ─────────────────────────────────────────────────
        if show('svc_rcpt_show_customer'):
            eng.left(f'Customer: {customer_name}')
        if show('svc_rcpt_show_customer_phone'):
            eng.left(f'Phone: {customer_phone}')
        if show('svc_rcpt_show_reg_no'):
            eng.left(f'Reg No: {reg_no}')
        if show('svc_rcpt_show_vehicle'):
            eng.left(f'Vehicle: {vehicle_text}')
        if show('svc_rcpt_show_mileage') and mileage != '-':
            eng.left(f'Mileage: {mileage} km')
        if show('svc_rcpt_show_technician'):
            eng.left(f'Technician: {technician}')

        eng.blank()

        # ── Issue / Diagnosis ──────────────────────────────────────────────────
        if show('svc_rcpt_show_issue'):
            eng.section_header('ISSUE')
            eng.indented(issue)
        if show('svc_rcpt_show_diagnosis', False) and diagnosis != '-':
            eng.section_header('DIAGNOSIS')
            eng.indented(diagnosis)

        eng.blank()

        # ── Labour ────────────────────────────────────────────────────────────
        if show('svc_rcpt_show_labour'):
            eng.section_header('LABOUR')
            eng.indented_pair('Labour Charge', f'{currency} {labor_total:,.2f}')

        eng.blank()

        # ── Parts / Materials ──────────────────────────────────────────────────
        if show('svc_rcpt_show_parts'):
            eng.section_header('PARTS / MATERIALS')
            part_lines = _collect_part_lines(job, width, currency)
            if part_lines:
                for line in part_lines:
                    eng.left(f'  {line}')
            else:
                eng.left('  No items')

        eng.blank()

        # ── Financials (right-aligned, no separator) ───────────────────────────
        if show('svc_rcpt_show_parts_total') and parts_total:
            eng.money_row('Parts Total', f'{currency} {parts_total:,.2f}')
        if show('svc_rcpt_show_grand_total'):
            eng.two_col_bold('Total', f'{currency} {grand_total:,.2f}')
        if show('svc_rcpt_show_paid'):
            eng.money_row('Paid', f'{currency} {paid_total:,.2f}')
        if show('svc_rcpt_show_balance'):
            eng.money_row('Balance', f'{currency} {balance_total:,.2f}')

        eng.blank()

        # ── Warranty / Collection note ─────────────────────────────────────────
        if show('svc_rcpt_show_warranty', False):
            warranty_text = layout.get('svc_rcpt_warranty_text', 'Warranty: 30 days on parts and labour.')
            eng.wrapped(warranty_text, align='center')
        if show('svc_rcpt_show_collection_note', False):
            note = layout.get('svc_rcpt_collection_note', 'Please bring this receipt when collecting.')
            eng.wrapped(note, align='center')

        # ── Footer ─────────────────────────────────────────────────────────────
        footer_text = layout.get('svc_rcpt_footer_text', 'Thank you for choosing us!')
        eng.center(footer_text)

        if show('svc_rcpt_show_job_number') and job_number != '-':
            eng.center(job_number)

        eng.blank(2)

        return eng.as_tagged_text()
