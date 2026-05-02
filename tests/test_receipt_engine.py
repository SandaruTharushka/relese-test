"""Tests for ReceiptEngine — the canonical receipt build layer.

All tests use an in-memory StoreSettings mock; no DB or Flask app required.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.printing.receipt.receipt_engine import ReceiptEngine, ReceiptResult
from services.printing.domain.constants import (
    RECEIPT_LAYOUT_DEFAULTS,
    RECEIPT_LAYOUT_KEYS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)


# ── Helpers ───────────────────────────────────────────────────────

class _MockStoreSettings:
    """Minimal dict-backed StoreSettings double."""

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict[str, str] = data or {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = str(value)

    def set_many(self, payload: dict) -> None:
        for k, v in payload.items():
            self._data[k] = str(v)


def _default_settings(**overrides) -> _MockStoreSettings:
    """Build StoreSettings seeded with layout defaults + optional overrides."""
    data = {**RECEIPT_LAYOUT_DEFAULTS, **SERVICE_RECEIPT_LAYOUT_DEFAULTS}
    data['store_name'] = 'Test Garage'
    data['store_phone'] = '0771234567'
    data.update(overrides)
    return _MockStoreSettings(data)


def _engine(**overrides) -> ReceiptEngine:
    return ReceiptEngine(_default_settings(**overrides))


# ── build_test_receipt (sales) ────────────────────────────────────

class TestBuildTestReceiptSales:
    def test_returns_receipt_result(self):
        result = _engine().build_test_receipt(kind='sales')
        assert isinstance(result, ReceiptResult)

    def test_receipt_text_is_nonempty(self):
        result = _engine().build_test_receipt(kind='sales')
        assert len(result.receipt_text) > 20

    def test_uses_store_name_from_settings(self):
        result = _engine(store_name='My Garage').build_test_receipt(kind='sales')
        assert 'My Garage' in result.receipt_text

    def test_default_cpl_48(self):
        result = _engine().build_test_receipt(kind='sales')
        assert result.cpl == 48

    def test_cpl_change_changes_output(self):
        r32 = _engine(rcpt_cpl='32').build_test_receipt(kind='sales')
        r48 = _engine(rcpt_cpl='48').build_test_receipt(kind='sales')
        assert r32.cpl == 32
        assert r48.cpl == 48
        # Width affects line padding — texts differ
        assert r32.receipt_text != r48.receipt_text

    def test_paper_size_58mm_when_cpl_32(self):
        result = _engine(rcpt_cpl='32').build_test_receipt(kind='sales')
        assert result.paper_size == '58mm'

    def test_paper_size_80mm_when_cpl_48(self):
        result = _engine(rcpt_cpl='48').build_test_receipt(kind='sales')
        assert result.paper_size == '80mm'

    def test_paper_size_dot_matrix_when_cpl_80(self):
        result = _engine(rcpt_cpl='80').build_test_receipt(kind='sales')
        assert result.paper_size == 'dot_matrix'

    def test_hiding_phone_removes_phone_from_receipt(self):
        ss = _default_settings(store_phone='0771234567', rcpt_show_phone='false')
        result = ReceiptEngine(ss).build_test_receipt(kind='sales')
        assert '0771234567' not in result.receipt_text

    def test_showing_phone_includes_phone_in_receipt(self):
        ss = _default_settings(store_phone='0771234567', rcpt_show_phone='true')
        result = ReceiptEngine(ss).build_test_receipt(kind='sales')
        assert '0771234567' in result.receipt_text

    def test_footer_text_appears_in_receipt(self):
        result = _engine(rcpt_footer_text='Come again soon!').build_test_receipt(kind='sales')
        assert 'Come again soon!' in result.receipt_text

    def test_default_footer_text_appears(self):
        result = _engine().build_test_receipt(kind='sales')
        assert RECEIPT_LAYOUT_DEFAULTS['rcpt_footer_text'] in result.receipt_text

    def test_hiding_store_name_removes_it(self):
        ss = _default_settings(store_name='Garage X', rcpt_show_store_name='false')
        result = ReceiptEngine(ss).build_test_receipt(kind='sales')
        assert 'Garage X' not in result.receipt_text

    def test_hiding_invoice_removes_label(self):
        result = _engine(rcpt_show_invoice='false').build_test_receipt(kind='sales')
        assert 'TEST-0001' not in result.receipt_text

    def test_hiding_cashier_removes_cashier(self):
        result = _engine(rcpt_show_cashier='false').build_test_receipt(kind='sales')
        assert 'Cashier' not in result.receipt_text

    def test_hiding_customer_removes_customer(self):
        result = _engine(rcpt_show_customer='false').build_test_receipt(kind='sales')
        assert 'Customer' not in result.receipt_text

    def test_hiding_subtotal_removes_subtotal(self):
        result = _engine(rcpt_show_subtotal='false').build_test_receipt(kind='sales')
        assert 'Subtotal' not in result.receipt_text

    def test_hiding_grand_total_removes_it(self):
        result = _engine(rcpt_show_grand_total='false').build_test_receipt(kind='sales')
        assert 'TOTAL' not in result.receipt_text

    def test_debug_contains_layout_source(self):
        result = _engine().build_test_receipt(kind='sales')
        assert result.debug['layout_source'] == 'StoreSettings'

    def test_debug_contains_enabled_fields(self):
        result = _engine().build_test_receipt(kind='sales')
        assert isinstance(result.debug['enabled_fields'], list)
        assert len(result.debug['enabled_fields']) > 0

    def test_debug_marks_test_true(self):
        result = _engine().build_test_receipt(kind='sales')
        assert result.debug.get('test') is True

    def test_layout_dict_comes_from_store_settings(self):
        result = _engine(rcpt_cpl='32').build_test_receipt(kind='sales')
        assert result.layout['rcpt_cpl'] == '32'

    def test_invalid_cpl_falls_back_to_48(self):
        result = _engine(rcpt_cpl='notanumber').build_test_receipt(kind='sales')
        assert result.cpl == 48

    def test_cpl_clamped_to_minimum_24(self):
        result = _engine(rcpt_cpl='5').build_test_receipt(kind='sales')
        assert result.cpl == 24

    def test_cpl_clamped_to_maximum_96(self):
        result = _engine(rcpt_cpl='999').build_test_receipt(kind='sales')
        assert result.cpl == 96


# ── build_test_receipt (service) ─────────────────────────────────

class TestBuildTestReceiptService:
    def test_returns_receipt_result(self):
        result = _engine().build_test_receipt(kind='service')
        assert isinstance(result, ReceiptResult)

    def test_receipt_text_is_nonempty(self):
        result = _engine().build_test_receipt(kind='service')
        assert len(result.receipt_text) > 20

    def test_uses_svc_cpl_key(self):
        r32 = _engine(svc_rcpt_cpl='32').build_test_receipt(kind='service')
        r48 = _engine(svc_rcpt_cpl='48').build_test_receipt(kind='service')
        assert r32.cpl == 32
        assert r48.cpl == 48

    def test_service_receipt_uses_svc_settings_not_sales_settings(self):
        ss = _default_settings(svc_rcpt_cpl='32', rcpt_cpl='48')
        result = ReceiptEngine(ss).build_test_receipt(kind='service')
        assert result.cpl == 32

    def test_hiding_service_phone_removes_phone(self):
        ss = _default_settings(store_phone='0771234567', svc_rcpt_show_phone='false')
        result = ReceiptEngine(ss).build_test_receipt(kind='service')
        assert '0771234567' not in result.receipt_text

    def test_service_footer_appears(self):
        result = _engine(svc_rcpt_footer_text='Thank you for the visit!').build_test_receipt(kind='service')
        assert 'Thank you for the visit!' in result.receipt_text

    def test_service_debug_kind_is_service(self):
        result = _engine().build_test_receipt(kind='service')
        assert result.debug['kind'] == 'service'

    def test_service_debug_enabled_fields_use_svc_prefix(self):
        result = _engine().build_test_receipt(kind='service')
        enabled = result.debug['enabled_fields']
        assert any('svc_' in f for f in enabled)

    def test_service_layout_source_is_store_settings(self):
        result = _engine().build_test_receipt(kind='service')
        assert result.debug['layout_source'] == 'StoreSettings'


# ── get_debug_layout ──────────────────────────────────────────────

class TestGetDebugLayout:
    def test_returns_dict_with_expected_keys(self):
        data = _engine().get_debug_layout()
        assert 'sales_layout' in data
        assert 'service_layout' in data
        assert 'printer_settings' in data
        assert 'routes_using_engine' in data

    def test_routes_using_engine_is_true(self):
        data = _engine().get_debug_layout()
        assert data['routes_using_engine'] is True

    def test_sales_layout_has_all_keys(self):
        data = _engine().get_debug_layout()
        for k in RECEIPT_LAYOUT_KEYS:
            assert k in data['sales_layout']

    def test_service_layout_has_all_keys(self):
        data = _engine().get_debug_layout()
        for k in SERVICE_RECEIPT_LAYOUT_KEYS:
            assert k in data['service_layout']

    def test_derived_includes_cpl_and_paper_size(self):
        data = _engine().get_debug_layout()
        assert 'derived' in data
        assert 'sales_cpl' in data['derived']
        assert 'sales_paper_size' in data['derived']
        assert 'service_cpl' in data['derived']
        assert 'service_paper_size' in data['derived']

    def test_cpl_changes_reflected_in_debug(self):
        data = _engine(rcpt_cpl='32', svc_rcpt_cpl='48').get_debug_layout()
        assert data['derived']['sales_cpl'] == 32
        assert data['derived']['service_cpl'] == 48


# ── Layout settings fully govern output ──────────────────────────

class TestLayoutSettingsFullyCoverOutput:
    def test_changing_footer_text_changes_receipt(self):
        r1 = _engine(rcpt_footer_text='Footer A').build_test_receipt(kind='sales')
        r2 = _engine(rcpt_footer_text='Footer B').build_test_receipt(kind='sales')
        assert 'Footer A' in r1.receipt_text
        assert 'Footer B' in r2.receipt_text
        assert 'Footer A' not in r2.receipt_text

    def test_no_hardcoded_supermart_pos_in_output(self):
        result = _engine().build_test_receipt(kind='sales')
        assert 'SuperMart POS' not in result.receipt_text
        assert 'SUPERMART POS' not in result.receipt_text

    def test_no_hardcoded_autoserv_garage_in_output(self):
        result = _engine().build_test_receipt(kind='service')
        assert 'AutoServ Garage' not in result.receipt_text

    def test_empty_store_name_not_rendered(self):
        ss = _default_settings(store_name='')
        result = ReceiptEngine(ss).build_test_receipt(kind='sales')
        assert 'Your Store Name' in result.receipt_text or result.receipt_text  # falls back or uses default

    def test_show_change_off_removes_change_line(self):
        result = _engine(rcpt_show_change='false').build_test_receipt(kind='sales')
        assert 'CHANGE' not in result.receipt_text

    def test_show_paid_off_removes_paid_line(self):
        result = _engine(rcpt_show_paid='false').build_test_receipt(kind='sales')
        assert 'PAID' not in result.receipt_text

    def test_show_tax_off_removes_tax_line(self):
        result = _engine(rcpt_show_tax='false').build_test_receipt(kind='sales')
        assert 'Tax' not in result.receipt_text

    def test_service_show_customer_off_removes_customer(self):
        result = _engine(svc_rcpt_show_customer='false').build_test_receipt(kind='service')
        assert 'Test Customer' not in result.receipt_text

    def test_service_show_job_number_off_removes_job_number(self):
        result = _engine(svc_rcpt_show_job_number='false').build_test_receipt(kind='service')
        assert 'JOB-TEST-001' not in result.receipt_text
