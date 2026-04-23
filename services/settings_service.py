"""Application-wide settings cache service.

Replaces N individual ``StoreSettings.get()`` calls per request with a
single batch load cached in process memory, with TTL-based invalidation.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any


_CACHE_TTL_SECONDS = 30
_NOT_SET = object()


class SettingsService:
    """Thread-safe, in-memory cache for StoreSettings key-value store.

    Usage::

        svc = SettingsService()
        name = svc.get('store_name', 'My Shop')
        tax_rate = svc.get_float('tax_rate', 0.0)
        svc.invalidate()          # force reload on next access
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._expires: float = 0.0
        self._model = None  # set lazily via _ensure_model

    # ── Internal ─────────────────────────────────────────────────────────────

    def _ensure_model(self):
        if self._model is None:
            from models import StoreSettings
            self._model = StoreSettings
        return self._model

    def _is_stale(self) -> bool:
        return time.monotonic() > self._expires

    def _reload(self) -> None:
        model = self._ensure_model()
        try:
            rows = model.query.all()
            self._cache = {row.key: (row.value or '') for row in rows}
            self._expires = time.monotonic() + _CACHE_TTL_SECONDS
        except Exception:
            # Keep stale cache rather than crash; just extend expiry briefly
            self._expires = time.monotonic() + 5.0

    def _get_raw(self, key: str, default: str = '') -> str:
        with self._lock:
            if self._is_stale():
                self._reload()
            return self._cache.get(key, default)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default: str = '') -> str:
        """Return the string value for *key*, or *default* if absent."""
        return self._get_raw(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Return the setting as a boolean (1/true/yes/on = True)."""
        fallback = '1' if default else '0'
        value = self._get_raw(key, fallback)
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    def get_int(self, key: str, default: int = 0, *, min_val: int = None, max_val: int = None) -> int:
        """Return the setting as an integer, clamped to [min_val, max_val]."""
        raw = self._get_raw(key, str(default))
        try:
            result = int(float(raw))
        except (TypeError, ValueError):
            result = default
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)
        return result

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Return the setting as a float."""
        raw = self._get_raw(key, str(default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    def get_decimal(self, key: str, default: str = '0.00') -> Decimal:
        """Return the setting as a Decimal (for money calculations)."""
        raw = self._get_raw(key, default)
        try:
            return Decimal(str(raw)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            try:
                return Decimal(default).quantize(Decimal('0.01'))
            except Exception:
                return Decimal('0.00')

    def get_tax_settings(self) -> dict[str, Any]:
        """Return canonical tax configuration dict."""
        tax_enabled_raw = self._get_raw('tax_enabled', None)
        if tax_enabled_raw in (None, ''):
            tax_enabled_raw = self._get_raw('pref_taxEnabled', '1')

        tax_rate_raw = self._get_raw('tax_rate', None)
        if tax_rate_raw in (None, ''):
            tax_rate_raw = self._get_raw('pref_taxRate', '0')

        tax_name = self._get_raw('tax_name', '') or self._get_raw('pref_taxName', 'Tax') or 'Tax'

        try:
            tax_rate = max(0.0, float(tax_rate_raw or 0))
        except (TypeError, ValueError):
            tax_rate = 0.0

        return {
            'tax_enabled': str(tax_enabled_raw).strip().lower() in {'1', 'true', 'yes', 'on'},
            'tax_rate': tax_rate,
            'tax_name': tax_name,
        }

    def get_all(self, prefix: str = None) -> dict[str, str]:
        """Return all settings, optionally filtered by key prefix."""
        with self._lock:
            if self._is_stale():
                self._reload()
            if prefix:
                return {k: v for k, v in self._cache.items() if k.startswith(prefix)}
            return dict(self._cache)

    def set(self, key: str, value: Any, *, commit: bool = True) -> None:
        """Persist *key=value* and invalidate cache."""
        model = self._ensure_model()
        model.set(key, value, _commit=commit)
        self.invalidate()

    def set_many(self, data: dict[str, Any], *, commit: bool = True) -> None:
        """Persist multiple settings and invalidate cache."""
        model = self._ensure_model()
        model.set_many(data)
        if commit:
            from models import db
            db.session.commit()
        self.invalidate()

    def invalidate(self) -> None:
        """Force cache reload on next access."""
        with self._lock:
            self._expires = 0.0


# Process-level singleton
_settings_service: SettingsService | None = None
_singleton_lock = threading.Lock()


def get_settings_service() -> SettingsService:
    """Return the process-level SettingsService singleton."""
    global _settings_service
    if _settings_service is None:
        with _singleton_lock:
            if _settings_service is None:
                _settings_service = SettingsService()
    return _settings_service
