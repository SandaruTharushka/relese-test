from __future__ import annotations

import ctypes
import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from runtime_paths import persistent_app_dir, persistent_path, resource_path
from version import APP_VERSION

APP_NAME = 'Garage Management System'
APP_ID = 'stalgo.garage.management.system.desktop'
FLASK_HOST = '127.0.0.1'
FLASK_PORT = 5000
FLASK_URL = f'http://{FLASK_HOST}:{FLASK_PORT}'
ICON_PATH = Path(resource_path('static', 'icons', 'icon.ico'))
WINDOW_MIN_SIZE = (1200, 720)
HEALTH_TIMEOUT_SECONDS = 90
LOG_DIR_NAME = 'logs'
LOG_FILE_NAME = 'desktop-runtime.log'

logger = logging.getLogger('garage.desktop')
_runtime_logging_configured = False

SPLASH_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:'Segoe UI',sans-serif;
    background:linear-gradient(135deg,#4F6CF6,#7C3AED);
    display:flex;align-items:center;justify-content:center;
    height:100vh;overflow:hidden;color:#fff;
  }
  .box{text-align:center;padding:40px 48px}
  .logo{font-size:64px;margin-bottom:20px;animation:pulse 2s infinite}
  .name{font-size:36px;font-weight:900;letter-spacing:-1px;margin-bottom:8px}
  .sub{font-size:14px;opacity:.75;margin-bottom:36px;letter-spacing:.5px}
  .ver{font-size:12px;opacity:.55;margin-top:24px;letter-spacing:1px}
  .bar-wrap{width:300px;height:4px;background:rgba(255,255,255,.2);border-radius:99px;overflow:hidden;margin:0 auto}
  .bar-fill{height:100%;background:#fff;border-radius:99px;width:0%;animation:load 2.5s ease forwards}
  .dots{display:flex;gap:8px;justify-content:center;margin-top:20px}
  .dot{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.4);animation:blink 1.2s infinite}
  .dot:nth-child(2){animation-delay:.3s}
  .dot:nth-child(3){animation-delay:.6s}
  @keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
  @keyframes load{0%{width:0%}60%{width:75%}100%{width:100%}}
  @keyframes blink{0%,100%{opacity:.3}50%{opacity:1}}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🔧</div>
  <div class="name">Garage Management System</div>
  <div class="sub">Starting secure desktop mode…</div>
  <div class="bar-wrap"><div class="bar-fill"></div></div>
  <div class="dots">
    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
  </div>
  <div class="ver">Version __APP_VERSION__ · Preparing local services…</div>
</div>
</body>
</html>"""
SPLASH_HTML = SPLASH_HTML.replace('__APP_VERSION__', APP_VERSION)

def log_file_path() -> Path:
    return Path(persistent_path(LOG_DIR_NAME, LOG_FILE_NAME))


def setup_runtime_logging() -> None:
    global _runtime_logging_configured
    log_path = log_file_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if _runtime_logging_configured:
        return

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(threadName)s %(name)s: %(message)s')

    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [file_handler, console_handler]

    logger.setLevel(logging.INFO)
    logger.propagate = True
    logger.info('Desktop runtime logging initialized at %s', log_path)

    def handle_exception(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.error('Unhandled exception', exc_info=(exc_type, exc_value, exc_tb))

    def handle_thread_exception(args: Any) -> None:
        logger.error(
            'Unhandled thread exception in %s',
            getattr(args, 'thread', None).name if getattr(args, 'thread', None) else 'unknown-thread',
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception
    _runtime_logging_configured = True


def _windows_subprocess_kwargs() -> dict[str, Any]:
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def _popen_without_console(args: list[str]) -> subprocess.Popen[Any]:
    return subprocess.Popen(args, **_windows_subprocess_kwargs())


def ensure_runtime_environment() -> None:
    os.chdir(persistent_app_dir())
    set_windows_app_id()
    env_path = persistent_path('.env')
    spec = importlib.util.find_spec('dotenv')
    if spec is None:
        return
    dotenv = importlib.import_module('dotenv')
    dotenv.load_dotenv(env_path)


def set_windows_app_id() -> None:
    if os.name != 'nt':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def import_optional(name: str) -> Any | None:
    if not module_available(name):
        return None
    return importlib.import_module(name)


def is_pywebview_safe() -> bool:
    # PyWebView is disabled. PySide6 Qt WebEngine is the primary desktop shell
    # on Python 3.11 (both source and frozen/PyInstaller builds). This provides
    # the most stable and predictable rendering path on Windows. Set this to
    # True only if you have thoroughly tested PyWebView on your target environment.
    return False


class LocalServer:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.health_thread: threading.Thread | None = None
        self._server: Any | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.thread and self.thread.is_alive():
                logger.info('Local Flask server start requested again; reusing existing server thread.')
                return
            if self._is_healthy():
                logger.info('Detected an already-running local Flask server at %s; reusing it.', FLASK_URL)
                self.error = None
                self.ready.set()
                return

            self.ready.clear()
            self.stop_requested.clear()
            self.error = None
            self._server = None
            self.thread = threading.Thread(target=self._run, name='garage-local-server', daemon=True)
            self.health_thread = threading.Thread(
                target=self._wait_until_ready,
                name='garage-health-check',
                daemon=True,
            )
            logger.info('Starting local Flask server on %s.', FLASK_URL)
            self.thread.start()
            self.health_thread.start()

    def _is_healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f'{FLASK_URL}/health', timeout=1) as response:
                return getattr(response, 'status', 200) == 200
        except Exception:
            return False

    def _run(self) -> None:
        try:
            app_module = importlib.import_module('app')
            flask_app = app_module.app

            # Diagnostic logging (Requirement 7)
            try:
                app_root = persistent_app_dir()
                meipass = getattr(sys, '_MEIPASS', 'Not a PyInstaller build')
                db_path = flask_app.config.get('DATABASE_FILE', 'Unknown')
                db_exists = Path(db_path).exists() if db_path != 'Unknown' else False
                with flask_app.app_context():
                    from models import User
                    try:
                        user_count = User.query.count()
                    except Exception:
                        user_count = -1
                logger.info('--- STARTUP DIAGNOSTICS ---')
                logger.info('App Root Path    : %s', app_root)
                logger.info('Bundle MEIPASS   : %s', meipass)
                logger.info('LocalAppData DB  : %s', db_path)
                logger.info('DB Exists        : %s', db_exists)
                logger.info('User Count       : %s', user_count)
                logger.info('---------------------------')
            except Exception as e:
                logger.warning('Diagnostic logging failed: %s', e)

            if module_available('waitress'):
                waitress = importlib.import_module('waitress')
                self._server = waitress.create_server(flask_app, host=FLASK_HOST, port=FLASK_PORT, threads=8)
                logger.info('Serving desktop runtime with Waitress.')
                self._server.run()
            else:
                serving = importlib.import_module('werkzeug.serving')
                self._server = serving.make_server(FLASK_HOST, FLASK_PORT, flask_app, threaded=True)
                logger.info('Serving desktop runtime with Werkzeug fallback.')
                self._server.serve_forever()
        except Exception as exc:
            if self._is_healthy():
                logger.warning(
                    'Local Flask server thread hit %s but the health endpoint is already live; treating as reused instance.',
                    exc,
                )
                self.error = None
            else:
                exc_str = str(exc)
                # Surface a clear, actionable message for the most common failure: port already in use.
                if any(keyword in exc_str.lower() for keyword in ('address already in use', 'only one usage', '10048', 'eaddrinuse')):
                    self.error = (
                        f'Port {FLASK_PORT} is already in use. '
                        'Another application is listening on this port. '
                        'Close it (or reboot) and try again.'
                    )
                else:
                    self.error = exc_str
                logger.exception('Local Flask server crashed during startup.')
            self.ready.set()

    def _wait_until_ready(self) -> None:
        deadline = time.time() + HEALTH_TIMEOUT_SECONDS
        health_url = f'{FLASK_URL}/health'
        while time.time() < deadline and not self.stop_requested.is_set():
            if self.error:
                self.ready.set()
                return
            try:
                with urllib.request.urlopen(health_url, timeout=1):
                    logger.info('Local Flask server passed health check at %s.', health_url)
                    self.ready.set()
                    return
            except Exception:
                time.sleep(0.25)
        if self.error is None and not self.stop_requested.is_set():
            self.error = (
                f'Garage Management System could not start within {HEALTH_TIMEOUT_SECONDS} seconds. '
                'This can happen on the first launch (database setup), when antivirus software '
                'is scanning the application, or if another program is using port 5000. '
                'Check the log file for details.'
            )
            logger.error('Startup timed out after %ds. Log: %s', HEALTH_TIMEOUT_SECONDS, log_file_path())
        self.ready.set()

    def wait(self, timeout: float | None = None) -> bool:
        is_ready = self.ready.wait(timeout=timeout)
        if not is_ready and self.error is None:
            self.error = 'Timed out while waiting for the local Flask server to start.'
        return is_ready and self.error is None

    def stop(self) -> None:
        self.stop_requested.set()
        server = self._server
        if server is not None:
            close = getattr(server, 'close', None)
            shutdown = getattr(server, 'shutdown', None)
            try:
                if callable(close):
                    close()
                elif callable(shutdown):
                    shutdown()
            except Exception:
                logger.exception('Failed while stopping the local Flask server.')
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=2)
        logger.info('Local Flask server stopped.')


class LicenseService:
    def __init__(self) -> None:
        self._module = import_optional('license')

    def check(self) -> dict[str, Any]:
        if self._module is None:
            return {'licensed': True, 'msg': 'Activation module unavailable', 'info': {}}
        return self._module.check()

    def activate(self, key: str, customer: str = '') -> dict[str, Any]:
        _ = customer
        if self._module is None:
            return {'ok': False, 'msg': 'Activation module unavailable'}
        return self._module.activate_offline(key)

    def activation_status(self) -> dict[str, Any]:
        if self._module is None:
            return {'activated': True, 'requires_activation': False}
        return self._module.activation_status()


class JsBridge:
    def __init__(self, license_service: LicenseService, window_controller: 'WindowController') -> None:
        self.license_service = license_service
        self.window_controller = window_controller

    def minimize_window(self) -> None:
        self.window_controller.minimize()

    def maximize_window(self) -> None:
        self.window_controller.toggle_maximize()

    def close_window(self) -> None:
        self.window_controller.close()

    def get_printers(self) -> dict[str, Any]:
        """Enumerate installed printers via native win32print API (no subprocess)."""
        if os.name != 'nt':
            return {'ok': True, 'printers': [], 'msg': 'Printer enumeration requires Windows.'}

        if not module_available('win32print'):
            logger.error('win32print unavailable — ensure pywin32 is installed and bundled')
            return {
                'ok': False,
                'printers': [],
                'msg': 'win32print is not available. Ensure pywin32 is installed.',
            }

        try:
            win32print = importlib.import_module('win32print')
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = sorted(dict.fromkeys(
                str(item[2]).strip()
                for item in win32print.EnumPrinters(flags)
                if len(item) >= 3 and item[2] and str(item[2]).strip()
            ))
            logger.info('win32print enumerated %d printer(s) via desktop API', len(printers))
            msg = 'No printers found. Install a printer driver and try again.' if not printers else None
            return {'ok': True, 'printers': printers, 'msg': msg}
        except Exception as exc:
            logger.exception('Desktop printer enumeration failed')
            return {
                'ok': False,
                'printers': [],
                'msg': f'Printer detection failed: {exc}',
            }

    def get_serial_ports(self) -> dict[str, Any]:
        try:
            serial_tools = import_optional('serial.tools.list_ports')
            if serial_tools is None:
                return {'ok': True, 'ports': ['COM1', 'COM2', 'COM3', 'COM4']}
            ports = [port.device for port in serial_tools.comports()]
            return {'ok': True, 'ports': ports}
        except Exception as exc:
            logger.exception('Desktop serial-port enumeration failed')
            return {'ok': False, 'msg': f'Serial-port detection failed: {exc}', 'ports': []}

    def open_cash_drawer(self) -> dict[str, Any]:
        if os.name != 'nt' or not module_available('win32print'):
            return {'ok': False, 'msg': 'Cash drawer support requires pywin32 on Windows.'}
        try:
            win32print = importlib.import_module('win32print')
            printer_name = win32print.GetDefaultPrinter()
            printer = win32print.OpenPrinter(printer_name)
            win32print.StartDocPrinter(printer, 1, ('Cash Drawer', None, 'RAW'))
            win32print.StartPagePrinter(printer)
            win32print.WritePrinter(printer, b'\x1b\x70\x00\x19\xfa')
            win32print.EndPagePrinter(printer)
            win32print.EndDocPrinter(printer)
            win32print.ClosePrinter(printer)
            return {'ok': True}
        except Exception as exc:
            return {'ok': False, 'msg': str(exc)}

    def check_license(self) -> dict[str, Any]:
        return self.license_service.check()

    def activate_license(self, key: str, customer: str = '') -> dict[str, Any]:
        return self.license_service.activate(key, customer)

    def trigger_backup(self) -> dict[str, Any]:
        backup_module = import_optional('backup')
        if backup_module is None or not hasattr(backup_module, 'do_backup'):
            return {'ok': False, 'msg': 'Backup module unavailable'}
        return backup_module.do_backup()

    def open_backup_folder(self) -> dict[str, Any]:
        backup_module = import_optional('backup')
        if backup_module is None:
            return {'ok': False, 'msg': 'Backup module unavailable'}
        backup_dir = backup_module.load_backup_config().get('backup_dir') or backup_module._backup_dir()
        try:
            if os.name == 'nt':
                os.startfile(backup_dir)
            elif sys.platform == 'darwin':
                _popen_without_console(['open', backup_dir])
            else:
                _popen_without_console(['xdg-open', backup_dir])
            return {'ok': True}
        except Exception as exc:
            logger.exception('Failed to open backup folder: %s', backup_dir)
            return {'ok': False, 'msg': str(exc)}


class WindowController:
    def set_window(self, window: Any) -> None:
        raise NotImplementedError

    def minimize(self) -> None:
        raise NotImplementedError

    def toggle_maximize(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def add_desktop_css_class(self) -> None:
        raise NotImplementedError


class QtController(WindowController):
    def __init__(self) -> None:
        self.window: Any | None = None

    def set_window(self, window: Any) -> None:
        self.window = window

    def minimize(self) -> None:
        if self.window is not None:
            self.window.showMinimized()

    def toggle_maximize(self) -> None:
        if self.window is None:
            return
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def close(self) -> None:
        if self.window is not None:
            self.window.close()

    def add_desktop_css_class(self) -> None:
        if self.window is None:
            return
        page = getattr(self.window, 'page', None)
        if page is None:
            return
        page().runJavaScript("document.documentElement.classList.add('desktop-app')")


class DesktopLauncher:
    def __init__(self) -> None:
        self.server = LocalServer()
        self.license_service = LicenseService()

    def run(self) -> None:
        ensure_runtime_environment()
        self.server.start()
        activation = self.license_service.activation_status()
        start_url = f'{FLASK_URL}/activation' if activation.get('requires_activation') else FLASK_URL
        try:
            logger.info('Using PySide6 Qt WebEngine as the desktop runtime.')
            self.run_qt_fallback(start_url)
        finally:
            self.server.stop()

    # ── Startup update check ──────────────────────────────────────────────────

    def _background_update_check(self, view: Any) -> None:
        """
        Background-thread worker: check GitHub for a newer release and, if one
        is found, inject a dismissible update banner into the running web page.

        Respects UPDATE_CHECK_INTERVAL_HOURS so it does not ping GitHub on every
        single launch. Never raises — any failure is logged at DEBUG level only.
        """
        try:
            from services.updater import UpdateChecker
            from update_config import (
                GITHUB_OWNER,
                GITHUB_REPO,
                UPDATE_CHECK_TIMEOUT_SECONDS,
                UPDATE_CHECK_INTERVAL_HOURS,
            )

            data_dir        = persistent_app_dir()
            last_check_file = data_dir / "last_update_check.txt"

            # Skip if we already checked recently
            if last_check_file.exists():
                try:
                    elapsed = time.time() - float(last_check_file.read_text().strip())
                    if elapsed < UPDATE_CHECK_INTERVAL_HOURS * 3600:
                        return
                except Exception:
                    pass  # corrupted file — proceed with check

            checker = UpdateChecker(
                GITHUB_OWNER, GITHUB_REPO, APP_VERSION,
                timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
            )
            info = checker.check()

            # Persist timestamp regardless of check outcome
            try:
                last_check_file.write_text(str(time.time()))
            except Exception:
                pass

            if not info.is_update_available or not info.installer_asset:
                return

            ver        = info.latest_version
            # Sanitise for safe JS string embedding
            ver_safe   = ver.replace("'", "").replace('"', "").replace("\\", "")

            QtCore = importlib.import_module("PySide6.QtCore")

            def inject_banner() -> None:
                js = f"""
(function() {{
  if (document.getElementById('sm-update-banner')) return;
  var b = document.createElement('div');
  b.id = 'sm-update-banner';
  b.style.cssText = [
    'position:fixed','top:0','left:0','right:0','z-index:9999',
    'background:#059669','color:#fff','padding:10px 20px',
    'display:flex','align-items:center','justify-content:space-between',
    'font-size:14px','font-family:var(--font-sans,sans-serif)',
    'box-shadow:0 2px 8px rgba(0,0,0,.3)'
  ].join(';');
  b.innerHTML =
    '<span style="font-weight:600">&#x1F4E6; Update Available — v{ver_safe}</span>' +
    '<a href="/settings" ' +
       'onclick="setTimeout(function(){{if(window.show)window.show(\\'updates\\');}},250)"' +
       'style="background:rgba(255,255,255,.2);color:#fff;padding:5px 14px;' +
              'border-radius:6px;text-decoration:none;font-size:13px;margin:0 12px"' +
    '>View Update</a>' +
    '<button onclick="document.getElementById(\\'sm-update-banner\\').remove()" ' +
       'style="background:none;border:1px solid rgba(255,255,255,.5);color:#fff;' +
              'padding:5px 10px;border-radius:6px;cursor:pointer;font-size:12px"' +
    '>✕</button>';
  document.body.prepend(b);
}})();
"""
                view.page().runJavaScript(js)

            # Schedule banner injection on the Qt main thread
            QtCore.QTimer.singleShot(0, inject_banner)

        except Exception as exc:
            logger.debug("Startup update check failed (non-critical): %s", exc)

    def run_qt_fallback(self, start_url: str) -> None:
        if not module_available('PySide6.QtWebEngineWidgets'):
            raise RuntimeError(
                'Desktop fallback is unavailable because PySide6 is not installed. '
                'Install requirements.txt or build the packaged EXE.'
            )

        QtCore = importlib.import_module('PySide6.QtCore')
        QtGui = importlib.import_module('PySide6.QtGui')
        QtWidgets = importlib.import_module('PySide6.QtWidgets')
        QtWebEngineWidgets = importlib.import_module('PySide6.QtWebEngineWidgets')

        controller = QtController()
        bridge = JsBridge(self.license_service, controller)
        qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        qt_app.setApplicationName(APP_NAME)
        qt_app.setApplicationDisplayName(APP_NAME)
        qt_app.setApplicationVersion(APP_VERSION)
        if ICON_PATH.exists():
            qt_app.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))

        class ModernSplashScreen(QtWidgets.QWidget):
            def __init__(self) -> None:
                super().__init__(None)
                self._dot_step = 0
                self._closed = False
                self._startup_timer = QtCore.QElapsedTimer()
                self._startup_timer.start()

                self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
                self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
                self.setFixedSize(640, 360)

                root_layout = QtWidgets.QVBoxLayout(self)
                root_layout.setContentsMargins(22, 22, 22, 22)

                self.card = QtWidgets.QFrame(self)
                self.card.setObjectName('splashCard')
                self.card.setStyleSheet(
                    '''
                    QFrame#splashCard {
                      background: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 1,
                        stop: 0 #102a63,
                        stop: 1 #5b3b9a
                      );
                      border-radius: 26px;
                      border: 1px solid rgba(255, 255, 255, 0.16);
                    }
                    QLabel {
                      color: #f4f6ff;
                      background: transparent;
                    }
                    '''
                )
                root_layout.addWidget(self.card)

                card_layout = QtWidgets.QVBoxLayout(self.card)
                card_layout.setContentsMargins(56, 42, 56, 38)
                card_layout.setSpacing(10)
                card_layout.setAlignment(QtCore.Qt.AlignCenter)

                logo = QtWidgets.QLabel('⬢')
                logo.setAlignment(QtCore.Qt.AlignCenter)
                logo.setStyleSheet('font-size: 28px; color: rgba(255,255,255,0.9);')
                card_layout.addWidget(logo)

                title = QtWidgets.QLabel('Garage Management System')
                title.setAlignment(QtCore.Qt.AlignCenter)
                title.setStyleSheet(
                    'font-size: 44px; font-weight: 800; letter-spacing: 0.4px;'
                    'text-shadow: 0 0 20px rgba(160,140,255,0.45);'
                )
                card_layout.addWidget(title)

                subtitle = QtWidgets.QLabel('Retail Management System')
                subtitle.setAlignment(QtCore.Qt.AlignCenter)
                subtitle.setStyleSheet('font-size: 18px; font-weight: 400; color: rgba(243,245,255,0.88);')
                card_layout.addWidget(subtitle)

                card_layout.addSpacing(6)
                self.loading_label = QtWidgets.QLabel('Starting application...')
                self.loading_label.setAlignment(QtCore.Qt.AlignCenter)
                self.loading_label.setStyleSheet('font-size: 15px; color: rgba(235,238,255,0.88);')
                card_layout.addWidget(self.loading_label)

                self.progress = QtWidgets.QProgressBar()
                self.progress.setRange(0, 0)
                self.progress.setFixedWidth(280)
                self.progress.setTextVisible(False)
                self.progress.setStyleSheet(
                    '''
                    QProgressBar {
                      border: 1px solid rgba(255,255,255,0.14);
                      background: rgba(255,255,255,0.1);
                      border-radius: 9px;
                      min-height: 12px;
                    }
                    QProgressBar::chunk {
                      border-radius: 9px;
                      background: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 #93c5fd,
                        stop: 1 #c4b5fd
                      );
                    }
                    '''
                )
                card_layout.addWidget(self.progress, alignment=QtCore.Qt.AlignCenter)

                card_layout.addStretch(1)
                footer = QtWidgets.QLabel('Powered by Cloud Crafters')
                footer.setAlignment(QtCore.Qt.AlignCenter)
                footer.setStyleSheet('font-size: 13px; color: rgba(239,241,255,0.75); letter-spacing: 0.4px;')
                card_layout.addWidget(footer)

                self.opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.card)
                self.opacity_effect.setOpacity(0.0)
                self.card.setGraphicsEffect(self.opacity_effect)

                self.fade_in = QtCore.QPropertyAnimation(self.opacity_effect, b'opacity', self)
                self.fade_in.setDuration(520)
                self.fade_in.setStartValue(0.0)
                self.fade_in.setEndValue(1.0)
                self.fade_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)

                self.zoom_in = QtCore.QPropertyAnimation(self.card, b'geometry', self)
                self.zoom_in.setDuration(520)
                self.zoom_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)

                self.dot_timer = QtCore.QTimer(self)
                self.dot_timer.setInterval(380)
                self.dot_timer.timeout.connect(self._animate_loading_text)

                self.force_close_timer = QtCore.QTimer(self)
                self.force_close_timer.setSingleShot(True)
                self.force_close_timer.setInterval(5000)
                self.force_close_timer.timeout.connect(self.close_splash)

            def show_splash(self) -> None:
                self.show()
                self.raise_()
                self.activateWindow()
                self._center_on_primary_screen()
                self._run_entry_animation()
                self.dot_timer.start()
                self.force_close_timer.start()

            def _center_on_primary_screen(self) -> None:
                screen = QtGui.QGuiApplication.primaryScreen()
                if screen is None:
                    return
                center = screen.availableGeometry().center()
                self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

            def _run_entry_animation(self) -> None:
                final_geometry = self.card.geometry()
                start_width = max(1, int(final_geometry.width() * 0.95))
                start_height = max(1, int(final_geometry.height() * 0.95))
                start_geometry = QtCore.QRect(
                    final_geometry.center().x() - start_width // 2,
                    final_geometry.center().y() - start_height // 2,
                    start_width,
                    start_height,
                )
                self.card.setGeometry(start_geometry)
                self.zoom_in.setStartValue(start_geometry)
                self.zoom_in.setEndValue(final_geometry)
                self.fade_in.start()
                self.zoom_in.start()

            def _animate_loading_text(self) -> None:
                self._dot_step = (self._dot_step + 1) % 4
                dots = '.' * self._dot_step
                self.loading_label.setText(f'Starting application{dots}')

            def close_splash(self) -> None:
                if self._closed:
                    return
                self._closed = True
                self.dot_timer.stop()
                self.force_close_timer.stop()
                self.close()

            def elapsed_seconds(self) -> float:
                return self._startup_timer.elapsed() / 1000.0

        splash = ModernSplashScreen()
        splash.show_splash()
        qt_app.processEvents()

        health_deadline = time.time() + HEALTH_TIMEOUT_SECONDS
        while not self.server.ready.is_set() and time.time() < health_deadline:
            qt_app.processEvents(QtCore.QEventLoop.AllEvents, 50)
            time.sleep(0.05)

        if not self.server.ready.is_set():
            self.server.error = self.server.error or (
                f'Garage Management System could not start within {HEALTH_TIMEOUT_SECONDS} seconds. '
                'This can happen on the first launch (database setup), when antivirus software '
                'is scanning the application, or if another program is using port 5000. '
                'Check the log file for details.'
            )

        if self.server.error:
            splash.close_splash()
            splash.close()
            _log_path = str(log_file_path())
            _msg = (
                f'{self.server.error}\n\n'
                f'Log file:\n{_log_path}\n\n'
                'Open the log file to see the full error details.'
            )
            logger.error('Startup failed — showing error dialog: %s', self.server.error)
            QtWidgets.QMessageBox.critical(None, f'{APP_NAME} — Startup Error', _msg)
            qt_app.quit()
            return

        # Brief stabilisation: the health endpoint responds as soon as the HTTP
        # server starts listening, but the Waitress worker threads may still be
        # spinning up.  A short wait ensures the first real request (GET /login)
        # is not dropped before a worker is ready to accept it.
        time.sleep(0.5)

        page_url = QtCore.QUrl(start_url)
        bridge_wrapper = None
        if hasattr(QtCore, 'QObject') and hasattr(QtWebEngineWidgets, 'QWebEngineView'):
            bridge_wrapper = self._build_qt_webchannel_bridge(QtCore, bridge)

        view = QtWebEngineWidgets.QWebEngineView()
        controller.set_window(view)
        view.setWindowTitle(APP_NAME)
        if ICON_PATH.exists():
            view.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
        view.resize(*WINDOW_MIN_SIZE)
        view.load(page_url)

        if bridge_wrapper is not None:
            QtWebChannel = importlib.import_module('PySide6.QtWebChannel')
            channel = QtWebChannel.QWebChannel(view.page())
            channel.registerObject('desktopBridge', bridge_wrapper)
            view.page().setWebChannel(channel)

            def install_bridge() -> None:
                js = """
                    (function () {
                      function connectBridge() {
                        if (!window.QWebChannel || window.pywebview) return;
                        new QWebChannel(qt.webChannelTransport, function(channel) {
                          const bridge = channel.objects.desktopBridge;
                          window.pywebview = {
                            api: new Proxy({}, {
                              get: function(_, prop) {
                                return function() {
                                  const args = Array.from(arguments);
                                  return new Promise(function(resolve) {
                                    bridge.invoke(String(prop), JSON.stringify(args), function(result) {
                                      try {
                                        resolve(JSON.parse(result));
                                      } catch (error) {
                                        resolve(result);
                                      }
                                    });
                                  });
                                };
                              }
                            })
                          };
                        });
                      }
                      if (window.qt && window.qt.webChannelTransport) {
                        connectBridge();
                      } else {
                        var script = document.createElement('script');
                        script.src = 'qrc:///qtwebchannel/qwebchannel.js';
                        script.onload = connectBridge;
                        document.head.appendChild(script);
                      }
                    })();
                """
                view.page().runJavaScript(js)
                controller.add_desktop_css_class()

            view.loadFinished.connect(lambda ok: install_bridge() if ok else None)
        else:
            view.loadFinished.connect(lambda ok: controller.add_desktop_css_class() if ok else None)

        view.showMaximized()
        fallback_close_timer = QtCore.QTimer(view)
        fallback_close_timer.setSingleShot(True)
        fallback_close_timer.setInterval(5000)
        fallback_close_timer.timeout.connect(splash.close_splash)
        fallback_close_timer.start()

        def on_first_page_ready(ok: bool) -> None:
            if ok or splash.elapsed_seconds() >= 5.0:
                splash.close_splash()
            view.loadFinished.disconnect(on_first_page_ready)

        view.loadFinished.connect(on_first_page_ready)

        # Schedule startup update check 8 s after the event loop begins.
        # Fires once; runs in a daemon thread so it never blocks the UI.
        _upd_timer = QtCore.QTimer()
        _upd_timer.setSingleShot(True)
        _upd_timer.setInterval(8000)
        _upd_timer.timeout.connect(
            lambda: threading.Thread(
                target=self._background_update_check,
                args=(view,),
                daemon=True,
                name="garage-update-check",
            ).start()
        )
        _upd_timer.start()

        qt_app.exec()

    def _build_qt_webchannel_bridge(self, QtCore: Any, bridge: JsBridge) -> Any:
        class BridgeAdapter(QtCore.QObject):
            @QtCore.Slot(str, str, result=str)
            def invoke(self, method_name: str, args_json: str) -> str:
                import json
                args = json.loads(args_json)
                method = getattr(bridge, method_name)
                result = method(*args)
                return json.dumps(result)

        return BridgeAdapter()


def _log_startup_diagnostics() -> None:
    """Emit structured diagnostics immediately after logging is ready.

    Shows: app root, bundle path, DB path, DB exists flag, user count.
    All information appears in the log file even before Flask initialises.
    """
    from runtime_paths import bundle_root, persistent_app_dir
    from database import inspect_sqlite_database

    _bundle = getattr(sys, '_MEIPASS', None)
    _pdir = persistent_app_dir()
    diag = inspect_sqlite_database()

    logger.info('[STARTUP] ── Garage Management System %s ──', APP_VERSION)
    logger.info('[STARTUP] app_root        = %s', _pdir)
    logger.info('[STARTUP] bundle_path     = %s', _bundle if _bundle else 'dev-mode (not frozen)')
    logger.info('[STARTUP] frozen          = %s', bool(_bundle))
    logger.info('[STARTUP] db_path         = %s', diag['database_path'])
    logger.info('[STARTUP] db_exists       = %s', diag['database_exists'])
    logger.info('[STARTUP] db_parent_ok    = %s (writable=%s)', diag['parent_exists'], diag['parent_writable'])
    logger.info('[STARTUP] users_count     = %s', diag.get('users_count', 'n/a (db not yet created)'))
    if diag.get('connect_error'):
        logger.warning('[STARTUP] db_connect_err = %s', diag['connect_error'])


def main() -> None:
    setup_runtime_logging()
    _log_startup_diagnostics()
    try:
        launcher = DesktopLauncher()
        logger.info('Starting %s desktop runtime version %s.', APP_NAME, APP_VERSION)
        launcher.run()
    except Exception as _exc:
        logger.exception('Fatal startup error')
        _log_path = str(log_file_path())
        _msg = (
            f'A fatal error prevented {APP_NAME} from starting.\n\n'
            f'Error: {_exc}\n\n'
            f'Log file:\n{_log_path}\n\n'
            'Open the log file for full details.'
        )
        try:
            import importlib as _il
            _QApp = _il.import_module('PySide6.QtWidgets')
            _app = _QApp.QApplication.instance() or _QApp.QApplication(sys.argv)
            _QApp.QMessageBox.critical(None, f'{APP_NAME} — Fatal Error', _msg)
            _app.quit()
        except Exception:
            print(_msg, file=sys.stderr)
        sys.exit(1)
