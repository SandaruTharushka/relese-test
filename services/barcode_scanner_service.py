from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BarcodeGenerationResult:
    barcode: str
    barcode_type: str
    mode: str


class BarcodeService:
    SUPPORTED_TYPES = {'code128', 'ean13', 'ean8', 'code39', 'qr'}
    SUPPORTED_MODES = {'random', 'product_id', 'prefix', 'manual'}

    @staticmethod
    def normalize_barcode_type(raw: str | None, fallback: str = 'code128') -> str:
        value = str(raw or fallback).strip().lower()
        return value if value in BarcodeService.SUPPORTED_TYPES else fallback

    @staticmethod
    def validate_by_type(value: str | None, barcode_type: str | None) -> tuple[bool, str]:
        barcode = (value or '').strip()
        btype = BarcodeService.normalize_barcode_type(barcode_type)
        if not barcode:
            return False, 'Barcode value is required'
        if btype == 'ean13' and not re.fullmatch(r'\d{13}', barcode):
            return False, 'EAN-13 requires exactly 13 digits'
        if btype == 'ean8' and not re.fullmatch(r'\d{8}', barcode):
            return False, 'EAN-8 requires exactly 8 digits'
        if btype == 'code39' and not re.fullmatch(r'[0-9A-Z\-\.\ $/+%]+', barcode):
            return False, 'CODE39 supports uppercase letters, digits, and - . space $ / + %'
        if btype == 'code128' and (len(barcode) < 2 or len(barcode) > 48):
            return False, 'CODE128 should be between 2 and 48 characters'
        return True, ''

    @staticmethod
    def _ean13_check_digit(body12: str) -> str:
        odds = sum(int(body12[i]) for i in range(0, 12, 2))
        evens = sum(int(body12[i]) for i in range(1, 12, 2))
        return str((10 - ((odds + evens * 3) % 10)) % 10)

    @staticmethod
    def _make_ean13_from_product_id(product_id: int) -> str:
        body = f'200{int(product_id):09d}'[:12]
        return body + BarcodeService._ean13_check_digit(body)

    @staticmethod
    def generate(
        *,
        product_id: int,
        mode: str,
        barcode_type: str,
        prefix: str,
        manual_value: str,
        exists_fn,
    ) -> BarcodeGenerationResult:
        resolved_mode = str(mode or 'random').strip().lower()
        if resolved_mode not in BarcodeService.SUPPORTED_MODES:
            resolved_mode = 'random'

        resolved_type = BarcodeService.normalize_barcode_type(barcode_type)
        safe_prefix = re.sub(r'[^A-Z0-9-]', '', str(prefix or 'SM').upper()).strip('-') or 'SM'

        if resolved_mode == 'manual':
            candidate = (manual_value or '').strip()
            ok, msg = BarcodeService.validate_by_type(candidate, resolved_type)
            if not ok:
                raise ValueError(msg)
            if exists_fn(candidate):
                raise ValueError(f'Barcode "{candidate}" already exists')
            return BarcodeGenerationResult(barcode=candidate, barcode_type=resolved_type, mode=resolved_mode)

        candidates: list[str] = []
        if resolved_mode == 'product_id':
            if resolved_type in {'ean13', 'ean8'}:
                candidates.append(BarcodeService._make_ean13_from_product_id(product_id))
                resolved_type = 'ean13'
            else:
                candidates.append(f'PID-{int(product_id):08d}')
        elif resolved_mode == 'prefix':
            if resolved_type in {'ean13', 'ean8'}:
                body = f'2{int(product_id):011d}'[-12:]
                candidates.append(body + BarcodeService._ean13_check_digit(body))
                resolved_type = 'ean13'
            else:
                candidates.append(f'{safe_prefix}-{int(product_id):08d}')
        else:
            for _ in range(20):
                if resolved_type in {'ean13', 'ean8'}:
                    body = ''.join(str(random.randint(0, 9)) for _ in range(12))
                    candidates.append(body + BarcodeService._ean13_check_digit(body))
                    resolved_type = 'ean13'
                else:
                    candidates.append(f'{safe_prefix}-{random.randint(10000000, 99999999)}')

        for candidate in candidates:
            if not exists_fn(candidate):
                return BarcodeGenerationResult(barcode=candidate, barcode_type=resolved_type, mode=resolved_mode)
        raise ValueError('Could not generate a unique barcode')

    @staticmethod
    def build_preview_payload(*, barcode: str, barcode_type: str) -> dict[str, Any]:
        return {
            'barcode': barcode,
            'barcode_type': BarcodeService.normalize_barcode_type(barcode_type),
            'image_url': f'/api/barcode/image?value={barcode}&type={barcode_type}',
        }


class ProductFillService:
    @staticmethod
    def product_payload(product: Any) -> dict[str, Any]:
        data = product.to_dict()
        data['sku'] = data.get('sku') or data.get('barcode') or f'P-{product.id:06d}'
        return {
            'id': product.id,
            'name': data.get('name') or '',
            'sell_price': data.get('sell_price') or 0,
            'wholesale_price': data.get('wholesale_price') or 0,
            'sku': data.get('sku') or '',
            'rack_number': data.get('rack_number') or '',
            'section_number': data.get('section_number') or '',
            'barcode': data.get('barcode') or '',
            'barcode_type': data.get('barcode_type') or 'normal',
        }


class ScannerService:
    @staticmethod
    def sanitize_scan_value(raw: str, *, prefix: str = '', suffix: str = '') -> str:
        value = str(raw or '').strip()
        if prefix and value.startswith(prefix):
            value = value[len(prefix):]
        if suffix and value.endswith(suffix):
            value = value[: -len(suffix)]
        return value.strip()


class LabelLayoutService:
    DEFAULTS = {
        'label_width_mm': '30.0',
        'label_height_mm': '20.0',
        'label_gap_mm': '2.0',
        'label_margin_top': '2.0',
        'label_margin_left': '2.0',
        'label_margin_right': '2.0',
        'label_margin_bottom': '2.0',
        'label_barcode_width_mm': '24.0',
        'label_barcode_height_mm': '8.0',
        'label_font_size': '9',
        'label_orientation': 'portrait',
        'label_text_align': 'center',
        'label_barcode_type': 'code128',
        'label_print_copies': '1',
        'label_preview_style': 'classic',
    }

    TOGGLE_KEYS = {
        'label_show_name': 'true',
        'label_show_price': 'true',
        'label_show_wholesale_price': 'false',
        'label_show_sku': 'true',
        'label_show_rack': 'true',
        'label_show_section': 'true',
        'label_show_barcode': 'true',
        'label_show_footer': 'false',
    }

    @staticmethod
    def load_settings(getter) -> dict[str, str]:
        out = {k: str(getter(k, v) or v) for k, v in LabelLayoutService.DEFAULTS.items()}
        for key, default in LabelLayoutService.TOGGLE_KEYS.items():
            out[key] = str(getter(key, default) or default).lower()
        return out
