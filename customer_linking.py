from __future__ import annotations

import re
from typing import Optional

from models import RetailCustomer, db
from shared_helpers import normalize_phone


def customer_code_from_id(customer_id: int) -> str:
    return f'CUST-{customer_id:06d}'


def find_customer_by_phone(phone: str | None) -> Optional[RetailCustomer]:
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return (
        RetailCustomer.query
        .filter(RetailCustomer.phone_normalized == normalized)
        .order_by(RetailCustomer.id.asc())
        .first()
    )


def ensure_customer_profile(*, name: str | None, phone: str | None, email: str | None = None,
                            nic: str | None = None, address: str | None = None,
                            notes: str | None = None, allow_create: bool = True) -> tuple[Optional[RetailCustomer], Optional[str]]:
    normalized_phone = normalize_phone(phone)
    existing = find_customer_by_phone(phone) if normalized_phone else None
    if existing:
        if not existing.phone and phone:
            existing.phone = (phone or '').strip() or None
        if normalized_phone and existing.phone_normalized != normalized_phone:
            existing.phone_normalized = normalized_phone
        if name and (not existing.name or existing.name.lower().startswith('walk-in')):
            existing.name = name.strip()
        return existing, 'matched_by_phone'

    if not allow_create:
        return None, None

    clean_name = (name or '').strip()
    if not clean_name:
        return None, None

    customer = RetailCustomer(
        name=clean_name,
        phone=(phone or '').strip() or None,
        phone_normalized=normalized_phone or None,
        email=(email or '').strip() or None,
        nic=(nic or '').strip() or None,
        address=(address or '').strip() or None,
        notes=(notes or '').strip() or None,
        status='active',
    )
    db.session.add(customer)
    db.session.flush()
    customer.customer_code = customer_code_from_id(customer.id)
    return customer, 'created'
