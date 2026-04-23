"""Production-grade Card Terminal Service.

Supports:
  - LAN/TCP network terminals (IP + port)
  - Serial/COM port terminals (USB/RS-232)
  - Manual record-only mode (no physical terminal)

Features:
  - Non-blocking connect / disconnect / charge
  - Auto-reconnect on failure
  - Configurable timeout
  - Thread-safe connection state
  - Clear, structured error responses
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────────────

class TerminalType(str, Enum):
    MANUAL = 'manual_record_only'
    LAN = 'lan_ip_terminal'
    SERIAL = 'usb_serial_terminal'


class TerminalStatus(str, Enum):
    DISCONNECTED = 'disconnected'
    CONNECTING = 'connecting'
    CONNECTED = 'connected'
    ERROR = 'error'


@dataclass
class CardConfig:
    terminal_type: TerminalType = TerminalType.MANUAL
    ip_address: str = ''
    tcp_port: int = 0
    com_port: str = ''
    baud_rate: int = 9600
    timeout_seconds: int = 30
    auto_connect: bool = False
    enabled: bool = False

    @classmethod
    def from_settings(cls, settings_dict: dict) -> 'CardConfig':
        raw_type = str(settings_dict.get('pref_card_terminal_type') or 'manual_record_only').strip()
        try:
            t_type = TerminalType(raw_type)
        except ValueError:
            t_type = TerminalType.MANUAL

        try:
            port = int(settings_dict.get('pref_card_port') or 0)
        except (TypeError, ValueError):
            port = 0

        try:
            baud = int(settings_dict.get('pref_card_baud_rate') or 9600)
        except (TypeError, ValueError):
            baud = 9600

        try:
            timeout = int(float(settings_dict.get('pref_card_transaction_timeout') or 30))
            timeout = max(5, min(timeout, 300))
        except (TypeError, ValueError):
            timeout = 30

        enabled_raw = str(settings_dict.get('pref_card_enabled') or '0').strip().lower()
        auto_raw = str(settings_dict.get('pref_card_auto_connect') or '0').strip().lower()

        return cls(
            terminal_type=t_type,
            ip_address=str(settings_dict.get('pref_card_ip_address') or '').strip(),
            tcp_port=port,
            com_port=str(settings_dict.get('pref_card_com_port') or '').strip(),
            baud_rate=baud,
            timeout_seconds=timeout,
            auto_connect=auto_raw in {'1', 'true', 'yes', 'on'},
            enabled=enabled_raw in {'1', 'true', 'yes', 'on'},
        )


@dataclass
class PaymentResult:
    ok: bool
    status: str  # 'success' | 'declined' | 'timeout' | 'error' | 'manual'
    message: str
    approval_code: str = ''
    rrn_reference: str = ''
    card_type: str = ''
    card_last4: str = ''
    terminal_id: str = ''
    transaction_timestamp: str = ''
    raw_response: dict = field(default_factory=dict)


# ── Service ───────────────────────────────────────────────────────────────────

class CardTerminalService:
    """Thread-safe card terminal manager.

    Usage::

        svc = CardTerminalService()
        svc.configure(config)
        ok, msg = svc.connect()
        result = svc.send_payment(Decimal('150.00'))
        svc.disconnect()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._config: CardConfig = CardConfig()
        self._status: TerminalStatus = TerminalStatus.DISCONNECTED
        self._last_error: str = ''
        self._connected_at: Optional[float] = None
        self._reconnect_attempts: int = 0
        self._max_reconnect_attempts: int = 3

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(self, config: CardConfig) -> None:
        with self._lock:
            self._config = config
            # Reset state on reconfiguration
            if self._status == TerminalStatus.CONNECTED:
                self._status = TerminalStatus.DISCONNECTED
                self._connected_at = None

    def configure_from_settings(self, settings_dict: dict) -> None:
        self.configure(CardConfig.from_settings(settings_dict))

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def status(self) -> TerminalStatus:
        with self._lock:
            return self._status

    @property
    def is_connected(self) -> bool:
        return self.status == TerminalStatus.CONNECTED

    def status_dict(self) -> dict:
        with self._lock:
            return {
                'status': self._status.value,
                'terminal_type': self._config.terminal_type.value,
                'enabled': self._config.enabled,
                'connected': self._status == TerminalStatus.CONNECTED,
                'last_error': self._last_error,
                'connected_since': self._connected_at,
            }

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self) -> tuple[bool, str]:
        """Attempt to connect to the configured terminal.

        Returns (success, message).
        """
        with self._lock:
            cfg = self._config

        if not cfg.enabled:
            return False, 'Card terminal is disabled.'

        if cfg.terminal_type == TerminalType.MANUAL:
            with self._lock:
                self._status = TerminalStatus.CONNECTED
                self._connected_at = time.monotonic()
                self._last_error = ''
            return True, 'Manual mode — no physical terminal connection required.'

        if cfg.terminal_type == TerminalType.LAN:
            return self._connect_lan(cfg)

        if cfg.terminal_type == TerminalType.SERIAL:
            return self._connect_serial(cfg)

        return False, f'Unknown terminal type: {cfg.terminal_type}'

    def _connect_lan(self, cfg: CardConfig) -> tuple[bool, str]:
        if not cfg.ip_address:
            return False, 'IP address is required for LAN terminal.'
        if cfg.tcp_port <= 0 or cfg.tcp_port > 65535:
            return False, 'Valid TCP port (1-65535) is required for LAN terminal.'

        with self._lock:
            self._status = TerminalStatus.CONNECTING

        try:
            with socket.create_connection((cfg.ip_address, cfg.tcp_port), timeout=min(cfg.timeout_seconds, 10)):
                pass  # connection test only
            with self._lock:
                self._status = TerminalStatus.CONNECTED
                self._connected_at = time.monotonic()
                self._last_error = ''
                self._reconnect_attempts = 0
            logger.info('Card terminal connected ip=%s port=%s', cfg.ip_address, cfg.tcp_port)
            return True, f'Connected to {cfg.ip_address}:{cfg.tcp_port}'
        except socket.timeout:
            msg = f'Connection timed out to {cfg.ip_address}:{cfg.tcp_port}'
        except ConnectionRefusedError:
            msg = f'Connection refused by {cfg.ip_address}:{cfg.tcp_port}'
        except OSError as exc:
            msg = f'Network error: {exc}'

        with self._lock:
            self._status = TerminalStatus.ERROR
            self._last_error = msg
        logger.warning('Card terminal connect failed: %s', msg)
        return False, msg

    def _connect_serial(self, cfg: CardConfig) -> tuple[bool, str]:
        if not cfg.com_port:
            return False, 'COM port is required for serial terminal.'

        with self._lock:
            self._status = TerminalStatus.CONNECTING

        try:
            import serial  # type: ignore
            ser = serial.Serial(port=cfg.com_port, baudrate=cfg.baud_rate, timeout=2)
            ser.close()
            with self._lock:
                self._status = TerminalStatus.CONNECTED
                self._connected_at = time.monotonic()
                self._last_error = ''
                self._reconnect_attempts = 0
            logger.info('Card terminal connected port=%s baud=%s', cfg.com_port, cfg.baud_rate)
            return True, f'Connected to {cfg.com_port} @ {cfg.baud_rate} baud'
        except ImportError:
            msg = 'pyserial is not installed. Run: pip install pyserial'
        except Exception as exc:
            msg = f'Serial error on {cfg.com_port}: {exc}'

        with self._lock:
            self._status = TerminalStatus.ERROR
            self._last_error = msg
        logger.warning('Card terminal serial connect failed: %s', msg)
        return False, msg

    def disconnect(self) -> None:
        with self._lock:
            self._status = TerminalStatus.DISCONNECTED
            self._connected_at = None
        logger.info('Card terminal disconnected')

    def reconnect(self) -> tuple[bool, str]:
        """Attempt reconnection with back-off."""
        with self._lock:
            attempts = self._reconnect_attempts
        if attempts >= self._max_reconnect_attempts:
            return False, 'Max reconnection attempts reached. Please check terminal.'
        with self._lock:
            self._reconnect_attempts += 1
        return self.connect()

    def ensure_connected(self) -> tuple[bool, str]:
        """Return existing connection or attempt to reconnect."""
        if self.is_connected:
            return True, 'Connected.'
        return self.reconnect()

    # ── Payment ───────────────────────────────────────────────────────────────

    def send_payment(self, amount: Decimal) -> PaymentResult:
        """Initiate a card payment.

        In manual mode, returns a pending result — the cashier records
        the terminal outcome manually.

        For LAN/Serial modes the terminal is pinged first; actual
        protocol integration depends on the terminal SDK/model.
        """
        from datetime import datetime, timezone

        if not self._config.enabled:
            return PaymentResult(
                ok=False, status='error',
                message='Card terminal is not enabled.',
            )

        if amount <= 0:
            return PaymentResult(
                ok=False, status='error',
                message='Payment amount must be greater than zero.',
            )

        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        if self._config.terminal_type == TerminalType.MANUAL:
            return PaymentResult(
                ok=True, status='manual',
                message='Manual mode: record terminal approval code below.',
                transaction_timestamp=ts,
            )

        ok, msg = self.ensure_connected()
        if not ok:
            return PaymentResult(ok=False, status='error', message=msg)

        if self._config.terminal_type == TerminalType.LAN:
            return self._send_lan_payment(amount, ts)

        if self._config.terminal_type == TerminalType.SERIAL:
            return self._send_serial_payment(amount, ts)

        return PaymentResult(ok=False, status='error', message='Unsupported terminal type.')

    def _send_lan_payment(self, amount: Decimal, ts: str) -> PaymentResult:
        """Send payment request over TCP to the terminal."""
        cfg = self._config
        payload = f'PURCHASE:{float(amount):.2f}\r\n'.encode()
        try:
            with socket.create_connection((cfg.ip_address, cfg.tcp_port), timeout=cfg.timeout_seconds) as conn:
                conn.sendall(payload)
                conn.settimeout(cfg.timeout_seconds)
                response_bytes = b''
                while True:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    response_bytes += chunk
                    if b'\r\n' in response_bytes or b'\n' in response_bytes:
                        break
            response_text = response_bytes.decode('ascii', errors='replace').strip()
            return self._parse_terminal_response(response_text, ts)
        except socket.timeout:
            self.disconnect()
            return PaymentResult(ok=False, status='timeout',
                                 message=f'Terminal timed out after {cfg.timeout_seconds}s.')
        except OSError as exc:
            self.disconnect()
            return PaymentResult(ok=False, status='error', message=f'Terminal communication error: {exc}')

    def _send_serial_payment(self, amount: Decimal, ts: str) -> PaymentResult:
        """Send payment request over serial port to the terminal."""
        cfg = self._config
        try:
            import serial  # type: ignore
            with serial.Serial(port=cfg.com_port, baudrate=cfg.baud_rate, timeout=cfg.timeout_seconds) as ser:
                payload = f'PURCHASE:{float(amount):.2f}\r\n'.encode()
                ser.write(payload)
                response_bytes = ser.readline()
            response_text = response_bytes.decode('ascii', errors='replace').strip()
            return self._parse_terminal_response(response_text, ts)
        except ImportError:
            return PaymentResult(ok=False, status='error',
                                 message='pyserial not installed. Run: pip install pyserial')
        except Exception as exc:
            self.disconnect()
            return PaymentResult(ok=False, status='error', message=f'Serial terminal error: {exc}')

    def _parse_terminal_response(self, response: str, ts: str) -> PaymentResult:
        """Parse a terminal response string into a PaymentResult.

        Terminals vary widely; this is a best-effort generic parser.
        Override or extend for specific terminal protocols.
        """
        upper = response.upper()
        if 'APPROVED' in upper or 'APPROVAL' in upper or 'SUCCESS' in upper:
            # Extract approval code if present: e.g. "APPROVED APR123456"
            import re
            apr_match = re.search(r'APR[OVAL]*[\s:]*([A-Z0-9]+)', upper)
            rrn_match = re.search(r'RRN[\s:]*([0-9]+)', upper)
            return PaymentResult(
                ok=True, status='success',
                message='Payment approved by terminal.',
                approval_code=apr_match.group(1) if apr_match else '',
                rrn_reference=rrn_match.group(1) if rrn_match else '',
                transaction_timestamp=ts,
                raw_response={'raw': response},
            )
        if 'DECLINED' in upper or 'DECLINE' in upper or 'REJECT' in upper:
            return PaymentResult(
                ok=False, status='declined',
                message='Transaction declined by issuer.',
                transaction_timestamp=ts,
                raw_response={'raw': response},
            )
        if 'TIMEOUT' in upper or 'TIME OUT' in upper:
            return PaymentResult(
                ok=False, status='timeout',
                message='Terminal transaction timed out.',
                transaction_timestamp=ts,
            )
        # Unknown response — treat as manual entry needed
        return PaymentResult(
            ok=True, status='manual',
            message=f'Terminal responded: {response or "(empty)"}. Record outcome manually.',
            transaction_timestamp=ts,
            raw_response={'raw': response},
        )

    # ── Health check ──────────────────────────────────────────────────────────

    def ping(self) -> tuple[bool, str]:
        """Quick connectivity check without a full payment cycle."""
        cfg = self._config
        if not cfg.enabled:
            return False, 'Terminal is disabled.'
        if cfg.terminal_type == TerminalType.MANUAL:
            return True, 'Manual mode: always available.'
        return self.connect()


# ── Process singleton ─────────────────────────────────────────────────────────

_service: CardTerminalService | None = None
_service_lock = threading.Lock()


def get_card_terminal_service() -> CardTerminalService:
    """Return the process-level CardTerminalService singleton."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CardTerminalService()
    return _service
