"""Centralised file-based logging configuration for Garage Management System.

Call ``configure_app_logging(app)`` once, after ``RUNTIME_DATA_DIR`` has been
set on ``app.config``, to attach rotating file handlers that survive across
restarts and are easy to share with support staff.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_app_logging(app) -> None:
    """Attach rotating INFO + ERROR file handlers to *app.logger*.

    Two log files are written:
    * ``<RUNTIME_DATA_DIR>/logs/app.log``   – INFO and above (general activity)
    * ``<RUNTIME_DATA_DIR>/logs/errors.log`` – ERROR and above (crash detail)

    Each file rotates at 2 MB and keeps 5 backups, so at most ~20 MB is used.
    Duplicate handlers are never added; calling this function more than once is
    safe (idempotent).
    """
    log_dir = Path(app.config['RUNTIME_DATA_DIR']) / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
    )

    app_handler = RotatingFileHandler(
        log_dir / 'app.log',
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=5,
        encoding='utf-8',
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        log_dir / 'errors.log',
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=5,
        encoding='utf-8',
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Avoid duplicate handlers when the module is reloaded (e.g. Flask reloader)
    if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
        app.logger.addHandler(app_handler)
        app.logger.addHandler(error_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False
    app.logger.info('Logging initialised — log_dir=%s', log_dir)
