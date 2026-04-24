import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine

from runtime_paths import bundle_root, is_frozen, persistent_app_dir


DEFAULT_DATABASE_FILE = 'supermart.db'


def _seed_bundled_database(writable_db_path: Path) -> None:
    """Copy the bundled supermart.db into the writable persistent location on first run.

    Only runs when packaged as a PyInstaller EXE. Never overwrites an existing
    database, so customer data is never lost across restarts or updates.
    """
    if not is_frozen():
        return
    if writable_db_path.exists():
        return
    bundled = bundle_root() / DEFAULT_DATABASE_FILE
    if not bundled.exists():
        return
    writable_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(bundled), str(writable_db_path))


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
    Existing databases are never overwritten.
    """
    db_name = (database_file or os.getenv('DATABASE_FILE', DEFAULT_DATABASE_FILE) or DEFAULT_DATABASE_FILE).strip()
    db_path = Path(db_name)
    if not db_path.is_absolute():
        db_path = persistent_app_dir() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_bundled_database(db_path)
    return str(db_path.resolve())


def sqlite_database_uri(database_file: str | None = None) -> str:
    return f"sqlite:///{resolve_database_path(database_file)}"


def configure_sqlite_app(app) -> str:
    db_path = resolve_database_path()
    app.config['DATABASE_FILE'] = db_path
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return db_path


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
