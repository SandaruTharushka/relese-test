import datetime
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine

from runtime_paths import bundle_root, is_frozen, persistent_app_dir

_startup_logger = logging.getLogger('garage.database')

DEFAULT_DATABASE_FILE = 'supermart.db'

# Tables that indicate a DB contains real customer/operational data.
# A bundled seed DB that already has rows in any of these tables is rejected.
_SEED_REJECT_TABLES = ('users', 'sales', 'sale_items', 'products', 'repair_jobs')
_SEED_MAX_ROWS = 1  # allow at most 1 row (e.g. a single default admin)

_SQLITE_MAGIC = b'SQLite format 3\x00'


def _is_valid_sqlite_file(path: Path) -> bool:
    """Return True only if *path* starts with the SQLite file-format magic bytes."""
    try:
        with path.open('rb') as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _validate_seed_database(bundled_db: Path) -> bool:
    """Return True only if the bundled seed DB is safe to copy as a starter DB.

    Rejects the seed when any checked table has more rows than _SEED_MAX_ROWS,
    which would indicate a customer-populated DB was accidentally packaged.
    """
    try:
        conn = sqlite3.connect(str(bundled_db), timeout=2)
        try:
            cur = conn.cursor()
            for table in _SEED_REJECT_TABLES:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if cur.fetchone() is None:
                    continue  # table doesn't exist yet — seed DB is empty
                cur.execute(f'SELECT COUNT(*) FROM {table}')  # noqa: S608
                row_count = cur.fetchone()[0]
                if row_count > _SEED_MAX_ROWS:
                    _startup_logger.warning(
                        'SEED-DB REJECTED: bundled %s has %d row(s) in table "%s" '
                        '— customer data detected; AppData DB will NOT be seeded.',
                        bundled_db.name,
                        row_count,
                        table,
                    )
                    return False
            return True
        finally:
            conn.close()
    except Exception as exc:
        _startup_logger.warning(
            'Seed-DB validation check failed (%s); proceeding cautiously.', exc
        )
        return False  # fail-safe: do not copy if we cannot validate


def _seed_bundled_database(writable_db_path: Path) -> None:
    """Copy the bundled supermart.db into the writable persistent location on first run.

    Only runs when packaged as a PyInstaller EXE. Never overwrites a valid
    existing database, so customer data is never lost across restarts or updates.
    Validates the bundled DB before copying — if it contains customer rows the
    copy is skipped and a warning is logged.

    If the existing file at writable_db_path is not a valid SQLite database
    (e.g. a zero-byte placeholder or a corrupt file), it is renamed as a dated
    backup and the fresh seed copy proceeds so the app can start cleanly.
    """
    if not is_frozen():
        return
    if writable_db_path.exists():
        if _is_valid_sqlite_file(writable_db_path):
            return  # healthy DB — never overwrite
        # The file exists but is not a valid SQLite database.  Rename it so the
        # operator can inspect it, then fall through to seed a fresh copy.
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bad_path = writable_db_path.with_name(
            f'{writable_db_path.stem}_corrupt_{stamp}{writable_db_path.suffix}'
        )
        try:
            writable_db_path.rename(bad_path)
            _startup_logger.warning(
                'Existing DB at %s is not a valid SQLite file — renamed to %s. '
                'A fresh database will be seeded.',
                writable_db_path,
                bad_path,
            )
        except OSError as exc:
            _startup_logger.error(
                'Cannot rename corrupt DB at %s (%s); seeding skipped.',
                writable_db_path,
                exc,
            )
            return
    bundled = bundle_root() / DEFAULT_DATABASE_FILE
    if not bundled.exists():
        return
    if not _validate_seed_database(bundled):
        _startup_logger.warning(
            'Bundled seed DB at %s was not copied — it failed validation '
            '(either not a valid SQLite file, or it contains customer data). '
            'A fresh empty DB will be created instead.',
            bundled,
        )
        return
    writable_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(bundled), str(writable_db_path))
    _startup_logger.info('Seeded fresh AppData DB from bundle: %s', writable_db_path)


_VALID_SYNC_MODES = {'OFF', 'NORMAL', 'FULL', 'EXTRA'}


def _resolve_synchronous_mode() -> str:
    """Resolve PRAGMA synchronous mode from env, defaulting to NORMAL.

    For production POS deployments that need maximum durability (no risk of
    losing a completed sale on power loss), set SQLITE_SYNCHRONOUS=FULL.
    """
    raw = (os.getenv('SQLITE_SYNCHRONOUS') or 'NORMAL').strip().upper()
    return raw if raw in _VALID_SYNC_MODES else 'NORMAL'


@event.listens_for(Engine, 'connect')
def _enable_sqlite_pragmas(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute(f'PRAGMA synchronous={_resolve_synchronous_mode()}')
        # Guard against runaway queries — report queries on large tables cap at 30s.
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.close()


def resolve_database_path(database_file: str | None = None) -> str:
    """Resolve the SQLite database path, keeping relative paths desktop-friendly.

    When running as a packaged EXE and the writable database does not yet exist,
    the bundled supermart.db is copied here first (first-run seeding).
    Valid existing databases are never overwritten.

    If the resolved path points to a file that is not a valid SQLite database
    (regardless of frozen/dev mode), the corrupt file is renamed as a backup so
    that SQLAlchemy can create a fresh schema instead of crashing.
    """
    db_name = (database_file or os.getenv('DATABASE_FILE', DEFAULT_DATABASE_FILE) or DEFAULT_DATABASE_FILE).strip()
    db_path = Path(db_name)
    if not db_path.is_absolute():
        db_path = persistent_app_dir() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_bundled_database(db_path)
    # Guard for dev mode (or any mode where seeding is skipped): if a file exists
    # at the target path but is not a valid SQLite database, rename it so the app
    # can initialise a fresh schema rather than failing on every request.
    if db_path.exists() and not _is_valid_sqlite_file(db_path):
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bad_path = db_path.with_name(
            f'{db_path.stem}_corrupt_{stamp}{db_path.suffix}'
        )
        try:
            db_path.rename(bad_path)
            _startup_logger.warning(
                'DB file at %s is not a valid SQLite database — renamed to %s. '
                'A fresh schema will be initialised on next startup.',
                db_path,
                bad_path,
            )
        except OSError as exc:
            _startup_logger.error(
                'Cannot rename corrupt DB at %s (%s); the app will likely fail to start.',
                db_path,
                exc,
            )
    return str(db_path.resolve())


def sqlite_database_uri(database_file: str | None = None) -> str:
    return f"sqlite:///{resolve_database_path(database_file)}"


def configure_sqlite_app(app) -> str:
    db_path = resolve_database_path()
    app.config['DATABASE_FILE'] = db_path
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return db_path


def log_startup_info(database_file: str | None = None) -> dict:
    """Log a structured startup summary and return it as a dict.

    Emits one INFO line with: db_path, db_exists, user_count, app_version,
    frozen.  Call this immediately after configure_sqlite_app() so that every
    startup produces an auditable record.
    """
    try:
        from version import APP_VERSION  # local import to avoid circular dep
    except Exception:
        APP_VERSION = 'unknown'

    diag = inspect_sqlite_database(database_file)
    info = {
        'db_path': diag['database_path'],
        'db_exists': diag['database_exists'],
        'users_count': diag.get('users_count'),
        'app_version': APP_VERSION,
        'frozen': is_frozen(),
    }
    _startup_logger.info(
        '[STARTUP] db_path=%s db_exists=%s users=%s version=%s frozen=%s',
        info['db_path'],
        info['db_exists'],
        info['users_count'],
        info['app_version'],
        info['frozen'],
    )
    return info


def inspect_sqlite_database(database_file: str | None = None) -> dict:
    """
    Collect lightweight SQLite diagnostics for logging and supportability.
    """
    db_path = Path(resolve_database_path(database_file))
    diagnostics = {
        'database_path': str(db_path),
        'database_exists': db_path.exists(),
        'parent_exists': db_path.parent.exists(),
        'parent_writable': os.access(db_path.parent, os.W_OK),
        'file_writable': os.access(db_path, os.W_OK) if db_path.exists() else None,
        'users_table_exists': None,
        'users_count': None,
        'connect_error': None,
    }

    if not db_path.exists():
        return diagnostics

    try:
        conn = sqlite3.connect(str(db_path), timeout=1)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            diagnostics['users_table_exists'] = cursor.fetchone() is not None
            if diagnostics['users_table_exists']:
                cursor.execute('SELECT COUNT(*) FROM users')
                diagnostics['users_count'] = cursor.fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        diagnostics['connect_error'] = str(exc)

    return diagnostics
