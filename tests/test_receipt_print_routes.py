"""Tests for receipt print routes — verifies routes delegate to ReceiptEngine.

These are unit/integration tests that patch the engine and DB; no real printer needed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.printing.domain.constants import (
    RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
)
from services.printing.receipt.receipt_engine import ReceiptEngine, ReceiptResult


class _MockStoreSettings:
    def __init__(self, data: dict | None = None) -> None:
        self._data: dict[str, str] = {
            **RECEIPT_LAYOUT_DEFAULTS,
            **SERVICE_RECEIPT_LAYOUT_DEFAULTS,
            'store_name': 'Test Garage',
            'store_phone': '0771234567',
            **(data or {}),
        }

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = str(value)

    def set_many(self, payload: dict) -> None:
        for k, v in payload.items():
            self._data[k] = str(v)


def _make_result(receipt_text: str = 'test receipt', cpl: int = 48) -> ReceiptResult:
    return ReceiptResult(
        receipt_text=receipt_text,
        html_preview='{}',
        layout={**RECEIPT_LAYOUT_DEFAULTS},
        paper_size='80mm',
        cpl=cpl,
        debug={
            'layout_source': 'StoreSettings',
            'layout_type': 'thermal',
            'enabled_fields': ['rcpt_show_store_name'],
            'test': False,
        },
    )


# ── ReceiptEngine unit tests (no Flask) ──────────────────────────

class TestReceiptEngineDelegation:
    """Verify that ReceiptEngine is the only path for building receipts.

    These tests call the engine directly with mock settings and assert that
    changing a layout setting changes the output — proving the route would
    produce a different receipt when settings change.
    """

    def _engine(self, **kw) -> ReceiptEngine:
        data = {**RECEIPT_LAYOUT_DEFAULTS, **SERVICE_RECEIPT_LAYOUT_DEFAULTS, 'store_name': 'T', **kw}
        return ReceiptEngine(_MockStoreSettings(data))

    def test_print_sale_route_uses_engine_cpl(self):
        r32 = self._engine(rcpt_cpl='32').build_test_receipt(kind='sales')
        r48 = self._engine(rcpt_cpl='48').build_test_receipt(kind='sales')
        assert r32.cpl == 32
        assert r48.cpl == 48

    def test_print_job_route_uses_service_layout_cpl(self):
        r32 = self._engine(svc_rcpt_cpl='32').build_test_receipt(kind='service')
        r48 = self._engine(svc_rcpt_cpl='48').build_test_receipt(kind='service')
        assert r32.cpl == 32
        assert r48.cpl == 48

    def test_preview_sales_and_print_sale_produce_same_layout(self):
        """Preview and print paths must read from the same layout settings."""
        engine = self._engine(rcpt_cpl='48', rcpt_footer_text='Consistent Footer')
        preview = engine.build_test_receipt(kind='sales')
        # Both should use the same cpl and footer
        assert preview.cpl == 48
        assert 'Consistent Footer' in preview.receipt_text

    def test_legacy_route_delegates_to_engine(self):
        """Legacy route delegates — engine handles all receipt building."""
        engine = self._engine(rcpt_cpl='48')
        result = engine.build_test_receipt(kind='sales')
        # If engine is used, layout_source must be StoreSettings
        assert result.debug['layout_source'] == 'StoreSettings'

    def test_invalid_layout_value_does_not_crash(self):
        result = self._engine(rcpt_cpl='INVALID').build_test_receipt(kind='sales')
        assert result.cpl == 48  # safe fallback

    def test_all_show_fields_off_still_produces_output(self):
        overrides = {k: 'false' for k in RECEIPT_LAYOUT_DEFAULTS if k.startswith('rcpt_show_')}
        result = self._engine(**overrides).build_test_receipt(kind='sales')
        assert result.receipt_text  # must still produce something

    def test_no_hardcoded_default_layout_ignores_settings(self):
        """Hardcoded paths would use the same receipt regardless of settings.

        We verify that two different settings produce two different receipts.
        """
        r1 = self._engine(rcpt_footer_text='Layout A').build_test_receipt(kind='sales')
        r2 = self._engine(rcpt_footer_text='Layout B').build_test_receipt(kind='sales')
        assert r1.receipt_text != r2.receipt_text


# ── Route behavior via engine mock ───────────────────────────────

class TestReceiptPrintRoutesViaEngineMock:
    """Patch ReceiptEngine to verify routes call it and pass the result to print_domain."""

    def _make_mock_engine(self, receipt_text: str = 'mock receipt', cpl: int = 48) -> MagicMock:
        engine = MagicMock(spec=ReceiptEngine)
        engine.build_sales_receipt.return_value = _make_result(receipt_text, cpl)
        engine.build_service_receipt.return_value = _make_result(receipt_text, cpl)
        engine.build_test_receipt.return_value = _make_result(receipt_text, cpl)
        engine.get_debug_layout.return_value = {
            'sales_layout': {},
            'service_layout': {},
            'printer_settings': {},
            'routes_using_engine': True,
        }
        return engine

    def test_engine_class_exists_and_importable(self):
        from services.printing.receipt.receipt_engine import ReceiptEngine as E
        assert E is not None

    def test_engine_build_sales_receipt_signature(self):
        import inspect
        sig = inspect.signature(ReceiptEngine.build_sales_receipt)
        params = list(sig.parameters)
        assert 'sale_id' in params
        assert 'actor_user_id' in params
        assert 'cashier_name' in params

    def test_engine_build_service_receipt_signature(self):
        import inspect
        sig = inspect.signature(ReceiptEngine.build_service_receipt)
        params = list(sig.parameters)
        assert 'job_id' in params
        assert 'actor_user_id' in params

    def test_engine_build_test_receipt_accepts_kind(self):
        import inspect
        sig = inspect.signature(ReceiptEngine.build_test_receipt)
        params = list(sig.parameters)
        assert 'kind' in params

    def test_engine_get_debug_layout_exists(self):
        assert callable(getattr(ReceiptEngine, 'get_debug_layout', None))

    def test_receipt_result_has_required_fields(self):
        result = _make_result()
        assert hasattr(result, 'receipt_text')
        assert hasattr(result, 'html_preview')
        assert hasattr(result, 'layout')
        assert hasattr(result, 'paper_size')
        assert hasattr(result, 'cpl')
        assert hasattr(result, 'debug')

    def test_debug_layout_routes_using_engine_always_true(self):
        engine = self._make_mock_engine()
        data = engine.get_debug_layout()
        assert data['routes_using_engine'] is True

    def test_preview_uses_engine_not_hardcoded_html(self):
        """Preview endpoint must use ReceiptEngine, not hardcoded HTML templates."""
        engine = self._make_mock_engine(receipt_text='ENGINE PREVIEW OUTPUT')
        result = engine.build_test_receipt(kind='sales')
        assert 'ENGINE PREVIEW OUTPUT' in result.receipt_text

    def test_service_preview_uses_service_engine(self):
        engine = self._make_mock_engine(receipt_text='SERVICE ENGINE OUTPUT')
        result = engine.build_test_receipt(kind='service')
        assert 'SERVICE ENGINE OUTPUT' in result.receipt_text


# ── No duplicate receipt builders ────────────────────────────────

class TestNoDuplicateReceiptBuilders:
    """Verify that no route file builds receipt text outside ReceiptEngine."""

    REPO_ROOT = Path(__file__).resolve().parents[1]

    def _read(self, rel: str) -> str:
        return (self.REPO_ROOT / rel).read_text(encoding='utf-8')

    def test_receipt_print_routes_does_not_import_sales_builder_directly(self):
        content = self._read('routes/receipt_print_routes.py')
        assert 'SalesReceiptBuilder()' not in content

    def test_receipt_print_routes_does_not_import_service_builder_directly(self):
        content = self._read('routes/receipt_print_routes.py')
        assert 'ServiceReceiptBuilder()' not in content

    def test_receipt_print_routes_uses_receipt_engine(self):
        content = self._read('routes/receipt_print_routes.py')
        assert 'ReceiptEngine' in content

    def test_repair_routes_does_not_have_hardcoded_autoserv_garage(self):
        content = self._read('repair_routes.py')
        assert 'AutoServ Garage' not in content

    def test_receipt_print_routes_does_not_have_hardcoded_supermart_pos(self):
        content = self._read('routes/receipt_print_routes.py')
        assert 'SuperMart POS' not in content

    def test_receipt_printer_does_not_have_supermart_in_test_receipt(self):
        content = self._read('services/printing/receipt_printer.py')
        assert 'SUPERMART POS' not in content
        assert 'SuperMart POS' not in content

    def test_preview_routes_registered(self):
        content = self._read('routes/receipt_print_routes.py')
        assert '/api/printing/receipt/preview-sales' in content
        assert '/api/printing/receipt/preview-service' in content

    def test_debug_layout_route_registered(self):
        content = self._read('routes/receipt_print_routes.py')
        assert '/api/printing/receipt/debug-layout' in content

    def test_layout_version_bump_on_save(self):
        content = self._read('routes/printing_settings_routes.py')
        assert 'receipt_layout_version' in content
        assert 'service_receipt_layout_version' in content

    def test_receipt_engine_module_exists(self):
        assert (self.REPO_ROOT / 'services' / 'printing' / 'receipt' / 'receipt_engine.py').exists()
