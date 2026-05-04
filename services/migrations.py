"""Versioned schema migration system.

Replaces the monolithic ``auto_migrate()`` function with a tracked,
idempotent migration runner.  Each migration has:

  - A unique integer version
  - A human-readable description
  - A list of SQL statements to execute

The ``schema_migrations`` table tracks which migrations have run.
Migrations are applied in version order and never re-applied.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)


class Migration(NamedTuple):
    version: int
    description: str
    statements: list[str]


# ── Migration registry ────────────────────────────────────────────────────────
# Add new migrations at the END.  Never edit existing entries.

MIGRATIONS: list[Migration] = [
    Migration(1, 'Core product and user columns', [
        "ALTER TABLE products ADD COLUMN price_per_kg FLOAT DEFAULT 0",
        "ALTER TABLE products ADD COLUMN barcode_type VARCHAR(10) DEFAULT 'normal'",
        "ALTER TABLE products ADD COLUMN wholesale_price FLOAT DEFAULT 0",
        "ALTER TABLE products ADD COLUMN sku VARCHAR(60)",
        "ALTER TABLE products ADD COLUMN rack_number VARCHAR(20) DEFAULT ''",
        "ALTER TABLE products ADD COLUMN section_number VARCHAR(20) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN email VARCHAR(150)",
    ]),
    Migration(2, 'Sales and wholesale columns', [
        "ALTER TABLE sales ADD COLUMN wholesale_customer_id INT NULL",
        "ALTER TABLE sales ADD COLUMN discount_percent FLOAT DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN subtotal FLOAT DEFAULT 0",
        "ALTER TABLE sale_items ADD COLUMN discount FLOAT DEFAULT 0",
        "ALTER TABLE suppliers ADD COLUMN credit_limit FLOAT DEFAULT 0",
        "ALTER TABLE suppliers ADD COLUMN balance FLOAT DEFAULT 0",
        "ALTER TABLE suppliers ADD COLUMN status VARCHAR(10) DEFAULT 'active'",
        "ALTER TABLE wholesale_customers ADD COLUMN business VARCHAR(200)",
        "ALTER TABLE wholesale_customers ADD COLUMN phone VARCHAR(20)",
        "ALTER TABLE wholesale_customers ADD COLUMN email VARCHAR(120)",
        "ALTER TABLE wholesale_customers ADD COLUMN address TEXT",
        "ALTER TABLE wholesale_customers ADD COLUMN credit_limit FLOAT DEFAULT 0",
        "ALTER TABLE wholesale_customers ADD COLUMN balance FLOAT DEFAULT 0",
        "ALTER TABLE wholesale_customers ADD COLUMN status VARCHAR(10) DEFAULT 'active'",
        "ALTER TABLE wholesale_customers ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
    ]),
    Migration(3, 'Stock movements and held orders', [
        "ALTER TABLE stock_movements ADD COLUMN note VARCHAR(200)",
        "ALTER TABLE held_orders ADD COLUMN label VARCHAR(100) NULL",
        "ALTER TABLE held_orders ADD COLUMN cart_json TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE held_orders ADD COLUMN cashier_id INT NULL",
        "ALTER TABLE held_orders ADD COLUMN wholesale_customer_id INT NULL",
        "ALTER TABLE held_orders ADD COLUMN discount FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE held_orders ADD COLUMN discount_percent FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE held_orders ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
    ]),
    Migration(4, 'Purchases and returns', [
        "ALTER TABLE purchases ADD COLUMN grn_number VARCHAR(40)",
        "ALTER TABLE purchases ADD COLUMN supplier_id INT NULL",
        "ALTER TABLE purchases ADD COLUMN purchase_date DATETIME DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE purchases ADD COLUMN total_amount FLOAT DEFAULT 0",
        "ALTER TABLE purchases ADD COLUMN notes TEXT",
        "ALTER TABLE purchases ADD COLUMN status VARCHAR(20) DEFAULT 'received'",
        "ALTER TABLE purchases ADD COLUMN created_by INT NULL",
        "ALTER TABLE purchase_items ADD COLUMN purchase_id INT NULL",
        "ALTER TABLE purchase_items ADD COLUMN product_id INT NULL",
        "ALTER TABLE purchase_items ADD COLUMN quantity FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE purchase_items ADD COLUMN unit_cost FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE purchase_items ADD COLUMN total FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE product_returns ADD COLUMN return_number VARCHAR(40)",
        "ALTER TABLE product_returns ADD COLUMN sale_id INT NULL",
        "ALTER TABLE product_returns ADD COLUMN return_date DATETIME DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE product_returns ADD COLUMN total_amount FLOAT DEFAULT 0",
        "ALTER TABLE product_returns ADD COLUMN reason VARCHAR(200)",
        "ALTER TABLE product_returns ADD COLUMN status VARCHAR(20) DEFAULT 'completed'",
        "ALTER TABLE product_returns ADD COLUMN processed_by INT NULL",
        "ALTER TABLE return_items ADD COLUMN return_id INT NULL",
        "ALTER TABLE return_items ADD COLUMN product_id INT NULL",
        "ALTER TABLE return_items ADD COLUMN quantity FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE return_items ADD COLUMN price FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE return_items ADD COLUMN total FLOAT NOT NULL DEFAULT 0",
    ]),
    Migration(5, 'v3.1 business logic improvements', [
        "ALTER TABLE sales ADD COLUMN tendered FLOAT DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN change_amount FLOAT DEFAULT 0",
        "ALTER TABLE wholesale_customers ADD COLUMN on_hold TINYINT(1) DEFAULT 0",
        "ALTER TABLE purchases ADD COLUMN paid_amount FLOAT DEFAULT 0",
        "ALTER TABLE purchases ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'",
        "ALTER TABLE product_returns ADD COLUMN return_type VARCHAR(20) DEFAULT 'refund'",
        "ALTER TABLE product_returns ADD COLUMN restock TINYINT(1) DEFAULT 1",
        "ALTER TABLE product_returns ADD COLUMN refund_amount FLOAT DEFAULT 0",
        "ALTER TABLE product_returns ADD COLUMN exchange_invoice VARCHAR(40)",
    ]),
    Migration(6, 'v4.1 force_password_change and audit log columns', [
        "ALTER TABLE users ADD COLUMN force_password_change TINYINT(1) DEFAULT 0",
        "ALTER TABLE user_logs ADD COLUMN target_type VARCHAR(80)",
        "ALTER TABLE user_logs ADD COLUMN target_id INT NULL",
        "ALTER TABLE user_logs ADD COLUMN metadata_summary VARCHAR(255)",
        "ALTER TABLE backup_logs ADD COLUMN backup_date DATETIME DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE backup_logs ADD COLUMN backup_file VARCHAR(200)",
        "ALTER TABLE backup_logs ADD COLUMN file_size VARCHAR(50)",
        "ALTER TABLE backup_logs ADD COLUMN status VARCHAR(20) DEFAULT 'pending'",
        "ALTER TABLE backup_logs ADD COLUMN destination VARCHAR(50) DEFAULT 'local'",
        "ALTER TABLE backup_logs ADD COLUMN gdrive_file_id VARCHAR(200)",
        "ALTER TABLE backup_logs ADD COLUMN notes VARCHAR(200)",
    ]),
    Migration(7, 'v4.2 warranty management', [
        "ALTER TABLE products ADD COLUMN warranty_period VARCHAR(50) DEFAULT 'none'",
        "ALTER TABLE sale_items ADD COLUMN warranty_period VARCHAR(50) DEFAULT 'none'",
        "ALTER TABLE sale_items ADD COLUMN warranty_expiry_date DATE NULL",
    ]),
    Migration(8, 'v5.0 garage / IMEI features', [
        "ALTER TABLE products ADD COLUMN is_imei_tracked TINYINT(1) DEFAULT 0",
        "ALTER TABLE products ADD COLUMN product_type VARCHAR(20) DEFAULT 'normal'",
        "ALTER TABLE products ADD COLUMN brand_id INT NULL",
        "ALTER TABLE sale_items ADD COLUMN serial_number VARCHAR(80) NULL",
        "ALTER TABLE sales ADD COLUMN retail_customer_id INT NULL",
        "ALTER TABLE sales ADD COLUMN customer_name_snapshot VARCHAR(150)",
        "ALTER TABLE sales ADD COLUMN customer_phone_snapshot VARCHAR(20)",
        "ALTER TABLE payments ADD COLUMN customer_id INT NULL",
        "ALTER TABLE repair_payments ADD COLUMN customer_id INT NULL",
        "ALTER TABLE retail_customers ADD COLUMN customer_code VARCHAR(30)",
        "ALTER TABLE retail_customers ADD COLUMN phone_normalized VARCHAR(20)",
        "ALTER TABLE vehicle_history ADD COLUMN customer_name_snapshot VARCHAR(150)",
        "ALTER TABLE vehicle_history ADD COLUMN customer_phone_snapshot VARCHAR(20)",
    ]),
    Migration(9, 'v6.0 garage vehicle fields on repair_jobs', [
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_make VARCHAR(80)",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_type VARCHAR(40) DEFAULT 'other'",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_brand_id INT NULL",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_model VARCHAR(80)",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_reg_no VARCHAR(20)",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_color VARCHAR(40)",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_year INTEGER",
        "ALTER TABLE repair_jobs ADD COLUMN vehicle_vin VARCHAR(20)",
        "ALTER TABLE repair_jobs ADD COLUMN odometer_in INTEGER",
        "ALTER TABLE repair_jobs ADD COLUMN odometer_out INTEGER",
        "ALTER TABLE repair_jobs ADD COLUMN fuel_level_in VARCHAR(10)",
        "ALTER TABLE repair_jobs ADD COLUMN service_type VARCHAR(40)",
        "ALTER TABLE repair_jobs ADD COLUMN customer_name_snapshot VARCHAR(150)",
        "ALTER TABLE repair_jobs ADD COLUMN customer_phone_snapshot VARCHAR(20)",
    ]),
    Migration(10, 'v5.1-5.4 security and session hardening', [
        "ALTER TABLE password_resets ADD COLUMN otp_hash VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN session_token VARCHAR(64) NULL",
        "ALTER TABLE users ADD COLUMN is_admin TINYINT(1) DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_owner TINYINT(1) DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_primary_admin TINYINT(1) DEFAULT 0",
    ]),
    Migration(11, 'v5.3 repair job schema hardening', [
        "ALTER TABLE repair_jobs ADD COLUMN broker_id INT NULL",
        "ALTER TABLE repair_jobs ADD COLUMN broker_name_snapshot VARCHAR(150)",
        "ALTER TABLE repair_jobs ADD COLUMN broker_commission_type VARCHAR(20)",
        "ALTER TABLE repair_jobs ADD COLUMN broker_commission_value NUMERIC(14,2) DEFAULT 0",
        "ALTER TABLE repair_jobs ADD COLUMN broker_commission_amount NUMERIC(14,2) DEFAULT 0",
        "ALTER TABLE repair_jobs ADD COLUMN broker_cash_price NUMERIC(14,2) DEFAULT 0",
        "ALTER TABLE repair_jobs ADD COLUMN technician_id INT NULL",
        "ALTER TABLE repair_jobs ADD COLUMN technician_name_snapshot VARCHAR(150)",
        "ALTER TABLE repair_jobs ADD COLUMN customer_id INT NULL",
        "ALTER TABLE repair_jobs ADD COLUMN customer_name VARCHAR(150)",
        "ALTER TABLE repair_jobs ADD COLUMN customer_phone VARCHAR(20)",
        "ALTER TABLE repair_jobs ADD COLUMN notes TEXT",
        "ALTER TABLE repair_jobs ADD COLUMN completed_date DATETIME",
        "ALTER TABLE repair_jobs ADD COLUMN promised_date DATE",
        "ALTER TABLE repair_jobs ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'",
        "ALTER TABLE repair_jobs ADD COLUMN advance_paid NUMERIC(14,2) DEFAULT 0",
    ]),
    Migration(12, 'v6.1 IMEI and card payment columns', [
        "ALTER TABLE sale_items ADD COLUMN imei_record_id INT NULL",
        "ALTER TABLE sale_items ADD COLUMN imei VARCHAR(20) NULL",
        "ALTER TABLE sale_items ADD COLUMN imei2 VARCHAR(20) NULL",
        "ALTER TABLE payments ADD COLUMN terminal_type VARCHAR(40)",
        "ALTER TABLE payments ADD COLUMN provider_name VARCHAR(120)",
        "ALTER TABLE payments ADD COLUMN terminal_id VARCHAR(80)",
        "ALTER TABLE payments ADD COLUMN merchant_id VARCHAR(80)",
        "ALTER TABLE payments ADD COLUMN card_type VARCHAR(40)",
        "ALTER TABLE payments ADD COLUMN card_last4 VARCHAR(4)",
        "ALTER TABLE payments ADD COLUMN approval_code VARCHAR(80)",
        "ALTER TABLE payments ADD COLUMN rrn_reference VARCHAR(120)",
        "ALTER TABLE payments ADD COLUMN terminal_status VARCHAR(30)",
        "ALTER TABLE payments ADD COLUMN terminal_timestamp DATETIME",
        "ALTER TABLE payments ADD COLUMN client_txn_key VARCHAR(120)",
        "ALTER TABLE payments ADD COLUMN terminal_note VARCHAR(200)",
        "ALTER TABLE payments ADD COLUMN gateway_ref VARCHAR(200)",
    ]),
    Migration(13, 'v6.2 installment and broker tables', [
        "ALTER TABLE installment_plans ADD COLUMN broker_id INT NULL",
        "ALTER TABLE installment_plans ADD COLUMN broker_name_snapshot VARCHAR(150)",
        "ALTER TABLE installment_plans ADD COLUMN broker_commission_type VARCHAR(20)",
        "ALTER TABLE installment_plans ADD COLUMN broker_commission_value NUMERIC(14,2) DEFAULT 0",
        "ALTER TABLE installment_plans ADD COLUMN broker_cash_price NUMERIC(14,2) DEFAULT 0",
    ]),
    Migration(14, 'v6.3 trade-in columns', [
        "ALTER TABLE sales ADD COLUMN trade_in_id INT NULL",
        "ALTER TABLE sales ADD COLUMN trade_in_value NUMERIC(14,2) DEFAULT 0",
    ]),
    Migration(15, 'v6.4 performance indexes', [
        "CREATE INDEX IF NOT EXISTS idx_sales_sale_date ON sales(sale_date)",
        "CREATE INDEX IF NOT EXISTS idx_sales_cashier_id ON sales(cashier_id)",
        "CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id ON sale_items(sale_id)",
        "CREATE INDEX IF NOT EXISTS idx_sale_items_product_id ON sale_items(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_sale_id ON payments(sale_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_method ON payments(method)",
        "CREATE INDEX IF NOT EXISTS idx_repair_jobs_status ON repair_jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_repair_jobs_received_date ON repair_jobs(received_date)",
        "CREATE INDEX IF NOT EXISTS idx_repair_jobs_customer_id ON repair_jobs(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_repair_jobs_vehicle_reg_no ON repair_jobs(vehicle_reg_no)",
        "CREATE INDEX IF NOT EXISTS idx_user_logs_user_id ON user_logs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_logs_date ON user_logs(date)",
        "CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)",
        "CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_product_id ON stock_movements(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_date ON stock_movements(date)",
        "CREATE INDEX IF NOT EXISTS idx_purchases_purchase_date ON purchases(purchase_date)",
        "CREATE INDEX IF NOT EXISTS idx_purchases_supplier_id ON purchases(supplier_id)",
    ]),
    Migration(16, 'v6.5 atomic sequences table', [
        """
        CREATE TABLE IF NOT EXISTS doc_sequences (
            prefix      TEXT    NOT NULL,
            last_seq    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (prefix)
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_sequences_prefix ON doc_sequences(prefix)",
    ]),
    Migration(17, 'v6.6 retail customer indexes', [
        "CREATE INDEX IF NOT EXISTS idx_retail_customers_phone ON retail_customers(phone_normalized)",
        "CREATE INDEX IF NOT EXISTS idx_retail_customers_customer_code ON retail_customers(customer_code)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_history_reg_no ON vehicle_history(reg_no)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_history_job_id ON vehicle_history(job_id)",
    ]),
    Migration(18, 'v6.7 wholesale retail link backfill marker', [
        "CREATE INDEX IF NOT EXISTS idx_wholesale_customers_retail_customer_id ON wholesale_customers(retail_customer_id)",
    ]),
    Migration(19, 'v8.1 add retail_customer_id to wholesale_customers', [
        "ALTER TABLE wholesale_customers ADD COLUMN retail_customer_id INTEGER NULL",
        "CREATE INDEX IF NOT EXISTS idx_wholesale_customers_retail_customer_id ON wholesale_customers(retail_customer_id)",
    ]),
    Migration(20, 'v8.2 stock tracking mode for products', [
        "ALTER TABLE products ADD COLUMN stock_tracking_type VARCHAR(30) NOT NULL DEFAULT 'QUANTITY_TRACKED'",
        "ALTER TABLE products ADD COLUMN availability_status VARCHAR(20) NOT NULL DEFAULT 'IN_STOCK'",
        "ALTER TABLE products ADD COLUMN stock_note VARCHAR(200)",
    ]),
    Migration(21, 'v8.3 auto discount rules and sale item discount metadata', [
        """
        CREATE TABLE IF NOT EXISTS auto_discount_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(120) NOT NULL,
            min_price NUMERIC(18,2) NOT NULL,
            max_price NUMERIC(18,2) NOT NULL,
            discount_percent NUMERIC(5,2) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "ALTER TABLE sale_items ADD COLUMN discount_percent NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE sale_items ADD COLUMN discount_source VARCHAR(20) DEFAULT 'none'",
        "ALTER TABLE sale_items ADD COLUMN auto_discount_rule_id INTEGER NULL",
    ]),
]


# ── Pre-migration backup ───────────────────────────────────────────────────────

def _backup_db_before_migration() -> str | None:
    """Create a timestamped backup of the live DB before any migrations run.

    Returns the backup file path (str) on success, or None on failure.
    Never raises — a backup failure is logged as a warning, not a crash.
    """
    try:
        from database import resolve_database_path  # local import — avoids circularity
        from runtime_paths import persistent_app_dir

        db_path = Path(resolve_database_path())
        if not db_path.exists() or db_path.stat().st_size == 0:
            return None

        backups_dir = persistent_app_dir() / 'backups'
        backups_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        backup_path = backups_dir / f'supermart_premigration_{stamp}.db'

        src_conn = sqlite3.connect(str(db_path), timeout=5)
        dst_conn = sqlite3.connect(str(backup_path), timeout=5)
        try:
            src_conn.backup(dst_conn, pages=200)
        finally:
            dst_conn.close()
            src_conn.close()

        logger.info('[MIGRATE] Pre-migration backup created: %s', backup_path.name)
        return str(backup_path)
    except Exception as exc:
        logger.warning('[MIGRATE] Pre-migration backup failed (continuing): %s', exc)
        return None


def _restore_from_backup(backup_path: str) -> bool:
    """Overwrite the live DB with *backup_path* to roll back a failed migration.

    Returns True on success, False on failure.
    """
    try:
        from database import resolve_database_path
        db_path = Path(resolve_database_path())
        src_conn = sqlite3.connect(backup_path, timeout=5)
        dst_conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            src_conn.backup(dst_conn, pages=200)
        finally:
            dst_conn.close()
            src_conn.close()
        logger.info('[MIGRATE] DB restored from backup: %s', backup_path)
        return True
    except Exception as exc:
        logger.error('[MIGRATE] DB restore FAILED: %s', exc)
        return False


# ── Runner ─────────────────────────────────────────────────────────────────────

def _ensure_migration_table(session) -> None:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version      INTEGER  PRIMARY KEY,
            description  TEXT     NOT NULL,
            applied_at   TEXT     NOT NULL,
            status       TEXT     NOT NULL DEFAULT 'applied'
        )
    """))
    # Add status column on existing installations that pre-date this schema.
    try:
        session.execute(text(
            "ALTER TABLE schema_migrations ADD COLUMN status TEXT NOT NULL DEFAULT 'applied'"
        ))
        session.commit()
    except Exception:
        pass  # column already exists — idempotent


def _applied_versions(session) -> set[int]:
    try:
        rows = session.execute(text("SELECT version FROM schema_migrations")).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _run_statement(session, sql: str) -> None:
    sql = sql.strip()
    if not sql:
        return
    try:
        session.execute(text(sql))
    except OperationalError as exc:
        msg = str(exc).lower()
        # SQLite raises "duplicate column" for ADD COLUMN — idempotent, safe to ignore
        if 'duplicate column' in msg or 'already exists' in msg:
            return
        raise


def run_migrations(session) -> list[int]:
    """Run any pending migrations inside *session*.

    Before the first migration is applied a timestamped backup is created in
    <persistent_app_dir>/backups/supermart_premigration_*.db.  If a migration
    fails hard the DB is restored from that backup and a RuntimeError is raised
    so the caller can surface a clear error to the operator.

    Returns a list of version numbers that were successfully applied.
    """
    _ensure_migration_table(session)
    applied = _applied_versions(session)
    pending = [m for m in sorted(MIGRATIONS, key=lambda m: m.version) if m.version not in applied]

    if not pending:
        return []

    # Take a single pre-migration backup before touching anything.
    premig_backup: str | None = _backup_db_before_migration()

    newly_applied: list[int] = []

    for migration in pending:
        logger.info(
            '[MIGRATE] Applying v%d: %s',
            migration.version,
            migration.description,
        )

        applied_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        migration_ok = True

        for sql in migration.statements:
            try:
                _run_statement(session, sql)
            except Exception as exc:
                logger.warning(
                    '[MIGRATE] v%d statement failed (continuing): %.120s — %s',
                    migration.version,
                    sql,
                    exc,
                )
                # Statement-level failures are non-fatal (e.g. duplicate column).
                # A hard failure at the session level would bubble up below.

        # Record outcome in schema_migrations.
        try:
            session.execute(
                text("""
                    INSERT OR IGNORE INTO schema_migrations
                        (version, description, applied_at, status)
                    VALUES (:v, :d, :a, :s)
                """),
                {
                    'v': migration.version,
                    'd': migration.description,
                    'a': applied_at,
                    's': 'applied' if migration_ok else 'failed',
                },
            )
            session.commit()
            newly_applied.append(migration.version)
        except Exception as exc:
            logger.error(
                '[MIGRATE] Failed to record migration v%d: %s', migration.version, exc
            )
            try:
                session.rollback()
            except Exception:
                pass

            if premig_backup:
                logger.error(
                    '[MIGRATE] Attempting DB restore from pre-migration backup: %s',
                    premig_backup,
                )
                _restore_from_backup(premig_backup)
            raise RuntimeError(
                f'Migration v{migration.version} ("{migration.description}") failed: {exc}. '
                + (
                    f'DB has been restored from {premig_backup}.'
                    if premig_backup
                    else 'No pre-migration backup was available for rollback.'
                )
            ) from exc

    if newly_applied:
        logger.info('[MIGRATE] Applied %d migration(s): %s', len(newly_applied), newly_applied)
    if 18 in newly_applied:
        try:
            from customer_linking import backfill_wholesale_retail_links
            updated = backfill_wholesale_retail_links(logger=logger)
            session.commit()
            logger.info('[MIGRATE] wholesale->retail link backfill completed updated=%s', updated)
        except Exception as exc:
            session.rollback()
            logger.warning('[MIGRATE] wholesale->retail link backfill failed: %s', exc)

    return newly_applied


def migration_status(session) -> list[dict]:
    """Return status of all migrations (applied or pending)."""
    try:
        _ensure_migration_table(session)
    except Exception:
        pass
    applied = _applied_versions(session)
    return [
        {
            'version': m.version,
            'description': m.description,
            'applied': m.version in applied,
        }
        for m in MIGRATIONS
    ]
