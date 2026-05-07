"""Smoke tests + comprehensive QA tests for the central printer + receipt system."""
from __future__ import annotations

import pytest
from unittest.mock import patch

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
from printing import service as printing_service


@pytest.fixture
def app_ctx():
    with appmod.app.test_request_context():
        yield


@pytest.fixture()
def db_app_ctx(flask_app):
    """Fresh in-memory DB with all tables; no users seeded."""
    from models import db
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        yield flask_app


@pytest.fixture()
def mock_printer_ready():
    """Patch validate_printer_ready to return an online printer status."""
    ready = {
        "can_print": True, "status": "online", "message": "Ready",
        "driver_installed": True, "connected": True, "name": "DEMO-PRINTER",
    }
    with patch("printing.service.validate_printer_ready", return_value=ready):
        yield ready


@pytest.fixture()
def mock_send_raw_ok():
    """Patch send_raw to simulate a successful raw print dispatch."""
    result = {"ok": True, "msg": "Sent 512 bytes", "bytes_written": 512}
    with patch("printing.service.send_raw", return_value=result):
        yield result


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
# settings facade — validation (error paths)
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
# settings facade — happy-path save/load round-trips (Group A)
# ---------------------------------------------------------------------------

def test_printer_settings_load_returns_defaults(db_app_ctx):
    result = PrinterSettings.load()
    assert "printer_default_paper_width" in result
    assert result["printer_default_paper_width"] == "80mm"
    assert result["printer_print_mode"] == "windows_raw"
    assert result["printer_auto_detect_enabled"] is True
    assert result["printer_is_enabled"] is True
    assert result["printer_receipt_name"] == ""
    assert len(result) == len(PrinterSettings.DEFAULTS)


def test_printer_settings_save_and_load_roundtrip(db_app_ctx):
    saved = PrinterSettings.save({
        "printer_receipt_name": "EPSON-TM88",
        "printer_default_paper_width": "58mm",
        "printer_print_mode": "escpos",
        "printer_is_enabled": False,
    })
    assert saved == 4
    loaded = PrinterSettings.load()
    assert loaded["printer_receipt_name"] == "EPSON-TM88"
    assert loaded["printer_default_paper_width"] == "58mm"
    assert loaded["printer_print_mode"] == "escpos"
    assert loaded["printer_is_enabled"] is False


def test_printer_settings_save_valid_connection_types(db_app_ctx):
    for ct in PrinterSettings.CONNECTION_TYPES:
        count = PrinterSettings.save({"printer_connection_type": ct})
        assert count == 1


def test_receipt_layout_save_and_load_roundtrip(db_app_ctx):
    saved = ReceiptLayoutSettings.save({
        "rcpt_layout_paper_width": "58mm",
        "rcpt_layout_show_footer": False,
        "rcpt_layout_header_alignment": "right",
        "rcpt_layout_font_size": 10,
    })
    assert saved == 4
    loaded = ReceiptLayoutSettings.load()
    assert loaded["rcpt_layout_paper_width"] == "58mm"
    assert loaded["rcpt_layout_show_footer"] is False
    assert loaded["rcpt_layout_header_alignment"] == "right"
    assert loaded["rcpt_layout_font_size"] == 10
    assert isinstance(loaded["rcpt_layout_font_size"], int)
    assert isinstance(loaded["rcpt_layout_show_footer"], bool)


def test_receipt_layout_reset_removes_saved_keys(db_app_ctx):
    ReceiptLayoutSettings.save({
        "rcpt_layout_paper_width": "58mm",
        "rcpt_layout_show_footer": False,
        "rcpt_layout_divider_style": "solid",
    })
    reset_count = ReceiptLayoutSettings.reset()
    assert reset_count >= 1
    loaded = ReceiptLayoutSettings.load()
    assert loaded["rcpt_layout_paper_width"] == "80mm"
    assert loaded["rcpt_layout_show_footer"] is True


def test_company_profile_save_and_load_roundtrip(db_app_ctx):
    saved = CompanyProfile.save({
        "company_name": "Perera AutoWorks",
        "company_phone": "011-1234567",
        "company_footer_text": "Thank you!",
    })
    assert saved == 3
    loaded = CompanyProfile.load()
    assert loaded["company_name"] == "Perera AutoWorks"
    assert loaded["company_phone"] == "011-1234567"
    assert loaded["company_footer_text"] == "Thank you!"


def test_company_profile_save_returns_zero_for_unknown_keys(db_app_ctx):
    result = CompanyProfile.save({"totally_unknown_key": "foo"})
    assert result == 0


def test_company_profile_legacy_fallback(db_app_ctx):
    from models import StoreSettings, db
    StoreSettings.set_many({"store_name": "LegacyGarage"})
    db.session.commit()
    loaded = CompanyProfile.load()
    assert loaded["company_name"] == "LegacyGarage"


# ---------------------------------------------------------------------------
# service layer (Group B)
# ---------------------------------------------------------------------------

def test_list_available_printers_returns_dict_shape(db_app_ctx):
    result = printing_service.list_available_printers()
    assert result["ok"] is True
    assert isinstance(result["printers"], list)
    assert isinstance(result["count"], int)
    assert isinstance(result["online_count"], int)


def test_validate_printer_exists_with_none_returns_not_ok(db_app_ctx):
    result = printing_service.validate_printer_exists(None)
    assert result["ok"] is False
    assert "No printer selected" in result["msg"]


def test_validate_printer_exists_with_empty_string_returns_not_ok(db_app_ctx):
    result = printing_service.validate_printer_exists("")
    assert result["ok"] is False
    assert "No printer selected" in result["msg"]


def test_validate_printer_exists_with_fake_name_returns_not_ok(db_app_ctx):
    result = printing_service.validate_printer_exists("DefinitelyNotARealPrinterName_xyz123")
    assert result["ok"] is False


def test_print_receipt_when_printing_disabled(db_app_ctx):
    PrinterSettings.save({
        "printer_is_enabled": False,
        "printer_receipt_name": "SOME-PRINTER",
    })
    with patch("printing.service.validate_printer_ready") as mock_vpr:
        mock_vpr.side_effect = AssertionError("should not be reached")
        result = printing_service.print_receipt("billing", 1)
    assert result["ok"] is False
    assert "disabled" in result["msg"].lower()


def test_print_receipt_when_no_printer_selected(db_app_ctx):
    PrinterSettings.save({
        "printer_is_enabled": True,
        "printer_receipt_name": "",
    })
    result = printing_service.print_receipt("billing", 1)
    assert result["ok"] is False
    assert "No receipt printer selected" in result["msg"]


def test_print_receipt_when_printer_not_ready(db_app_ctx):
    PrinterSettings.save({
        "printer_is_enabled": True,
        "printer_receipt_name": "FAKE-PRINTER",
        "printer_print_mode": "windows_raw",
    })
    not_ready = {
        "can_print": False, "status": "offline",
        "message": "Port unreachable", "connected": False,
    }
    with patch("printing.service.validate_printer_ready", return_value=not_ready):
        result = printing_service.print_receipt("billing", 1)
    assert result["ok"] is False


def test_test_receipt_print_no_printer_selected(db_app_ctx):
    PrinterSettings.save({"printer_receipt_name": ""})
    result = printing_service.test_receipt_print()
    assert result["ok"] is False
    assert "No receipt printer selected" in result["msg"]


def test_test_label_print_no_printer_selected(db_app_ctx):
    PrinterSettings.save({"printer_label_name": ""})
    result = printing_service.test_label_print()
    assert result["ok"] is False
    assert "No label printer selected" in result["msg"]


def test_test_receipt_print_with_printer_ready(db_app_ctx, mock_printer_ready, mock_send_raw_ok):
    PrinterSettings.save({
        "printer_receipt_name": "DEMO-PRINTER",
        "printer_print_mode": "windows_raw",
        "printer_is_enabled": True,
    })
    result = printing_service.test_receipt_print()
    assert result["ok"] is True
    assert result["printer_name"] == "DEMO-PRINTER"
    assert "renderer_used" in result


# ---------------------------------------------------------------------------
# API endpoint integration (Group C)
# ---------------------------------------------------------------------------

def test_api_printers_list_returns_ok(auth_client):
    resp = auth_client.get("/api/settings/printers/list")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert isinstance(data["printers"], list)
    assert "count" in data
    assert "online_count" in data


def test_api_printers_status_no_printer_selected(auth_client):
    resp = auth_client.get("/api/settings/printers/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "ok" in data


def test_api_printers_save_valid_settings(auth_client):
    payload = {
        "printer_default_paper_width": "80mm",
        "printer_print_mode": "html",
    }
    resp = auth_client.post("/api/settings/printers/save", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["saved"] == 2


def test_api_printers_save_invalid_paper_width(auth_client):
    resp = auth_client.post(
        "/api/settings/printers/save",
        json={"printer_default_paper_width": "99mm"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "paper width" in data["msg"].lower()


def test_api_layout_get_returns_all_defaults(auth_client):
    resp = auth_client.get("/api/settings/receipt-layout")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    layout = data["layout"]
    for key in ReceiptLayoutSettings.DEFAULTS:
        assert key in layout, f"Missing layout key: {key}"


def test_api_layout_save_valid_payload(auth_client):
    resp = auth_client.post(
        "/api/settings/receipt-layout/save",
        json={"rcpt_layout_paper_width": "58mm", "rcpt_layout_show_footer": False},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_api_layout_save_invalid_enum_returns_400(auth_client):
    resp = auth_client.post(
        "/api/settings/receipt-layout/save",
        json={"rcpt_layout_divider_style": "sparkles"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_layout_reset_restores_defaults(auth_client):
    auth_client.post(
        "/api/settings/receipt-layout/save",
        json={"rcpt_layout_paper_width": "58mm"},
    )
    resp = auth_client.post("/api/settings/receipt-layout/reset")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["layout"]["rcpt_layout_paper_width"] == "80mm"


def test_api_company_get_returns_all_keys(auth_client):
    resp = auth_client.get("/api/settings/company-intro")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    for key in CompanyProfile.DEFAULTS:
        assert key in data["company"], f"Missing company key: {key}"


def test_api_company_save_json_payload(auth_client):
    payload = {"company_name": "Test Motors", "company_phone": "0771234567"}
    resp = auth_client.post("/api/settings/company-intro/save", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["company"]["company_name"] == "Test Motors"


def test_api_layout_preview_returns_html(auth_client):
    resp = auth_client.post(
        "/api/settings/receipt-layout/preview",
        json={"type": "billing"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "<html" in data["html"].lower()


def test_api_layout_preview_invalid_type_returns_400(auth_client):
    resp = auth_client.post(
        "/api/settings/receipt-layout/preview",
        json={"type": "bogus_type"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_printers_test_no_printer_returns_400(auth_client):
    resp = auth_client.post("/api/settings/printers/test", json={"kind": "receipt"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False


@pytest.mark.parametrize("url,method", [
    ("/api/settings/printers/list", "GET"),
    ("/api/settings/receipt-layout", "GET"),
    ("/api/settings/company-intro", "GET"),
])
def test_api_unauthenticated_requests_redirect(client, url, method):
    resp = client.open(url, method=method)
    assert resp.status_code in (302, 401)


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


# ---------------------------------------------------------------------------
# receipt rendering — content flags (Group D)
# ---------------------------------------------------------------------------

def test_render_text_includes_company_name(app_ctx):
    ctx = _sample_context("billing")
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_company_name"] = True
    company = CompanyProfile.load()
    company["company_name"] = "SUPERMART_MOTORS_QA"
    text = render_receipt_text("billing", ctx, layout, company)
    assert "SUPERMART_MOTORS_QA" in text


def test_render_text_hides_company_name_when_flag_false(app_ctx):
    ctx = _sample_context("billing")
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_company_name"] = False
    company = CompanyProfile.load()
    company["company_name"] = "HIDDEN_CORP_XYZ"
    text = render_receipt_text("billing", ctx, layout, company)
    assert "HIDDEN_CORP_XYZ" not in text


def test_render_text_hides_customer_when_flag_false(app_ctx):
    ctx = _sample_context("billing")
    ctx["customer_name"] = "UNIQUE_CUSTOMER_TOKEN_QA"
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_customer"] = False
    company = CompanyProfile.load()
    text = render_receipt_text("billing", ctx, layout, company)
    assert "UNIQUE_CUSTOMER_TOKEN_QA" not in text


def test_render_text_shows_customer_when_flag_true(app_ctx):
    ctx = _sample_context("billing")
    ctx["customer_name"] = "VISIBLE_CUSTOMER_TOKEN_QA"
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_customer"] = True
    company = CompanyProfile.load()
    text = render_receipt_text("billing", ctx, layout, company)
    assert "VISIBLE_CUSTOMER_TOKEN_QA" in text


def test_render_html_includes_company_name(app_ctx):
    ctx = _sample_context("job")
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_company_name"] = True
    company = CompanyProfile.load()
    company["company_name"] = "HTML_GARAGE_TEST_QA"
    html = render_receipt_html("job", ctx, layout, company)
    assert "HTML_GARAGE_TEST_QA" in html


def test_render_escpos_contains_company_name_bytes(app_ctx):
    ctx = _sample_context("billing")
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_company_name"] = True
    company = CompanyProfile.load()
    company["company_name"] = "TestGarageESCPOS"
    raw = render_receipt_escpos("billing", ctx, layout, company)
    assert b"TestGarageESCPOS" in raw


def test_render_text_no_divider_when_style_none(app_ctx):
    ctx = _sample_context("billing")
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_divider_style"] = "none"
    company = CompanyProfile.load()
    text = render_receipt_text("billing", ctx, layout, company)
    assert "---" not in text
    assert "===" not in text


@pytest.mark.parametrize("rtype", VALID_TYPES)
def test_all_receipt_types_render_doc_number_in_escpos(app_ctx, rtype):
    ctx = _sample_context(rtype)
    layout = ReceiptLayoutSettings.load()
    company = CompanyProfile.load()
    raw = render_receipt_escpos(rtype, ctx, layout, company)
    assert ctx["doc_number"].encode() in raw


def test_render_html_job_includes_vehicle(app_ctx):
    ctx = _sample_context("job")
    ctx["vehicle"] = "CTI-204888 / Toyota Axio QA"
    layout = ReceiptLayoutSettings.load()
    layout["rcpt_layout_show_vehicle"] = True
    company = CompanyProfile.load()
    html = render_receipt_html("job", ctx, layout, company)
    assert "CTI-204888" in html
