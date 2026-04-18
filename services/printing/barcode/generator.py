"""Barcode generation service — moved from app.py and barcode_scanner_service.py.

This is now the single canonical location for barcode generation, validation,
and product assignment logic.
"""
from __future__ import annotations

import random
import re
from typing import Any

from services.printing.label.validator import validate_barcode, SUPPORTED_BARCODE_TYPES


class BarcodeGeneratorService:
    """Generates, validates, and assigns barcodes to products."""

    SUPPORTED_MODES = frozenset({'random', 'product_id', 'prefix', 'manual'})

    @staticmethod
    def normalize_type(raw: str | None, fallback: str = 'code128') -> str:
        value = str(raw or fallback).strip().lower()
        return value if value in SUPPORTED_BARCODE_TYPES else fallback

    @staticmethod
    def _ean13_check_digit(body12: str) -> str:
        odds = sum(int(body12[i]) for i in range(0, 12, 2))
        evens = sum(int(body12[i]) for i in range(1, 12, 2))
        return str((10 - ((odds + evens * 3) % 10)) % 10)

    @staticmethod
    def _make_ean13_from_product_id(product_id: int) -> str:
        body = f'200{int(product_id):09d}'[:12]
        return body + BarcodeGeneratorService._ean13_check_digit(body)

    @classmethod
    def generate(
        cls,
        *,
        product_id: int,
        mode: str,
        barcode_type: str,
        prefix: str,
        manual_value: str,
        exists_fn,
    ) -> dict[str, str]:
        """Generate a unique barcode.

        Returns {'barcode': str, 'barcode_type': str, 'mode': str}
        Raises ValueError on failure.
        """
        resolved_mode = str(mode or 'random').strip().lower()
        if resolved_mode not in cls.SUPPORTED_MODES:
            resolved_mode = 'random'

        resolved_type = cls.normalize_type(barcode_type)
        safe_prefix = re.sub(r'[^A-Z0-9-]', '', str(prefix or 'SM').upper()).strip('-') or 'SM'

        if resolved_mode == 'manual':
            candidate = (manual_value or '').strip()
            ok, msg = validate_barcode(candidate, resolved_type)
            if not ok:
                raise ValueError(msg)
            if exists_fn(candidate):
                raise ValueError(f'Barcode "{candidate}" already exists')
            return {'barcode': candidate, 'barcode_type': resolved_type, 'mode': resolved_mode}

        candidates: list[str] = []
        if resolved_mode == 'product_id':
            if resolved_type in {'ean13', 'ean8'}:
                candidates.append(cls._make_ean13_from_product_id(product_id))
                resolved_type = 'ean13'
            else:
                candidates.append(f'PID-{int(product_id):08d}')
        elif resolved_mode == 'prefix':
            if resolved_type in {'ean13', 'ean8'}:
                body = f'2{int(product_id):011d}'[-12:]
                candidates.append(body + cls._ean13_check_digit(body))
                resolved_type = 'ean13'
            else:
                candidates.append(f'{safe_prefix}-{int(product_id):08d}')
        else:  # random
            for _ in range(20):
                if resolved_type in {'ean13', 'ean8'}:
                    body = ''.join(str(random.randint(0, 9)) for _ in range(12))
                    candidates.append(body + cls._ean13_check_digit(body))
                    resolved_type = 'ean13'
                else:
                    candidates.append(f'{safe_prefix}-{random.randint(10000000, 99999999)}')

        for candidate in candidates:
            if not exists_fn(candidate):
                return {'barcode': candidate, 'barcode_type': resolved_type, 'mode': resolved_mode}

        raise ValueError('Could not generate a unique barcode. Try a different mode or prefix.')

    @staticmethod
    def build_preview_payload(*, barcode: str, barcode_type: str) -> dict[str, Any]:
        return {
            'barcode': barcode,
            'barcode_type': BarcodeGeneratorService.normalize_type(barcode_type),
            'image_url': f'/api/printing/barcode/image?value={barcode}&type={barcode_type}',
        }
