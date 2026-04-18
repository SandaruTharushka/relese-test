"""Unified offline activation/trial module for all runtimes."""

import os, json, hashlib, hmac, platform, uuid, re
from datetime import datetime, timedelta
from pathlib import Path

from runtime_paths import persistent_app_dir

# ── Storage location (with fallback) ─────────────────────────────────────────
def _get_license_dir():
    """Get license directory — tries ProgramData first, falls back to Documents."""
    primary = os.path.join(os.environ.get('PROGRAMDATA', r'C:\ProgramData'), 'SuperMartPOS')
    try:
        os.makedirs(primary, exist_ok=True)
        # Test if we can write
        test_file = os.path.join(primary, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        return primary
    except (PermissionError, OSError):
        pass

    # Fallback to user's Documents folder
    fallback = os.path.join(str(Path.home()), 'Documents', 'SuperMartPOS')
    try:
        os.makedirs(fallback, exist_ok=True)
        return fallback
    except Exception:
        pass

    # Last resort: same directory as the script
    local = os.path.join(str(persistent_app_dir()), '.license')
    os.makedirs(local, exist_ok=True)
    return local


LICENSE_DIR  = _get_license_dir()
ACTIVATION_FILE = os.path.join(LICENSE_DIR, 'activation.json')


# ── License secret (set LICENSE_SECRET in .env) ────────────────────────────────
LICENSE_SECRET = os.getenv('LICENSE_SECRET', 'supermart-license-secret-change-this')
ACTIVATION_SECRET = os.getenv('OFFLINE_ACTIVATION_SECRET', LICENSE_SECRET)
TRIAL_DAYS = 7
OWNER_CONTACT = {
    'email': 'sandarutharushka044@gmail.com',
    'whatsapp': '0788140255',
}


OFFLINE_ACTIVATION_RE = re.compile(r'^SMPOS-[A-F0-9]{8}-[A-F0-9]{8}-[A-F0-9]{8}$')


def _get_machine_id() -> str:
    """Generate a unique machine fingerprint from hostname + platform."""
    try:
        raw = f"{platform.node()}|{platform.system()}|{platform.processor()}|{uuid.getnode()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
    except Exception:
        return "unknown-machine"


def get_install_id() -> str:
    """Stable machine identifier shown to customer for manual activation."""
    machine_hash = _get_machine_id()
    return f"SMPOS-{machine_hash[:8].upper()}-{machine_hash[8:16].upper()}-{machine_hash[16:24].upper()}"


def _derive_activation_key(install_id: str) -> str:
    digest = hmac.new(
        ACTIVATION_SECRET.encode(),
        install_id.strip().upper().encode(),
        hashlib.sha256,
    ).hexdigest().upper()
    return f"SMPOS-{digest[:8]}-{digest[8:16]}-{digest[16:24]}"


def _activation_file_payload() -> dict:
    now = datetime.now()
    return {
        "install_id": get_install_id(),
        "machine_id": _get_machine_id(),
        "trial_started_at": now.strftime("%Y-%m-%d"),
        "trial_active": True,
        "trial_days_left": TRIAL_DAYS,
        "activated": False,
        "activation_key": "",
        "activated_at": None,
    }


def _load_activation_data() -> dict:
    try:
        with open(ACTIVATION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_activation_data(data: dict) -> None:
    os.makedirs(LICENSE_DIR, exist_ok=True)
    with open(ACTIVATION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def ensure_activation_initialized() -> dict:
    data = _load_activation_data()
    install_id = get_install_id()
    changed = False
    if not data:
        data = _activation_file_payload()
        changed = True
    if not data.get("install_id"):
        data["install_id"] = install_id
        changed = True
    if not data.get("machine_id"):
        data["machine_id"] = _get_machine_id()
        changed = True
    if not data.get("trial_started_at"):
        data["trial_started_at"] = datetime.now().strftime("%Y-%m-%d")
        changed = True
    if "trial_active" not in data:
        data["trial_active"] = not bool(data.get("activated"))
        changed = True
    if "trial_days_left" not in data:
        data["trial_days_left"] = 0 if data.get("activated") else _trial_days_left(data.get("trial_started_at") or "")
        changed = True
    if changed:
        _save_activation_data(data)
    return data


def _trial_days_left(trial_started_at: str) -> int:
    try:
        started = datetime.strptime(trial_started_at, "%Y-%m-%d").date()
    except Exception:
        started = datetime.now().date()
    expires_on = started + timedelta(days=TRIAL_DAYS)
    return max(0, (expires_on - datetime.now().date()).days)


def activation_status() -> dict:
    data = ensure_activation_initialized()
    install_id = data.get("install_id") or get_install_id()
    machine_id = data.get("machine_id") or _get_machine_id()
    trial_started_at = data.get("trial_started_at") or datetime.now().strftime("%Y-%m-%d")
    activated = bool(data.get("activated"))
    key = (data.get("activation_key") or "").strip().upper()
    expected_key = _derive_activation_key(install_id)
    if activated:
        if key != expected_key or machine_id != _get_machine_id():
            activated = False
            data["activated"] = False
            data["activation_key"] = ""
            _save_activation_data(data)
    if activated:
        days_left = 0
        trial_active = False
        trial_expired = False
    else:
        days_left = _trial_days_left(trial_started_at)
        trial_active = days_left > 0
        trial_expired = days_left <= 0
    if data.get("trial_days_left") != days_left or data.get("trial_active") != trial_active:
        data["trial_days_left"] = days_left
        data["trial_active"] = trial_active
        _save_activation_data(data)
    requires_activation = (not activated) and trial_expired
    return {
        "activated": activated,
        "requires_activation": requires_activation,
        "license_type": "Lifetime" if activated else "Trial",
        "trial_active": trial_active,
        "trial_days_total": TRIAL_DAYS,
        "trial_days_left": days_left,
        "trial_started_at": trial_started_at,
        "trial_expired": trial_expired,
        "install_id": install_id,
        "machine_id": machine_id,
        "activated_at": data.get("activated_at"),
        "owner_contact": OWNER_CONTACT,
    }


def verify_offline_activation_key(key: str) -> bool:
    value = (key or "").strip().upper()
    if not OFFLINE_ACTIVATION_RE.fullmatch(value):
        return False
    install_id = get_install_id()
    return value == _derive_activation_key(install_id)


def activate_offline(key: str) -> dict:
    value = (key or "").strip().upper()
    if not OFFLINE_ACTIVATION_RE.fullmatch(value):
        return {
            "ok": False,
            "msg": "Invalid activation key format. Expected: SMPOS-XXXXXXXX-XXXXXXXX-XXXXXXXX",
        }
    if not verify_offline_activation_key(value):
        return {
            "ok": False,
            "msg": "Activation key is invalid for this installation ID.",
        }
    data = ensure_activation_initialized()
    data["activated"] = True
    data["activation_key"] = value
    data["activated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["trial_active"] = False
    data["trial_days_left"] = 0
    data["install_id"] = get_install_id()
    data["machine_id"] = _get_machine_id()
    _save_activation_data(data)
    status = activation_status()
    return {"ok": True, "msg": "Activation successful.", "status": status}


def activate(key: str, customer_name: str = "") -> dict:
    """Compatibility wrapper to keep existing call sites working."""
    _ = customer_name
    return activate_offline(key)


def load() -> dict | None:
    """Load saved activation data."""
    data = ensure_activation_initialized()
    return data if data else None


def check() -> dict:
    """Compatibility wrapper mapped to unified offline activation state."""
    status = activation_status()
    allowed = not status.get("requires_activation", True)
    if status.get("activated"):
        msg = "Activated"
    elif allowed:
        msg = f"Trial mode ({status.get('trial_days_left', 0)} day(s) left)"
    else:
        msg = "Trial expired. Activation required."
    return {"licensed": allowed, "msg": msg, "info": status}


def deactivate():
    """Reset activation state to trial mode (for testing / uninstall)."""
    data = ensure_activation_initialized()
    data["activated"] = False
    data["activation_key"] = ""
    data["activated_at"] = None
    data["trial_started_at"] = datetime.now().strftime("%Y-%m-%d")
    data["trial_active"] = True
    data["trial_days_left"] = TRIAL_DAYS
    _save_activation_data(data)


def days_remaining(data: dict) -> int | None:
    """Remaining trial days for non-activated installations."""
    if not data or data.get("activated"):
        return None
    return _trial_days_left(data.get("trial_started_at") or "")
