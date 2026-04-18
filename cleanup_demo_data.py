#!/usr/bin/env python3
"""Safe cleanup utility for removing obvious demo/dev records from local databases.

Usage:
  python cleanup_demo_data.py --dry-run   # default
  python cleanup_demo_data.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, or_

from app import app, db
from models import (
    Category,
    HeldOrder,
    InstallmentPlan,
    Product,
    RepairJob,
    RetailCustomer,
    Supplier,
    WholesaleCustomer,
)

SEED_BARCODES = {
    '8901234567890', '8901234567891', '8901234567892', '8901234567893',
    '8901234567894', '8901234567895', '8901234567896', '8901234567897',
    '8901234567898', '8901234567899', '8901234500001', '8901234500002',
}
SEED_PRODUCT_NAMES = {
    'Fresh Whole Milk', 'White Bread Loaf', 'Orange Juice 1L', 'Basmati Rice 5kg',
    'Chicken Eggs (12)', 'Cheddar Cheese', 'Sparkling Water 6pk',
    'Dark Chocolate Bar', 'Instant Noodles', 'Sunflower Oil 1L',
    'Detergent Powder', 'Tomato Sauce 500g',
}
SEED_CATEGORY_NAMES = {
    'Dairy', 'Bakery', 'Beverages', 'Grains', 'Snacks', 'Cooking', 'Household', 'Condiments',
}

DEMO_KEYWORDS = (
    'demo', 'sample', 'test', 'mock', 'fake', 'placeholder', 'temp', 'dummy', 'example',
)


@dataclass
class CleanupStep:
    label: str
    count_fn: Callable[[], int]
    delete_fn: Callable[[], int]


def _ilike_any(column, values: tuple[str, ...]):
    return or_(*[func.lower(column).like(f'%{v}%') for v in values])


def cleanup_steps() -> list[CleanupStep]:
    return [
        CleanupStep(
            label='Seeded products by known barcode/name',
            count_fn=lambda: Product.query.filter(
                or_(Product.barcode.in_(SEED_BARCODES), Product.name.in_(SEED_PRODUCT_NAMES))
            ).count(),
            delete_fn=lambda: Product.query.filter(
                or_(Product.barcode.in_(SEED_BARCODES), Product.name.in_(SEED_PRODUCT_NAMES))
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo-named categories (only if empty)',
            count_fn=lambda: Category.query.filter(
                Category.name.in_(SEED_CATEGORY_NAMES),
                ~Category.products.any(),
            ).count(),
            delete_fn=lambda: Category.query.filter(
                Category.name.in_(SEED_CATEGORY_NAMES),
                ~Category.products.any(),
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo/test suppliers',
            count_fn=lambda: Supplier.query.filter(
                or_(
                    _ilike_any(Supplier.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(Supplier.email, ''), DEMO_KEYWORDS),
                )
            ).count(),
            delete_fn=lambda: Supplier.query.filter(
                or_(
                    _ilike_any(Supplier.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(Supplier.email, ''), DEMO_KEYWORDS),
                )
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo/test retail customers',
            count_fn=lambda: RetailCustomer.query.filter(
                or_(
                    _ilike_any(RetailCustomer.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RetailCustomer.email, ''), DEMO_KEYWORDS),
                    func.lower(func.coalesce(RetailCustomer.email, '')).like('%@example.%'),
                )
            ).count(),
            delete_fn=lambda: RetailCustomer.query.filter(
                or_(
                    _ilike_any(RetailCustomer.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RetailCustomer.email, ''), DEMO_KEYWORDS),
                    func.lower(func.coalesce(RetailCustomer.email, '')).like('%@example.%'),
                )
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo/test wholesale customers',
            count_fn=lambda: WholesaleCustomer.query.filter(
                or_(
                    _ilike_any(WholesaleCustomer.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(WholesaleCustomer.business, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(WholesaleCustomer.email, ''), DEMO_KEYWORDS),
                    func.lower(func.coalesce(WholesaleCustomer.email, '')).like('%@example.%'),
                )
            ).count(),
            delete_fn=lambda: WholesaleCustomer.query.filter(
                or_(
                    _ilike_any(WholesaleCustomer.name, DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(WholesaleCustomer.business, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(WholesaleCustomer.email, ''), DEMO_KEYWORDS),
                    func.lower(func.coalesce(WholesaleCustomer.email, '')).like('%@example.%'),
                )
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo/test repair jobs',
            count_fn=lambda: RepairJob.query.filter(
                or_(
                    _ilike_any(func.coalesce(RepairJob.customer_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RepairJob.issue_reported, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RepairJob.notes, ''), DEMO_KEYWORDS),
                )
            ).count(),
            delete_fn=lambda: RepairJob.query.filter(
                or_(
                    _ilike_any(func.coalesce(RepairJob.customer_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RepairJob.issue_reported, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(RepairJob.notes, ''), DEMO_KEYWORDS),
                )
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Demo/test installment plans',
            count_fn=lambda: InstallmentPlan.query.filter(
                or_(
                    _ilike_any(func.coalesce(InstallmentPlan.customer_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(InstallmentPlan.product_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(InstallmentPlan.notes, ''), DEMO_KEYWORDS),
                )
            ).count(),
            delete_fn=lambda: InstallmentPlan.query.filter(
                or_(
                    _ilike_any(func.coalesce(InstallmentPlan.customer_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(InstallmentPlan.product_name, ''), DEMO_KEYWORDS),
                    _ilike_any(func.coalesce(InstallmentPlan.notes, ''), DEMO_KEYWORDS),
                )
            ).delete(synchronize_session=False),
        ),
        CleanupStep(
            label='Temporary held orders tagged as demo/test',
            count_fn=lambda: HeldOrder.query.filter(
                _ilike_any(func.coalesce(HeldOrder.label, ''), DEMO_KEYWORDS)
            ).count(),
            delete_fn=lambda: HeldOrder.query.filter(
                _ilike_any(func.coalesce(HeldOrder.label, ''), DEMO_KEYWORDS)
            ).delete(synchronize_session=False),
        ),
    ]


def run_cleanup(apply_changes: bool) -> int:
    steps = cleanup_steps()
    total_candidates = 0

    print('=== SuperMart POS demo-data cleanup ===')
    print(f"Mode: {'APPLY' if apply_changes else 'DRY RUN'}")
    for step in steps:
        count = step.count_fn()
        total_candidates += count
        print(f'- {step.label}: {count}')

    if not apply_changes:
        print('\nNo data was deleted (dry-run). Re-run with --apply to commit changes.')
        return total_candidates

    deleted_total = 0
    for step in steps:
        deleted = step.delete_fn()
        deleted_total += deleted

    db.session.commit()
    print(f'\nDeleted rows: {deleted_total}')
    return deleted_total


def main() -> int:
    parser = argparse.ArgumentParser(description='Remove obvious demo/sample records from SuperMart POS database.')
    parser.add_argument('--apply', action='store_true', help='Apply deletions. Without this flag the script only prints matches.')
    parser.add_argument('--dry-run', action='store_true', help='Force dry-run mode (default).')
    args = parser.parse_args()

    apply_changes = bool(args.apply and not args.dry_run)
    with app.app_context():
        run_cleanup(apply_changes=apply_changes)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
