"""Printing settings routes — canonical settings API for all print products.

Replaces scattered printer settings endpoints across printer_routes.py.
All endpoints use the /api/printing/settings/* namespace.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from services.printing.domain.constants import (
    LABEL_PRINTER_KEYS,
    RECEIPT_LAYOUT_KEYS,
    RECEIPT_PRINTER_KEYS,
    SCANNER_KEYS,
    SERVICE_RECEIPT_LAYOUT_KEYS,
)


def register_printing_settings_routes(
    app,
    *,
    db,
    StoreSettings,
    printer_service,
    log_action,
    user_has_any_role,
) -> None:

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    def _load(keys, defaults_map):
        return {k: str(StoreSettings.get(k, defaults_map.get(k, '')) or defaults_map.get(k, '')) for k in keys}

    def _save_keys(request_data, allowed_keys, label):
        payload = {k: v for k, v in (request_data or {}).items() if k in set(allowed_keys)}
        if not payload:
            return jsonify({'ok': False, 'msg': f'No recognized {label} keys provided'}), 400
        try:
            StoreSettings.set_many(payload)
            db.session.commit()
            log_action(f'{label} updated', target_type='printer_settings', metadata={'keys': sorted(payload.keys())})
            return jsonify({'ok': True, 'saved': len(payload)}), 200
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to save {label}: {exc}'}), 500

    def _save_layout_with_version(request_data, allowed_keys, label, version_prefix: str):
        """Save layout keys and bump the layout version counter + timestamp."""
        from datetime import datetime, timezone
        payload = {k: v for k, v in (request_data or {}).items() if k in set(allowed_keys)}
        if not payload:
            return jsonify({'ok': False, 'msg': f'No recognized {label} keys provided'}), 400
        try:
            StoreSettings.set_many(payload)

            # Bump version counter and record timestamp
            version_key = f'{version_prefix}_layout_version'
            updated_key = f'{version_prefix}_layout_updated_at'
            try:
                current_version = int(StoreSettings.get(version_key, '0') or '0')
            except (TypeError, ValueError):
                current_version = 0
            StoreSettings.set(version_key, str(current_version + 1))
            StoreSettings.set(updated_key, datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))

            db.session.commit()
            log_action(
                f'{label} updated',
                target_type='printer_settings',
                metadata={'keys': sorted(payload.keys()), 'version': current_version + 1},
            )
            return jsonify({
                'ok': True,
                'saved': len(payload),
                version_key: current_version + 1,
            }), 200
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to save {label}: {exc}'}), 500

    # ── Receipt printer hardware settings ────────────────────────
    @app.route('/api/printing/settings/receipt', methods=['GET', 'POST'])
    @login_required
    def api_printing_settings_receipt():
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import RECEIPT_PRINTER_DEFAULTS
            return jsonify({'ok': True, 'settings': _load(RECEIPT_PRINTER_KEYS, RECEIPT_PRINTER_DEFAULTS)})
        return _save_keys(request.get_json(silent=True), RECEIPT_PRINTER_KEYS, 'receipt printer settings')

    # ── Receipt layout settings (sales receipts) ─────────────────
    @app.route('/api/printing/settings/receipt-layout', methods=['GET', 'POST'])
    @login_required
    def api_printing_settings_receipt_layout():
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS
            settings = _load(RECEIPT_LAYOUT_KEYS, RECEIPT_LAYOUT_DEFAULTS)
            settings['receipt_layout_version'] = str(StoreSettings.get('receipt_layout_version', '0') or '0')
            settings['receipt_layout_updated_at'] = str(StoreSettings.get('receipt_layout_updated_at', '') or '')
            return jsonify({'ok': True, 'settings': settings})
        return _save_layout_with_version(
            request.get_json(silent=True), RECEIPT_LAYOUT_KEYS, 'receipt layout settings', 'receipt'
        )

    # ── Service receipt layout settings (job/service receipts) ───
    @app.route('/api/printing/settings/service-receipt-layout', methods=['GET', 'POST'])
    @login_required
    def api_printing_settings_service_receipt_layout():
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import SERVICE_RECEIPT_LAYOUT_DEFAULTS
            settings = _load(SERVICE_RECEIPT_LAYOUT_KEYS, SERVICE_RECEIPT_LAYOUT_DEFAULTS)
            settings['service_receipt_layout_version'] = str(StoreSettings.get('service_receipt_layout_version', '0') or '0')
            settings['service_receipt_layout_updated_at'] = str(StoreSettings.get('service_receipt_layout_updated_at', '') or '')
            return jsonify({'ok': True, 'settings': settings})
        return _save_layout_with_version(
            request.get_json(silent=True), SERVICE_RECEIPT_LAYOUT_KEYS, 'service receipt layout settings', 'service_receipt'
        )

    # ── Label printer settings ────────────────────────────────────
    @app.route('/api/printing/settings/label', methods=['GET', 'POST'])
    @login_required
    def api_printing_settings_label():
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import LABEL_PRINTER_DEFAULTS
            return jsonify({'ok': True, 'settings': _load(LABEL_PRINTER_KEYS, LABEL_PRINTER_DEFAULTS)})
        return _save_keys(request.get_json(silent=True), LABEL_PRINTER_KEYS, 'label printer settings')

    # ── Scanner / barcode input settings ─────────────────────────
    @app.route('/api/printing/settings/scanner', methods=['GET', 'POST'])
    @login_required
    def api_printing_settings_scanner():
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import SCANNER_DEFAULTS
            return jsonify({'ok': True, 'settings': _load(SCANNER_KEYS, SCANNER_DEFAULTS)})
        data = request.get_json(silent=True) or {}
        payload = {k: v for k, v in data.items() if k in set(SCANNER_KEYS)}
        if not payload:
            return jsonify({'ok': False, 'msg': 'No recognized scanner settings keys provided'}), 400
        try:
            StoreSettings.set_many(payload)
            # Clean up legacy label_printer_selection shadow key (scanner MUST NOT own label config)
            if StoreSettings.get('label_printer_selection', ''):
                StoreSettings.set('label_printer_selection', '')
            db.session.commit()
            log_action('Scanner settings updated', target_type='scanner_settings', metadata={'keys': sorted(payload.keys())})
            return jsonify({'ok': True, 'saved': len(payload)}), 200
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to save scanner settings: {exc}'}), 500

    # ── Printer list ──────────────────────────────────────────────
    @app.route('/api/printing/printers', methods=['GET'])
    @login_required
    def api_printing_printers_list():
        _require_admin()
        try:
            items = printer_service.list_printers_detailed()
            clean = [
                {
                    'name': str(e.get('name') or '').strip(),
                    'is_default': bool(e.get('is_default')),
                    'status': str(e.get('status') or 'unknown'),
                    'printer_type': str(e.get('printer_type') or 'unknown'),
                    'port_type': str(e.get('port_type') or 'unknown'),
                }
                for e in items if str(e.get('name') or '').strip()
            ]
            default = printer_service.get_default_printer([c['name'] for c in clean])
            return jsonify({'ok': True, 'printers': [c['name'] for c in clean], 'default': default, 'items': clean})
        except Exception:
            app.logger.exception('Printer list enumeration failed')
            return jsonify({'ok': True, 'printers': [], 'default': '', 'items': []})

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES (redirect to new paths)
    # Retained until UI is updated; removed in Phase 11.
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/printer/settings', methods=['GET', 'POST'])
    @login_required
    def api_printer_settings_compat():
        """Legacy compat for /api/printer/settings — delegates to new endpoint."""
        _require_admin()
        if request.method == 'GET':
            from services.printing.domain.constants import RECEIPT_PRINTER_DEFAULTS, LABEL_PRINTER_DEFAULTS
            all_keys = RECEIPT_PRINTER_KEYS + LABEL_PRINTER_KEYS
            all_defaults = {**RECEIPT_PRINTER_DEFAULTS, **LABEL_PRINTER_DEFAULTS}
            return jsonify({'ok': True, 'settings': _load(all_keys, all_defaults)})
        data = request.get_json(silent=True) or {}
        from services.printing.domain.constants import RECEIPT_PRINTER_DEFAULTS, LABEL_PRINTER_DEFAULTS
        allowed = set(RECEIPT_PRINTER_KEYS + LABEL_PRINTER_KEYS)
        payload = {k: v for k, v in data.items() if k in allowed}
        if not payload:
            return jsonify({'ok': False, 'msg': 'No recognized printer settings keys provided'}), 400
        try:
            StoreSettings.set_many(payload)
            db.session.commit()
            log_action('Printer settings updated (compat)', target_type='printer_settings', metadata={'keys': sorted(payload.keys())})
            from services.printing.settings_service import PrintSettingsService
            settings_svc = PrintSettingsService(StoreSettings)
            receipt_cfg = printer_service.normalize_receipt_config(settings_svc.load_receipt())
            label_cfg = printer_service.normalize_label_config(settings_svc.load_label())
            receipt_status = printer_service.build_receipt_status(receipt_cfg)
            label_status = printer_service.build_label_status(label_cfg)
            validation_ok = bool(receipt_status.get('connected'))
            return jsonify({
                'ok': True,
                'saved': len(payload),
                'validation_ok': validation_ok,
                'receipt': receipt_status,
                'label': label_status,
            })
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to save: {exc}'}), 500

    @app.route('/api/receipt-printer/settings', methods=['GET', 'POST'])
    @login_required
    def api_receipt_printer_settings_compat():
        return api_printing_settings_receipt()

    @app.route('/api/receipt-layout/settings', methods=['GET', 'POST'])
    @login_required
    def api_receipt_layout_settings_compat():
        return api_printing_settings_receipt_layout()

    @app.route('/api/barcode-label/settings', methods=['GET', 'POST'])
    @login_required
    def api_barcode_label_settings_compat():
        return api_printing_settings_label()

    @app.route('/api/scanner/settings', methods=['GET', 'POST'])
    @login_required
    def api_scanner_settings_compat():
        return api_printing_settings_scanner()

    @app.route('/api/barcode-scanner/config', methods=['GET', 'POST'])
    @login_required
    def api_barcode_scanner_config_compat():
        return api_printing_settings_scanner()
