from __future__ import annotations

from app import DESTRUCTIVE_ADMIN_TOOLS_ENV, env_flag_is_enabled
from shared_helpers import validate_password_strength


def test_destructive_admin_tools_default_false_when_env_missing(monkeypatch):
    monkeypatch.delenv(DESTRUCTIVE_ADMIN_TOOLS_ENV, raising=False)

    assert env_flag_is_enabled(DESTRUCTIVE_ADMIN_TOOLS_ENV, default=False) is False


def test_validate_password_strength_rejects_simple_passwords():
    error = validate_password_strength('password123')

    assert error is not None


def test_validate_password_strength_accepts_strong_passwords():
    error = validate_password_strength('N9v!q7R@t2Lm')

    assert error is None
