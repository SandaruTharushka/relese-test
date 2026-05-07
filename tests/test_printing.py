"""Smoke tests for the central printer + receipt system."""
from __future__ import annotations

import pytest

import app as appmod
from printing.models import (
    CompanyProfile,
    PrinterSettings,
    ReceiptLayoutSettings,
)
from printing.printer_detector import (
    get_printer_status,
    list_printers_with_status,
)
from printing.receipt_engine import (
    VALID_TYPES,
    render_receipt_escpos,
    render_receipt_html,
    render_receipt_text,
)
from printing.routes import _sample_context


@pytest.fixture
def app_ctx():
    with appmod.app.test_request_context():
        yield


# ---------------------------------------------------------------------------
# routes registered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    "/settings/printers",
    "/settings/receipt-layout",
    "/settings/company-intro",
    "/api/settings/printers/list",
    "/api/settings/printers/status",
    "/api/settings/printers/save",
    "/api/settings/printers/test",
    "/api/settings/receipt-layout",
    "/api/settings/receipt-layout/save",
    "/api/settings/receipt-layout/preview",
    "/api/settings/receipt-layout/reset",
    "/api/settings/company-intro",
    "/api/settings/company-intro/save",
    "/api/receipts/billing/<int:source_id>/preview",
    "/api/receipts/billing/<int:source_id>/print",
    "/api/receipts/job/<int:source_id>/preview",
    "/api/receipts/job/<int:source_id>/print",
    "/api/receipts/return/<int:source_id>/preview",
    "/api/receipts/return/<int:source_id>/print",
    "/repairs/<int:source_id>/receipt",
])
def test_canonical_routes_registered(rule):
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    assert rule in rules, f"missing canonical route: {rule}"


# ---------------------------------------------------------------------------
# settings facade
# ---------------------------------------------------------------------------

def test_company_profile_load_returns_full_dict(app_ctx):
    company = CompanyProfile.load()
    for key in CompanyProfile.DEFAULTS:
        assert key in company


def test_printer_settings_validates_paper_width(app_ctx):
    with pytest.raises(ValueError):
        PrinterSettings.save({"printer_default_paper_width": "70mm"})


def test_printer_settings_validates_print_mode(app_ctx):
    with pytest.raises(ValueError):
        PrinterSettings.save({"printer_print_mode": "telepathy"})


def test_layout_settings_validates_enums(app_ctx):
    with pytest.raises(ValueError):
        ReceiptLayoutSettings.save({"rcpt_layout_paper_width": "100mm"})
    with pytest.raises(ValueError):
        ReceiptLayoutSettings.save({"rcpt_layout_divider_style": "wavy"})


def test_layout_settings_font_size_bounds(app_ctx):
    with pytest.raises(ValueError):
        ReceiptLayoutSettings.save({"rcpt_layout_font_size": 4})
    with pytest.raises(ValueError):
        ReceiptLayoutSettings.save({"rcpt_layout_font_size": 99})


# ---------------------------------------------------------------------------
# printer detection
# ---------------------------------------------------------------------------

def test_get_printer_status_empty():
    info = get_printer_status("")
    assert info["status"] == "unknown"
    assert info["can_print"] is False


def test_get_printer_status_unknown_name():
    info = get_printer_status("DefinitelyNotARealPrinterName_xyz123")
    assert info["status"] in ("offline", "unknown")
    assert info["can_print"] is False


def test_list_printers_returns_list():
    result = list_printers_with_status()
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# receipt rendering — every type uses the same engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rtype", VALID_TYPES)
def test_render_html_for_all_types(app_ctx, rtype):
    ctx = _sample_context(rtype)
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    company["company_name"] = "Test Garage"
    html = render_receipt_html(rtype, ctx, layout, company)
    assert "<html" in html.lower()
    assert ctx["doc_number"] in html
    assert ctx["type_title"] in html


@pytest.mark.parametrize("rtype", VALID_TYPES)
def test_render_text_includes_totals(app_ctx, rtype):
    ctx = _sample_context(rtype)
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    text = render_receipt_text(rtype, ctx, layout, company)
    assert "Grand Total" in text
    assert "Rs." in text


@pytest.mark.parametrize("rtype", VALID_TYPES)
def test_render_escpos_returns_bytes(app_ctx, rtype):
    ctx = _sample_context(rtype)
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    raw = render_receipt_escpos(rtype, ctx, layout, company)
    assert isinstance(raw, bytes)
    assert raw.startswith(b"\x1b@")  # ESC @ initialise
    assert b"\x1dV\x00" in raw  # GS V cut


def test_layout_paper_width_drives_text_width(app_ctx):
    company = CompanyProfile.load()
    ctx = _sample_context("billing")
    layout_80 = ReceiptLayoutSettings.load()
    layout_80["rcpt_layout_paper_width"] = "80mm"
    layout_58 = dict(layout_80)
    layout_58["rcpt_layout_paper_width"] = "58mm"
    text_80 = render_receipt_text("billing", ctx, layout_80, company)
    text_58 = render_receipt_text("billing", ctx, layout_58, company)
    # 80mm uses ~48 chars wide, 58mm ~32. Lines should differ in max length.
    max_80 = max(len(line) for line in text_80.splitlines())
    max_58 = max(len(line) for line in text_58.splitlines())
    assert max_80 > max_58


def test_layout_change_affects_all_three_receipt_types(app_ctx):
    """Hide the footer once → all three receipt types lose it."""
    company = CompanyProfile.load()
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_footer"] = False
    company["company_footer_text"] = "FOOTER_TOKEN_XYZ"
    for rtype in VALID_TYPES:
        ctx = _sample_context(rtype)
        text = render_receipt_text(rtype, ctx, layout, company)
        html = render_receipt_html(rtype, ctx, layout, company)
        assert "FOOTER_TOKEN_XYZ" not in text
        assert "FOOTER_TOKEN_XYZ" not in html
