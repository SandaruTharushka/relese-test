"""Tests for receipt layout settings — constants, defaults, and persistence logic.

Verifies that RECEIPT_LAYOUT_KEYS / RECEIPT_LAYOUT_DEFAULTS are the single
source of truth, that every key has a default, and that saved values are
loaded correctly on the next build.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.printing.domain.constants import (
    RECEIPT_LAYOUT_DEFAULTS,
    RECEIPT_LAYOUT_KEYS,
    SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)
from services.printing.receipt.receipt_engine import ReceiptEngine


class _MockStoreSettings:
    def __init__(self, data: dict | None = None) -> None:
        self._data: dict[str, str] = data or {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = str(value)

    def set_many(self, payload: dict) -> None:
        for k, v in payload.items():
            self._data[k] = str(v)


# ── Constants completeness ────────────────────────────────────────

class TestConstantsCompleteness:
    def test_every_sales_layout_key_has_default(self):
        for k in RECEIPT_LAYOUT_KEYS:
            assert k in RECEIPT_LAYOUT_DEFAULTS, f'Missing default for {k}'

    def test_every_service_layout_key_has_default(self):
        for k in SERVICE_RECEIPT_LAYOUT_KEYS:
            assert k in SERVICE_RECEIPT_LAYOUT_DEFAULTS, f'Missing default for {k}'

    def test_no_thermal_80_hardcoded_in_defaults(self):
        combined = str(RECEIPT_LAYOUT_DEFAULTS) + str(SERVICE_RECEIPT_LAYOUT_DEFAULTS)
        assert 'thermal_80' not in combined.lower()

    def test_no_supermart_pos_in_defaults(self):
        combined = str(RECEIPT_LAYOUT_DEFAULTS) + str(SERVICE_RECEIPT_LAYOUT_DEFAULTS)
        assert 'SuperMart POS' not in combined
        assert 'SUPERMART POS' not in combined

    def test_default_cpl_is_valid_int(self):
        assert int(RECEIPT_LAYOUT_DEFAULTS['rcpt_cpl']) in range(24, 97)

    def test_default_service_cpl_is_valid_int(self):
        assert int(SERVICE_RECEIPT_LAYOUT_DEFAULTS['svc_rcpt_cpl']) in range(24, 97)

    def test_show_flags_default_to_true_or_false_string(self):
        for k, v in RECEIPT_LAYOUT_DEFAULTS.items():
            if k.startswith('rcpt_show_'):
                assert v in {'true', 'false'}, f'Key {k} has unexpected value {v!r}'

    def test_service_show_flags_default_to_true_or_false_string(self):
        for k, v in SERVICE_RECEIPT_LAYOUT_DEFAULTS.items():
            if k.startswith('svc_rcpt_show_'):
                assert v in {'true', 'false'}, f'Key {k} has unexpected value {v!r}'

    def test_sales_layout_keys_all_start_with_rcpt(self):
        for k in RECEIPT_LAYOUT_KEYS:
            assert k.startswith('rcpt_'), f'Key {k!r} does not start with rcpt_'

    def test_service_layout_keys_all_start_with_svc_rcpt(self):
        for k in SERVICE_RECEIPT_LAYOUT_KEYS:
            assert k.startswith('svc_rcpt_'), f'Key {k!r} does not start with svc_rcpt_'


# ── Settings loaded from StoreSettings ───────────────────────────

class TestSettingsLoadedFromStoreSettings:
    def _engine_with(self, **kw) -> ReceiptEngine:
        data = {**RECEIPT_LAYOUT_DEFAULTS, **SERVICE_RECEIPT_LAYOUT_DEFAULTS, 'store_name': 'Test', **kw}
        return ReceiptEngine(_MockStoreSettings(data))

    def test_cpl_setting_used_in_build(self):
        for cpl_val in ('32', '48', '80'):
            engine = self._engine_with(rcpt_cpl=cpl_val)
            result = engine.build_test_receipt(kind='sales')
            assert result.cpl == int(cpl_val)
            assert result.layout['rcpt_cpl'] == cpl_val

    def test_footer_text_persisted_and_used(self):
        engine = self._engine_with(rcpt_footer_text='Custom Footer')
        result = engine.build_test_receipt(kind='sales')
        assert result.layout['rcpt_footer_text'] == 'Custom Footer'
        assert 'Custom Footer' in result.receipt_text

    def test_show_phone_false_persisted_and_used(self):
        engine = self._engine_with(store_phone='0770000001', rcpt_show_phone='false')
        result = engine.build_test_receipt(kind='sales')
        assert result.layout['rcpt_show_phone'] == 'false'
        assert '0770000001' not in result.receipt_text

    def test_show_phone_true_persisted_and_used(self):
        engine = self._engine_with(store_phone='0770000001', rcpt_show_phone='true')
        result = engine.build_test_receipt(kind='sales')
        assert '0770000001' in result.receipt_text

    def test_service_cpl_independent_from_sales_cpl(self):
        engine = self._engine_with(rcpt_cpl='48', svc_rcpt_cpl='32')
        sales = engine.build_test_receipt(kind='sales')
        service = engine.build_test_receipt(kind='service')
        assert sales.cpl == 48
        assert service.cpl == 32

    def test_sales_layout_does_not_affect_service_receipt(self):
        # Changing sales footer must not change service receipt footer
        engine = self._engine_with(
            rcpt_footer_text='SALES ONLY',
            svc_rcpt_footer_text='SERVICE ONLY',
        )
        sales = engine.build_test_receipt(kind='sales')
        service = engine.build_test_receipt(kind='service')
        assert 'SALES ONLY' in sales.receipt_text
        assert 'SALES ONLY' not in service.receipt_text
        assert 'SERVICE ONLY' in service.receipt_text
        assert 'SERVICE ONLY' not in sales.receipt_text

    def test_changed_setting_takes_effect_on_next_build(self):
        ss = _MockStoreSettings({
            **RECEIPT_LAYOUT_DEFAULTS,
            **SERVICE_RECEIPT_LAYOUT_DEFAULTS,
            'store_name': 'Test',
            'rcpt_footer_text': 'Before',
        })
        engine1 = ReceiptEngine(ss)
        r1 = engine1.build_test_receipt(kind='sales')
        assert 'Before' in r1.receipt_text

        # Simulate user saving new footer
        ss.set('rcpt_footer_text', 'After')
        engine2 = ReceiptEngine(ss)
        r2 = engine2.build_test_receipt(kind='sales')
        assert 'After' in r2.receipt_text
        assert 'Before' not in r2.receipt_text

    def test_invalid_layout_value_falls_back_safely(self):
        # Invalid cpl should not raise
        engine = self._engine_with(rcpt_cpl='INVALID')
        result = engine.build_test_receipt(kind='sales')
        assert result.cpl == 48  # fallback

    def test_empty_store_name_does_not_crash(self):
        engine = self._engine_with(store_name='')
        result = engine.build_test_receipt(kind='sales')
        assert isinstance(result, type(result))
        assert result.receipt_text  # must still produce output

    def test_all_defaults_produce_valid_receipt(self):
        """Default settings must always produce a valid receipt without errors."""
        data = {
            **RECEIPT_LAYOUT_DEFAULTS,
            **SERVICE_RECEIPT_LAYOUT_DEFAULTS,
            'store_name': 'Test Store',
        }
        engine = ReceiptEngine(_MockStoreSettings(data))
        sales = engine.build_test_receipt(kind='sales')
        service = engine.build_test_receipt(kind='service')
        assert sales.receipt_text
        assert service.receipt_text

    def test_debug_layout_reflects_current_settings(self):
        engine = self._engine_with(rcpt_cpl='32', svc_rcpt_cpl='80')
        debug = engine.get_debug_layout()
        assert debug['derived']['sales_cpl'] == 32
        assert debug['derived']['service_cpl'] == 80
        assert debug['derived']['sales_paper_size'] == '58mm'
        assert debug['derived']['service_paper_size'] == 'dot_matrix'


# ── Field toggle behavior ─────────────────────────────────────────

class TestFieldToggleBehavior:
    """Each toggle must affect exactly the right output and nothing else."""

    TOGGLE_CASES = [
        # (setting_key, store_key, store_value, expected_in_output)
        ('rcpt_show_store_name', 'store_name', 'Garage X', 'Garage X'),
        ('rcpt_show_phone', 'store_phone', '0771111111', '0771111111'),
        ('rcpt_show_address', 'store_address', '123 Main St', '123 Main St'),
        ('rcpt_show_email', 'store_email', 'test@test.com', 'test@test.com'),
        ('rcpt_show_invoice', None, None, 'TEST-0001'),
        ('rcpt_show_cashier', None, None, 'Cashier'),
        ('rcpt_show_subtotal', None, None, 'Subtotal'),
        ('rcpt_show_grand_total', None, None, 'TOTAL'),
        ('rcpt_show_paid', None, None, 'PAID'),
        ('rcpt_show_change', None, None, 'CHANGE'),
        ('rcpt_footer_text', None, None, None),  # handled by separate test
    ]

    def _engine_with(self, **kw) -> ReceiptEngine:
        data = {**RECEIPT_LAYOUT_DEFAULTS, **SERVICE_RECEIPT_LAYOUT_DEFAULTS, 'store_name': 'T', **kw}
        return ReceiptEngine(_MockStoreSettings(data))

    def test_show_store_name_toggle(self):
        on = self._engine_with(store_name='Garage X', rcpt_show_store_name='true')
        off = self._engine_with(store_name='Garage X', rcpt_show_store_name='false')
        assert 'Garage X' in on.build_test_receipt(kind='sales').receipt_text
        assert 'Garage X' not in off.build_test_receipt(kind='sales').receipt_text

    def test_show_phone_toggle(self):
        on = self._engine_with(store_phone='0771111111', rcpt_show_phone='true')
        off = self._engine_with(store_phone='0771111111', rcpt_show_phone='false')
        assert '0771111111' in on.build_test_receipt(kind='sales').receipt_text
        assert '0771111111' not in off.build_test_receipt(kind='sales').receipt_text

    def test_show_address_toggle(self):
        on = self._engine_with(store_address='123 Main St', rcpt_show_address='true')
        off = self._engine_with(store_address='123 Main St', rcpt_show_address='false')
        assert '123 Main St' in on.build_test_receipt(kind='sales').receipt_text
        assert '123 Main St' not in off.build_test_receipt(kind='sales').receipt_text

    def test_show_email_toggle(self):
        on = self._engine_with(store_email='test@test.com', rcpt_show_email='true')
        off = self._engine_with(store_email='test@test.com', rcpt_show_email='false')
        assert 'test@test.com' in on.build_test_receipt(kind='sales').receipt_text
        assert 'test@test.com' not in off.build_test_receipt(kind='sales').receipt_text

    def test_service_show_warranty_toggle(self):
        on = self._engine_with(svc_rcpt_show_warranty='true', svc_rcpt_warranty_text='1 year warranty')
        off = self._engine_with(svc_rcpt_show_warranty='false', svc_rcpt_warranty_text='1 year warranty')
        assert '1 year warranty' in on.build_test_receipt(kind='service').receipt_text
        assert '1 year warranty' not in off.build_test_receipt(kind='service').receipt_text
