"""Central receipt builder.

One engine produces all three receipt types (billing / job / return). Layout
flags from :class:`ReceiptLayoutSettings` and company info from
:class:`CompanyProfile` are applied uniformly so changing settings updates
*every* receipt.

Only the body data differs per receipt type — header, footer, divider style,
column layout and money formatting are shared.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from flask import current_app, render_template
from escpos.printer import Dummy
from pathlib import Path

from printing.models import (
    CompanyProfile,
    PrinterSettings,
    ReceiptLayoutSettings,
    all_settings_snapshot,
)

log = logging.getLogger(__name__)

VALID_TYPES = ("billing", "job", "return")

_TYPE_TITLES = {
    "billing": "SALES RECEIPT",
    "job": "JOB RECEIPT",
    "return": "RETURN RECEIPT",
}


# ---------------------------------------------------------------------------
# money helpers
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value: Any) -> str:
    """Format an amount as ``Rs. 12,345.00``."""
    d = _to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"Rs. {d:,.2f}"


def _qty(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


def _fmt_dt(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y %b %d - %H:%M")
    return str(value)


# ---------------------------------------------------------------------------
# context builders (one per receipt type)
# ---------------------------------------------------------------------------

def _ensure_models():
    """Lazy import to avoid circular imports during app boot."""
    from models import (
        ProductReturn,
        RepairJob,
        Sale,
    )
    return Sale, RepairJob, ProductReturn


def _build_billing_context(source_id: int) -> Dict[str, Any]:
    Sale, _, _ = _ensure_models()
    sale: Optional["Sale"] = Sale.query.get(source_id)
    if not sale:
        raise LookupError(f"Sale #{source_id} not found")

    items: List[Dict[str, Any]] = []
    for it in sale.items:
        name = (it.product.name if it.product else "") or "Item"
        line_total = _to_decimal(it.total)
        items.append(
            {
                "name": name,
                "qty": it.quantity or 0,
                "qty_str": _qty(it.quantity),
                "price": _to_decimal(it.price),
                "price_str": f"{_to_decimal(it.price):,.2f}",
                "value": line_total,
                "value_str": f"{line_total:,.2f}",
            }
        )

    paid_total = sum((_to_decimal(p.amount) for p in sale.payments), Decimal("0"))
    grand = _to_decimal(sale.total_amount)
    balance = grand - paid_total
    if balance < 0:
        balance = Decimal("0")

    return {
        "type": "billing",
        "type_title": _TYPE_TITLES["billing"],
        "doc_number": sale.invoice_number or f"SALE-{sale.id}",
        "doc_label": "Receipt No",
        "datetime": _fmt_dt(sale.sale_date),
        "cashier": (sale.cashier_user.full_name if getattr(sale, "cashier_user", None) else ""),
        "customer_name": sale.customer_name_snapshot
            or (sale.wholesale_customer.name if sale.wholesale_customer else "")
            or (sale.retail_customer.name if sale.retail_customer else "")
            or "",
        "customer_phone": sale.customer_phone_snapshot or "",
        "vehicle": "",
        "items": items,
        "subtotal": _to_decimal(sale.subtotal),
        "discount": _to_decimal(sale.discount),
        "tax": _to_decimal(sale.tax),
        "grand_total": grand,
        "paid": paid_total,
        "balance": balance,
        "outstanding": balance,
        "payment_method": ", ".join(sorted({(p.method or "").title() for p in sale.payments if p.method})),
        "items_count": sum(int(it.quantity or 0) for it in sale.items),
        "extra": {},
    }


def _build_job_context(source_id: int) -> Dict[str, Any]:
    _, RepairJob, _ = _ensure_models()
    job: Optional["RepairJob"] = RepairJob.query.get(source_id)
    if not job:
        raise LookupError(f"Repair job #{source_id} not found")

    items: List[Dict[str, Any]] = []
    for p in job.parts:
        line_total = _to_decimal(p.total)
        items.append(
            {
                "name": p.part_name or "",
                "qty": p.quantity or 0,
                "qty_str": _qty(p.quantity),
                "price": _to_decimal(p.sell_price),
                "price_str": f"{_to_decimal(p.sell_price):,.2f}",
                "value": line_total,
                "value_str": f"{line_total:,.2f}",
            }
        )
    labour = _to_decimal(job.labour_charge)
    if labour > 0:
        items.append(
            {
                "name": "Labour Charge",
                "qty": 1,
                "qty_str": "1",
                "price": labour,
                "price_str": f"{labour:,.2f}",
                "value": labour,
                "value_str": f"{labour:,.2f}",
            }
        )

    parts_total = sum((_to_decimal(p.total) for p in job.parts), Decimal("0"))
    subtotal = parts_total + labour
    grand = _to_decimal(job.total_amount) or subtotal
    advance = _to_decimal(job.advance_paid)
    extra_paid = sum((_to_decimal(p.amount) for p in job.payments), Decimal("0"))
    paid_total = advance + extra_paid
    balance = grand - paid_total
    if balance < 0:
        balance = Decimal("0")

    vehicle = " / ".join(
        bit for bit in [
            (job.vehicle_reg_no or "").strip(),
            " ".join(b for b in [(job.vehicle_make or ""), (job.vehicle_model or "")] if b).strip(),
        ] if bit
    )

    return {
        "type": "job",
        "type_title": _TYPE_TITLES["job"],
        "doc_number": job.job_number,
        "doc_label": "Job No",
        "datetime": _fmt_dt(job.received_date),
        "cashier": (job.technician.full_name if getattr(job, "technician", None) else ""),
        "customer_name": job.customer_name_snapshot or job.customer_name or "",
        "customer_phone": job.customer_phone_snapshot or job.customer_phone or "",
        "vehicle": vehicle,
        "items": items,
        "subtotal": subtotal,
        "discount": Decimal("0"),
        "tax": Decimal("0"),
        "grand_total": grand,
        "paid": paid_total,
        "balance": balance,
        "outstanding": balance,
        "payment_method": "",
        "items_count": sum(int(p.quantity or 0) for p in job.parts),
        "extra": {
            "service_type": job.service_type or "",
            "issue_reported": job.issue_reported or "",
            "diagnosis": job.diagnosis or "",
            "odometer_in": job.odometer_in or "",
        },
    }


def _build_return_context(source_id: int) -> Dict[str, Any]:
    _, _, ProductReturn = _ensure_models()
    ret: Optional["ProductReturn"] = ProductReturn.query.get(source_id)
    if not ret:
        raise LookupError(f"Return #{source_id} not found")

    items: List[Dict[str, Any]] = []
    for it in ret.items:
        name = (it.product.name if it.product else "") or "Item"
        line_total = _to_decimal(it.total)
        items.append(
            {
                "name": name,
                "qty": it.quantity or 0,
                "qty_str": _qty(it.quantity),
                "price": _to_decimal(it.price),
                "price_str": f"{_to_decimal(it.price):,.2f}",
                "value": line_total,
                "value_str": f"{line_total:,.2f}",
            }
        )

    grand = _to_decimal(ret.total_amount)
    refund = _to_decimal(ret.refund_amount)
    return {
        "type": "return",
        "type_title": _TYPE_TITLES["return"],
        "doc_number": ret.return_number,
        "doc_label": "Return No",
        "datetime": _fmt_dt(ret.return_date),
        "cashier": (ret.processor.full_name if getattr(ret, "processor", None) else ""),
        "customer_name": (ret.sale.customer_name_snapshot if ret.sale else "") or "",
        "customer_phone": (ret.sale.customer_phone_snapshot if ret.sale else "") or "",
        "vehicle": "",
        "items": items,
        "subtotal": grand,
        "discount": Decimal("0"),
        "tax": Decimal("0"),
        "grand_total": grand,
        "paid": refund,
        "balance": Decimal("0"),
        "outstanding": Decimal("0"),
        "payment_method": ret.return_type or "refund",
        "items_count": sum(int(it.quantity or 0) for it in ret.items),
        "extra": {
            "original_invoice": ret.sale.invoice_number if ret.sale else "",
            "reason": ret.reason or "",
            "return_type": ret.return_type or "refund",
            "refund_amount": refund,
        },
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def build_receipt_context(receipt_type: str, source_id: int) -> Dict[str, Any]:
    """Return a normalised dict for a single receipt, regardless of type."""
    if receipt_type not in VALID_TYPES:
        raise ValueError(f"Invalid receipt_type {receipt_type!r}")
    try:
        sid = int(source_id)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid source_id {source_id!r}")
    if sid <= 0:
        raise ValueError(f"Invalid source_id {source_id!r}")

    if receipt_type == "billing":
        ctx = _build_billing_context(sid)
    elif receipt_type == "job":
        ctx = _build_job_context(sid)
    else:
        ctx = _build_return_context(sid)

    ctx["money"] = _money
    return ctx


def render_receipt_html(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
    *,
    auto_print: bool = False,
) -> str:
    """Render a receipt as a self-contained HTML page (preview + print)."""
    layout = layout_settings or ReceiptLayoutSettings.load()
    company = company_profile or CompanyProfile.load()
    rendered = render_template(
        "printing/receipt.html",
        ctx=context,
        layout=layout,
        company=company,
        receipt_type=receipt_type,
        auto_print=auto_print,
        money=_money,
    )
    log.info(
        "receipt rendered html type=%s doc=%s items=%d width=%s",
        receipt_type,
        context.get("doc_number"),
        len(context.get("items", [])),
        layout.get("rcpt_layout_paper_width"),
    )
    return rendered


def render_receipt_text(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Plain-text representation suitable for thermal raw print or logs."""
    layout = layout_settings or ReceiptLayoutSettings.load()
    company = company_profile or CompanyProfile.load()
    width = 32 if (layout.get("rcpt_layout_paper_width") == "58mm") else 48

    divider_style = str(layout.get("rcpt_layout_divider_style") or "dashed").strip().lower()
    divider_enabled = divider_style != "none"

    def line(ch: str = "-") -> str:
        if not divider_enabled:
            return ""
        return ch * width

    def append_divider(out: List[str], ch: str = "-") -> None:
        div = line(ch)
        if div:
            out.append(div)

    def center(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        return s.center(width)

    def kv(left: str, right: str) -> str:
        right = right or ""
        space = max(1, width - len(left) - len(right))
        return f"{left}{' ' * space}{right}"

    out: List[str] = []
    if layout.get("rcpt_layout_show_company_name") and company.get("company_name"):
        out.append(center(company["company_name"]))
    if company.get("company_slogan"):
        out.append(center(company["company_slogan"]))
    if layout.get("rcpt_layout_show_address") and company.get("company_address"):
        for chunk in (company["company_address"] or "").split("\n"):
            out.append(center(chunk))
    if layout.get("rcpt_layout_show_phone") and company.get("company_phone"):
        out.append(center(f"Tel: {company['company_phone']}"))
    if layout.get("rcpt_layout_show_email") and company.get("company_email"):
        out.append(center(company["company_email"]))
    append_divider(out)
    out.append(center(context["type_title"]))
    append_divider(out)
    out.append(kv(f"{context['doc_label']}:", context["doc_number"]))
    out.append(kv("Date:", context["datetime"]))
    if layout.get("rcpt_layout_show_cashier") and context.get("cashier"):
        out.append(kv("Cashier:", context["cashier"]))
    if layout.get("rcpt_layout_show_customer") and context.get("customer_name"):
        out.append(kv("Customer:", context["customer_name"]))
    if layout.get("rcpt_layout_show_vehicle") and context.get("vehicle"):
        out.append(kv("Vehicle:", context["vehicle"]))
    append_divider(out)
    if width >= 42:
        out.append(f"{'Item':<22}{'Price':>8}{'Qty':>4}{'Value':>10}"[:width])
        append_divider(out)
        for it in context["items"]:
            name = it["name"]
            for chunk in [name[i:i + 22] for i in range(0, len(name), 22)] or [""]:
                if chunk == name[:22]:
                    out.append(
                        f"{chunk:<22}{it['price_str']:>8}{it['qty_str']:>4}{it['value_str']:>10}"
                    )
                else:
                    out.append(f"  {chunk}")
    else:
        for it in context["items"]:
            out.append(it["name"])
            out.append(kv(
                f"  {it['price_str']} x {it['qty_str']}",
                it["value_str"],
            ))
    append_divider(out)
    if layout.get("rcpt_layout_show_payment_summary"):
        out.append(kv("Sub Total:", _money(context["subtotal"])))
        if context.get("discount"):
            out.append(kv("Discount:", _money(context["discount"])))
        if context.get("tax"):
            out.append(kv("Tax:", _money(context["tax"])))
        out.append(kv("Grand Total:", _money(context["grand_total"])))
        out.append(kv("Total Paid:", _money(context["paid"])))
        out.append(kv("Balance:", _money(context["balance"])))
        append_divider(out)
    if layout.get("rcpt_layout_show_footer"):
        footer = company.get("company_footer_text") or "Thank you!"
        out.append(center(footer))
        if company.get("company_receipt_note"):
            out.append(center(company["company_receipt_note"]))
    return "\n".join(out) + "\n"


# ESC/POS bytes — minimal but real enough to push to a thermal printer.
_ESC = b"\x1b"
_GS = b"\x1d"
_CUT = _GS + b"V" + b"\x00"
_INIT = _ESC + b"@"
_ALIGN_CENTER = _ESC + b"a" + b"\x01"
_ALIGN_LEFT = _ESC + b"a" + b"\x00"
_BOLD_ON = _ESC + b"E" + b"\x01"
_BOLD_OFF = _ESC + b"E" + b"\x00"


def _resolve_logo_path(logo_rel: str) -> Optional[Path]:
    if not logo_rel:
        return None
    # /static/uploads/logos/... -> static/uploads/logos/...
    clean = logo_rel.lstrip("/")
    base = Path(__file__).resolve().parent.parent
    p = base / clean
    return p if p.exists() else None


def _prepare_logo_for_escpos(logo_path: Path, paper_width: str = "80mm") -> Optional[Path]:
    """Resize logo image to fit thermal paper width and return a temp PNG path."""
    import tempfile
    suffix = logo_path.suffix.lower()
    if suffix == ".svg":
        return None  # SVG cannot be rasterised here; skip silently
    try:
        from PIL import Image
        max_px = 384 if paper_width == "80mm" else 256
        img = Image.open(str(logo_path)).convert("RGB")
        if img.width > max_px:
            ratio = max_px / img.width
            img = img.resize((max_px, max(1, int(img.height * ratio))), Image.LANCZOS)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        return Path(tmp.name)
    except Exception as e:
        log.warning("logo prepare failed: %s", e)
        return None


def render_receipt_escpos(
    receipt_type: str,
    context: Dict[str, Any],
    layout_settings: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Generate raw ESC/POS bytes from the same context."""
    layout = layout_settings or ReceiptLayoutSettings.load()
    company = company_profile or CompanyProfile.load()
    paper_width = str(layout.get("rcpt_layout_paper_width") or "80mm")

    payload = _INIT

    # 1. Logo
    if layout.get("rcpt_layout_show_logo") and company.get("company_logo_path"):
        lp = _resolve_logo_path(company["company_logo_path"])
        if lp:
            tmp_logo: Optional[Path] = None
            try:
                tmp_logo = _prepare_logo_for_escpos(lp, paper_width)
                if tmp_logo:
                    p = Dummy()
                    p._raw(_ALIGN_CENTER)
                    p.image(str(tmp_logo))
                    p._raw(_ALIGN_LEFT)
                    payload += p.output
                    payload += b"\n"
            except Exception as e:
                log.warning("escpos logo render failed: %s", e)
            finally:
                if tmp_logo and tmp_logo.exists():
                    try:
                        tmp_logo.unlink()
                    except Exception:
                        pass

    # 2. Text Content
    text = render_receipt_text(receipt_type, context, layout, company)
    payload += _ALIGN_LEFT
    safe = text.encode("cp437", errors="replace")
    payload += safe
    payload += b"\n\n\n"
    if layout.get("rcpt_layout_auto_cut", True):
        payload += _CUT
    return payload


def render_garage_receipt(
    receipt_type: str,
    receipt_data: Dict[str, Any],
    company_profile: Dict[str, Any],
    layout_settings: Dict[str, Any],
    *,
    fmt: str = "html",
) -> Any:
    """Convenience facade — pick output format with a single call."""
    if fmt == "html":
        return render_receipt_html(
            receipt_type, receipt_data, layout_settings, company_profile
        )
    if fmt == "text":
        return render_receipt_text(
            receipt_type, receipt_data, layout_settings, company_profile
        )
    if fmt == "escpos":
        return render_receipt_escpos(
            receipt_type, receipt_data, layout_settings, company_profile
        )
    raise ValueError(f"Unsupported render format: {fmt!r}")


def snapshot_settings() -> Dict[str, Any]:
    """Helper for callers that want everything in one go."""
    return all_settings_snapshot()
