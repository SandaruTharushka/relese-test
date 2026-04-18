"""Startup health checks for SuperMart POS desktop runtime.

Call ``run_startup_checks(app)`` after app config is ready. The function returns
human-readable issue strings (empty list = all good) and logs each issue.
"""

from pathlib import Path

CRITICAL_SUBDIRECTORIES = (
    'logs',
    'backups',
    'uploads',
    'logos',
    'invoices',
    'exports',
    'config',
    'license',
)


def run_startup_checks(app) -> list[str]:
    """Run pre-flight checks and return a list of issue strings.

    Side-effects: required directories are created if they are missing.
    """
    issues: list[str] = []

    runtime_dir = Path(app.config['RUNTIME_DATA_DIR'])
    _ensure_dir(runtime_dir, issues)

    for name in CRITICAL_SUBDIRECTORIES:
        _ensure_dir(runtime_dir / name, issues)

    for folder in (runtime_dir, *(runtime_dir / name for name in CRITICAL_SUBDIRECTORIES)):
        _check_writable(folder, issues)

    db_file = app.config.get('DATABASE_FILE')
    if db_file:
        db_parent = Path(db_file).parent
        _ensure_dir(db_parent, issues)
        _check_writable(db_parent, issues)

    env_file = app.config.get('RUNTIME_ENV_FILE')
    if env_file:
        env_parent = Path(env_file).parent
        _ensure_dir(env_parent, issues)
        _check_writable(env_parent, issues)

    if issues:
        for issue in issues:
            app.logger.warning('Startup check issue: %s', issue)
        app.logger.warning(
            'Startup completed with %d issue(s) — limited functionality may occur until fixed.',
            len(issues),
        )
    else:
        app.logger.info('Startup checks passed — runtime directories and files are writable.')

    return issues


def _ensure_dir(path: Path, issues: list[str]) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        issues.append(f'Cannot create directory {path}: {exc}')


def _check_writable(path: Path, issues: list[str]) -> None:
    if not path.exists():
        return
    test_file = path / '.write_test'
    try:
        test_file.write_text('ok', encoding='utf-8')
        test_file.unlink(missing_ok=True)
    except Exception as exc:
        issues.append(f'Directory not writable {path}: {exc}')
