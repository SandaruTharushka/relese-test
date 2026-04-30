import os, sys, json, subprocess, threading, webbrowser, random, tempfile, re, shutil, secrets, time, math, zipfile, hashlib, unicodedata
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, date as date_type, timezone
from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, flash, abort, session, Response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_wtf.csrf import CSRFError, CSRFProtect
from flask_talisman import Talisman
from dotenv import load_dotenv
from sqlalchemy import func, extract, text, inspect as sa_inspect, or_
from sqlalchemy.exc import SQLAlchemyError
from models import (db, User, Category, Supplier, Product,
                    Sale, SaleItem, Payment, StockMovement, UserLog, PasswordReset,
                    WholesaleCustomer, StoreSettings, SupplierTransaction,
                    WholesaleTransaction, HeldOrder,
                    Purchase, PurchaseItem, ProductReturn, ReturnItem,
                    ProductBarcode, BackupLog,
                    RetailCustomer,
                    ProductVariantGroup, ProductVariant,
                    RepairJob, RepairJobPart, RepairPayment, VehicleInspection, VehicleHistory,
                    VehicleBrand,
                    InstallmentPlan, Installment,
                    Broker, BrokerCommissionPayment,
                    ExpenseCategory, ExpenseEntry)
from customer_routes import register_customer_routes
from variant_routes import register_variant_routes
from repair_routes import register_repair_routes
from vehicle_routes import register_vehicle_routes
from installment_routes import register_installment_routes
from payhere import build_payment_data, verify_notify, PAYHERE_ENDPOINT, is_sandbox_mode
from backup import backup_bp, start_auto_backup_scheduler
from database import configure_sqlite_app, inspect_sqlite_database
from runtime_paths import ensure_persistent_app_structure, persistent_app_dir, persistent_path, resource_path
from logging_setup import configure_app_logging
from reset_utils import delete_all_rows, ordered_delete_tables
from startup_checks import run_startup_checks
from printer_routes import register_printer_routes
from settings_routes import register_settings_routes
from reports_routes import register_reports_routes
from service_analytics_routes import register_service_analytics_routes
from suppliers_wholesale_routes import register_suppliers_wholesale_routes
from purchases_returns_routes import register_purchases_returns_routes
from sales_routes import register_sales_routes
from broker_routes import register_broker_routes
from expense_routes import register_expense_routes, seed_default_expense_categories
# ── New canonical printing route modules ──────────────────────────────
from routes.printing_settings_routes import register_printing_settings_routes
from routes.receipt_print_routes import register_receipt_print_routes
from routes.label_print_routes import register_label_print_routes
from routes.barcode_routes import register_barcode_routes
from routes.diagnostics_routes import register_diagnostics_routes
from update_routes import update_bp
from version import __version__
import license as lic_module
from services.printer_service import PrinterService
from services.printing.label_printer import LabelPrintExecutionService
from services.barcode_scanner_service import BarcodeService, ScannerService, ProductFillService, LabelLayoutService

from shared_helpers import (
    WARRANTY_DAYS,
    _parse_env_key,
    _quote_env_value,
    _summarize_audit_metadata,
    contains_sql_like,
    decode_barcode,
    validate_password_strength,
    is_admin_role,
    is_pos_operator_role,
    is_admin,
    normalize_role,
    role_from_user,
    user_has_any_role,
)

ENV_FILE_PATH = persistent_path('.env')
LEGACY_INSECURE_SECRET_KEY = 'supermart-pos-secret-2026'
_INSECURE_SECRET_KEY_PREFIXES = ('supermart-pos-secret-', 'change-me', 'changeme', 'secret-key', 'your-secret')
INITIAL_SECURITY_SETUP_KEY = 'security_first_run_pending'
PRIMARY_ADMIN_USER_ID_KEY = 'primary_admin_user_id'
APP_RUNTIME_MODE_ENV = 'APP_RUNTIME_MODE'
DESTRUCTIVE_ADMIN_TOOLS_ENV = 'ENABLE_DESTRUCTIVE_ADMIN_TOOLS'
DEFAULT_IDLE_TIMEOUT_MINUTES  = 12 * 60
DEFAULT_REMEMBER_DAYS         = 30
OTP_EXPIRY_MINUTES            = 5
OTP_MAX_ATTEMPTS              = 5
AUDIT_LOGS_DEFAULT_PER_PAGE   = 50
DB_HEALTH_RECHECK_INTERVAL    = 50
BARCODE_EAN13_ATTEMPTS        = 10
RECENT_SALES_LIMIT            = 8
RECENT_MOVEMENTS_LIMIT        = 30
TOP_PRODUCTS_LIMIT            = 5
PROFIT_TOP_PRODUCTS_LIMIT     = 50
RUNTIME_ENV_DEFAULTS = {
    APP_RUNTIME_MODE_ENV: 'production',
}

PASSWORD_RESET_RATE_LIMITS = {
    'forgot_password:ip': {'limit': 5, 'window_seconds': 15 * 60},
    'forgot_password:identity': {'limit': 3, 'window_seconds': 15 * 60},
    'verify_otp:ip': {'limit': 10, 'window_seconds': 10 * 60},
    'verify_otp:identity': {'limit': 5, 'window_seconds': 10 * 60},
}
_PASSWORD_RESET_RATE_LIMIT_STORE = {}
_PASSWORD_RESET_RATE_LIMIT_LOCK = threading.Lock()
_LOW_STOCK_CACHE_TTL_SECONDS = 60
_low_stock_cache = {'count': 0, 'expires': 0}
_low_stock_cache_lock = threading.Lock()
HARDWARE_SETTINGS_FILE = persistent_path('config', 'hardware_settings.json')

def _invalidate_low_stock_cache():
    with _low_stock_cache_lock:
        _low_stock_cache['expires'] = 0

def _get_low_stock_count():
    now = time.time()
    with _low_stock_cache_lock:
        if _low_stock_cache['expires'] > now:
            return _low_stock_cache['count']
        cached_count = _low_stock_cache['count']

    try:
        fresh_count = Product.query.filter(
            Product.stock_qty <= Product.low_stock_lvl,
            Product.status == 'active'
        ).count()
    except Exception:
        return cached_count

    with _low_stock_cache_lock:
        _low_stock_cache['count'] = fresh_count
        _low_stock_cache['expires'] = now + _LOW_STOCK_CACHE_TTL_SECONDS
        return _low_stock_cache['count']


def _is_card_terminal_connected_for_checkout() -> bool:
    """Non-blocking check using the in-memory CardTerminalService state."""
    try:
        return _card_terminal_svc.is_connected
    except Exception:
        return False

def role_rank(role):
    hierarchy = {
        'Cashier': 0,
        'Operator': 1,
        'Manager': 2,
        'Admin': 3,
        'Developer': 4,
    }
    return hierarchy.get(normalize_role(role, default=''), -1)


def is_developer_role(role):
    return normalize_role(role, default='').lower() == 'developer'


def _permission_debug_context():
    return {
        'username': getattr(current_user, 'username', 'anonymous'),
        'role': role_from_user(current_user, default=''),
    }


def _log_permission_result(allowed, *, allowed_roles=None):
    context = _permission_debug_context()
    app.logger.info(
        '[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s',
        context['username'],
        context['role'],
        bool(allowed),
        ','.join(allowed_roles or []),
        request.path,
    )


def require_roles(*roles):
    allowed = user_has_any_role(current_user, *roles)
    _log_permission_result(allowed, allowed_roles=[normalize_role(r, default='') for r in roles])
    if not allowed:
        abort(403)

def ensure_env_file():
    env_dir = os.path.dirname(ENV_FILE_PATH)
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
    if not os.path.exists(ENV_FILE_PATH):
        generated_secret = secrets.token_urlsafe(64)
        template = (
            "# Auto-generated by Garage Management System on first run.\n"
            "# Configure these settings via the Settings menu in the application.\n"
            "# DO NOT share this file or commit it to version control.\n"
            "\n"
            f"SECRET_KEY={generated_secret}\n"
            "APP_RUNTIME_MODE=production\n"
            "\n"
            "# PayHere — configure via Settings if you use online payments\n"
            "PAYHERE_MERCHANT_ID=\n"
            "PAYHERE_MERCHANT_SECRET=\n"
            "PAYHERE_SANDBOX=false\n"
            "\n"
            "SESSION_COOKIE_SECURE=0\n"
            "REMEMBER_COOKIE_SECURE=0\n"
        )
        with open(ENV_FILE_PATH, 'w', encoding='utf-8', newline='\n') as f:
            f.write(template)

def ensure_runtime_env_defaults(env_path=ENV_FILE_PATH):
    missing = {}
    env_file = Path(env_path)
    try:
        existing_lines = env_file.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        existing_lines = []

    existing_keys = {_parse_env_key(line) for line in existing_lines}
    for key, value in RUNTIME_ENV_DEFAULTS.items():
        if key not in existing_keys:
            missing[key] = value
            os.environ.setdefault(key, value)

    if missing:
        try:
            update_env_file(missing, env_path=env_path)
        except OSError:
            # Keep startup working even if the runtime .env file is temporarily unavailable.
            pass

def update_env_file(updates, env_path=ENV_FILE_PATH):
    """
    Safely create/update .env keys without removing unrelated values.
    - Preserves comments and non-target keys
    - De-duplicates updated keys
    - Uses atomic replace for Windows/Linux safety
    """
    ensure_env_file()
    env_file = Path(env_path)
    parent = env_file.parent
    parent.mkdir(parents=True, exist_ok=True)

    try:
        existing_lines = env_file.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        existing_lines = []
    except OSError as exc:
        raise OSError(f'Unable to read .env file: {exc}') from exc

    updates = {str(k): '' if v is None else str(v) for k, v in updates.items()}
    found_keys = set()
    output_lines = []

    for line in existing_lines:
        key = _parse_env_key(line)
        if key in updates:
            if key in found_keys:
                continue
            output_lines.append(f'{key}={_quote_env_value(updates[key])}')
            found_keys.add(key)
        else:
            output_lines.append(line)

    for key, value in updates.items():
        if key not in found_keys:
            output_lines.append(f'{key}={_quote_env_value(value)}')

    content = '\n'.join(output_lines).rstrip() + '\n'
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=str(parent), newline='\n') as tf:
            tf.write(content)
            temp_name = tf.name
        os.replace(temp_name, env_file)
    except OSError as exc:
        raise OSError(f'Unable to write .env file: {exc}') from exc

_PAYHERE_PLACEHOLDER_VALUES = {
    'your_payhere_secret_here',
    'your_secret_here',
    '',
}

def _warn_placeholder_configs(flask_app) -> None:
    """Warn at startup about unconfigured PayHere credentials."""
    ph_secret = (os.getenv('PAYHERE_MERCHANT_SECRET') or '').strip()

    if ph_secret in _PAYHERE_PLACEHOLDER_VALUES:
        flask_app.logger.warning(
            'PayHere secret not configured — PayHere payments will fail. '
            'Update PAYHERE_MERCHANT_SECRET in .env if you use online payments.'
        )

def env_flag_is_enabled(name, default=False):
    raw = os.getenv(name)
    if not raw or not raw.strip():  # Missing or empty → use default; NEVER defaults to enabled
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}

def _is_insecure_secret_key(key: str) -> bool:
    """Return True if the key is a known placeholder or insecure default."""
    if not key:
        return True
    if key == LEGACY_INSECURE_SECRET_KEY:
        return True
    lower = key.lower()
    return any(lower.startswith(prefix) for prefix in _INSECURE_SECRET_KEY_PREFIXES)


def ensure_runtime_secret_key():
    secret_key = (os.getenv('SECRET_KEY') or '').strip()
    if secret_key and not _is_insecure_secret_key(secret_key):
        return secret_key

    generated_secret = secrets.token_urlsafe(64)
    try:
        update_env_file({'SECRET_KEY': generated_secret})
    except OSError:
        # Keep startup working even if the writable runtime folder is temporarily unavailable.
        pass
    os.environ['SECRET_KEY'] = generated_secret
    return generated_secret

def get_idle_timeout_minutes():
    raw = os.getenv('SESSION_IDLE_TIMEOUT_MINUTES', str(DEFAULT_IDLE_TIMEOUT_MINUTES)).strip()
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = DEFAULT_IDLE_TIMEOUT_MINUTES
    return max(15, minutes)

def get_remember_cookie_days():
    raw = os.getenv('REMEMBER_COOKIE_DAYS', str(DEFAULT_REMEMBER_DAYS)).strip()
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = DEFAULT_REMEMBER_DAYS
    return max(1, days)

ensure_persistent_app_structure()
try:
    ensure_env_file()
    load_dotenv(ENV_FILE_PATH)
    ensure_runtime_env_defaults()
except OSError as env_err:
    # Keep startup resilient when the runtime .env file is temporarily unreadable.
    print(f"  [STARTUP] Warning: runtime environment file is not writable: {env_err}")
RUNTIME_SECRET_KEY = ensure_runtime_secret_key()

app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static')
)
Talisman(
    app,
    force_https=False,  # Desktop app uses HTTP on localhost.
    strict_transport_security=False,
    content_security_policy={
        'default-src': ["'self'"],
        'script-src': ["'self'", "'unsafe-inline'", 'https://cdnjs.cloudflare.com', 'https://cdn.jsdelivr.net'],
        'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
        'font-src': ["'self'", 'https://fonts.gstatic.com'],
        'img-src': ["'self'", 'data:', 'https:', 'blob:'],
        'connect-src': ["'self'", 'https://world.openfoodfacts.org'],
        'frame-src': ["'self'", 'blob:'],
        'child-src': ["'self'", 'blob:'],
    },
    referrer_policy='strict-origin',
    frame_options='SAMEORIGIN',
    x_content_type_options=True,
)
app.config['RUNTIME_DATA_DIR'] = str(persistent_app_dir())
app.config['RUNTIME_ENV_FILE'] = ENV_FILE_PATH
app.config['SECRET_KEY'] = RUNTIME_SECRET_KEY
app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = True
app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
app.config.setdefault('SESSION_COOKIE_NAME', 'gms_session')
app.config.setdefault('REMEMBER_COOKIE_HTTPONLY', True)
app.config.setdefault('REMEMBER_COOKIE_SAMESITE', 'Lax')
app.config.setdefault('REMEMBER_COOKIE_NAME', 'gms_remember')
app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(minutes=get_idle_timeout_minutes()))
app.config.setdefault('REMEMBER_COOKIE_DURATION', timedelta(days=get_remember_cookie_days()))
app.config.setdefault('SESSION_REFRESH_EACH_REQUEST', True)
app.config.setdefault('REMEMBER_COOKIE_REFRESH_EACH_REQUEST', True)
_secure_cookies = env_flag_is_enabled('SESSION_COOKIE_SECURE', default=False)
app.config['SESSION_COOKIE_SECURE']  = _secure_cookies
app.config['REMEMBER_COOKIE_SECURE'] = env_flag_is_enabled('REMEMBER_COOKIE_SECURE', default=_secure_cookies)
if not _secure_cookies:
    app.logger.debug(
        'Secure cookies OFF — set SESSION_COOKIE_SECURE=1 in .env when using HTTPS.'
    )
app.config.setdefault('WTF_CSRF_TIME_LIMIT', None)

configure_app_logging(app)
printer_service = PrinterService(app.logger)
label_print_service = LabelPrintExecutionService(app.logger)

from services.settings_service import get_settings_service
from services.card_terminal_service import get_card_terminal_service, CardConfig
_settings_svc = get_settings_service()
_card_terminal_svc = get_card_terminal_service()

def _attempt_printer_autoconnect_on_startup():
    try:
        # Batch-load all settings once instead of 8+ individual queries
        all_settings = _settings_svc.get_all()
        receipt_cfg = {
            'printer_type': all_settings.get('printer_type', 'windows') or 'windows',
            'printer_name': all_settings.get('printer_name', '') or '',
            'printer_ip': all_settings.get('printer_ip', '') or '',
            'printer_port': all_settings.get('printer_port', '9100') or '9100',
        }
        label_cfg = {
            'label_printer_type': all_settings.get('label_printer_type', 'windows') or 'windows',
            'label_printer_name': all_settings.get('label_printer_name', '') or '',
            'label_printer_ip': all_settings.get('label_printer_ip', '') or '',
            'label_printer_port': all_settings.get('label_printer_port', '9100') or '9100',
        }
        receipt = printer_service.resolve_receipt_printer(receipt_cfg)
        label = printer_service.resolve_label_printer(label_cfg)
        app.logger.info(
            'printer_autoconnect_startup receipt_connected=%s receipt_name=%s label_connected=%s label_name=%s',
            receipt.connected,
            receipt.name,
            label.connected,
            label.name,
        )
    except Exception:
        app.logger.warning('Printer startup auto-connect check failed', exc_info=True)

def _attempt_card_terminal_autoconnect():
    """Auto-connect card terminal if enabled and auto_connect setting is on."""
    try:
        cfg_dict = _settings_svc.get_all(prefix='pref_card_')
        card_cfg = CardConfig.from_settings(cfg_dict)
        _card_terminal_svc.configure(card_cfg)
        if card_cfg.enabled and card_cfg.auto_connect:
            ok, msg = _card_terminal_svc.connect()
            app.logger.info('card_terminal_autoconnect ok=%s msg=%s', ok, msg)
    except Exception:
        app.logger.warning('Card terminal auto-connect failed', exc_info=True)
configure_sqlite_app(app)
app.logger.info(
    'Runtime data directory initialized at %s (env=%s db=%s)',
    app.config['RUNTIME_DATA_DIR'],
    app.config['RUNTIME_ENV_FILE'],
    app.config.get('DATABASE_FILE'),
)

db.init_app(app)
with app.app_context():
    _attempt_printer_autoconnect_on_startup()
    _attempt_card_terminal_autoconnect()
app.register_blueprint(backup_bp)
app.register_blueprint(update_bp)
start_auto_backup_scheduler(app)
_warn_placeholder_configs(app)
run_startup_checks(app)

csrf = CSRFProtect()
csrf.init_app(app)

_db_ok = False

# In-memory active session tracker.  Keyed by session id (str) → dict with
# user_id, login_time, last_seen, ip.  Declared here so it is always defined
# before any before_request hook or route handler can reference it.
_active_sessions: dict = {}
_active_sessions_lock = threading.Lock()

@app.route('/health')
def health():
    return 'ok'


def _wants_json_response() -> bool:
    """Return True when the client expects a JSON response."""
    return (
        request.is_json
        or request.path.startswith('/api/')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _normalize_email(raw_email: str) -> str:
    return (raw_email or '').strip().lower()


def _find_user_for_login(raw_identifier: str) -> User | None:
    """Find a user by username or email with case-insensitive fallbacks."""
    identifier = (raw_identifier or '').strip()
    if not identifier:
        return None

    lower_identifier = identifier.lower()
    if '@' in identifier:
        user = User.query.filter(func.lower(User.email) == lower_identifier).first()
        if user:
            return user

    # Primary lookup for username.
    user = User.query.filter_by(username=identifier).first()
    if user:
        return user

    # Backward-compatible fallback for historical case-mismatch data entry.
    candidates = User.query.filter(func.lower(User.username) == lower_identifier).all()
    if len(candidates) == 1:
        return candidates[0]

    # Final fallback: case-insensitive email lookup for non-email-like input.
    email_candidates = User.query.filter(func.lower(User.email) == lower_identifier).all()
    if len(email_candidates) == 1:
        return email_candidates[0]
    return None


def _upgrade_legacy_password_hash_if_needed(user: User, plain_password: str) -> bool:
    """
    Re-hash legacy/plaintext credentials into Werkzeug format after a successful
    compatibility login.
    Returns True when an upgrade was persisted.
    """
    stored = (getattr(user, 'password', '') or '').strip()
    if not stored:
        return False
    if stored.startswith(('scrypt:', 'pbkdf2:')):
        return False
    if not user.check_password(plain_password):
        return False

    user.set_password(plain_password)
    db.session.commit()
    app.logger.warning(
        '[AUTH] Upgraded legacy password hash format for username=%s',
        user.username,
    )
    return True


def _safe_redirect_target(target: str | None, fallback_endpoint: str) -> str:
    """Return a same-origin redirect target or the fallback URL.

    Defends against open-redirect attacks when we rely on Referer: external
    hosts are rejected and fall back to the supplied endpoint.
    """
    if not target:
        return url_for(fallback_endpoint)
    try:
        from urllib.parse import urlparse
        parsed = urlparse(target)
    except Exception:
        return url_for(fallback_endpoint)
    if parsed.scheme and parsed.netloc:
        host_url = (request.host_url or '').rstrip('/')
        try:
            host = urlparse(host_url).netloc
        except Exception:
            host = ''
        if parsed.netloc != host:
            return url_for(fallback_endpoint)
    return target


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    message = getattr(e, 'description', 'CSRF validation failed.')
    if _wants_json_response():
        # "session token is missing" means the server-side session lost its CSRF
        # token (e.g. secret-key rotation, expired cookie).  Signal the client to
        # reload the page so a fresh token is issued rather than showing a
        # confusing technical error message.
        session_token_missing = 'session token' in message.lower()
        return jsonify({
            'ok': False,
            'msg': 'Your session has expired. The page will reload automatically — please try again.' if session_token_missing else message,
            'csrf_expired': session_token_missing,
        }), 400
    flash(message, 'error')
    return redirect(_safe_redirect_target(request.referrer, 'login'))


@app.errorhandler(400)
def handle_bad_request(e):
    message = getattr(e, 'description', 'Bad request.')
    app.logger.warning('400 Bad Request path=%s message=%s', request.path, message)
    if _wants_json_response():
        return jsonify({'ok': False, 'error': message}), 400
    flash(str(message), 'error')
    return redirect(_safe_redirect_target(request.referrer, 'dashboard'))


@app.errorhandler(403)
def handle_forbidden(e):
    message = 'You do not have permission to perform this action.'
    context = _permission_debug_context()
    app.logger.warning(
        '403 Forbidden path=%s user=%s role=%s allowed=%s',
        request.path,
        context['username'],
        context['role'],
        False,
    )
    if _wants_json_response():
        return jsonify({'ok': False, 'error': message}), 403
    flash(message, 'error')
    return redirect(url_for('dashboard'))


@app.errorhandler(404)
def handle_not_found(e):
    message = 'Requested page or record was not found.'
    app.logger.warning('404 Not Found path=%s', request.path)
    if _wants_json_response():
        return jsonify({'ok': False, 'error': message}), 404
    flash(message, 'warning')
    return redirect(url_for('dashboard'))


@app.errorhandler(SQLAlchemyError)
def handle_database_error(e):
    try:
        db.session.rollback()
    except Exception:
        pass
    app.logger.exception('Database error at path=%s', request.path)
    if _wants_json_response():
        return jsonify({'ok': False, 'error': 'Database operation failed. Please try again.'}), 500
    flash('Database operation failed. Please try again.', 'error')
    return redirect(_safe_redirect_target(request.referrer, 'dashboard'))


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    try:
        db.session.rollback()
    except Exception:
        pass
    app.logger.exception('Unhandled exception at path=%s', request.path)
    if _wants_json_response():
        return jsonify({'ok': False, 'error': 'Unexpected server error. Please try again.'}), 500
    flash('Unexpected error occurred. Please try again.', 'error')
    return redirect(_safe_redirect_target(request.referrer, 'dashboard'))


DB_ERROR_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Garage Management System — Database Error</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#0F172A;display:flex;align-items:center;justify-content:center;min-height:100vh;color:#E2E8F0;padding:20px}
.card{background:#1E293B;border:1px solid #334155;border-radius:20px;padding:48px;max-width:560px;width:100%;text-align:center}
.icon{font-size:64px;margin-bottom:20px}
h1{font-size:24px;font-weight:800;color:#FCA5A5;margin-bottom:12px}
p{font-size:14px;color:#94A3B8;line-height:1.8;margin-bottom:16px}
.steps{text-align:left;background:#0F172A;border-radius:12px;padding:24px;margin:20px 0}
.step{margin-bottom:12px;font-size:13px;color:#CBD5E1}
.step b{color:#4F6CF6}
.step code{background:#334155;padding:2px 8px;border-radius:4px;font-size:12px;color:#7C93F8}
.btn{display:inline-block;padding:12px 32px;margin-top:16px;background:linear-gradient(135deg,#4F6CF6,#7C3AED);border:none;border-radius:12px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none}
.error-detail{font-size:11px;color:#475569;margin-top:20px;background:#0F172A;padding:12px;border-radius:8px;word-break:break-all;max-height:80px;overflow:auto}
</style></head>
<body><div class="card">
  <div class="icon">🔴</div>
  <h1>Database Connection Failed</h1>
  <p>Garage Management System cannot open the configured database.<br>Please follow these steps:</p>
  <div class="steps">
    <div class="step"><b>Step 1:</b> If using SQLite, ensure app folder is writable</div>
    <div class="step"><b>Step 2:</b> Ensure the configured SQLite folder exists and is writable</div>
    <div class="step"><b>Step 3:</b> Check <code>DATABASE_FILE</code> in <code>.env</code> if you changed it</div>
    <div class="step"><b>Step 4:</b> Click Try Again below</div>
  </div>
  <a class="btn" href="/" onclick="location.reload();return false;">🔄 Try Again</a>
  <div class="error-detail">Error: {{ERROR}}</div>
</div></body></html>"""

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'basic'

# Register current_user as a Jinja2 global so it is available in macros/includes
# (context processors do NOT pass into macros reliably across Jinja2 versions)
app.jinja_env.globals['current_user'] = current_user

@login_manager.user_loader
def load_user(uid):
    try:
        user = db.session.get(User, int(uid))
        if user:
            user.role = normalize_role(getattr(user, 'role', None))
        return user
    except Exception:
        return None

@app.context_processor
def inject_current_user():
    return dict(current_user=current_user)

@app.context_processor
def inject_globals():
    """Inject global template variables available on every page."""
    low_stock_count = _get_low_stock_count()
    password_change_required = requires_password_change(current_user)
    reset_mode = factory_reset_mode()
    reset_mode_label = 'Support/Development' if reset_mode == 'support' else 'Production-safe'
    return dict(
        g_low_stock_count=low_stock_count,
        g_destructive_admin_tools_enabled=True,
        g_factory_reset_mode=reset_mode,
        g_factory_reset_mode_label=reset_mode_label,
        g_is_developer=is_developer_role(getattr(current_user, 'role', '')),
        g_full_factory_reset_unlocked=can_access_full_factory_reset(current_user),
        g_password_change_required=password_change_required,
        store_name=_settings_svc.get('store_name', 'Garage') or 'Garage',
    )

def _get_store_setting_row(key):
    return StoreSettings.query.filter_by(key=key).first()

def is_initial_security_setup_pending():
    try:
        row = _get_store_setting_row(INITIAL_SECURITY_SETUP_KEY)
        return bool(row and row.value == '1')
    except Exception:
        return False

def set_initial_security_setup_pending(is_pending):
    row = _get_store_setting_row(INITIAL_SECURITY_SETUP_KEY)
    value = '1' if is_pending else '0'
    if row:
        row.value = value
    else:
        db.session.add(StoreSettings(key=INITIAL_SECURITY_SETUP_KEY, value=value))


def _set_store_setting_value(key, value):
    row = _get_store_setting_row(key)
    stored_value = '' if value is None else str(value)
    if row:
        row.value = stored_value
    else:
        db.session.add(StoreSettings(key=key, value=stored_value))


def get_primary_admin_user_id():
    try:
        row = _get_store_setting_row(PRIMARY_ADMIN_USER_ID_KEY)
        if not row or not (row.value or '').strip():
            return None
        return int(row.value)
    except Exception:
        return None


def primary_admin_exists():
    try:
        explicit_owner = User.query.filter_by(is_primary_admin=True).first()
        if explicit_owner:
            return True
        stored_user_id = get_primary_admin_user_id()
        if stored_user_id is None:
            return False
        owner = db.session.get(User, stored_user_id)
        return owner is not None
    except Exception:
        return False


def assign_primary_admin(user: User):
    """Mark a user as the original owner/admin with full admin capabilities."""
    if not user:
        return
    # Safety: enforce a single primary admin record in the DB.
    User.query.filter(User.id != user.id, User.is_primary_admin.is_(True)).update(
        {
            User.is_primary_admin: False,
            User.is_owner: False,
        },
        synchronize_session=False,
    )
    user.role = 'Admin'
    user.is_admin = True
    user.is_owner = True
    user.is_primary_admin = True
    _set_store_setting_value(PRIMARY_ADMIN_USER_ID_KEY, user.id)
    set_initial_security_setup_pending(False)


def reset_system_to_first_run_state():
    """Reset users/auth/settings to a clean first-run boot state."""
    # 1) Remove all transactional/business data (includes user logs).
    _perform_reset_all_data(db.session)

    # 2) Remove auth + settings data that could keep stale privileges/sessions.
    db.session.query(PasswordReset).delete(synchronize_session=False)
    db.session.query(User).delete(synchronize_session=False)
    db.session.query(StoreSettings).delete(synchronize_session=False)

    # 3) Recreate the minimum boot settings required for first-run setup.
    db.session.add(StoreSettings(key=INITIAL_SECURITY_SETUP_KEY, value='1'))
    db.session.add(StoreSettings(key=PRIMARY_ADMIN_USER_ID_KEY, value=''))

    # 4) Reset sqlite auto-increment sequence for predictable clean state.
    try:
        db.session.execute(text(
            "DELETE FROM sqlite_sequence WHERE name IN ('users', 'password_resets')"
        ))
    except Exception:
        pass  # sqlite_sequence may not exist on an empty/new DB


def _delete_license_artifacts():
    """Delete all persisted activation/license artifacts for full developer reset."""
    for path in {
        getattr(lic_module, 'LICENSE_FILE', None),
        getattr(lic_module, 'ACTIVATION_FILE', None),
        getattr(lic_module, '_OLD_LICENSE_FILE', None),
    }:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            app.logger.exception('Failed to remove license artifact %s', path)


def _clear_session_state_for_reset(session_scope='all'):
    """Clear in-memory/auth session state after destructive resets."""
    if session_scope == 'all':
        User.query.update({User.session_token: None}, synchronize_session=False)
        with _active_sessions_lock:
            _active_sessions.clear()
    else:
        if current_user and getattr(current_user, 'is_authenticated', False):
            db.session.query(User).filter_by(id=current_user.id).update(
                {User.session_token: None},
                synchronize_session=False,
            )
        sid = session.get('_id')
        if sid:
            with _active_sessions_lock:
                _active_sessions.pop(sid, None)

    session.clear()
    logout_user()


def reset_business_data(session):
    """Production-safe reset: wipe business/transactional records only."""
    return _perform_reset_all_data(session)


def _ensure_primary_admin_assignment_for_authenticated_user(user: User):
    """Assign first authenticated user as owner only if no owner exists yet."""
    if not user:
        return
    if primary_admin_exists():
        return
    assign_primary_admin(user)

def requires_password_change(user):
    # Force-password-change prompts are disabled; manual change is available in Settings → Users.
    return False

def is_first_run_admin_setup_required():
    try:
        return User.query.count() == 0
    except Exception as exc:
        _log_database_failure('first-run admin setup check', exc)
        return False

def destructive_admin_tools_enabled():
    return app.config.get('ENABLE_DESTRUCTIVE_ADMIN_TOOLS', True)


def app_runtime_mode():
    value = (os.getenv(APP_RUNTIME_MODE_ENV, 'production') or 'production').strip().lower()
    if value in {'dev', 'development', 'debug', 'support'}:
        return 'support'
    return 'production'


def factory_reset_mode():
    return 'support'

def require_destructive_admin_tools():
    pass


def can_access_full_factory_reset(user):
    role = role_from_user(user, default='')
    return is_developer_role(role) or is_admin_role(role)


def _allowed_without_activation(endpoint):
    if not endpoint:
        return False
    return endpoint in {
        'activation_setup',
        'api_activation_status',
        'api_activation_activate',
        # License endpoints that must remain reachable for the activation flow
        'api_license_activate',
        'api_license_check',
        'api_license_info',
        'static',
        'health',
    }


def _allowed_before_first_run_setup(endpoint):
    if not endpoint:
        return False
    return endpoint in {
        'setup_admin_page',
        'setup_admin',
        'logout',
        'activation_setup',
        'api_activation_status',
        'api_activation_activate',
        'api_license_activate',
        'api_license_check',
        'api_license_info',
        'static',
        'health',
    }

def _current_audit_user_id():
    try:
        if current_user and getattr(current_user, 'is_authenticated', False):
            return current_user.id
    except Exception:
        pass
    return None

def log_action(action, *, target_type=None, target_id=None, metadata=None, user_id=None, commit=True):
    try:
        db.session.add(UserLog(
            user_id=user_id if user_id is not None else _current_audit_user_id(),
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_summary=_summarize_audit_metadata(metadata),
            ip=request.remote_addr,
        ))
        if commit:
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _database_diagnostics():
    try:
        return inspect_sqlite_database(app.config.get('DATABASE_FILE'))
    except Exception as exc:
        return {'diagnostics_error': str(exc)}


def _log_database_failure(context, exc):
    app.logger.exception(
        'Database failure during %s: %s | diagnostics=%s',
        context,
        exc,
        _database_diagnostics(),
    )


def _recover_database_schema_if_needed():
    inspector = sa_inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    if 'users' in existing_tables:
        return False

    app.logger.warning(
        'SQLite database is reachable but schema is incomplete; rebuilding missing tables. '
        'diagnostics=%s',
        _database_diagnostics(),
    )
    db.create_all()
    try:
        from services.migrations import run_migrations
        run_migrations(db.session)
    except Exception as _mig_err:
        app.logger.warning('[RECOVER] Migration error (falling back to auto_migrate): %s', _mig_err)
        try:
            auto_migrate()
        except Exception:
            pass
    seed_database()
    seed_default_expense_categories()
    app.logger.info('[RECOVER] Schema recovery complete.')
    return True

def validate_sale_items(items_data):
    """Validate cart items before creating a sale. Returns list of error strings (empty = OK).

    Stock quantity is always enforced to prevent overselling.
    The noNegStock setting controls whether the system allows negative stock at the
    adjustment level; at point-of-sale we always block selling more than available stock.
    """
    errors = []
    for item in items_data:
        pid = item.get('product_id')
        if not pid:
            errors.append('Item missing product_id')
            continue
        p = db.session.get(Product, pid)
        if not p or p.status != 'active':
            errors.append(f'Product #{pid} not found or inactive')
            continue
        requested_qty = float(item.get('qty', 1))
        if p.stock_qty < requested_qty:
            errors.append(
                f'Not enough stock for "{p.name}". '
                f'Available: {p.stock_qty}, Requested: {requested_qty}'
            )
            continue
    return errors

def barcode_in_use(barcode, exclude_product_id=None):
    """Return True if a barcode exists in products or product_barcodes."""
    if not barcode:
        return False
    q = Product.query.filter(Product.barcode == barcode, Product.status == 'active')
    if exclude_product_id:
        q = q.filter(Product.id != exclude_product_id)
    if q.first():
        return True
    alias_q = ProductBarcode.query.filter(ProductBarcode.barcode == barcode)
    if exclude_product_id:
        alias_q = alias_q.filter(ProductBarcode.product_id != exclude_product_id)
    return alias_q.first() is not None


def normalize_lookup_token(value: str) -> str:
    """Normalize scanned/typed lookup values for resilient matching."""
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def find_sale_by_scanned_value(raw_value: str):
    """Find sale by invoice number, including receipt-barcode variants."""
    value = str(raw_value or '').strip()
    if not value:
        return None
    direct = Sale.query.filter_by(invoice_number=value).first()
    if direct:
        return direct

    normalized_scan = normalize_lookup_token(value)
    if not normalized_scan:
        return None

    embedded_invoice = re.search(r'(INV[-\s]?\d{8}[-\s]?\d{1,6})', value, re.IGNORECASE)
    if embedded_invoice:
        embedded = embedded_invoice.group(1).replace(' ', '').upper()
        direct_embedded = Sale.query.filter_by(invoice_number=embedded).first()
        if direct_embedded:
            return direct_embedded

    # Fallback: compare normalized values (supports scanners that strip symbols).
    recent_sales = Sale.query.order_by(Sale.id.desc()).limit(500).all()
    for sale in recent_sales:
        if normalize_lookup_token(sale.invoice_number or '') == normalized_scan:
            return sale
    return None

def normalize_product_type(raw_value, fallback='normal'):
    value = str(raw_value or '').strip().lower()
    if value in {'normal'}:
        return value
    return fallback

def gen_ean13(product_id):
    """Generate a valid EAN-13 barcode: prefix 200 + 5-digit product id + 4 random + 1 check digit."""
    body = f'200{product_id:05d}{random.randint(0, 9999):04d}'
    odds  = sum(int(body[i]) for i in range(0, 12, 2))
    evens = sum(int(body[i]) for i in range(1, 12, 2))
    check = (10 - ((odds + evens * 3) % 10)) % 10
    return body + str(check)

def validate_barcode_by_type(value, barcode_type):
    """Legacy compat shim — delegates to canonical label.validator."""
    from services.printing.label.validator import validate_barcode
    ok, msg = validate_barcode(value, barcode_type)
    return ok, msg

def generate_barcode_for_product(product, mode='random', prefix='SM'):
    """Legacy compat shim — delegates to canonical barcode.generator."""
    from services.printing.barcode.generator import BarcodeGeneratorService
    try:
        result = BarcodeGeneratorService.generate(
            product_id=product.id,
            mode=mode or 'random',
            barcode_type='code128',
            prefix=prefix or 'SM',
            manual_value='',
            exists_fn=lambda val: barcode_in_use(val, exclude_product_id=product.id),
        )
        return result['barcode']
    except ValueError:
        return None

def gen_invoice(retry_offset=0):
    """Generate a unique, race-condition-free invoice number using atomic DB sequence."""
    from services.atomic_sequence import next_sequence as _next_seq
    return _next_seq(db.session, 'INV')

@app.before_request
def check_db_connection():
    global _db_ok
    if request.endpoint == 'health':
        return None

    # When _db_ok is True, still recheck every 50 requests to catch silent DB drops
    if _db_ok and hasattr(check_db_connection, '_counter'):
        check_db_connection._counter += 1
        if check_db_connection._counter < DB_HEALTH_RECHECK_INTERVAL:
            return None
        check_db_connection._counter = 0
    else:
        check_db_connection._counter = 0

    try:
        with db.engine.connect() as _health_conn:
            _health_conn.execute(text('SELECT 1'))
        _recover_database_schema_if_needed()
        _db_ok = True
        return None
    except Exception as e:
        _db_ok = False
        _log_database_failure('before_request connection check', e)
        wants_json = (
            request.is_json
            or request.path.startswith('/api/')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )
        if wants_json:
            return jsonify({'ok': False, 'msg': 'Service temporarily unavailable. Please try again shortly.'}), 503
        error_msg = str(e).replace("'", "\\'").replace('"', '\\"')
        html = DB_ERROR_HTML.replace('{{ERROR}}', error_msg)
        return Response(html, status=503, content_type='text/html')

@app.before_request
def enforce_password_change():
    """Preserve the hook but disable forced redirects; UI now warns in-app instead."""
    return None

@app.before_request
def enforce_activation_and_first_run_flow():
    endpoint = request.endpoint or ''
    activation = lic_module.activation_status()
    if activation.get('requires_activation'):
        if _allowed_without_activation(endpoint):
            return None
        if request.path.startswith('/api/'):
            return jsonify({
                'ok': False,
                'code': 'ACTIVATION_REQUIRED',
                'msg': 'Activation is required before using the application.',
                'redirect': url_for('activation_setup'),
            }), 403
        return redirect(url_for('activation_setup'))

    if is_first_run_admin_setup_required():
        if _allowed_before_first_run_setup(endpoint):
            return None
        if request.path.startswith('/api/'):
            return jsonify({
                'ok': False,
                'code': 'FIRST_RUN_SETUP_REQUIRED',
                'msg': 'First-run setup is required before using the application.',
                'redirect': url_for('setup_admin_page'),
            }), 409
        return redirect(url_for('setup_admin_page'))
    return None

@app.before_request
def enforce_session_security():
    if request.endpoint in {'health', 'static'}:
        return None

    # Prune stale sessions periodically to prevent unbounded memory growth
    if hasattr(enforce_session_security, '_prune_counter'):
        enforce_session_security._prune_counter += 1
    else:
        enforce_session_security._prune_counter = 0
    if enforce_session_security._prune_counter >= 200:
        enforce_session_security._prune_counter = 0
        cutoff = datetime.now() - timedelta(hours=13)
        with _active_sessions_lock:
            stale = [sid for sid, info in _active_sessions.items()
                     if datetime.strptime(info.get('last_seen', '2000-01-01 00:00:00'),
                                          '%Y-%m-%d %H:%M:%S') < cutoff]
            for sid in stale:
                _active_sessions.pop(sid, None)

    session.permanent = True

    if not current_user.is_authenticated:
        session.pop('_last_activity', None)
        return None

    stored_token = session.get('_session_token')
    if stored_token != current_user.session_token:
        if stored_token is None and current_user.session_token is not None:
            # Session was lost (e.g. server restart) but the remember-me cookie is
            # still valid.  Re-attach the token so subsequent checks pass without
            # forcing the user to log in again after every server restart.
            session['_session_token'] = current_user.session_token
        else:
            # Genuine token mismatch — an administrator terminated this session.
            # IMPORTANT: clear the session BEFORE calling logout_user() so that
            # Flask-Login's internal session["_remember"]="clear" signal survives.
            # Calling session.clear() after logout_user() was erasing that signal,
            # leaving the remember-me cookie intact in the browser and causing an
            # infinite redirect loop (ERR_TOO_MANY_REDIRECTS).
            session.clear()
            logout_user()
            if request.endpoint != 'login':
                flash('Your session was terminated by an administrator.', 'warning')
                return redirect(url_for('login'))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    idle_timeout = app.permanent_session_lifetime or timedelta(minutes=DEFAULT_IDLE_TIMEOUT_MINUTES)
    last_seen_raw = session.get('_last_activity')
    if last_seen_raw:
        try:
            last_seen = datetime.fromisoformat(last_seen_raw)
        except ValueError:
            last_seen = None
        if last_seen and now - last_seen > idle_timeout:
            sid = session.pop('_id', None)
            if sid:
                with _active_sessions_lock:
                    _active_sessions.pop(sid, None)
            session.clear()
            logout_user()
            if request.is_json:
                return jsonify({'ok': False, 'msg': 'Your session timed out due to inactivity. Please sign in again.'}), 401
            flash('Your session timed out due to inactivity. Please sign in again.', 'error')
            return redirect(url_for('login'))

    sid = session.get('_id')
    if sid:
        with _active_sessions_lock:
            tracked = _active_sessions.get(sid)
            if tracked:
                tracked['last_seen'] = now.strftime('%Y-%m-%d %H:%M:%S')
                tracked['ip'] = request.remote_addr
            else:
                # Session cookie is valid (Flask-Login confirmed the user) but the
                # in-memory tracker lost this entry — most likely a server restart
                # cleared _active_sessions while the client still held a valid
                # remember-me or session cookie.  Re-register instead of logging out
                # so the desktop app (where the server restarts often) stays usable.
                _active_sessions[sid] = {
                    'user_id': current_user.id,
                    'login_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'last_seen': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'ip': request.remote_addr,
                }
    else:
        sid = secrets.token_urlsafe(24)
        session['_id'] = sid
        with _active_sessions_lock:
            _active_sessions[sid] = {
                'user_id': current_user.id,
                'login_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                'last_seen': now.strftime('%Y-%m-%d %H:%M:%S'),
                'ip': request.remote_addr,
            }

    session['_last_activity'] = now.isoformat()
    return None

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('billing' if is_pos_operator_role(current_user.role) else 'dashboard'))
    activation = lic_module.activation_status()
    if activation.get('requires_activation'):
        return redirect(url_for('activation_setup'))
    first_run_setup_required = is_first_run_admin_setup_required()
    # Redirect GET requests to the dedicated setup page when no users exist
    if request.method == 'GET' and first_run_setup_required:
        return redirect(url_for('setup_admin_page'))
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        try:
            username = (data.get('username') or '').strip()
            password = data.get('password', '')
            if first_run_setup_required:
                app.logger.info(
                    'Login blocked during first-run setup username=%s ip=%s',
                    username or '<blank>',
                    request.remote_addr,
                )
                message = 'No administrator account exists yet. Complete the first-run setup to create your admin account.'
                if request.is_json:
                    return jsonify({
                        'ok': False,
                        'msg': message,
                        'code': 'FIRST_RUN_SETUP_REQUIRED',
                        'first_run_setup_required': True,
                    }), 409
                flash(message, 'warning')
                return render_template('login.html', first_run_setup_required=True), 409

            user = _find_user_for_login(username)
            password_ok = bool(user and user.check_password(password))
            if user and password_ok and user.status == 'active':
                user.role = normalize_role(user.role)
                user.is_admin = is_admin(user)
                _ensure_primary_admin_assignment_for_authenticated_user(user)
                _upgrade_legacy_password_hash_if_needed(user, password)
                login_user(user, remember=True)
                user.session_token = secrets.token_urlsafe(32)
                db.session.commit()
                session['_session_token'] = user.session_token
                try:
                    _is_card_terminal_connected_for_checkout()
                except Exception:
                    app.logger.exception('Hardware auto-connect on login failed user=%s', user.username)
                try:
                    log_action(
                        'Login',
                        target_type='user',
                        target_id=user.id,
                        metadata={'username': user.username, 'role': user.role},
                        user_id=user.id,
                    )
                except Exception:
                    pass
                # Track active session
                sid = secrets.token_urlsafe(24)
                session.permanent = True
                session['_id'] = sid
                session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                with _active_sessions_lock:
                    _active_sessions[sid] = {
                        'user_id': user.id,
                        'login_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'last_seen': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'ip': request.remote_addr,
                    }
                force_change = requires_password_change(user)
                if request.is_json:
                    return jsonify({'ok': True, 'role': user.role, 'name': user.full_name,
                                    'force_password_change': bool(force_change)})
                return redirect(url_for('dashboard'))
            app.logger.info('Invalid login attempt identity=%s ip=%s', username or '<blank>', request.remote_addr)
            if request.is_json:
                return jsonify({
                    'ok': False,
                    'msg': 'Invalid username or password',
                    'code': 'INVALID_CREDENTIALS',
                }), 401
            flash('Invalid username or password', 'error')
        except Exception as exc:
            _log_database_failure('login', exc)
            if request.is_json:
                return jsonify({
                    'ok': False,
                    'msg': 'Database connection error. Please verify the POS database is available.',
                    'code': 'DATABASE_ERROR',
                }), 503
            flash('Login is temporarily unavailable. Please try again shortly.', 'error')
    show_default_creds = False
    return render_template('login.html', first_run_setup_required=first_run_setup_required,
                           show_default_creds=show_default_creds)

@app.route('/setup-admin', methods=['GET'])
def setup_admin_page():
    """First-run setup page. Redirects away if the system is already initialised."""
    if current_user.is_authenticated:
        if is_first_run_admin_setup_required():
            session.clear()
            logout_user()
        else:
            role = getattr(current_user, 'role', '')
            return redirect(url_for('billing' if is_pos_operator_role(role) else 'dashboard'))
    activation = lic_module.activation_status()
    if activation.get('requires_activation'):
        return redirect(url_for('activation_setup'))
    if not is_first_run_admin_setup_required():
        # System already has users — send to login (or dashboard if logged in)
        role = getattr(current_user, 'role', '')
        if current_user.is_authenticated:
            return redirect(url_for('billing' if is_pos_operator_role(role) else 'dashboard'))
        return redirect(url_for('login'))
    return render_template('setup_admin.html')


@app.route('/setup-admin', methods=['POST'])
def setup_admin():
    try:
        activation = lic_module.activation_status()
        if activation.get('requires_activation'):
            return jsonify({
                'ok': False,
                'msg': 'Activation is required before creating the first admin account.',
                'code': 'ACTIVATION_REQUIRED',
            }), 403
        if not is_first_run_admin_setup_required():
            return jsonify({
                'ok': False,
                'msg': 'Initial admin setup is no longer available because a user account already exists.',
                'code': 'SETUP_ALREADY_COMPLETED',
            }), 409
        if primary_admin_exists():
            return jsonify({
                'ok': False,
                'msg': 'Initial admin setup is already completed.',
                'code': 'PRIMARY_ADMIN_ALREADY_EXISTS',
            }), 409

        data = request.get_json(silent=True) or request.form
        username = (data.get('username') or '').strip()
        full_name = (data.get('full_name') or '').strip()
        shop_name = (data.get('shop_name') or '').strip()
        email = _normalize_email(data.get('email') or '')
        password = data.get('password') or ''
        confirm_password = data.get('confirm_password') or ''

        if not shop_name or not username or not full_name or not email or not password:
            return jsonify({'ok': False, 'msg': 'Please fill in all required fields.'}), 400
        if password != confirm_password:
            return jsonify({'ok': False, 'msg': 'Passwords do not match. Please confirm your password.'}), 400
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,80}', username):
            return jsonify({'ok': False, 'msg': 'Username must be 3-80 characters and use only letters, numbers, dots, dashes, or underscores.'}), 400

        pw_error = validate_password_strength(
            password,
            username=username,
            email=email,
            full_name=full_name,
        )
        if pw_error:
            return jsonify({'ok': False, 'msg': pw_error}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({'ok': False, 'msg': 'That username is already in use.'}), 409
        if User.query.filter(func.lower(User.email) == email).first():
            return jsonify({'ok': False, 'msg': 'That email is already in use.'}), 409

        user = User(
            username=username,
            full_name=full_name,
            email=email,
            role='Admin',
            is_admin=True,
            is_owner=True,
            is_primary_admin=True,
            status='active',
            force_password_change=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        StoreSettings.set('store_name', shop_name)
        if not StoreSettings.get('store_email', ''):
            StoreSettings.set('store_email', email)
        assign_primary_admin(user)
        db.session.commit()

        app.logger.info('Initial admin created username=%s email=%s ip=%s', user.username, user.email, request.remote_addr)
        log_action(
            'Initial admin created',
            target_type='user',
            target_id=user.id,
            metadata={'username': user.username, 'role': user.role},
            user_id=user.id,
        )
        return jsonify({'ok': True, 'redirect': '/login'})
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        _log_database_failure('setup-admin', exc)
        return jsonify({
            'ok': False,
            'msg': 'Admin setup is temporarily unavailable. Check the server logs for details.',
            'code': 'SETUP_ADMIN_ERROR',
        }), 503

@app.route('/activation', methods=['GET'])
def activation_setup():
    status = lic_module.activation_status()
    if current_user.is_authenticated:
        if status.get('requires_activation'):
            session.clear()
            logout_user()
        else:
            role = getattr(current_user, 'role', '')
            return redirect(url_for('billing' if is_pos_operator_role(role) else 'dashboard'))
    if status.get('activated'):
        return redirect(url_for('setup_admin_page' if is_first_run_admin_setup_required() else 'login'))
    continue_url = '/setup-admin' if is_first_run_admin_setup_required() else '/login'
    return render_template('activation.html', status=status, continue_url=continue_url)

@app.route('/api/activation/status', methods=['GET'])
def api_activation_status():
    return jsonify({'ok': True, 'status': lic_module.activation_status()})

@app.route('/api/activation/activate', methods=['POST'])
def api_activation_activate():
    data = request.get_json(silent=True) or {}
    key = (data.get('key') or '').strip()
    result = lic_module.activate_offline(key)
    if result.get('ok'):
        log_action(
            'Offline activation completed',
            target_type='license',
            metadata={'status': 'success'},
            commit=True,
        )
        result['redirect'] = '/setup-admin' if is_first_run_admin_setup_required() else '/login'
        return jsonify(result)
    return jsonify(result), 400

@app.route('/logout')
@login_required
def logout():
    log_action('Logout')
    sid = session.pop('_id', None)
    session.pop('_last_activity', None)
    if sid:
        with _active_sessions_lock:
            _active_sessions.pop(sid, None)
    logout_user()
    return redirect(url_for('login'))

@app.route('/force-change-password')
@login_required
def force_change_password():
    return redirect(url_for('settings') + '#users')

@app.route('/api/admin/bootstrap-status')
@login_required
def api_bootstrap_status():
    """Diagnostic endpoint: returns first-run security setup state.

    Useful for health-checks and CLI-based diagnostics after default
    bootstrap credentials were removed.
    Restricted to Admin users.
    """
    if not is_admin_role(current_user.role):
        abort(403)
    try:
        return jsonify({
            'ok': True,
            'total_users': User.query.count(),
            'first_run_setup_required': is_first_run_admin_setup_required(),
            'security_first_run_pending': is_initial_security_setup_pending(),
            'bootstrap_users': [],
        })
    except Exception as exc:
        _log_database_failure('api_bootstrap_status', exc)
        return jsonify({'ok': False, 'msg': str(exc)}), 503

def _windows_subprocess_kwargs():
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def _run_hidden_subprocess(args, **kwargs):
    return subprocess.run(args, **_windows_subprocess_kwargs(), **kwargs)


register_settings_routes(
    app,
    db=db,
    User=User,
    Category=Category,
    Supplier=Supplier,
    Product=Product,
    StoreSettings=StoreSettings,
    ENV_FILE_PATH=ENV_FILE_PATH,
    log_action=log_action,
    update_env_file=update_env_file,
    requires_password_change=requires_password_change,
)

register_printer_routes(
    app,
    db=db,
    StoreSettings=StoreSettings,
    printer_service=printer_service,
    log_action=log_action,
    user_has_any_role=user_has_any_role,
    UserLog=UserLog,
    Product=Product,
)

# ── New canonical printing routes ────────────────────────────────────────
from services.printing.settings_service import PrintSettingsService
from services.printing.domain_service import PrintingDomainService
_printer_settings_svc = PrintSettingsService(StoreSettings)
_print_domain_svc = PrintingDomainService(
    printer_service=printer_service,
    settings=_printer_settings_svc,
    logger=app.logger,
    log_action=log_action,
    user_log_model=UserLog,
)

register_printing_settings_routes(
    app,
    db=db,
    StoreSettings=StoreSettings,
    printer_service=printer_service,
    log_action=log_action,
    user_has_any_role=user_has_any_role,
)

register_receipt_print_routes(
    app,
    db=db,
    StoreSettings=StoreSettings,
    printer_service=printer_service,
    print_domain=_print_domain_svc,
    log_action=log_action,
    user_has_any_role=user_has_any_role,
    Sale=Sale,
    RepairJob=RepairJob,
    UserLog=UserLog,
)

register_label_print_routes(
    app,
    db=db,
    StoreSettings=StoreSettings,
    printer_service=printer_service,
    print_domain=_print_domain_svc,
    log_action=log_action,
    user_has_any_role=user_has_any_role,
    Product=Product,
)

register_barcode_routes(
    app,
    db=db,
    StoreSettings=StoreSettings,
    log_action=log_action,
    user_has_any_role=user_has_any_role,
    Product=Product,
    barcode_in_use=barcode_in_use,
)

register_diagnostics_routes(
    app,
    printer_service=printer_service,
    print_domain=_print_domain_svc,
    settings=_printer_settings_svc,
    user_has_any_role=user_has_any_role,
    UserLog=UserLog,
)

register_reports_routes(
    app,
    db=db,
    Product=Product,
    Supplier=Supplier,
    Purchase=Purchase,
    Sale=Sale,
    SaleItem=SaleItem,
    Payment=Payment,
    User=User,
    log_action=log_action,
)

register_suppliers_wholesale_routes(
    app,
    log_action=log_action,
)

register_purchases_returns_routes(
    app,
    db=db,
    Purchase=Purchase,
    PurchaseItem=PurchaseItem,
    ProductReturn=ProductReturn,
    ReturnItem=ReturnItem,
    Supplier=Supplier,
    Product=Product,
    StockMovement=StockMovement,
    SupplierTransaction=SupplierTransaction,
    Sale=Sale,
    SaleItem=SaleItem,
    Payment=Payment,
    WholesaleCustomer=WholesaleCustomer,
    WholesaleTransaction=WholesaleTransaction,
    StoreSettings=StoreSettings,
    User=User,
    log_action=log_action,
    validate_sale_items=validate_sale_items,
    gen_invoice=gen_invoice,
)

register_sales_routes(
    app,
    csrf=csrf,
    db=db,
    Category=Category,
    HeldOrder=HeldOrder,
    Payment=Payment,
    Product=Product,
    Sale=Sale,
    SaleItem=SaleItem,
    StockMovement=StockMovement,
    StoreSettings=StoreSettings,
    WholesaleCustomer=WholesaleCustomer,
    WholesaleTransaction=WholesaleTransaction,
    build_payment_data=build_payment_data,
    verify_notify=verify_notify,
    PAYHERE_ENDPOINT=PAYHERE_ENDPOINT,
    gen_invoice=gen_invoice,
    log_action=log_action,
    validate_sale_items=validate_sale_items,
    WARRANTY_DAYS=WARRANTY_DAYS,
    card_terminal_connected_checker=_is_card_terminal_connected_for_checkout,
)

register_customer_routes(app, log_action=log_action)
register_vehicle_routes(app, log_action=log_action)
register_variant_routes(app, log_action=log_action)
register_repair_routes(app, log_action=log_action, print_domain=_print_domain_svc)
register_installment_routes(app, log_action=log_action)
register_broker_routes(app, log_action=log_action)
register_expense_routes(app, log_action=log_action)
register_service_analytics_routes(
    app,
    db=db,
    RepairJob=RepairJob,
    RepairJobPart=RepairJobPart,
    RepairPayment=RepairPayment,
    User=User,
    Sale=Sale,
    SaleItem=SaleItem,
    Product=Product,
)


@app.route('/print/receipt/<string:receipt_type>/<int:record_id>')
@login_required
def print_receipt_router(receipt_type, record_id):
    "Unified receipt print entrypoint for POS modules."
    normalized_type = (receipt_type or '').strip().lower()
    if normalized_type in {'repair', 'repairs'}:
        return redirect(url_for('repair_job_receipt', jid=record_id, auto_print=request.args.get('auto_print', '1')))
    if normalized_type in {'return', 'returns', 'refund'}:
        return redirect(url_for('refund_receipt_page', rid=record_id, auto_print=request.args.get('auto_print', '1')))
    if normalized_type in {'sale', 'sales'}:
        return redirect(url_for('sales_receipt_page', sid=record_id, auto_print=request.args.get('auto_print', '1')))
    return jsonify({'ok': False, 'error': f'Unsupported receipt type: {normalized_type}'}), 404

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json(silent=True) or request.form
    email = _normalize_email(data.get('email') or '')
    username = (data.get('username') or '').strip()

    if not email or not username:
        return jsonify({'ok': False, 'msg': 'Please fill in all required fields.'}), 400

    user_for_email = User.query.filter(func.lower(User.email) == email).first()
    if not user_for_email:
        return jsonify({'ok': False, 'msg': 'Email not found'}), 404

    user_for_username = _find_user_for_login(username)
    if not user_for_username or user_for_username.id != user_for_email.id:
        return jsonify({'ok': False, 'msg': 'Username and email do not match'}), 400

    session['password_reset_user_id'] = user_for_email.id
    session['password_reset_email'] = email
    return jsonify({'ok': True, 'msg': 'Identity verified. Please enter your new password.'})


@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    return jsonify({'ok': False, 'msg': 'OTP verification is not required. Use the reset password form.'}), 404


@app.route('/reset-password', methods=['GET'])
def reset_password_page():
    if current_user.is_authenticated:
        role = getattr(current_user, 'role', '')
        return redirect(url_for('billing' if is_pos_operator_role(role) else 'dashboard'))
    return render_template('reset_password.html')


@app.route('/reset-password', methods=['POST'])
def reset_password_route():
    data = request.get_json(silent=True) or request.form
    new_pw = data.get('password', '')
    confirm_pw = data.get('confirm_password', '')
    user_id = session.get('password_reset_user_id')
    email = _normalize_email(session.get('password_reset_email') or '')

    if not user_id or not email:
        return jsonify({'ok': False, 'msg': 'Please verify your email and username first.'}), 400
    if not new_pw or not confirm_pw:
        return jsonify({'ok': False, 'msg': 'Please fill in all required fields.'}), 400
    if new_pw != confirm_pw:
        return jsonify({'ok': False, 'msg': 'Passwords do not match. Please confirm your password.'}), 400

    user = db.session.get(User, int(user_id))
    if not user or _normalize_email(user.email or '') != email:
        session.pop('password_reset_user_id', None)
        session.pop('password_reset_email', None)
        return jsonify({'ok': False, 'msg': 'Please verify your email and username first.'}), 400

    pw_error = validate_password_strength(
        new_pw,
        username=user.username if user else '',
        email=user.email if user else '',
        full_name=user.full_name if user else '',
    )
    if pw_error:
        return jsonify({'ok': False, 'msg': pw_error}), 400
    user.set_password(new_pw)
    user.force_password_change = False
    db.session.commit()
    session.pop('password_reset_user_id', None)
    session.pop('password_reset_email', None)
    log_action(
        'Password reset via email + username',
        target_type='user',
        target_id=user.id,
        metadata={'username': user.username},
        user_id=user.id,
    )
    return jsonify({'ok': True, 'msg': 'Password changed successfully'})

# ── PAGE ROUTES ───────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.now().date()
    q = Sale.query.filter(func.date(Sale.sale_date) == today, Sale.status == 'completed')
    today_revenue = q.with_entities(func.sum(Sale.total_amount)).scalar() or 0
    today_count   = q.count()
    avg_basket    = (today_revenue / today_count) if today_count else 0
    total_products = Product.query.filter_by(status='active').count()
    low_stock = Product.query.filter(Product.stock_qty <= Product.low_stock_lvl, Product.status == 'active').all()
    low_stock_count = len(low_stock)
    weekly = []
    for i in range(6, -1, -1):
        d   = today - timedelta(days=i)
        rev = Sale.query.filter(func.date(Sale.sale_date) == d, Sale.status == 'completed').with_entities(func.sum(Sale.total_amount)).scalar() or 0
        weekly.append({'day': d.strftime('%a'), 'sales': float(rev)})
    recent = Sale.query.order_by(Sale.sale_date.desc()).limit(RECENT_SALES_LIMIT).all()
    # Service job stats for dashboard
    month_start = today.replace(day=1)
    service_today = RepairJob.query.filter(func.date(RepairJob.received_date) == today).count()
    service_in_progress = RepairJob.query.filter(
        RepairJob.status.in_(['received', 'diagnosing', 'waiting_parts', 'in_progress'])
    ).count()
    service_ready = RepairJob.query.filter(RepairJob.status == 'ready').count()
    service_revenue_today = db.session.query(func.coalesce(func.sum(RepairJob.total_amount), 0)).filter(
        func.date(RepairJob.received_date) == today,
        RepairJob.status.notin_(['cancelled'])
    ).scalar() or 0
    service_revenue_month = db.session.query(func.coalesce(func.sum(RepairJob.total_amount), 0)).filter(
        func.date(RepairJob.received_date) >= month_start,
        RepairJob.status.notin_(['cancelled'])
    ).scalar() or 0
    recent_jobs = RepairJob.query.order_by(RepairJob.received_date.desc()).limit(5).all()
    return render_template('dashboard.html',
        today_revenue=today_revenue, today_count=today_count,
        avg_basket=avg_basket, total_products=total_products,
        low_stock=low_stock, low_stock_count=low_stock_count,
        weekly=json.dumps(weekly), recent=recent,
        service_today=service_today, service_in_progress=service_in_progress,
        service_ready=service_ready,
        service_revenue_today=float(service_revenue_today),
        service_revenue_month=float(service_revenue_month),
        recent_jobs=recent_jobs)

@app.route('/products')
@login_required
def products_page():
    require_roles('Admin', 'Operator', 'Manager')
    return render_template('products.html',
        products=Product.query.filter_by(status='active').all(),
        categories=Category.query.all(),
        suppliers=Supplier.query.filter_by(status='active').all())

@app.route('/inventory')
@login_required
def inventory():
    prods     = Product.query.filter_by(status='active').all()
    low       = [p for p in prods if p.stock_qty <= p.low_stock_lvl]
    total_val = sum(float(p.sell_price or 0) * float(p.stock_qty or 0) for p in prods)
    movements = StockMovement.query.order_by(StockMovement.date.desc()).limit(30).all()
    return render_template('inventory.html',
        products=prods, low_stock=low, low_stock_count=len(low),
        total_val=total_val, movements=movements)

# ── API: PRODUCTS ─────────────────────────────────────────────────────────────
@app.route('/api/products', methods=['GET'])
@login_required
def api_products():
    q   = request.args.get('q', '')
    cat = request.args.get('cat', '')
    product_type = normalize_product_type(request.args.get('product_type', ''), fallback='')
    query = Product.query.filter_by(status='active')
    if q:
        term = contains_sql_like(q)
        query = query.filter(
            db.or_(
                Product.name.ilike(term, escape='\\'),
                Product.barcode.ilike(term, escape='\\'),
            )
        )
    if cat:
        query = query.filter(Product.category_id == cat)
    if product_type:
        query = query.filter(Product.product_type == product_type)
    return jsonify([p.to_dict() for p in query.all()])

@app.route('/api/products/search')
@login_required
def api_products_search():
    """GET /api/products/search?q=keyword&limit=10
    Search active products and return compact JSON rows for autocomplete widgets."""
    q = request.args.get('q', '').strip()
    product_type = normalize_product_type(request.args.get('product_type', ''), fallback='')
    try:
        limit = min(max(int(request.args.get('limit', 10)), 1), 100)
    except (TypeError, ValueError):
        limit = 10
    if not q:
        return jsonify([])

    term = contains_sql_like(q)
    filters = [Product.status == 'active']
    if product_type:
        filters.append(Product.product_type == product_type)

    results = Product.query.filter(
        *filters,
        db.or_(
            Product.name.ilike(term, escape='\\'),
            Product.barcode.ilike(term, escape='\\'),
            Product.rack_number.ilike(term, escape='\\'),
            Product.section_number.ilike(term, escape='\\'),
        )
    ).order_by(Product.name.asc()).limit(limit).all()

    return jsonify([
        {
            'id': p.id,
            'name': p.name,
            'price': float(p.sell_price or 0),
            'stock_qty': p.stock_qty,
            'barcode': p.barcode or '',
            'rack_number': p.rack_number or '',
            'section_number': p.section_number or '',
        }
        for p in results
    ])


@app.route('/api/barcode/validate')
@login_required
def api_barcode_validate():
    barcode = (request.args.get('barcode') or '').strip()
    exclude_product_id = request.args.get('exclude_product_id')
    try:
        exclude_product_id = int(exclude_product_id) if exclude_product_id else None
    except (TypeError, ValueError):
        exclude_product_id = None
    if not barcode:
        return jsonify({'ok': False, 'error': 'Barcode is required'}), 400
    available = not barcode_in_use(barcode, exclude_product_id=exclude_product_id)
    return jsonify({'ok': True, 'barcode': barcode, 'available': available})

@app.route('/api/products/barcode/<barcode>')
@login_required
def api_product_barcode(barcode):
    # Check primary product.barcode first, then product_barcodes alias table
    p = Product.query.filter_by(barcode=barcode, status='active').first()
    if not p:
        pb = ProductBarcode.query.filter_by(barcode=barcode).first()
        if pb:
            p = Product.query.filter_by(id=pb.product_id, status='active').first()
    if not p:
        return jsonify({'success': False}), 404
    payload = p.to_dict()
    payload.update({'success': True, 'product': p.to_dict()})
    return jsonify(payload), 200

@app.route('/api/products/create', methods=['POST'])
@login_required
def api_create_product_quick():
    d = request.get_json(silent=True) or {}
    barcode = (d.get('barcode') or '').strip()
    name = (d.get('name') or '').strip()
    category_name = (d.get('category') or '').strip()

    if not barcode:
        return jsonify({'success': False, 'error': 'Barcode is required'}), 400
    if not name:
        return jsonify({'success': False, 'error': 'Product name is required'}), 400

    try:
        price = float(d.get('price', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Price must be a valid number'}), 400
    if price <= 0:
        return jsonify({'success': False, 'error': 'Price must be greater than zero'}), 400

    if barcode_in_use(barcode):
        return jsonify({'success': False, 'error': f'Barcode "{barcode}" already exists'}), 409

    category_id = None
    if category_name:
        cat = Category.query.filter(func.lower(Category.name) == category_name.lower()).first()
        category_id = cat.id if cat else None
    product_type = normalize_product_type(d.get('product_type'), fallback='normal')

    try:
        p = Product(
            barcode=barcode,
            name=name,
            sell_price=price,
            buy_price=0,
            stock_qty=0,
            low_stock_lvl=10,
            category_id=category_id,
            status='active',
            product_type=product_type,
            is_imei_tracked=0,
        )
        db.session.add(p)
        db.session.commit()
        out = p.to_dict()
        return jsonify({'success': True, 'product': out, **out}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Failed to create product: {str(e)}'}), 500

@app.route('/api/products', methods=['POST'])
@login_required
def api_add_product():
    require_roles('Admin', 'Operator', 'Manager')
    d = request.get_json(silent=True)
    if not d:
        return jsonify({'error': 'Invalid or missing JSON body'}), 400

    # ── Required field validation ──────────────────────────────────────────
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Product name is required'}), 400

    try:
        sell_price = float(d.get('sell_price', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Sell price must be a valid number'}), 400
    if sell_price < 0:
        return jsonify({'error': 'Sell price cannot be negative'}), 400

    try:
        buy_price       = float(d.get('buy_price', 0) or 0)
        wholesale_price = float(d.get('wholesale_price', 0) or 0)
        stock_qty       = int(float(d.get('stock_qty', 0) or 0))
        low_stock_lvl   = int(float(d.get('low_stock_lvl', 10) or 10))
        price_per_kg    = float(d.get('price_per_kg', 0) or 0)
    except (TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid numeric value: {str(e)}'}), 400

    # ── Duplicate barcode check ────────────────────────────────────────────
    barcode = (d.get('barcode') or '').strip() or None
    sku = (d.get('sku') or '').strip() or None
    if barcode and barcode_in_use(barcode):
        return jsonify({'error': f'Barcode "{barcode}" is already used by another product'}), 409
    if sku and Product.query.filter_by(sku=sku).first():
        return jsonify({'error': f'SKU "{sku}" is already used by another product'}), 409
    product_type = normalize_product_type(d.get('product_type'), fallback='normal')

    try:
        p = Product(
            barcode=barcode, name=name,
            sku=sku,
            category_id=d.get('category_id') or None,
            supplier_id=d.get('supplier_id') or None,
            buy_price=buy_price, sell_price=sell_price,
            wholesale_price=wholesale_price,
            stock_qty=stock_qty, low_stock_lvl=low_stock_lvl,
            price_per_kg=price_per_kg,
            barcode_type=d.get('barcode_type', 'normal'),
            rack_number=d.get('rack_number', ''),
            section_number=d.get('section_number', ''),
            warranty_period=d.get('warranty_period', 'none'),
            product_type=product_type,
            is_imei_tracked=0,
        )
        db.session.add(p)
        db.session.flush()
        # Auto-generate EAN-13 if no barcode provided
        if not p.barcode:
            for _ in range(BARCODE_EAN13_ATTEMPTS):
                candidate = gen_ean13(p.id)
                if not Product.query.filter_by(barcode=candidate).first():
                    p.barcode = candidate
                    break
        db.session.commit()
        _invalidate_low_stock_cache()
        log_action(f'Added product: {p.name}')
        return jsonify(p.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to add product: {str(e)}'}), 500

@app.route('/api/products/<int:pid>', methods=['GET'])
@login_required
def api_get_product(pid):
    p = db.session.get(Product, pid)
    if not p:
        abort(404)
    return jsonify(p.to_dict())


def _parse_product_warranty_months(raw_warranty):
    normalized = str(raw_warranty or '').strip().lower()
    if not normalized or normalized == 'none':
        return None
    match = re.search(r'(\d+)', normalized)
    if not match:
        return None
    try:
        months = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return months if months >= 0 else None


@app.route('/api/products/<int:pid>/details', methods=['GET'])
@login_required
def api_product_details(pid):
    product = db.session.get(Product, pid)
    if not product or product.status != 'active':
        return jsonify({'success': False, 'error': 'Product not found'}), 404

    warranty_period = _parse_product_warranty_months(product.warranty_period)

    payload = {
        'id': product.id,
        'name': product.name,
        'brand': '',
        'model': '',
        'color': '',
        'storage': '',
        'purchase_price': float(product.buy_price or 0),
        'sale_price': float(product.sell_price or 0),
        'warranty_period': warranty_period,
        'warranty_status': '',
        'supplier_id': product.supplier_id,
        'supplier_name': product.supplier_obj.name if product.supplier_obj else '',
    }
    return jsonify({'success': True, 'product': payload})

@app.route('/api/products/<int:pid>', methods=['PUT'])
@login_required
def api_update_product(pid):
    require_roles('Admin', 'Operator', 'Manager')
    p = db.session.get(Product, pid)
    if not p:
        abort(404)
    d = request.get_json(silent=True)
    if not d:
        return jsonify({'error': 'Invalid or missing JSON body'}), 400

    # ── Required field validation ──────────────────────────────────────────
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Product name is required'}), 400

    # ── Duplicate barcode check (exclude this product) ─────────────────────
    barcode = (d.get('barcode') or '').strip() or None
    sku = (d.get('sku') or '').strip() or None
    if barcode and barcode_in_use(barcode, exclude_product_id=pid):
        return jsonify({'error': f'Barcode "{barcode}" is already used by another product'}), 409
    if sku and Product.query.filter(Product.sku == sku, Product.id != pid).first():
        return jsonify({'error': f'SKU "{sku}" is already used by another product'}), 409
    product_type = normalize_product_type(d.get('product_type', p.product_type), fallback='normal')

    try:
        p.name    = name
        p.barcode = barcode
        for k in ['category_id', 'supplier_id', 'sell_price', 'buy_price',
                  'wholesale_price', 'stock_qty', 'low_stock_lvl', 'price_per_kg',
                  'barcode_type', 'rack_number', 'section_number', 'warranty_period', 'sku']:
            if k in d:
                setattr(p, k, d[k])
        p.product_type = product_type
        p.is_imei_tracked = 0
        db.session.commit()
        if 'stock_qty' in d:
            _invalidate_low_stock_cache()
        return jsonify(p.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update product: {str(e)}'}), 500

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@login_required
def api_delete_product(pid):
    require_roles('Admin', 'Operator', 'Manager')

    product = db.session.get(Product, pid)
    if not product:
        return jsonify({'success': False, 'error': 'Product not found'}), 404

    try:
        product.status = 'inactive'
        db.session.commit()
        log_action(
            f'Product inactivated: {product.name}',
            target_type='product',
            target_id=product.id,
            metadata={'status': product.status, 'barcode': product.barcode},
        )
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Failed to delete product: {str(e)}'}), 500

# ── API: CATEGORIES ───────────────────────────────────────────────────────────
@app.route('/api/categories', methods=['GET', 'POST'])
@login_required
def api_categories():
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        name = (d.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Category name is required.'}), 400
        c = Category(name=name, description=(d.get('description') or '').strip())
        db.session.add(c); db.session.commit()
        return jsonify(c.to_dict()), 201
    return jsonify([c.to_dict() for c in Category.query.all()])

@app.route('/api/categories/<int:cid>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def api_category(cid):
    c = Category.query.get_or_404(cid)
    if request.method == 'GET':
        return jsonify(c.to_dict())
    if request.method == 'DELETE':
        require_roles('Admin', 'Operator')
        active = Product.query.filter_by(category_id=cid, status='active').count()
        if active > 0:
            return jsonify({'ok': False, 'msg': f'Cannot delete — {active} active products use this category.'}), 400
        db.session.delete(c); db.session.commit()
        log_action(f'Deleted category: {c.name}')
        return jsonify({'ok': True})
    # PUT — update name/description
    require_roles('Admin', 'Operator', 'Manager')
    d = request.get_json() or {}
    new_name = (d.get('name') or '').strip()
    if new_name:
        existing = Category.query.filter(Category.name == new_name, Category.id != cid).first()
        if existing:
            return jsonify({'ok': False, 'msg': 'A category with that name already exists.'}), 400
        c.name = new_name
    if 'description' in d:
        c.description = (d.get('description') or '').strip()
    db.session.commit()
    log_action(f'Updated category: {c.name}')
    return jsonify(c.to_dict())

# ── API: USERS ────────────────────────────────────────────────────────────────
@app.route('/api/users', methods=['GET', 'POST'])
@login_required
def api_users():
    # Security: user creation is restricted to Admin to prevent Operator privilege escalation.
    require_roles('Admin')
    app.logger.debug("api_users accessed by %s (%s)", getattr(current_user, 'username', 'unknown'), getattr(current_user, 'role', None))
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        username = (d.get('username') or '').strip()
        password = d.get('password') or ''
        new_role = normalize_role((d.get('role') or 'Cashier').strip())
        app.logger.debug(
            "User management action=add_user actor=%s role=%s",
            getattr(current_user, 'username', 'unknown'),
            getattr(current_user, 'role', None),
        )
        if not username:
            return jsonify({'error': 'Username is required.'}), 400
        if not password:
            return jsonify({'error': 'Password is required.'}), 400
        pw_error = validate_password_strength(
            password,
            username=username,
            email=d.get('email', ''),
            full_name=d.get('full_name', ''),
        )
        if pw_error:
            return jsonify({'error': pw_error}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username already exists'}), 400
        u = User(username=username, full_name=d.get('full_name', ''), role=new_role,
                 email=d.get('email', ''), force_password_change=True,
                 is_admin=is_admin(new_role), is_owner=False, is_primary_admin=False)
        u.set_password(password)
        db.session.add(u); db.session.commit()
        return jsonify(u.to_dict()), 201
    return jsonify([u.to_dict() for u in User.query.all()])

@app.route('/api/users/<int:uid>', methods=['PUT', 'DELETE'])
@login_required
def api_user(uid):
    require_roles('Admin', 'Operator')
    u = User.query.get_or_404(uid)
    if request.method == 'DELETE':
        u.status = 'inactive'; db.session.commit()
        log_action(
            f'User inactivated: {u.username}',
            target_type='user',
            target_id=u.id,
            metadata={'status': u.status, 'role': u.role},
        )
        return jsonify({'ok': True})
    d = request.get_json(silent=True) or {}
    # Security: prevent users from promoting others to a role equal to or higher than their own.
    if 'role' in d and role_rank(normalize_role((d.get('role') or '').strip())) >= role_rank(current_user.role):
        abort(403)
    for k in ['full_name', 'role', 'status', 'email']:
        if k in d:
            setattr(u, k, normalize_role(d[k]) if k == 'role' else d[k])
    if 'role' in d:
        u.is_admin = is_admin(u)
        if not is_admin(u):
            u.is_owner = False
            u.is_primary_admin = False
    if d.get('password'):
        pw_error = validate_password_strength(
            d.get('password', ''),
            username=d.get('username', u.username),
            email=d.get('email', u.email),
            full_name=d.get('full_name', u.full_name),
        )
        if pw_error:
            return jsonify({'error': pw_error}), 400
        u.set_password(d['password'])
        u.force_password_change = True
    db.session.commit()
    if d.get('password'):
        log_action(
            f'Password reset completed for user: {u.username}',
            target_type='user',
            target_id=u.id,
            metadata={'role': u.role, 'reset_by_admin': True},
        )
    return jsonify(u.to_dict())

@app.route('/api/stats')
@login_required
def api_stats():
    today = datetime.now().date()
    q     = Sale.query.filter(func.date(Sale.sale_date) == today, Sale.status == 'completed')
    rev   = q.with_entities(func.sum(Sale.total_amount)).scalar() or 0
    count = q.count()
    low   = Product.query.filter(Product.stock_qty <= Product.low_stock_lvl).count()
    return jsonify({'today_revenue': float(rev), 'today_count': count,
                    'avg_basket': float(rev / count) if count else 0, 'low_stock': low})

# ── API: PRINTER / DRAWER ─────────────────────────────────────────────────────
def _printing_error(message: str, *, code: str, status: int, extra: dict[str, Any] | None = None):
    payload = {'ok': False, 'code': code, 'error': message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _get_label_printer_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        'label_printer_type': (StoreSettings.get('label_printer_type', 'windows') or 'windows').strip().lower(),
        'label_printer_name': (StoreSettings.get('label_printer_name', '') or '').strip(),
        'label_printer_ip': (StoreSettings.get('label_printer_ip', '') or '').strip(),
        'label_orientation': str(StoreSettings.get('label_orientation', 'portrait') or 'portrait').strip().lower(),
        'label_text_align': str(StoreSettings.get('label_text_align', 'center') or 'center').strip().lower(),
        'label_custom_footer': str(StoreSettings.get('label_custom_footer', '') or '').strip(),
    }
    for float_key, default in (
        ('label_width_mm', 30.0),
        ('label_height_mm', 20.0),
        ('label_gap_mm', 3.0),
        ('label_margin_top', 2.0),
        ('label_margin_left', 2.0),
        ('label_margin_right', 2.0),
        ('label_margin_bottom', 2.0),
        ('label_barcode_width_mm', 24.0),
        ('label_barcode_height_mm', 8.0),
    ):
        try:
            cfg[float_key] = float(StoreSettings.get(float_key, default) or default)
        except (TypeError, ValueError):
            cfg[float_key] = default
    for int_key, default in (
        ('label_printer_port', 9100),
        ('label_dpi', 203),
        ('label_font_size', 9),
        ('label_darkness', 50),
        ('label_speed', 2),
    ):
        try:
            cfg[int_key] = int(StoreSettings.get(int_key, default) or default)
        except (TypeError, ValueError):
            cfg[int_key] = default
    for bool_key, default in (
        ('label_show_name', True),
        ('label_show_price', True),
        ('label_show_barcode', True),
        ('label_show_wholesale_price', False),
        ('label_show_sku', False),
        ('label_show_rack', False),
        ('label_show_section', False),
        ('label_show_company', False),
        ('label_show_footer', False),
    ):
        raw_value = StoreSettings.get(bool_key, 'true' if default else 'false')
        cfg[bool_key] = str(raw_value).strip().lower() == 'true'
    cfg['label_barcode_type'] = str(StoreSettings.get('label_barcode_type', 'code128') or 'code128').strip().lower()
    return cfg


def resolve_label_printer(cfg: dict[str, Any]) -> dict[str, Any]:
    return printer_service.resolve_label_printer(cfg).to_dict()


@app.route('/api/drawer/open', methods=['POST'])
@login_required
def api_drawer_open():
    import socket as _socket

    command = b'\x1b\x70\x00\x19\xfa'
    printer_ip = (StoreSettings.get('printer_ip', '') or '').strip()
    try:
        printer_port = int(StoreSettings.get('printer_port', '9100') or 9100)
    except (TypeError, ValueError):
        printer_port = 9100

    if printer_ip:
        try:
            with _socket.create_connection((printer_ip, printer_port), timeout=5) as sock:
                sock.sendall(command)
            return jsonify({'ok': True, 'msg': f'Cash drawer open signal sent to {printer_ip}:{printer_port}'})
        except Exception as exc:
            app.logger.exception(
                'Cash drawer open via TCP failed user=%s printer_ip=%s printer_port=%s',
                getattr(current_user, 'username', 'anonymous'),
                printer_ip,
                printer_port,
            )
            return jsonify({'ok': False, 'msg': f'Failed to open cash drawer via network printer: {exc}'}), 500

    printer_name = (StoreSettings.get('printer_name', '') or '').strip()
    if os.name == 'nt' and sys.platform == 'win32' and printer_name:
        try:
            payload_bytes = command.encode('utf-8') if isinstance(command, str) else command
            target_printer = printer_service.send_raw_to_windows_printer(
                printer_name,
                payload_bytes,
                document_name='Garage Management System Cash Drawer Open',
            )
            return jsonify({'ok': True, 'msg': f'Cash drawer open signal sent to {target_printer}'})
        except Exception as exc:
            app.logger.exception(
                'Cash drawer open via Windows printer failed user=%s printer_name=%s',
                getattr(current_user, 'username', 'anonymous'),
                printer_name,
            )
            return jsonify({'ok': False, 'msg': f'Failed to open cash drawer via Windows printer: {exc}'}), 500

    return jsonify({'ok': False, 'msg': 'Configure printer IP or printer name in Settings to open the cash drawer.'}), 400

def _sniff_uploaded_backup_file(filepath: str, filename: str, declared_mimetype: str | None = None) -> tuple[bool, str | None]:
    ext = (os.path.splitext(filename or '')[1] or '').lower()
    allowed_extensions = {'.sql', '.gz', '.zip', '.db'}
    if ext not in allowed_extensions:
        return False, 'Only .sql, .gz, .zip, or .db files allowed'

    normalized_mimetype = (declared_mimetype or '').split(';', 1)[0].strip().lower()
    allowed_mimetypes = {
        '.db': {'application/vnd.sqlite3', 'application/x-sqlite3', 'application/octet-stream'},
        '.zip': {'application/zip', 'application/x-zip-compressed', 'multipart/x-zip', 'application/octet-stream'},
        '.gz': {'application/gzip', 'application/x-gzip', 'application/octet-stream'},
        '.sql': {'application/sql', 'application/x-sql', 'text/plain', 'text/sql', 'text/x-sql', 'application/octet-stream'},
    }
    if normalized_mimetype and normalized_mimetype not in allowed_mimetypes[ext]:
        return False, f'Unexpected content type for {ext} upload: {normalized_mimetype}'

    try:
        with open(filepath, 'rb') as fh:
            header = fh.read(512)
    except OSError:
        return False, 'Uploaded file could not be read for validation'

    if not header:
        return False, 'Uploaded file is empty'

    if ext == '.db':
        if not header.startswith(b'SQLite format 3\x00'):
            return False, 'The uploaded .db file is not a valid SQLite database file'
        return True, None

    if ext == '.zip':
        if not header.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
            return False, 'The uploaded .zip file does not have a valid ZIP signature'
        if not zipfile.is_zipfile(filepath):
            return False, 'The uploaded .zip file is not a valid ZIP archive'
        return True, None

    if ext == '.gz':
        if not header.startswith(b'\x1f\x8b'):
            return False, 'The uploaded .gz file does not have a valid GZip signature'
        return True, None

    if b'\x00' in header:
        return False, 'The uploaded .sql file must be plain text SQL, not a binary file'

    try:
        with open(filepath, 'rb') as fh:
            sample_bytes = fh.read(8192)
        sample = sample_bytes.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        return False, 'The uploaded .sql file must be valid UTF-8 or ASCII text'
    except OSError:
        return False, 'Uploaded SQL file could not be read for validation'

    sql_markers = (
        'create table',
        'insert into',
        'alter table',
        'begin transaction',
        'commit',
        'pragma',
        'drop table',
        'update ',
        'delete from',
    )
    if not any(marker in sample.lower() for marker in sql_markers):
        return False, 'The uploaded .sql file does not appear to contain SQL statements'
    return True, None


# ── API: BACKUP UPLOAD-RESTORE ────────────────────────────────────────────────
@app.route('/api/backup/upload-restore', methods=['POST'])
@login_required
def api_backup_upload_restore():
    # Full-database restore is destructive and must be Admin-only.
    require_roles('Admin')
    if 'file' not in request.files:
        return jsonify({'ok': False, 'msg': 'No file uploaded'}), 400
    uploaded = request.files['file']
    if not uploaded.filename:
        return jsonify({'ok': False, 'msg': 'Empty filename'}), 400
    if not str(uploaded.filename).lower().endswith('.zip'):
        return jsonify({'ok': False, 'msg': 'Please select a Garage Management System backup .zip file.'}), 400

    suffix = os.path.splitext(uploaded.filename)[1] or '.tmp'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        uploaded.save(tf.name)
        tmp_path = tf.name

    try:
        is_valid, validation_msg = _sniff_uploaded_backup_file(
            tmp_path,
            uploaded.filename,
            declared_mimetype=uploaded.mimetype or uploaded.content_type,
        )
        if not is_valid:
            return jsonify({'ok': False, 'msg': validation_msg or 'Invalid backup file'}), 400

        # Use backup.py restore helper for SQLite file-based restores
        from backup import do_restore
        result = do_restore(tmp_path)
        if not result.get('ok'):
            return jsonify({'ok': False, 'msg': result.get('msg', 'Restore failed')}), 500
        log_action(
            f'Database restored from uploaded file: {uploaded.filename}',
            target_type='backup_restore',
            metadata={'filename': uploaded.filename},
        )
        return jsonify({'ok': True, 'msg': f'Database restored from {uploaded.filename}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

# ── API: INVENTORY EXPORT ─────────────────────────────────────────────────────
@app.route('/api/inventory/export')
@login_required
def api_inventory_export():
    require_roles('Admin', 'Operator', 'Manager')
    products = Product.query.filter_by(status='active').order_by(Product.name).all()
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Barcode', 'Name', 'Category', 'Supplier', 'Buy Price', 'Sell Price',
                     'Wholesale Price', 'Stock Qty', 'Low Stock Level', 'Status', 'Rack', 'Section', 'Stock Value'])
    for p in products:
        writer.writerow([
            p.id, p.barcode or '', p.name,
            p.cat.name if p.cat else '',
            p.supplier_obj.name if p.supplier_obj else '',
            p.buy_price, p.sell_price, p.wholesale_price or 0,
            p.stock_qty, p.low_stock_lvl, p.status,
            p.rack_number or '', p.section_number or '',
            round((p.sell_price or 0) * (p.stock_qty or 0), 2),
        ])
    output.seek(0)
    log_action('Inventory exported to CSV')
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=inventory_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )

# ── API: AUDIT LOGS ───────────────────────────────────────────────────────────
@app.route('/api/audit-logs')
@login_required
def api_audit_logs():
    require_roles('Admin', 'Operator')
    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', 50))
    except (ValueError, TypeError):
        per_page = 50
    user_id  = request.args.get('user_id', '')
    q        = request.args.get('q', '')
    query    = UserLog.query
    if user_id:
        try:
            query = query.filter_by(user_id=int(user_id))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid user_id parameter'}), 400
    if q:
        query = query.filter(UserLog.action.ilike(contains_sql_like(q), escape='\\'))
    total  = query.count()
    logs   = query.order_by(UserLog.date.desc()).offset((page-1)*per_page).limit(per_page).all()
    users  = {u.id: u.full_name for u in User.query.all()}
    return jsonify({
        'total': total, 'page': page, 'per_page': per_page,
        'logs': [{
            'id': l.id,
            'user': users.get(l.user_id, f'User #{l.user_id}'),
            'action': l.action,
            'target_type': l.target_type or '',
            'target_id': l.target_id,
            'metadata_summary': l.metadata_summary or '',
            'date': l.date.strftime('%Y-%m-%d %H:%M:%S') if l.date else '',
            'ip': l.ip or '',
        } for l in logs]
    })

# ── API: ACTIVE SESSIONS ──────────────────────────────────────────────────────
@app.route('/api/sessions')
@login_required
def api_sessions_list():
    require_roles('Admin', 'Operator')
    users = {u.id: u.to_dict() for u in User.query.all()}
    result = []
    with _active_sessions_lock:
        sessions_copy = dict(_active_sessions)
    for sid, info in sessions_copy.items():
        u = users.get(info.get('user_id'), {})
        result.append({
            'session_id': sid,
            'user_id': info.get('user_id'),
            'username': u.get('username', ''),
            'full_name': u.get('full_name', ''),
            'role': u.get('role', ''),
            'login_time': info.get('login_time', ''),
            'last_seen': info.get('last_seen', ''),
            'ip': info.get('ip', ''),
            'is_current': sid == session.get('_id'),
        })
    result.sort(key=lambda x: x['login_time'], reverse=True)
    return jsonify(result)

@app.route('/api/sessions/<sid>', methods=['DELETE'])
@login_required
def api_session_kill(sid):
    require_roles('Admin', 'Operator')
    killed_user_id = None
    with _active_sessions_lock:
        tracked = _active_sessions.get(sid)
        if tracked:
            killed_user_id = tracked.get('user_id')
        _active_sessions.pop(sid, None)
    if killed_user_id:
        killed_user = User.query.get(killed_user_id)
        if killed_user:
            killed_user.session_token = None
            db.session.commit()
    log_action(
        f'Killed session {sid[:8]}...',
        target_type='session',
        metadata={'session_id_prefix': sid[:8]},
    )
    return jsonify({'ok': True})

@app.route('/api/sessions/logout-all', methods=['POST'])
@login_required
def api_sessions_logout_all():
    require_roles('Admin', 'Operator')
    current_sid = session.get('_id')
    user_ids_to_reset = set()
    killed = 0
    with _active_sessions_lock:
        for sid, info in list(_active_sessions.items()):
            if sid != current_sid:
                _active_sessions.pop(sid, None)
                user_id = info.get('user_id')
                if user_id and user_id != current_user.id:
                    user_ids_to_reset.add(user_id)
                killed += 1
    if user_ids_to_reset:
        User.query.filter(User.id.in_(user_ids_to_reset)).update(
            {'session_token': None},
            synchronize_session=False,
        )
        db.session.commit()
    log_action(
        f'Forced logout of all sessions ({killed} killed)',
        target_type='session',
        metadata={'killed': killed},
    )
    return jsonify({'ok': True, 'killed': killed})

# ── API: FORCE PASSWORD CHANGE ────────────────────────────────────────────────
@app.route('/api/users/change-password', methods=['POST'])
@login_required
def api_change_password():
    d = request.get_json() or {}
    current_pw = d.get('current_password', '')
    new_pw = d.get('password', '')
    confirm_pw = d.get('confirm_password', '')

    if 'current_password' not in d or not str(current_pw).strip():
        return jsonify({'ok': False, 'msg': 'Current password is required.'}), 400
    if not new_pw:
        return jsonify({'ok': False, 'msg': 'New password is required.'}), 400
    if confirm_pw and confirm_pw != new_pw:
        return jsonify({'ok': False, 'msg': 'New password and confirmation do not match.'}), 400

    password_change_required = requires_password_change(current_user)
    if not current_user.check_password(current_pw):
        return jsonify({'ok': False, 'msg': 'Current password is incorrect.'}), 400
    if current_user.check_password(new_pw):
        return jsonify({'ok': False, 'msg': 'New password must be different from your current password.'}), 400

    if not new_pw.strip():
        return jsonify({'ok': False, 'msg': 'Please enter a new password.'}), 400
    if new_pw != new_pw.strip():
        return jsonify({'ok': False, 'msg': 'Password cannot start or end with spaces.'}), 400
    pw_error = validate_password_strength(
        new_pw,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
    )
    if pw_error:
        return jsonify({'ok': False, 'msg': pw_error}), 400
    current_user.set_password(new_pw)
    current_user.force_password_change = False
    if is_admin(current_user) and is_initial_security_setup_pending():
        set_initial_security_setup_pending(False)
    db.session.commit()
    log_action(
        'Password changed',
        target_type='user',
        target_id=current_user.id,
        metadata={'username': current_user.username, 'password_change_required': password_change_required},
    )
    next_page = '/billing' if is_pos_operator_role(current_user.role) else '/dashboard'
    return jsonify({
        'ok': True,
        'msg': 'Password updated successfully.',
        'redirect': next_page,
        'force_password_change': False,
    })

# ── API: LICENSE ──────────────────────────────────────────────────────────────
@app.route('/api/license/check')
def api_license_check():
    status = lic_module.activation_status()
    result = lic_module.check()
    result['status'] = status
    return jsonify(result)

@app.route('/api/license/activate', methods=['POST'])
def api_license_activate():
    data = request.get_json(silent=True) or {}
    key = (data.get('key') or '').strip()
    result = lic_module.activate_offline(key)
    log_action(
        'License activation attempted' if not result.get('ok') else 'License activated',
        target_type='license',
        metadata={
            'key_suffix': key[-4:] if key else '',
            'status': 'success' if result.get('ok') else 'failed',
        },
        commit=True,
    )
    if result.get('ok'):
        result['redirect'] = '/setup-admin' if is_first_run_admin_setup_required() else '/login'
        return jsonify(result)
    return jsonify(result), 400

@app.route('/api/license/info')
@login_required
def api_license_info():
    result = lic_module.check()
    info   = result.get('info') or {}
    return jsonify({'ok': result['licensed'], 'info': info, 'days_remaining': lic_module.days_remaining(info)})

@app.route('/api/system/status', methods=['GET'])
@login_required
def api_system_status():
    require_roles('Admin', 'Operator')

    license_status = lic_module.check()
    return jsonify(
        {
            'payhere_sandbox': bool(is_sandbox_mode()),
            'license_valid': bool(license_status.get('licensed')),
            'destructive_tools_enabled': True,
        }
    )

@app.route('/api/barcode/scan/<path:barcode>')
@login_required
def api_barcode_scan(barcode):
    decoded = decode_barcode(barcode)
    btype   = decoded['type']
    if btype == 'normal':
        product = Product.query.filter_by(barcode=barcode, status='active').first()
        if not product:
            # Check alias barcodes table
            pb = ProductBarcode.query.filter_by(barcode=barcode).first()
            if pb:
                product = Product.query.filter_by(id=pb.product_id, status='active').first()
        if not product: return jsonify({'ok': False, 'msg': f'Product not found: {barcode}'})
        return jsonify({'ok': True, 'type': 'normal', 'product': product.to_dict(),
                        'quantity': 1, 'price': product.sell_price, 'total': product.sell_price, 'label': product.name})
    elif btype == 'weight':
        product_code = decoded['product_code']
        weight_kg    = decoded['weight_kg']
        term = contains_sql_like(product_code)
        product = (Product.query.filter(Product.barcode.like(term, escape='\\'), Product.status == 'active').first()
                   or Product.query.filter_by(id=int(product_code), status='active').first())
        if not product: return jsonify({'ok': False, 'msg': f'Weight product not found (code: {product_code})'})
        price_per_kg = product.price_per_kg or product.sell_price
        total = round(weight_kg * price_per_kg, 2)
        return jsonify({'ok': True, 'type': 'weight', 'product': product.to_dict(),
                        'quantity': weight_kg, 'weight_kg': weight_kg, 'weight_g': decoded['weight_g'],
                        'price_per_kg': price_per_kg, 'price': total, 'total': total,
                        'label': f"{product.name} ({weight_kg:.3f}kg)"})
    elif btype == 'price':
        product_code = decoded['product_code']
        price_lkr    = decoded['price_lkr']
        term = contains_sql_like(product_code)
        product = (Product.query.filter(Product.barcode.like(term, escape='\\'), Product.status == 'active').first()
                   or Product.query.filter_by(id=int(product_code), status='active').first())
        if not product: return jsonify({'ok': False, 'msg': f'Price product not found (code: {product_code})'})
        return jsonify({'ok': True, 'type': 'price', 'product': product.to_dict(),
                        'quantity': 1, 'price': price_lkr, 'total': price_lkr,
                        'label': f"{product.name} (price-coded)"})
    return jsonify({'ok': False, 'msg': 'Unknown barcode format'})

# ── BARCODE ONLINE LOOKUP (OpenFoodFacts) ─────────────────────────────────────


@app.route('/api/barcode-scanner/products')
@login_required
def api_barcode_scanner_products():
    require_roles('Admin', 'Operator', 'Manager', 'Developer')
    rows = Product.query.filter_by(status='active').order_by(Product.name.asc()).all()
    payload = [
        {
            'id': p.id,
            'name': p.name,
            'barcode': p.barcode or '',
            'sku': p.sku or '',
            'sell_price': float(p.sell_price or 0),
        }
        for p in rows
    ]
    return jsonify({'ok': True, 'products': payload})


@app.route('/api/barcode-scanner/product/<int:pid>')
@login_required
def api_barcode_scanner_product(pid):
    require_roles('Admin', 'Operator', 'Manager', 'Developer')
    product = db.session.get(Product, pid)
    if not product or product.status != 'active':
        return jsonify({'ok': False, 'error': 'Product not found'}), 404
    return jsonify({'ok': True, 'product': ProductFillService.product_payload(product)})


@app.route('/api/barcode-scanner/generate', methods=['POST'])
@login_required
def api_barcode_scanner_generate():
    require_roles('Admin', 'Operator', 'Manager', 'Developer')
    data = request.get_json(silent=True) or {}
    product_id = int(data.get('product_id') or 0)
    if not product_id:
        return jsonify({'ok': False, 'error': 'Product is required'}), 400
    product = db.session.get(Product, product_id)
    if not product or product.status != 'active':
        return jsonify({'ok': False, 'error': 'Product not found'}), 404

    mode = data.get('mode') or StoreSettings.get('barcode_auto_mode', 'random')
    barcode_type = data.get('barcode_type') or StoreSettings.get('label_barcode_type', 'code128')
    prefix = data.get('prefix') or StoreSettings.get('barcode_prefix', 'SM')
    manual = data.get('barcode') or ''

    def _exists(candidate: str) -> bool:
        return barcode_in_use(candidate, exclude_product_id=product.id)

    try:
        result = BarcodeService.generate(
            product_id=product.id,
            mode=mode,
            barcode_type=barcode_type,
            prefix=prefix,
            manual_value=manual,
            exists_fn=_exists,
        )
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400

    preview = BarcodeService.build_preview_payload(barcode=result.barcode, barcode_type=result.barcode_type)
    return jsonify({'ok': True, 'barcode': result.barcode, 'barcode_type': result.barcode_type, 'mode': result.mode, 'preview': preview})


@app.route('/api/barcode-scanner/label-preview', methods=['POST'])
@login_required
def api_barcode_scanner_label_preview():
    require_roles('Admin', 'Operator', 'Manager', 'Developer')
    from io import BytesIO
    import base64

    data = request.get_json(silent=True) or {}
    product = data.get('product') or {}

    settings = _get_label_printer_config()
    settings.update({k: v for k, v in data.get('settings', {}).items() if k in settings or k.startswith('label_')})

    image = label_print_service.render_label_image(
        barcode_value=str(product.get('barcode') or ''),
        product_name=str(product.get('name') or ''),
        price=str(product.get('sell_price') or ''),
        sku=str(product.get('sku') or ''),
        company_name=StoreSettings.get('store_name', '') or '',
        custom_footer=str(settings.get('label_custom_footer') or ''),
        width_mm=float(settings.get('label_width_mm', 30.0)),
        height_mm=float(settings.get('label_height_mm', 20.0)),
        dpi=int(settings.get('label_dpi', 203)),
        barcode_type=str(settings.get('label_barcode_type', 'code128')),
        barcode_width_mm=float(settings.get('label_barcode_width_mm', 24.0)),
        barcode_height_mm=float(settings.get('label_barcode_height_mm', 8.0)),
        font_size=int(settings.get('label_font_size', 9)),
        text_align=str(settings.get('label_text_align', 'center')),
        margin_top_mm=float(settings.get('label_margin_top', 2.0)),
        margin_left_mm=float(settings.get('label_margin_left', 2.0)),
        margin_right_mm=float(settings.get('label_margin_right', 2.0)),
        margin_bottom_mm=float(settings.get('label_margin_bottom', 2.0)),
        show_name=str(settings.get('label_show_name', 'true')).lower() == 'true',
        show_price=str(settings.get('label_show_price', 'true')).lower() == 'true',
        show_barcode=str(settings.get('label_show_barcode', 'true')).lower() == 'true',
        show_wholesale_price=str(settings.get('label_show_wholesale_price', 'false')).lower() == 'true',
        show_sku=str(settings.get('label_show_sku', 'false')).lower() == 'true',
        show_rack=str(settings.get('label_show_rack', 'false')).lower() == 'true',
        show_section=str(settings.get('label_show_section', 'false')).lower() == 'true',
        show_footer=str(settings.get('label_show_footer', 'false')).lower() == 'true',
    )
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return jsonify({'ok': True, 'image_data_url': f'data:image/png;base64,{encoded}'})


@app.route('/api/barcode-scanner/search', methods=['POST'])
@login_required
def api_barcode_scanner_search():
    require_roles('Admin', 'Operator', 'Manager', 'Developer')
    data = request.get_json(silent=True) or {}
    value = str(data.get('value') or '').strip()
    target = str(data.get('target') or StoreSettings.get('scanner_target', 'product')).strip().lower()
    value = ScannerService.sanitize_scan_value(
        value,
        prefix=str(StoreSettings.get('scanner_prefix', '') or ''),
        suffix=str(StoreSettings.get('scanner_suffix', '') or ''),
    )
    if not value:
        return jsonify({'ok': False, 'error': 'Scanned value is required'}), 400

    if target in {'product', 'both'}:
        product = Product.query.filter_by(barcode=value, status='active').first()
        if not product:
            alias = ProductBarcode.query.filter_by(barcode=value).first()
            if alias:
                product = db.session.get(Product, alias.product_id)
        if product and product.status == 'active':
            return jsonify({'ok': True, 'type': 'product', 'product': ProductFillService.product_payload(product)})

    if target in {'invoice', 'both'}:
        sale = find_sale_by_scanned_value(value)
        if sale:
            return jsonify({'ok': True, 'type': 'invoice', 'invoice': {'id': sale.id, 'invoice_number': sale.invoice_number, 'total_amount': float(sale.total_amount or 0), 'sale_date': sale.sale_date.strftime('%Y-%m-%d %H:%M:%S')}})

    return jsonify({'ok': False, 'error': f'No product/invoice found for {value}'}), 404


@app.route('/barcode_lookup/<path:barcode>')
@login_required
def barcode_lookup(barcode):
    """Lookup barcode from OpenFoodFacts when not found in local DB.
    Returns: { found, name, brand, category, barcode, image }
    """
    import requests as http_req
    barcode = barcode.strip()
    # Double-check local DB first (safety net)
    local = Product.query.filter_by(barcode=barcode, status='active').first()
    if not local:
        pb = ProductBarcode.query.filter_by(barcode=barcode).first()
        if pb:
            local = Product.query.filter_by(id=pb.product_id, status='active').first()
    if local:
        return jsonify({'found': True, 'local': True, 'product': local.to_dict()})
    # Call OpenFoodFacts API
    try:
        url = f'https://world.openfoodfacts.org/api/v0/product/{barcode}.json'
        r = http_req.get(url, timeout=6, headers={'User-Agent': 'GarageManagementSystem/3.0'})
        data = r.json()
        if data.get('status') == 1 and data.get('product'):
            p = data['product']
            name     = (p.get('product_name') or p.get('product_name_en') or '').strip()
            brand    = (p.get('brands') or '').strip()
            # Best category guess from tags
            cat_tags = p.get('categories_tags') or []
            category = ''
            for tag in cat_tags:
                if tag.startswith('en:'):
                    category = tag[3:].replace('-', ' ').title()
                    break
            return jsonify({
                'found': True, 'local': False,
                'barcode': barcode, 'name': name,
                'brand': brand, 'category': category,
                'image': p.get('image_front_small_url') or p.get('image_url') or '',
                'nutriscore': p.get('nutriscore_grade', '').upper(),
            })
        return jsonify({'found': False, 'barcode': barcode,
                        'msg': 'Not found in OpenFoodFacts database'})
    except Exception as e:
        return jsonify({'found': False, 'barcode': barcode, 'error': str(e)})


# ── GOOGLE DRIVE BACKUP ───────────────────────────────────────────────────────
def _gdrive_headers(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

def _gdrive_get_token():
    """Exchange stored OAuth2 refresh_token for an access_token."""
    import requests as http_req
    client_id     = StoreSettings.get('gdrive_client_id', '')
    client_secret = StoreSettings.get('gdrive_client_secret', '')
    refresh_token = StoreSettings.get('gdrive_refresh_token', '')
    if not all([client_id, client_secret, refresh_token]):
        return None
    try:
        r = http_req.post('https://oauth2.googleapis.com/token', data={
            'client_id': client_id, 'client_secret': client_secret,
            'refresh_token': refresh_token, 'grant_type': 'refresh_token',
        }, timeout=10)
        return r.json().get('access_token')
    except Exception:
        return None

def _gdrive_upload(filepath, filename, folder_id, token):
    """Upload a file to Google Drive and return the file ID."""
    import requests as http_req
    metadata = {'name': filename}
    if folder_id:
        metadata['parents'] = [folder_id]
    with open(filepath, 'rb') as f:
        file_data = f.read()
    # Multipart upload
    boundary = '==boundary=='
    meta_part = json.dumps(metadata).encode()
    body = (
        f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode()
        + meta_part + f'\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        + file_data + f'\r\n--{boundary}--'.encode()
    )
    r = http_req.post(
        'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart',
        headers={'Authorization': f'Bearer {token}',
                 'Content-Type': f'multipart/related; boundary={boundary}'},
        data=body, timeout=60
    )
    if r.status_code in (200, 201):
        return r.json().get('id')
    raise Exception(f'GDrive upload failed: {r.status_code} {r.text[:200]}')

@app.route('/api/backup/gdrive/status')
@login_required
def api_gdrive_status():
    require_roles('Admin', 'Operator')
    connected    = bool(StoreSettings.get('gdrive_refresh_token', ''))
    folder_id    = StoreSettings.get('gdrive_folder_id', '')
    freq         = StoreSettings.get('gdrive_backup_freq', 'daily')
    enabled      = StoreSettings.get('gdrive_backup_enabled', '0') == '1'
    last_log     = BackupLog.query.filter_by(destination='gdrive')\
                      .order_by(BackupLog.backup_date.desc()).first()
    return jsonify({
        'connected': connected, 'folder_id': folder_id,
        'freq': freq, 'enabled': enabled,
        'last_backup': last_log.to_dict() if last_log else None,
    })

@app.route('/api/backup/gdrive/save-config', methods=['POST'])
@login_required
def api_gdrive_save_config():
    require_roles('Admin', 'Operator')
    d = request.get_json() or {}
    for k in ['gdrive_client_id', 'gdrive_client_secret', 'gdrive_refresh_token',
              'gdrive_folder_id', 'gdrive_backup_freq', 'gdrive_backup_enabled']:
        if k in d:
            StoreSettings.set(k, str(d[k]))
    log_action('Google Drive backup config updated')
    return jsonify({'ok': True})

@app.route('/api/backup/gdrive/trigger', methods=['POST'])
@login_required
def api_gdrive_trigger():
    """Manually trigger a Google Drive backup."""
    require_roles('Admin', 'Operator')
    log_entry = BackupLog(destination='gdrive', status='pending')
    db.session.add(log_entry); db.session.commit()
    def _do_backup(log_id):
        with app.app_context():
            log = db.session.get(BackupLog, log_id)
            if not log: return
            tmp_path = None
            try:
                from backup import _sqlite_file_path, safe_sqlite_backup

                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                fname = f'garage_gdrive_{ts}.db'
                tmp_path = os.path.join(tempfile.gettempdir(), fname)
                sqlite_src = _sqlite_file_path()
                if not os.path.exists(sqlite_src):
                    raise Exception(f'SQLite database not found: {sqlite_src}')
                safe_sqlite_backup(sqlite_src, tmp_path)

                # Verify the backup was actually written
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                    raise Exception('Database backup file is empty — check DB credentials and permissions.')

                file_size = f'{os.path.getsize(tmp_path) // 1024} KB'

                # Upload to Google Drive
                token     = _gdrive_get_token()
                folder_id = StoreSettings.get('gdrive_folder_id', '')
                gdrive_id = None
                if token:
                    gdrive_id = _gdrive_upload(tmp_path, fname, folder_id, token)
                    notes = 'Uploaded to Google Drive OK'
                elif StoreSettings.get('gdrive_refresh_token', ''):
                    notes = 'GDrive token refresh failed — check OAuth credentials'
                else:
                    notes = 'GDrive not configured — dump created but not uploaded'

                log.backup_file    = fname
                log.file_size      = file_size
                log.status         = 'success'
                log.gdrive_file_id = gdrive_id or ''
                log.notes          = notes
                db.session.commit()
            except Exception as e:
                app.logger.error('GDrive backup failed: %s', e, exc_info=True)
                if log:
                    log.status = 'failed'
                    log.notes  = str(e)[:190]
                    db.session.commit()
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
    threading.Thread(target=_do_backup, args=(log_entry.id,), daemon=True).start()
    return jsonify({'ok': True, 'log_id': log_entry.id,
                    'msg': 'Backup started in background'})

@app.route('/api/backup/gdrive/logs')
@login_required
def api_gdrive_logs():
    require_roles('Admin', 'Operator')
    logs = BackupLog.query.order_by(BackupLog.backup_date.desc()).limit(20).all()
    return jsonify([l.to_dict() for l in logs])

@app.route('/api/backup/gdrive/auth-url')
@login_required
def api_gdrive_auth_url():
    """Return the Google OAuth2 authorization URL for user to grant Drive access."""
    require_roles('Admin', 'Operator')
    client_id = StoreSettings.get('gdrive_client_id', '')
    if not client_id:
        return jsonify({'ok': False, 'msg': 'Enter Google Client ID first'})
    redirect_uri = request.host_url.rstrip('/') + '/api/backup/gdrive/callback'
    scope = 'https://www.googleapis.com/auth/drive.file'
    url = (f'https://accounts.google.com/o/oauth2/v2/auth'
           f'?client_id={client_id}&redirect_uri={redirect_uri}'
           f'&response_type=code&scope={scope}&access_type=offline&prompt=consent')
    return jsonify({'ok': True, 'url': url})

@app.route('/api/backup/gdrive/callback')
@login_required
def api_gdrive_callback():
    """OAuth2 callback — exchange auth code for refresh token."""
    import requests as http_req
    code         = request.args.get('code', '')
    client_id    = StoreSettings.get('gdrive_client_id', '')
    client_secret= StoreSettings.get('gdrive_client_secret', '')
    redirect_uri = request.host_url.rstrip('/') + '/api/backup/gdrive/callback'
    if not code:
        return redirect(url_for('settings') + '#backup')
    try:
        r = http_req.post('https://oauth2.googleapis.com/token', data={
            'code': code, 'client_id': client_id, 'client_secret': client_secret,
            'redirect_uri': redirect_uri, 'grant_type': 'authorization_code',
        }, timeout=10)
        tokens = r.json()
        if 'refresh_token' in tokens:
            StoreSettings.set('gdrive_refresh_token', tokens['refresh_token'])
            flash('Google Drive connected successfully!', 'success')
        else:
            flash(f'OAuth error: {tokens.get("error_description","Unknown error")}', 'error')
    except Exception as e:
        flash(f'Connection failed: {e}', 'error')
    return redirect(url_for('settings'))


# ── PROFIT REPORT PAGE ────────────────────────────────────────────────────────
@app.route('/profit-report')
@login_required
def profit_report():
    require_roles('Admin', 'Operator', 'Manager')
    return render_template('profit_report.html')

# ── API: PROFIT DATA ──────────────────────────────────────────────────────────
@app.route('/api/profit/daily')
@login_required
def api_profit_daily():
    require_roles('Admin', 'Operator', 'Manager')
    try:
        days = min(365, max(1, int(request.args.get('days', 30))))
    except (ValueError, TypeError):
        days = 30
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)

    sale_cost_subq = db.session.query(
        SaleItem.sale_id.label('sale_id'),
        func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0).label('cost')
    ).join(Product, SaleItem.product_id == Product.id)\
     .group_by(SaleItem.sale_id)\
     .subquery()

    rows = db.session.query(
        func.date(Sale.sale_date).label('sale_day'),
        func.coalesce(func.sum(Sale.total_amount), 0).label('revenue'),
        func.coalesce(func.sum(sale_cost_subq.c.cost), 0).label('cost'),
        func.count(Sale.id).label('orders')
    ).outerjoin(sale_cost_subq, sale_cost_subq.c.sale_id == Sale.id)\
     .filter(
         func.date(Sale.sale_date).between(start, today),
         Sale.status == 'completed'
     )\
     .group_by(func.date(Sale.sale_date))\
     .all()

    row_map = {}
    for r in rows:
        day = r.sale_day
        if isinstance(day, str):
            day = datetime.strptime(day, '%Y-%m-%d').date()
        row_map[day] = {
            'revenue': float(r.revenue or 0),
            'cost': float(r.cost or 0),
            'orders': int(r.orders or 0),
        }

    data = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        values = row_map.get(d, {'revenue': 0.0, 'cost': 0.0, 'orders': 0})
        data.append({
            'date': str(d), 'day': d.strftime('%d/%m'),
            'revenue': values['revenue'], 'cost': values['cost'],
            'profit': values['revenue'] - values['cost'], 'orders': values['orders'],
        })
    return jsonify(data)

@app.route('/api/profit/monthly')
@login_required
def api_profit_monthly():
    require_roles('Admin', 'Operator', 'Manager')
    try:
        months = min(24, max(1, int(request.args.get('months', 12))))
    except (ValueError, TypeError):
        months = 12
    today  = datetime.now().date()
    from calendar import month_abbr
    first_of_this_month = today.replace(day=1)
    start_month_index = first_of_this_month.year * 12 + first_of_this_month.month - (months - 1)
    start_year = (start_month_index - 1) // 12
    start_month = (start_month_index - 1) % 12 + 1
    start_date = date_type(start_year, start_month, 1)

    sale_cost_subq = db.session.query(
        SaleItem.sale_id.label('sale_id'),
        func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0).label('cost')
    ).join(Product, SaleItem.product_id == Product.id)\
     .group_by(SaleItem.sale_id)\
     .subquery()

    sale_year = extract('year', Sale.sale_date)
    sale_month = extract('month', Sale.sale_date)
    rows = db.session.query(
        sale_year.label('year'),
        sale_month.label('month'),
        func.coalesce(func.sum(Sale.total_amount), 0).label('revenue'),
        func.coalesce(func.sum(sale_cost_subq.c.cost), 0).label('cost'),
        func.count(Sale.id).label('orders')
    ).outerjoin(sale_cost_subq, sale_cost_subq.c.sale_id == Sale.id)\
     .filter(
         func.date(Sale.sale_date).between(start_date, today),
         Sale.status == 'completed'
     )\
     .group_by(sale_year, sale_month)\
     .all()

    row_map = {
        (int(r.year), int(r.month)): {
            'revenue': float(r.revenue or 0),
            'cost': float(r.cost or 0),
            'orders': int(r.orders or 0),
        }
        for r in rows
    }

    data = []
    for i in range(months - 1, -1, -1):
        month = (today.month - i - 1) % 12 + 1
        year  = today.year + ((today.month - i - 1) // 12)
        values = row_map.get((year, month), {'revenue': 0.0, 'cost': 0.0, 'orders': 0})
        data.append({
            'month': month_abbr[month], 'year': year,
            'label': f"{month_abbr[month]} {year}",
            'revenue': values['revenue'], 'cost': values['cost'],
            'profit': values['revenue'] - values['cost'], 'orders': values['orders'],
        })
    return jsonify(data)

@app.route('/api/profit/by-product')
@login_required
def api_profit_by_product():
    require_roles('Admin', 'Operator', 'Manager')
    from_d = request.args.get('from', '')
    to_d   = request.args.get('to', '')
    today  = datetime.now().date()

    if from_d and to_d:
        try:
            start = datetime.strptime(from_d, '%Y-%m-%d').date()
            end   = datetime.strptime(to_d,   '%Y-%m-%d').date()
        except ValueError:
            start = today.replace(day=1); end = today
    else:
        start = today.replace(day=1); end = today

    rows = db.session.query(
        Product.id,
        Product.name,
        func.coalesce(func.sum(SaleItem.quantity), 0).label('qty_sold'),
        func.coalesce(func.sum(SaleItem.total), 0).label('revenue'),
        func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0).label('cost'),
    ).join(SaleItem, SaleItem.product_id == Product.id)\
     .join(Sale, Sale.id == SaleItem.sale_id)\
     .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')\
     .group_by(Product.id, Product.name)\
     .order_by(func.coalesce(func.sum(SaleItem.total) - func.sum(SaleItem.quantity * Product.buy_price), 0).desc())\
     .limit(50).all()

    return jsonify([{
        'product_id': r.id, 'name': r.name,
        'qty_sold': float(r.qty_sold), 'revenue': float(r.revenue),
        'cost': float(r.cost), 'profit': float(r.revenue) - float(r.cost),
        'margin': round((float(r.revenue) - float(r.cost)) / float(r.revenue) * 100, 1) if r.revenue else 0,
    } for r in rows])

# ── STOCK ADJUSTMENT API ──────────────────────────────────────────────────────
@app.route('/api/stock/adjust', methods=['POST'])
@login_required
def api_stock_adjust():
    require_roles('Admin', 'Operator', 'Manager')
    d = request.get_json(silent=True) or {}
    note = str(d.get('note', '') or '').strip()

    try:
        pid = int(d.get('product_id') or 0)
        qty_value = d.get('qty', 0)
        parsed_qty = int(float(qty_value))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Please enter a valid numeric quantity.'}), 400

    if pid <= 0:
        return jsonify({'ok': False, 'msg': 'Select a valid product before adjusting stock.'}), 400

    p = db.session.get(Product, pid)
    if not p:
        return jsonify({'ok': False, 'msg': 'Selected product was not found.'}), 404

    if d.get('absolute'):
        if parsed_qty < 0:
            return jsonify({'ok': False, 'msg': 'Stock level cannot be negative.'}), 400
        new_qty = parsed_qty
        qty_delta = new_qty - int(p.stock_qty)
        p.stock_qty = new_qty
    else:
        qty_delta = parsed_qty
        no_neg = StoreSettings.get('noNegStock', '0') == '1'
        if no_neg and p.stock_qty + qty_delta < 0:
            return jsonify({'ok': False, 'msg': 'Adjustment would result in negative stock'}), 400
        p.stock_qty += qty_delta

    db.session.add(StockMovement(product_id=p.id, movement_type='adjustment', quantity=qty_delta, note=note))
    db.session.commit()
    _invalidate_low_stock_cache()
    log_action(f'Stock adjusted {p.name}: {qty_delta:+d} → {p.stock_qty}')
    return jsonify({'ok': True, 'new_qty': p.stock_qty})

# ── API: PROFIT MARGIN DISTRIBUTION ─────────────────────────────────────────
@app.route('/api/dashboard/profit-margins')
@login_required
def api_profit_margin_distribution():
    """Return product profit margin distribution for dashboard doughnut chart."""
    try:
        products = Product.query.filter_by(status='active').all()
        low = medium = high = 0
        for p in products:
            if p.sell_price and p.sell_price > 0:
                margin = (p.sell_price - (p.buy_price or 0)) / p.sell_price * 100
                if margin < 10:
                    low += 1
                elif margin < 30:
                    medium += 1
                else:
                    high += 1
        return jsonify({
            'ok': True,
            'labels': ['Low Margin (0–10%)', 'Medium Margin (10–30%)', 'High Margin (30%+)'],
            'data':   [low, medium, high],
            'total':  len(products),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

RESET_ALL_DATA_EXCLUDED_TABLES = frozenset({
    User.__table__.name,
    PasswordReset.__table__.name,
    StoreSettings.__table__.name,
})


def _reset_all_data_tables():
    # Build the reset scope from metadata so future models/tables are included
    # automatically. This prevents FK failures when new business tables are
    # added but not manually listed here.
    reset_table_names = [
        table.name
        for table in db.metadata.sorted_tables
        if table.name not in RESET_ALL_DATA_EXCLUDED_TABLES
    ]
    return ordered_delete_tables(
        db.metadata,
        reset_table_names,
    )


def _factory_reset_keep_license_tables():
    return ordered_delete_tables(
        db.metadata,
        [table.name for table in db.metadata.sorted_tables],
    )


def _perform_reset_all_data(session):
    return delete_all_rows(session, _reset_all_data_tables())


def _perform_factory_reset_keep_license(session):
    deleted_rows = delete_all_rows(session, _factory_reset_keep_license_tables())
    session.add(StoreSettings(key=INITIAL_SECURITY_SETUP_KEY, value='1'))
    session.add(StoreSettings(key=PRIMARY_ADMIN_USER_ID_KEY, value=''))
    return deleted_rows


def _perform_full_factory_reset(session):
    deleted_rows = delete_all_rows(
        session,
        _factory_reset_keep_license_tables(),
        disable_sqlite_foreign_keys=True,
    )
    _delete_license_artifacts()
    session.add(StoreSettings(key=INITIAL_SECURITY_SETUP_KEY, value='1'))
    session.add(StoreSettings(key=PRIMARY_ADMIN_USER_ID_KEY, value=''))
    return deleted_rows


# ── API: ADMIN — RESET BUSINESS DATA ──────────────────────────────────────────
@app.route('/api/admin/reset-business-data', methods=['POST'])
@app.route('/api/admin/reset-all', methods=['POST'])  # Backward-compatible alias.
@login_required
def api_reset_business_data():
    """Delete resettable business data using FK-safe child-first ordering."""
    require_roles('Admin', 'Operator')

    app.logger.warning(
        'Business data reset requested path=%s user_id=%s mode=%s',
        request.path,
        current_user.id,
        factory_reset_mode(),
    )
    from backup import _do_backup_sync
    pre_reset_backup = _do_backup_sync(backup_type='pre-reset', reason='before_reset_business_data')
    if not pre_reset_backup.get('ok'):
        return jsonify({
            'ok': False,
            'error': 'Could not create a safety backup before reset. Please try again.',
        }), 500
    try:
        with db.session.begin_nested():
            deleted_rows = reset_business_data(db.session)
        db.session.commit()
        _clear_session_state_for_reset(session_scope='current')
    except Exception as exc:
        db.session.rollback()
        app.logger.exception('Business data reset failed path=%s user_id=%s', request.path, current_user.id)
        return jsonify({
            'ok': False,
            'error': 'Business data reset failed. No changes were committed.',
            'details': str(exc) or exc.__class__.__name__,
        }), 500

    log_action(
        'Business data reset completed',
        target_type='admin_destructive_action',
        metadata={
            'action': 'reset_business_data',
            'tables': [entry['table'] for entry in deleted_rows],
            'factory_reset_mode': factory_reset_mode(),
        },
    )
    return jsonify({
        'ok': True,
        'msg': 'Reset All Data completed successfully. License, users, and settings were preserved.',
        'deleted_tables': deleted_rows,
        'safety_backup': pre_reset_backup.get('name'),
        'activation_preserved': True,
    })


# ── API: ADMIN — FACTORY RESET (full re-install) ──────────────────────────────
@app.route('/api/admin/factory-reset', methods=['POST'])
@login_required
def api_factory_reset():
    """Factory reset that keeps activation/license artifacts intact."""
    if not is_admin_role(current_user.role):
        abort(403)

    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'FACTORY_RESET':
        return jsonify({'ok': False, 'msg': 'Confirmation string "FACTORY_RESET" is required.'}), 400

    mode = 'factory_keep_license'
    app.logger.warning(
        'Factory reset requested user_id=%s ip=%s mode=%s',
        current_user.id,
        request.remote_addr,
        mode,
    )
    from backup import _do_backup_sync
    pre_reset_backup = _do_backup_sync(backup_type='pre-reset', reason='before_factory_reset')
    if not pre_reset_backup.get('ok'):
        return jsonify({
            'ok': False,
            'error': 'Could not create a safety backup before factory reset. Please try again.',
        }), 500

    try:
        with db.session.begin_nested():
            deleted_rows = _perform_factory_reset_keep_license(db.session)
        db.session.commit()
        _clear_session_state_for_reset(session_scope='all')
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception('Factory reset failed ip=%s', request.remote_addr)
        return jsonify({
            'ok': False,
            'error': 'Factory reset failed. No changes were committed.',
            'details': str(exc) or exc.__class__.__name__,
        }), 500

    success_msg = 'Factory reset complete. Activation/license was preserved. Complete first-run setup to continue.'
    redirect_url = '/setup-admin'

    log_action(
        'Factory reset completed',
        target_type='admin_destructive_action',
        metadata={
            'action': 'factory_reset',
            'mode': mode,
            'activation_preserved': True,
            'tables': [entry['table'] for entry in deleted_rows],
        },
    )
    return jsonify({
        'ok': True,
        'msg': success_msg,
        'mode': mode,
        'safety_backup': pre_reset_backup.get('name'),
        'activation_preserved': True,
        'redirect': redirect_url,
    })


@app.route('/api/admin/full-factory-reset', methods=['POST'])
@login_required
def api_full_factory_reset():
    if not can_access_full_factory_reset(current_user) or not destructive_admin_tools_enabled():
        abort(403)

    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'FULL_FACTORY_RESET':
        return jsonify({'ok': False, 'msg': 'Confirmation string "FULL_FACTORY_RESET" is required.'}), 400

    app.logger.warning(
        'Full factory reset requested user_id=%s ip=%s role=%s',
        current_user.id,
        request.remote_addr,
        current_user.role,
    )
    try:
        with db.session.begin_nested():
            deleted_rows = _perform_full_factory_reset(db.session)
        db.session.commit()
        _clear_session_state_for_reset(session_scope='all')
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception('Full factory reset failed ip=%s', request.remote_addr)
        return jsonify({
            'ok': False,
            'error': 'Full factory reset failed. No changes were committed.',
            'details': str(exc) or exc.__class__.__name__,
        }), 500

    log_action(
        'Full factory reset completed',
        target_type='admin_destructive_action',
        metadata={
            'action': 'full_factory_reset',
            'mode': 'developer_only',
            'activation_preserved': False,
            'tables': [entry['table'] for entry in deleted_rows],
        },
    )
    return jsonify({
        'ok': True,
        'msg': 'Full factory reset complete. License data was deleted. Restarting into fresh-install state.',
        'mode': 'full_factory_reset',
        'activation_preserved': False,
        'redirect': '/activation',
    })


# ── PRODUCT BARCODE MANAGEMENT ────────────────────────────────────────────────

@app.route('/api/products/<int:pid>/barcodes', methods=['GET', 'POST'])
@login_required
def api_product_barcodes(pid):
    require_roles('Admin', 'Operator', 'Manager')
    if request.method == 'POST':
        d = request.get_json()
        bc = d.get('barcode', '').strip()
        if not bc:
            return jsonify({'error': 'Barcode required'}), 400
        if ProductBarcode.query.filter_by(barcode=bc).first():
            return jsonify({'error': 'Barcode already exists'}), 409
        pb = ProductBarcode(
            product_id=pid,
            barcode=bc,
            barcode_type=d.get('barcode_type', 'normal'),
            is_primary=int(d.get('is_primary', 0)))
        db.session.add(pb)
        db.session.commit()
        return jsonify(pb.to_dict()), 201
    barcodes = ProductBarcode.query.filter_by(product_id=pid).all()
    return jsonify([b.to_dict() for b in barcodes])

@app.route('/api/products/barcodes/<int:bid>', methods=['DELETE'])
@login_required
def api_delete_product_barcode(bid):
    require_roles('Admin', 'Operator', 'Manager')
    pb = ProductBarcode.query.get_or_404(bid)
    db.session.delete(pb)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>/generate-barcode')
@login_required
def api_generate_barcode(pid):
    require_roles('Admin', 'Operator', 'Manager')
    p = db.session.get(Product, pid)
    if not p:
        abort(404)
    # Try up to BARCODE_EAN13_ATTEMPTS times to get a unique barcode
    for _ in range(BARCODE_EAN13_ATTEMPTS):
        candidate = gen_ean13(pid)
        if not Product.query.filter_by(barcode=candidate).first() and \
           not ProductBarcode.query.filter_by(barcode=candidate).first():
            return jsonify({'barcode': candidate})
    return jsonify({'error': 'Could not generate unique barcode'}), 500

@app.route('/api/barcode/image')
@login_required
def api_barcode_image():
    try:
        from barcode import EAN8, EAN13, Code128, Code39
        from barcode.writer import ImageWriter
    except Exception:
        return jsonify({'error': 'python-barcode dependency is not installed'}), 500

    value = (request.args.get('value') or '').strip()
    barcode_type = (request.args.get('type') or 'code128').strip().lower()
    is_valid, msg = validate_barcode_by_type(value, barcode_type)
    if not is_valid:
        return jsonify({'error': msg}), 400

    klass_map = {'ean13': EAN13, 'ean8': EAN8, 'code128': Code128, 'code39': Code39}
    barcode_cls = klass_map.get(barcode_type, Code128)
    try:
        barcode_obj = barcode_cls(value, writer=ImageWriter())
        out = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        try:
            base = out.name[:-4]
            barcode_obj.save(base, options={'module_height': 12.0, 'quiet_zone': 2.5, 'write_text': True})
            with open(base + '.png', 'rb') as fh:
                data = fh.read()
        finally:
            out.close()
            for suffix in ['', '.png']:
                try:
                    os.remove(out.name[:-4] + suffix)
                except Exception:
                    pass
        return Response(data, mimetype='image/png')
    except Exception as exc:
        return jsonify({'error': f'Failed to generate barcode image: {exc}'}), 500

@app.route('/api/barcode/save', methods=['POST'])
@login_required
def api_barcode_save():
    require_roles('Admin', 'Operator', 'Manager')
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    barcode_value = (data.get('barcode') or '').strip()
    barcode_type = (data.get('barcode_type') or 'code128').strip().lower()
    if not product_id:
        return jsonify({'ok': False, 'error': 'Please select a product'}), 400
    product = db.session.get(Product, int(product_id))
    if not product or product.status != 'active':
        return jsonify({'ok': False, 'error': 'Product not found'}), 404
    valid, error_msg = validate_barcode_by_type(barcode_value, barcode_type)
    if not valid:
        return _printing_error(error_msg, code='INVALID_BARCODE', status=400)
    if barcode_in_use(barcode_value, exclude_product_id=product.id):
        return jsonify({'ok': False, 'error': f'Barcode "{barcode_value}" already exists'}), 409
    product.barcode = barcode_value
    product.barcode_type = barcode_type or product.barcode_type
    db.session.commit()
    log_action('Barcode saved to product', target_type='product', target_id=product.id, metadata={'barcode': barcode_value, 'barcode_type': barcode_type})
    return jsonify({'ok': True, 'msg': f'Barcode saved to {product.name}', 'product_id': product.id, 'barcode': barcode_value})

@app.route('/api/barcode/generate', methods=['POST'])
@login_required
def api_barcode_generate():
    require_roles('Admin', 'Operator', 'Manager')
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'ok': False, 'error': 'Product is required'}), 400
    product = db.session.get(Product, int(product_id))
    if not product or product.status != 'active':
        return jsonify({'ok': False, 'error': 'Product not found'}), 404
    mode = data.get('mode') or 'random'
    prefix = data.get('prefix') or 'SM'
    candidate = generate_barcode_for_product(product, mode=mode, prefix=prefix)
    if not candidate:
        return jsonify({'ok': False, 'error': 'Could not generate a unique barcode'}), 500
    return jsonify({'ok': True, 'barcode': candidate, 'mode': mode})

@app.route('/api/barcode/generate-missing', methods=['POST'])
@login_required
def api_barcode_generate_missing():
    require_roles('Admin', 'Operator', 'Manager')
    data = request.get_json(silent=True) or {}
    mode = data.get('mode') or 'random'
    prefix = data.get('prefix') or 'SM'
    overwrite_existing = str(data.get('overwrite', 'false')).strip().lower() == 'true'
    products = Product.query.filter(Product.status == 'active').order_by(Product.id.asc()).all()
    updated = 0
    skipped = 0
    for product in products:
        if (product.barcode or '').strip() and not overwrite_existing:
            skipped += 1
            continue
        candidate = generate_barcode_for_product(product, mode=mode, prefix=prefix)
        if not candidate:
            skipped += 1
            continue
        product.barcode = candidate
        updated += 1
    db.session.commit()
    return jsonify({'ok': True, 'updated': updated, 'skipped': skipped, 'overwrite': overwrite_existing})

# ── BOOTSTRAP DIAGNOSTICS ─────────────────────────────────────────────────────
def _print_startup_user_summary():
    """Print a concise bootstrap-user status table on every startup.

    Helps operators and developers quickly verify whether default credentials
    are present and whether the first-run flags are in the expected state.
    """
    try:
        db_path = app.config.get('DATABASE_FILE', '<unset>')
        print(f'  [STARTUP] Active SQLite DB file: {db_path}')
        total = User.query.count()
        print(f'  [STARTUP] Users in DB: {total}')
        admin_user = User.query.filter(func.lower(User.role) == 'admin').first()
        print(f'  [STARTUP] Admin user exists: {bool(admin_user)}')
        first_run = is_first_run_admin_setup_required()
        sec_pending = is_initial_security_setup_pending()
        print(
            f'  [STARTUP] first_run_setup_required={first_run}  '
            f'security_first_run_pending={sec_pending}'
        )
    except Exception as diag_err:
        print(f'  [STARTUP] Could not print user summary: {diag_err}')


def ensure_admin_user_exists():
    users = User.query.order_by(User.id.asc()).all()
    if not users:
        # When first-run setup is pending (fresh install or after factory reset),
        # do NOT create a bootstrap admin.  The operator must go through the
        # /setup-admin page so that the first real account becomes the primary
        # owner/admin with a proper password — never an auto-generated one.
        if is_initial_security_setup_pending():
            app.logger.info(
                '[RBAC] First-run setup pending — skipping bootstrap admin creation. '
                'User must complete /setup-admin to create the initial admin account.'
            )
            return None
        # Safety fallback only: if the database has no users AND the first-run
        # flag is somehow absent/disabled, create a temporary bootstrap admin so
        # the app stays operational.  The account is marked force_password_change
        # so the operator is forced to set a real password on first login.
        bootstrap_admin = User(
            username='admin',
            full_name='System Administrator',
            role='Admin',
            email='',
            force_password_change=True,
            is_admin=True,
            is_owner=True,
            is_primary_admin=True,
            status='active',
        )
        bootstrap_admin.set_password('admin123')
        db.session.add(bootstrap_admin)
        db.session.flush()
        assign_primary_admin(bootstrap_admin)
        db.session.commit()
        app.logger.warning('[RBAC] No users found. Created default admin username=admin.')
        return bootstrap_admin

    changed = False
    for user in users:
        normalized_role = normalize_role(user.role)
        if user.role != normalized_role:
            user.role = normalized_role
            changed = True
        expected_admin = is_admin(user)
        if bool(user.is_admin) != expected_admin:
            user.is_admin = expected_admin
            changed = True

    admin_user = next((u for u in users if is_admin(u)), None)
    if not admin_user and users:
        admin_user = users[0]
        assign_primary_admin(admin_user)
        changed = True
        app.logger.warning(
            '[RBAC] No Admin role found. Promoted first user username=%s id=%s to Admin.',
            admin_user.username,
            admin_user.id,
        )

    if changed:
        db.session.commit()
    return admin_user


def seed_database():
    """Seed only production-safe baseline settings.

    Intentionally does NOT create any sample/demo categories, products, or stock.
    """
    for k, v in StoreSettings.default_seed_values().items():
        if not StoreSettings.query.filter_by(key=k).first():
            db.session.add(StoreSettings(key=k, value=v))
    if not StoreSettings.query.filter_by(key=INITIAL_SECURITY_SETUP_KEY).first():
        pending_value = '1' if is_first_run_admin_setup_required() else '0'
        db.session.add(StoreSettings(key=INITIAL_SECURITY_SETUP_KEY, value=pending_value))
        app.logger.info('[SEED] Initialised %s=%s', INITIAL_SECURITY_SETUP_KEY, pending_value)
    if not StoreSettings.query.filter_by(key=PRIMARY_ADMIN_USER_ID_KEY).first():
        primary_admin = User.query.filter_by(is_primary_admin=True).first()
        db.session.add(StoreSettings(
            key=PRIMARY_ADMIN_USER_ID_KEY,
            value=str(primary_admin.id) if primary_admin else '',
        ))
        app.logger.info('[SEED] Initialised %s', PRIMARY_ADMIN_USER_ID_KEY)
    db.session.commit()
    print("  [BOOTSTRAP] Production baseline settings ready.")

    default_vehicle_brands = {
        'car': ['Toyota', 'Suzuki', 'Nissan', 'Honda', 'Mitsubishi', 'Mazda', 'Hyundai', 'Kia', 'BYD', 'MG', 'Chery', 'Volkswagen', 'BMW', 'Audi', 'Mercedes-Benz'],
        'bike': ['Bajaj', 'TVS', 'Hero', 'Yamaha', 'Honda', 'Kawasaki', 'KTM', 'Demak'],
        'three_wheeler': ['Bajaj', 'TVS', 'Piaggio'],
        'van': ['Toyota', 'Nissan', 'Suzuki', 'Mitsubishi', 'DFSK', 'JMC'],
        'lorry': ['Tata', 'Ashok Leyland', 'Isuzu', 'Fuso', 'Hino', 'Mahindra'],
        'bus': ['Tata', 'Ashok Leyland', 'Isuzu', 'Fuso', 'Hino'],
        'suv': ['Toyota', 'Nissan', 'Mitsubishi', 'Mahindra', 'Land Rover', 'Jeep', 'Kia', 'Hyundai', 'Micro'],
        'tractor': ['Mahindra', 'Sonalika', 'Massey Ferguson', 'New Holland', 'Kubota'],
        'other': ['Isuzu'],
    }
    seeded = 0
    for vehicle_type, names in default_vehicle_brands.items():
        for name in names:
            clean = re.sub(r'\s+', ' ', str(name or '').strip())
            normalized = re.sub(r'[^a-z0-9]+', '', clean.lower())
            if not clean or not normalized:
                continue
            existing = VehicleBrand.query.filter_by(normalized_name=normalized, vehicle_type=vehicle_type).first()
            if existing:
                continue
            db.session.add(VehicleBrand(
                name=clean,
                normalized_name=normalized,
                vehicle_type=vehicle_type,
                is_custom=False,
                is_active=True,
                usage_count=0,
            ))
            seeded += 1
    if seeded:
        db.session.commit()
        app.logger.info('[SEED] Added %s default vehicle brands.', seeded)

# ── AUTO-MIGRATION ────────────────────────────────────────────────────────────
def auto_migrate():
    def _sqlite_table_info(table_name):
        rows = db.session.execute(text(f'PRAGMA table_info("{table_name}")')).mappings().all()
        return [
            {
                'name': row.get('name'),
                'type': (row.get('type') or ''),
            }
            for row in rows
        ]

    migrations = [
        ('products',             'price_per_kg',         'ALTER TABLE products ADD COLUMN price_per_kg FLOAT DEFAULT 0'),
        ('products',             'barcode_type',         "ALTER TABLE products ADD COLUMN barcode_type VARCHAR(10) DEFAULT 'normal'"),
        ('products',             'wholesale_price',      'ALTER TABLE products ADD COLUMN wholesale_price FLOAT DEFAULT 0'),
        ('products',             'sku',                  'ALTER TABLE products ADD COLUMN sku VARCHAR(60)'),
        ('products',             'rack_number',          "ALTER TABLE products ADD COLUMN rack_number VARCHAR(20) DEFAULT ''"),
        ('products',             'section_number',       "ALTER TABLE products ADD COLUMN section_number VARCHAR(20) DEFAULT ''"),
        ('users',                'email',                'ALTER TABLE users ADD COLUMN email VARCHAR(150)'),
        ('sales',                'wholesale_customer_id','ALTER TABLE sales ADD COLUMN wholesale_customer_id INT NULL'),
        ('sales',                'discount_percent',     'ALTER TABLE sales ADD COLUMN discount_percent FLOAT DEFAULT 0'),
        ('sales',                'subtotal',             'ALTER TABLE sales ADD COLUMN subtotal FLOAT DEFAULT 0'),
        ('sale_items',           'discount',             'ALTER TABLE sale_items ADD COLUMN discount FLOAT DEFAULT 0'),
        ('suppliers',            'credit_limit',         'ALTER TABLE suppliers ADD COLUMN credit_limit FLOAT DEFAULT 0'),
        ('suppliers',            'balance',              'ALTER TABLE suppliers ADD COLUMN balance FLOAT DEFAULT 0'),
        ('suppliers',            'status',               "ALTER TABLE suppliers ADD COLUMN status VARCHAR(10) DEFAULT 'active'"),
        ('wholesale_customers',  'business',             'ALTER TABLE wholesale_customers ADD COLUMN business VARCHAR(200)'),
        ('wholesale_customers',  'phone',                'ALTER TABLE wholesale_customers ADD COLUMN phone VARCHAR(20)'),
        ('wholesale_customers',  'email',                'ALTER TABLE wholesale_customers ADD COLUMN email VARCHAR(120)'),
        ('wholesale_customers',  'address',              'ALTER TABLE wholesale_customers ADD COLUMN address TEXT'),
        ('wholesale_customers',  'credit_limit',         'ALTER TABLE wholesale_customers ADD COLUMN credit_limit FLOAT DEFAULT 0'),
        ('wholesale_customers',  'balance',              'ALTER TABLE wholesale_customers ADD COLUMN balance FLOAT DEFAULT 0'),
        ('wholesale_customers',  'status',               "ALTER TABLE wholesale_customers ADD COLUMN status VARCHAR(10) DEFAULT 'active'"),
        ('wholesale_customers',  'created_at',           'ALTER TABLE wholesale_customers ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP'),
        # held_orders columns (table may exist from old version without these cols)
        ('stock_movements', 'note',              "ALTER TABLE stock_movements ADD COLUMN note VARCHAR(200)"),
        ('held_orders', 'label',                 "ALTER TABLE held_orders ADD COLUMN label VARCHAR(100) NULL"),
        ('held_orders', 'cart_json',             "ALTER TABLE held_orders ADD COLUMN cart_json LONGTEXT NOT NULL DEFAULT ''"),
        ('held_orders', 'cashier_id',            "ALTER TABLE held_orders ADD COLUMN cashier_id INT NULL"),
        ('held_orders', 'wholesale_customer_id', "ALTER TABLE held_orders ADD COLUMN wholesale_customer_id INT NULL"),
        ('held_orders', 'discount',              "ALTER TABLE held_orders ADD COLUMN discount FLOAT NOT NULL DEFAULT 0"),
        ('held_orders', 'discount_percent',      "ALTER TABLE held_orders ADD COLUMN discount_percent FLOAT NOT NULL DEFAULT 0"),
        ('held_orders', 'created_at',            "ALTER TABLE held_orders ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        # purchases
        ('purchases', 'grn_number',    "ALTER TABLE purchases ADD COLUMN grn_number VARCHAR(40)"),
        ('purchases', 'supplier_id',   "ALTER TABLE purchases ADD COLUMN supplier_id INT NULL"),
        ('purchases', 'purchase_date', "ALTER TABLE purchases ADD COLUMN purchase_date DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ('purchases', 'total_amount',  "ALTER TABLE purchases ADD COLUMN total_amount FLOAT DEFAULT 0"),
        ('purchases', 'notes',         "ALTER TABLE purchases ADD COLUMN notes TEXT"),
        ('purchases', 'status',        "ALTER TABLE purchases ADD COLUMN status VARCHAR(20) DEFAULT 'received'"),
        ('purchases', 'created_by',    "ALTER TABLE purchases ADD COLUMN created_by INT NULL"),
        # purchase_items
        ('purchase_items', 'purchase_id', "ALTER TABLE purchase_items ADD COLUMN purchase_id INT NULL"),
        ('purchase_items', 'product_id',  "ALTER TABLE purchase_items ADD COLUMN product_id INT NULL"),
        ('purchase_items', 'quantity',    "ALTER TABLE purchase_items ADD COLUMN quantity FLOAT NOT NULL DEFAULT 0"),
        ('purchase_items', 'unit_cost',   "ALTER TABLE purchase_items ADD COLUMN unit_cost FLOAT NOT NULL DEFAULT 0"),
        ('purchase_items', 'total',       "ALTER TABLE purchase_items ADD COLUMN total FLOAT NOT NULL DEFAULT 0"),
        # product_returns
        ('product_returns', 'return_number', "ALTER TABLE product_returns ADD COLUMN return_number VARCHAR(40)"),
        ('product_returns', 'sale_id',       "ALTER TABLE product_returns ADD COLUMN sale_id INT NULL"),
        ('product_returns', 'return_date',   "ALTER TABLE product_returns ADD COLUMN return_date DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ('product_returns', 'total_amount',  "ALTER TABLE product_returns ADD COLUMN total_amount FLOAT DEFAULT 0"),
        ('product_returns', 'reason',        "ALTER TABLE product_returns ADD COLUMN reason VARCHAR(200)"),
        ('product_returns', 'status',        "ALTER TABLE product_returns ADD COLUMN status VARCHAR(20) DEFAULT 'completed'"),
        ('product_returns', 'processed_by',  "ALTER TABLE product_returns ADD COLUMN processed_by INT NULL"),
        # return_items
        ('return_items', 'return_id',  "ALTER TABLE return_items ADD COLUMN return_id INT NULL"),
        ('return_items', 'product_id', "ALTER TABLE return_items ADD COLUMN product_id INT NULL"),
        ('return_items', 'quantity',   "ALTER TABLE return_items ADD COLUMN quantity FLOAT NOT NULL DEFAULT 0"),
        ('return_items', 'price',      "ALTER TABLE return_items ADD COLUMN price FLOAT NOT NULL DEFAULT 0"),
        ('return_items', 'total',      "ALTER TABLE return_items ADD COLUMN total FLOAT NOT NULL DEFAULT 0"),
        # v3.1 — business logic improvements
        ('sales',               'tendered',          "ALTER TABLE sales ADD COLUMN tendered FLOAT DEFAULT 0"),
        ('sales',               'change_amount',     "ALTER TABLE sales ADD COLUMN change_amount FLOAT DEFAULT 0"),
        ('wholesale_customers', 'on_hold',           "ALTER TABLE wholesale_customers ADD COLUMN on_hold TINYINT(1) DEFAULT 0"),
        ('purchases',           'paid_amount',       "ALTER TABLE purchases ADD COLUMN paid_amount FLOAT DEFAULT 0"),
        ('purchases',           'payment_status',    "ALTER TABLE purchases ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'"),
        ('product_returns',     'return_type',       "ALTER TABLE product_returns ADD COLUMN return_type VARCHAR(20) DEFAULT 'refund'"),
        ('product_returns',     'restock',           "ALTER TABLE product_returns ADD COLUMN restock TINYINT(1) DEFAULT 1"),
        ('product_returns',     'refund_amount',     "ALTER TABLE product_returns ADD COLUMN refund_amount FLOAT DEFAULT 0"),
        ('product_returns',     'exchange_invoice',  "ALTER TABLE product_returns ADD COLUMN exchange_invoice VARCHAR(40)"),
        # v4.1 — force_password_change + store_credit columns
        ('users', 'force_password_change', "ALTER TABLE users ADD COLUMN force_password_change TINYINT(1) DEFAULT 0"),
        ('user_logs', 'target_type', "ALTER TABLE user_logs ADD COLUMN target_type VARCHAR(80)"),
        ('user_logs', 'target_id', "ALTER TABLE user_logs ADD COLUMN target_id INT NULL"),
        ('user_logs', 'metadata_summary', "ALTER TABLE user_logs ADD COLUMN metadata_summary VARCHAR(255)"),
        # v4.0 — backup_logs table columns (table created by db.create_all)
        ('backup_logs', 'backup_date',    "ALTER TABLE backup_logs ADD COLUMN backup_date DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ('backup_logs', 'backup_file',    "ALTER TABLE backup_logs ADD COLUMN backup_file VARCHAR(200)"),
        ('backup_logs', 'file_size',      "ALTER TABLE backup_logs ADD COLUMN file_size VARCHAR(50)"),
        ('backup_logs', 'status',         "ALTER TABLE backup_logs ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"),
        ('backup_logs', 'destination',    "ALTER TABLE backup_logs ADD COLUMN destination VARCHAR(50) DEFAULT 'local'"),
        ('backup_logs', 'gdrive_file_id', "ALTER TABLE backup_logs ADD COLUMN gdrive_file_id VARCHAR(200)"),
        ('backup_logs', 'notes',          "ALTER TABLE backup_logs ADD COLUMN notes VARCHAR(200)"),
        # v4.2 — warranty management
        ('products',   'warranty_period',      "ALTER TABLE products ADD COLUMN warranty_period VARCHAR(50) DEFAULT 'none'"),
        ('sale_items', 'warranty_period',      "ALTER TABLE sale_items ADD COLUMN warranty_period VARCHAR(50) DEFAULT 'none'"),
        ('sale_items', 'warranty_expiry_date', "ALTER TABLE sale_items ADD COLUMN warranty_expiry_date DATE NULL"),
        # v5.0 — garage features
        ('products',   'is_imei_tracked',      "ALTER TABLE products ADD COLUMN is_imei_tracked TINYINT(1) DEFAULT 0"),
        ('products',   'product_type',         "ALTER TABLE products ADD COLUMN product_type VARCHAR(20) DEFAULT 'normal'"),
        ('products',   'brand_id',             "ALTER TABLE products ADD COLUMN brand_id INT NULL"),
        ('sale_items', 'serial_number',        "ALTER TABLE sale_items ADD COLUMN serial_number VARCHAR(80) NULL"),
        ('sales',      'retail_customer_id',   "ALTER TABLE sales ADD COLUMN retail_customer_id INT NULL"),
        ('sales',      'customer_name_snapshot', "ALTER TABLE sales ADD COLUMN customer_name_snapshot VARCHAR(150)"),
        ('sales',      'customer_phone_snapshot', "ALTER TABLE sales ADD COLUMN customer_phone_snapshot VARCHAR(20)"),
        ('payments',   'customer_id', "ALTER TABLE payments ADD COLUMN customer_id INT NULL"),
        ('repair_payments', 'customer_id', "ALTER TABLE repair_payments ADD COLUMN customer_id INT NULL"),
        ('retail_customers', 'customer_code', "ALTER TABLE retail_customers ADD COLUMN customer_code VARCHAR(30)"),
        ('retail_customers', 'phone_normalized', "ALTER TABLE retail_customers ADD COLUMN phone_normalized VARCHAR(20)"),
        ('vehicle_history', 'customer_name_snapshot', "ALTER TABLE vehicle_history ADD COLUMN customer_name_snapshot VARCHAR(150)"),
        ('vehicle_history', 'customer_phone_snapshot', "ALTER TABLE vehicle_history ADD COLUMN customer_phone_snapshot VARCHAR(20)"),
        # v6.0 — garage vehicle fields on repair_jobs
        ('repair_jobs', 'vehicle_make',        "ALTER TABLE repair_jobs ADD COLUMN vehicle_make VARCHAR(80)"),
        ('repair_jobs', 'vehicle_type',        "ALTER TABLE repair_jobs ADD COLUMN vehicle_type VARCHAR(40) DEFAULT 'other'"),
        ('repair_jobs', 'vehicle_brand_id',    "ALTER TABLE repair_jobs ADD COLUMN vehicle_brand_id INT NULL"),
        ('repair_jobs', 'vehicle_model',       "ALTER TABLE repair_jobs ADD COLUMN vehicle_model VARCHAR(80)"),
        ('repair_jobs', 'vehicle_reg_no',      "ALTER TABLE repair_jobs ADD COLUMN vehicle_reg_no VARCHAR(20)"),
        ('repair_jobs', 'vehicle_color',       "ALTER TABLE repair_jobs ADD COLUMN vehicle_color VARCHAR(40)"),
        ('repair_jobs', 'vehicle_year',        "ALTER TABLE repair_jobs ADD COLUMN vehicle_year INTEGER"),
        ('repair_jobs', 'vehicle_vin',         "ALTER TABLE repair_jobs ADD COLUMN vehicle_vin VARCHAR(20)"),
        ('repair_jobs', 'odometer_in',         "ALTER TABLE repair_jobs ADD COLUMN odometer_in INTEGER"),
        ('repair_jobs', 'odometer_out',        "ALTER TABLE repair_jobs ADD COLUMN odometer_out INTEGER"),
        ('repair_jobs', 'fuel_level_in',       "ALTER TABLE repair_jobs ADD COLUMN fuel_level_in VARCHAR(10)"),
        ('repair_jobs', 'service_type',        "ALTER TABLE repair_jobs ADD COLUMN service_type VARCHAR(40)"),
        ('repair_jobs', 'customer_name_snapshot', "ALTER TABLE repair_jobs ADD COLUMN customer_name_snapshot VARCHAR(150)"),
        ('repair_jobs', 'customer_phone_snapshot', "ALTER TABLE repair_jobs ADD COLUMN customer_phone_snapshot VARCHAR(20)"),
        # v5.1 — password reset OTP hardening
        ('password_resets', 'otp_hash',        "ALTER TABLE password_resets ADD COLUMN otp_hash VARCHAR(64)"),
        # v5.2 — DB-backed login session invalidation
        ('users', 'session_token',             "ALTER TABLE users ADD COLUMN session_token VARCHAR(64) NULL"),
        # v5.4 — owner admin flags
        ('users', 'is_admin',                  "ALTER TABLE users ADD COLUMN is_admin TINYINT(1) DEFAULT 0"),
        ('users', 'is_owner',                  "ALTER TABLE users ADD COLUMN is_owner TINYINT(1) DEFAULT 0"),
        ('users', 'is_primary_admin',          "ALTER TABLE users ADD COLUMN is_primary_admin TINYINT(1) DEFAULT 0"),
        # v5.3 — repair jobs schema hardening for upgraded databases
        ('repair_jobs', 'job_number',           "ALTER TABLE repair_jobs ADD COLUMN job_number VARCHAR(40)"),
        ('repair_jobs', 'customer_id',          "ALTER TABLE repair_jobs ADD COLUMN customer_id INT NULL"),
        ('repair_jobs', 'customer_name',        "ALTER TABLE repair_jobs ADD COLUMN customer_name VARCHAR(150)"),
        ('repair_jobs', 'customer_phone',       "ALTER TABLE repair_jobs ADD COLUMN customer_phone VARCHAR(20)"),
        ('repair_jobs', 'issue_reported',       "ALTER TABLE repair_jobs ADD COLUMN issue_reported TEXT"),
        ('repair_jobs', 'diagnosis',            "ALTER TABLE repair_jobs ADD COLUMN diagnosis TEXT"),
        ('repair_jobs', 'status',               "ALTER TABLE repair_jobs ADD COLUMN status VARCHAR(20) DEFAULT 'received'"),
        ('repair_jobs', 'received_date',        "ALTER TABLE repair_jobs ADD COLUMN received_date DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ('repair_jobs', 'promised_date',        "ALTER TABLE repair_jobs ADD COLUMN promised_date DATETIME NULL"),
        ('repair_jobs', 'completed_date',       "ALTER TABLE repair_jobs ADD COLUMN completed_date DATETIME NULL"),
        ('repair_jobs', 'labour_charge',        "ALTER TABLE repair_jobs ADD COLUMN labour_charge FLOAT DEFAULT 0"),
        ('repair_jobs', 'total_amount',         "ALTER TABLE repair_jobs ADD COLUMN total_amount FLOAT DEFAULT 0"),
        ('repair_jobs', 'advance_paid',         "ALTER TABLE repair_jobs ADD COLUMN advance_paid FLOAT DEFAULT 0"),
        ('repair_jobs', 'payment_status',       "ALTER TABLE repair_jobs ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'"),
        ('repair_jobs', 'technician_id',        "ALTER TABLE repair_jobs ADD COLUMN technician_id INT NULL"),
        ('repair_jobs', 'created_by',           "ALTER TABLE repair_jobs ADD COLUMN created_by INT NULL"),
        ('repair_jobs', 'notes',                "ALTER TABLE repair_jobs ADD COLUMN notes TEXT"),
        ('repair_job_parts', 'job_id',          "ALTER TABLE repair_job_parts ADD COLUMN job_id INT NULL"),
        ('repair_job_parts', 'product_id',      "ALTER TABLE repair_job_parts ADD COLUMN product_id INT NULL"),
        ('repair_job_parts', 'part_name',       "ALTER TABLE repair_job_parts ADD COLUMN part_name VARCHAR(150)"),
        ('repair_job_parts', 'quantity',        "ALTER TABLE repair_job_parts ADD COLUMN quantity FLOAT DEFAULT 1"),
        ('repair_job_parts', 'unit_cost',       "ALTER TABLE repair_job_parts ADD COLUMN unit_cost FLOAT DEFAULT 0"),
        ('repair_job_parts', 'sell_price',      "ALTER TABLE repair_job_parts ADD COLUMN sell_price FLOAT DEFAULT 0"),
        ('repair_job_parts', 'total',           "ALTER TABLE repair_job_parts ADD COLUMN total FLOAT DEFAULT 0"),
        # v5.4 — card terminal transaction metadata
        ('payments', 'terminal_type',           "ALTER TABLE payments ADD COLUMN terminal_type VARCHAR(40)"),
        ('payments', 'provider_name',           "ALTER TABLE payments ADD COLUMN provider_name VARCHAR(120)"),
        ('payments', 'terminal_id',             "ALTER TABLE payments ADD COLUMN terminal_id VARCHAR(80)"),
        ('payments', 'merchant_id',             "ALTER TABLE payments ADD COLUMN merchant_id VARCHAR(80)"),
        ('payments', 'card_type',               "ALTER TABLE payments ADD COLUMN card_type VARCHAR(40)"),
        ('payments', 'card_last4',              "ALTER TABLE payments ADD COLUMN card_last4 VARCHAR(4)"),
        ('payments', 'approval_code',           "ALTER TABLE payments ADD COLUMN approval_code VARCHAR(80)"),
        ('payments', 'rrn_reference',           "ALTER TABLE payments ADD COLUMN rrn_reference VARCHAR(120)"),
        ('payments', 'terminal_status',         "ALTER TABLE payments ADD COLUMN terminal_status VARCHAR(30)"),
        ('payments', 'terminal_timestamp',      "ALTER TABLE payments ADD COLUMN terminal_timestamp DATETIME NULL"),
        ('payments', 'client_txn_key',          "ALTER TABLE payments ADD COLUMN client_txn_key VARCHAR(120)"),
        ('payments', 'terminal_note',           "ALTER TABLE payments ADD COLUMN terminal_note VARCHAR(200)"),
        # v5.5 — installment retail finance module
        ('installment_plans', 'agreement_no',   "ALTER TABLE installment_plans ADD COLUMN agreement_no VARCHAR(40)"),
        ('installment_plans', 'invoice_number', "ALTER TABLE installment_plans ADD COLUMN invoice_number VARCHAR(40)"),
        ('installment_plans', 'customer_address',"ALTER TABLE installment_plans ADD COLUMN customer_address TEXT"),
        ('installment_plans', 'product_name',   "ALTER TABLE installment_plans ADD COLUMN product_name VARCHAR(180)"),
        ('installment_plans', 'product_details',"ALTER TABLE installment_plans ADD COLUMN product_details TEXT"),
        ('installment_plans', 'device_imei',    "ALTER TABLE installment_plans ADD COLUMN device_imei VARCHAR(20)"),
        ('installment_plans', 'device_serial',  "ALTER TABLE installment_plans ADD COLUMN device_serial VARCHAR(80)"),
        ('installment_plans', 'cash_price',     "ALTER TABLE installment_plans ADD COLUMN cash_price NUMERIC(14,2) DEFAULT 0"),
        ('installment_plans', 'service_charge', "ALTER TABLE installment_plans ADD COLUMN service_charge NUMERIC(14,2) DEFAULT 0"),
        ('installment_plans', 'interest_rate',  "ALTER TABLE installment_plans ADD COLUMN interest_rate FLOAT DEFAULT 0"),
        ('installment_plans', 'payment_frequency', "ALTER TABLE installment_plans ADD COLUMN payment_frequency VARCHAR(20) DEFAULT 'monthly'"),
        ('installment_plans', 'first_due_date', "ALTER TABLE installment_plans ADD COLUMN first_due_date DATETIME NULL"),
        ('installment_plans', 'grace_period_days', "ALTER TABLE installment_plans ADD COLUMN grace_period_days INT DEFAULT 0"),
        ('installment_plans', 'late_fee_type',  "ALTER TABLE installment_plans ADD COLUMN late_fee_type VARCHAR(20) DEFAULT 'none'"),
        ('installment_plans', 'late_fee_value', "ALTER TABLE installment_plans ADD COLUMN late_fee_value NUMERIC(14,2) DEFAULT 0"),
        ('installment_plans', 'guarantor_name', "ALTER TABLE installment_plans ADD COLUMN guarantor_name VARCHAR(150)"),
        ('installment_plans', 'guarantor_phone',"ALTER TABLE installment_plans ADD COLUMN guarantor_phone VARCHAR(20)"),
        ('installment_plans', 'guarantor_nic',  "ALTER TABLE installment_plans ADD COLUMN guarantor_nic VARCHAR(20)"),
        ('installment_plans', 'guarantor_address', "ALTER TABLE installment_plans ADD COLUMN guarantor_address TEXT"),
        ('installment_plans', 'agreement_terms',"ALTER TABLE installment_plans ADD COLUMN agreement_terms TEXT"),
        ('installment_plans', 'closed_at',      "ALTER TABLE installment_plans ADD COLUMN closed_at DATETIME NULL"),
        ('installment_plans', 'closed_reason',  "ALTER TABLE installment_plans ADD COLUMN closed_reason VARCHAR(40)"),
        ('installment_plans', 'repossessed',    "ALTER TABLE installment_plans ADD COLUMN repossessed TINYINT(1) DEFAULT 0"),
        ('installments', 'penalty_amount',      "ALTER TABLE installments ADD COLUMN penalty_amount NUMERIC(14,2) DEFAULT 0"),
        # v7.0 — linked customer profile system
        ('vehicle_history',   'customer_id',   "ALTER TABLE vehicle_history ADD COLUMN customer_id INT NULL"),
        ('retail_customers',  'updated_at',    "ALTER TABLE retail_customers ADD COLUMN updated_at DATETIME NULL"),
        # v8.0 — broker referral fields on repair_jobs
        ('repair_jobs', 'broker_id',                "ALTER TABLE repair_jobs ADD COLUMN broker_id INT NULL"),
        ('repair_jobs', 'broker_name_snapshot',     "ALTER TABLE repair_jobs ADD COLUMN broker_name_snapshot VARCHAR(150)"),
        ('repair_jobs', 'broker_commission_type',   "ALTER TABLE repair_jobs ADD COLUMN broker_commission_type VARCHAR(10)"),
        ('repair_jobs', 'broker_commission_value',  "ALTER TABLE repair_jobs ADD COLUMN broker_commission_value NUMERIC(14,2) DEFAULT 0"),
        ('repair_jobs', 'broker_commission_amount', "ALTER TABLE repair_jobs ADD COLUMN broker_commission_amount NUMERIC(14,2) DEFAULT 0"),
        ('repair_jobs', 'broker_cash_price',        "ALTER TABLE repair_jobs ADD COLUMN broker_cash_price NUMERIC(14,2) DEFAULT 0"),
    ]
    money_type_migrations = [
        ('sales', 'total_amount'),
        ('sales', 'tax'),
        ('sales', 'subtotal'),
        ('sales', 'tendered'),
        ('sales', 'change_amount'),
        ('sales', 'trade_in_value'),
        ('sale_items', 'price'),
        ('sale_items', 'discount'),
        ('sale_items', 'total'),
        ('payments', 'amount'),
        ('purchases', 'total_amount'),
        ('purchases', 'paid_amount'),
        ('product_returns', 'total_amount'),
        ('product_returns', 'refund_amount'),
        ('return_items', 'price'),
        ('return_items', 'total'),
        ('repair_jobs', 'labour_charge'),
        ('repair_jobs', 'total_amount'),
        ('repair_jobs', 'advance_paid'),
        ('repair_job_parts', 'unit_cost'),
        ('repair_job_parts', 'sell_price'),
        ('repair_job_parts', 'total'),
        ('installment_plans', 'cash_price'),
        ('installment_plans', 'service_charge'),
        ('installment_plans', 'late_fee_value'),
        ('installments', 'penalty_amount'),
        ('installment_payments', 'amount'),
    ]
    try:
        inspector = sa_inspect(db.engine)
        existing_tables = inspector.get_table_names()
        is_sqlite = db.engine.dialect.name == 'sqlite'
        column_cache = {}

        def _get_columns(table_name):
            if table_name in column_cache:
                return column_cache[table_name]
            if is_sqlite:
                cols = _sqlite_table_info(table_name)
            else:
                cols = inspector.get_columns(table_name)
            column_cache[table_name] = cols
            return cols

        for table, column, sql in migrations:
            if table not in existing_tables: continue
            existing_cols = [c['name'] for c in _get_columns(table)]
            if column not in existing_cols:
                print(f"  [MIGRATE] Adding: {table}.{column}")
                try:
                    db.session.execute(text(sql)); db.session.commit()
                    column_cache.pop(table, None)
                except Exception as col_err:
                    db.session.rollback()
                    col_err_text = str(col_err)
                    should_retry_sqlite_timestamp = (
                        is_sqlite
                        and 'non-constant default' in col_err_text.lower()
                        and 'CURRENT_TIMESTAMP' in sql
                    )
                    if should_retry_sqlite_timestamp:
                        fallback_sql = re.sub(r"\s+NOT\s+NULL\s+DEFAULT\s+\(?CURRENT_TIMESTAMP\)?", "", sql, flags=re.IGNORECASE)
                        fallback_sql = re.sub(r"\s+DEFAULT\s+\(?CURRENT_TIMESTAMP\)?", "", fallback_sql, flags=re.IGNORECASE)
                        try:
                            db.session.execute(text(fallback_sql))
                            db.session.execute(text(
                                f"UPDATE {table} SET {column} = CURRENT_TIMESTAMP WHERE {column} IS NULL"
                            ))
                            db.session.commit()
                            column_cache.pop(table, None)
                            print(
                                f"  [MIGRATE] Retried without non-constant default: {table}.{column} "
                                "(backfilled with CURRENT_TIMESTAMP)"
                            )
                        except Exception as fallback_err:
                            print(f"  [MIGRATE] Skipped: {fallback_err}")
                            db.session.rollback()
                    else:
                        print(f"  [MIGRATE] Skipped: {col_err}")

        for table, column in money_type_migrations:
            if table not in existing_tables:
                continue
            col_meta = next((c for c in _get_columns(table) if c['name'] == column), None)
            if not col_meta:
                continue
            col_type = str(col_meta.get('type', '')).upper()
            if 'FLOAT' in col_type or 'REAL' in col_type or 'DOUBLE' in col_type:
                if is_sqlite:
                    print(
                        f"  [MIGRATE] SQLite type-change required for {table}.{column}: "
                        "FLOAT -> NUMERIC(14,2). SQLite does not support ALTER COLUMN TYPE."
                    )
                    print(
                        f"  [MIGRATE] Strategy ({table}.{column}): "
                        "1) add new NUMERIC(14,2) column, "
                        "2) copy/cast data, "
                        "3) drop old column, "
                        "4) rename new column."
                    )
                else:
                    print(
                        f"  [MIGRATE] Type-change required for {table}.{column}: "
                        "FLOAT -> NUMERIC(14,2)."
                    )

        if 'vehicle_brands' in existing_tables:
            db.session.execute(text("""
                UPDATE vehicle_brands
                SET normalized_name = lower(replace(trim(coalesce(name, '')), ' ', ''))
                WHERE coalesce(normalized_name, '') = ''
            """))
            db.session.commit()
            db.session.execute(text("""
                DELETE FROM vehicle_brands
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM vehicle_brands
                    GROUP BY normalized_name, vehicle_type
                )
            """))
            db.session.commit()
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicle_brand_normalized_type_idx "
                "ON vehicle_brands(normalized_name, vehicle_type)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_vehicle_brands_vehicle_type_active_idx "
                "ON vehicle_brands(vehicle_type, is_active)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_vehicle_brands_usage_count_idx "
                "ON vehicle_brands(usage_count)"
            ))
            db.session.commit()

        # repair_jobs performance indexes — safe on both new and existing databases
        if 'repair_jobs' in existing_tables:
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_status "
                "ON repair_jobs(status)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_received_date "
                "ON repair_jobs(received_date)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_payment_status "
                "ON repair_jobs(payment_status)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_technician_id "
                "ON repair_jobs(technician_id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_customer_id "
                "ON repair_jobs(customer_id)"
            ))
            # Composite: most analytics queries filter (received_date, status) together
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repair_jobs_received_status "
                "ON repair_jobs(received_date, status)"
            ))
            db.session.commit()

        # Resolve legacy bi-directional sale_items<->imei_records links safely:
        # source of truth is imei_records.sale_item_id -> sale_items.id.
        if is_sqlite and {'sale_items', 'imei_records'}.issubset(set(existing_tables)):
            sale_item_cols = {c['name'] for c in _get_columns('sale_items')}
            imei_cols = {c['name'] for c in _get_columns('imei_records')}
            if {'imei_record_id'}.issubset(sale_item_cols) and {'id', 'sale_item_id'}.issubset(imei_cols):
                db.session.execute(text("""
                    UPDATE imei_records
                    SET sale_item_id = (
                        SELECT si.id
                        FROM sale_items si
                        WHERE si.imei_record_id = imei_records.id
                        LIMIT 1
                    )
                    WHERE sale_item_id IS NULL
                """))
                db.session.commit()

        if 'password_resets' in existing_tables:
            reset_cols = [c['name'] for c in _get_columns('password_resets')]
            if 'otp_hash' in reset_cols:
                rows = db.session.execute(
                    text("SELECT id, otp FROM password_resets WHERE otp_hash IS NULL AND otp IS NOT NULL")
                ).fetchall()
                if rows:
                    for reset_id, existing_otp in rows:
                        otp_value = str(existing_otp or '')
                        if len(otp_value) == 64:
                            otp_digest = otp_value
                        else:
                            otp_digest = hashlib.sha256(otp_value.encode('utf-8')).hexdigest()
                        db.session.execute(
                            text("UPDATE password_resets SET otp_hash = :otp_hash WHERE id = :id"),
                            {'otp_hash': otp_digest, 'id': reset_id},
                        )
                    db.session.commit()

        if 'users' in existing_tables:
            user_cols = [c['name'] for c in inspector.get_columns('users')]
            if 'role' in user_cols:
                db.session.execute(text("UPDATE users SET role = 'Admin' WHERE lower(trim(role)) IN ('admin', 'administrator')"))
                db.session.execute(text("UPDATE users SET role = 'Operator' WHERE lower(trim(role)) = 'operator'"))
                db.session.execute(text("UPDATE users SET role = 'Manager' WHERE lower(trim(role)) = 'manager'"))
                db.session.execute(text("UPDATE users SET role = 'Cashier' WHERE role IS NULL OR trim(role) = '' OR lower(trim(role)) = 'cashier'"))
                db.session.commit()
            if 'is_admin' in user_cols:
                db.session.execute(
                    text("UPDATE users SET is_admin = CASE WHEN lower(trim(role)) = 'admin' THEN 1 ELSE 0 END")
                )
                db.session.commit()

        if 'products' in existing_tables:
            product_cols = [c['name'] for c in inspector.get_columns('products')]
            if 'product_type' in product_cols:
                db.session.execute(text("UPDATE products SET product_type = 'imei' WHERE is_imei_tracked = 1 AND (product_type IS NULL OR trim(product_type)='' )"))
                db.session.execute(text("UPDATE products SET product_type = 'normal' WHERE product_type IS NULL OR trim(product_type)='' OR lower(product_type) NOT IN ('normal','imei')"))
            if 'is_imei_tracked' in product_cols and 'product_type' in product_cols:
                db.session.execute(text("UPDATE products SET is_imei_tracked = CASE WHEN lower(product_type) = 'imei' THEN 1 ELSE 0 END"))
            db.session.commit()
        if 'retail_customers' in existing_tables:
            retail_cols = [c['name'] for c in _get_columns('retail_customers')]
            if 'phone_normalized' in retail_cols:
                db.session.execute(text("""
                    UPDATE retail_customers
                    SET phone_normalized = substr(replace(replace(replace(replace(coalesce(phone,''), '+', ''), ' ', ''), '-', ''), '(', ''), -9)
                    WHERE coalesce(phone, '') <> '' AND (phone_normalized IS NULL OR trim(phone_normalized) = '')
                """))
            if 'customer_code' in retail_cols:
                db.session.execute(text("""
                    UPDATE retail_customers
                    SET customer_code = printf('CUST-%06d', id)
                    WHERE customer_code IS NULL OR trim(customer_code) = ''
                """))
            db.session.commit()
        if {'sales', 'payments'}.issubset(set(existing_tables)):
            db.session.execute(text("""
                UPDATE payments
                SET customer_id = (
                    SELECT s.retail_customer_id FROM sales s WHERE s.id = payments.sale_id
                )
                WHERE customer_id IS NULL
            """))
            db.session.commit()

        # v7.0 backfill — populate vehicle_history.customer_id from repair_jobs
        if {'vehicle_history', 'repair_jobs'}.issubset(set(existing_tables)):
            vh_cols = [c['name'] for c in _get_columns('vehicle_history')]
            rj_cols = [c['name'] for c in _get_columns('repair_jobs')]
            if 'customer_id' in vh_cols:
                db.session.execute(text("""
                    UPDATE vehicle_history
                    SET customer_id = (
                        SELECT rj.customer_id FROM repair_jobs rj WHERE rj.id = vehicle_history.job_id
                    )
                    WHERE customer_id IS NULL
                """))
                db.session.commit()
                print("  [MIGRATE] Backfilled vehicle_history.customer_id from repair_jobs.")

            if 'customer_name_snapshot' in rj_cols and 'customer_name' in rj_cols:
                updated = db.session.execute(text("""
                    UPDATE repair_jobs
                    SET customer_name_snapshot = customer_name,
                        customer_phone_snapshot = customer_phone
                    WHERE customer_name_snapshot IS NULL OR customer_name_snapshot = ''
                """)).rowcount
                db.session.commit()
                if updated > 0:
                    print(f"  [MIGRATE] Backfilled {updated} repair_jobs snapshots.")

        if not StoreSettings.query.filter_by(key=PRIMARY_ADMIN_USER_ID_KEY).first():
            primary_admin = User.query.filter_by(is_primary_admin=True).first()
            _set_store_setting_value(PRIMARY_ADMIN_USER_ID_KEY, primary_admin.id if primary_admin else '')
            db.session.commit()
    except Exception as e:
        print(f"  [MIGRATE] Warning: {e}")
        try: db.session.rollback()
        except Exception: pass

    try:
        db.session.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_sale_invoice_number ON sales(invoice_number)'
        ))
        db.session.commit()
    except Exception as idx_err:
        print(f"  [MIGRATE] Warning (uq_sale_invoice_number): {idx_err}")
        try:
            db.session.rollback()
        except Exception:
            pass

    try:
        db.session.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_imei_records_imei_unique ON imei_records(imei)'
        ))
        db.session.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_imei_records_status_created ON imei_records(status, created_at)'
        ))
        db.session.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_imei_records_barcode_value ON imei_records(barcode_value)'
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='in_stock' WHERE status IS NULL OR trim(status)='' OR lower(status) IN ('in_stock','active')"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='sold' WHERE lower(status)='sold'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='reserved' WHERE lower(status)='reserved'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='returned' WHERE lower(status)='returned'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='blacklisted' WHERE lower(status)='blacklisted'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='under_repair' WHERE lower(status) IN ('repaired','under_repair')"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='damaged' WHERE lower(status)='damaged'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET status='replaced' WHERE lower(status)='replaced'"
        ))
        db.session.execute(text(
            "UPDATE imei_records SET product_name_snapshot = (SELECT name FROM products p WHERE p.id = imei_records.product_id) WHERE product_name_snapshot IS NULL OR trim(product_name_snapshot)=''"
        ))
        db.session.commit()
    except Exception as idx_err:
        print(f"  [MIGRATE] Warning (idx_imei_records_imei_unique): {idx_err}")
        try:
            db.session.rollback()
        except Exception:
            pass

# ── INIT ──────────────────────────────────────────────────────────────────────
try:
    with app.app_context():
        _db_path = app.config.get('DATABASE_FILE', '')
        _is_fresh_db = not os.path.exists(_db_path) or os.path.getsize(_db_path) == 0
        if _is_fresh_db:
            print("  [STARTUP] Fresh database detected — creating schema ...")
        else:
            print("  [STARTUP] Existing database found — verifying schema ...")

        db.create_all()
        try:
            from services.migrations import run_migrations
            applied = run_migrations(db.session)
            if applied:
                app.logger.info('[MIGRATE] Applied migrations: %s', applied)
        except Exception as _mig_err:
            app.logger.warning('[MIGRATE] Migration error (falling back to auto_migrate): %s', _mig_err)
            try:
                auto_migrate()
            except Exception as _auto_err:
                app.logger.warning('[MIGRATE] auto_migrate fallback also failed: %s', _auto_err)

        try:
            short_pw_users = User.query.filter(func.length(User.password) < 60).all()
            for u in short_pw_users:
                app.logger.warning(
                    '[SECURITY] User %s has a suspiciously short password hash — possible plaintext!',
                    u.username
                )
        except Exception:
            pass

        # seed_database() is isolated so that a transient baseline-settings
        # write error does not block startup.
        try:
            seed_database()
        except Exception as _seed_err:
            app.logger.warning('[STARTUP] seed_database() failed (non-fatal): %s', _seed_err)
            try:
                db.session.rollback()
            except Exception:
                pass

        try:
            seed_default_expense_categories()
        except Exception as _seed_exp_err:
            app.logger.warning('[STARTUP] seed_default_expense_categories() failed (non-fatal): %s', _seed_exp_err)
            try:
                db.session.rollback()
            except Exception:
                pass

        ensure_admin_user_exists()
        _print_startup_user_summary()
        _db_ok = True
        print("  [STARTUP] Database initialised successfully.")
except Exception as e:
    _log_database_failure('startup initialization', e)
    print(f"\n  [WARNING] Database not available: {e}")
    _db_ok = False

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print()
    print("  ============================================")
    print(f"    Garage Management System  v{__version__}  — Production Server")
    print("  ============================================")
    print(f"  Server: http://localhost:5000")
    print("  First run: create a secure admin account in the setup screen")
    print()
    try:
        from waitress import serve
        print("  Using Waitress production server"); print()
        serve(app, host='127.0.0.1', port=5000, threads=8, channel_timeout=120, _quiet=False)
    except ImportError:
        print("  [WARNING] Waitress not found, using Flask dev server")
        app.run(debug=False, host='127.0.0.1', port=5000, use_reloader=False)
