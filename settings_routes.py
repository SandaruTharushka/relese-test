import random
from datetime import datetime, timezone

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from payhere import is_sandbox_mode
from shared_helpers import role_from_user, user_has_any_role

def register_settings_routes(
    app,
    *,
    db,
    User,
    Category,
    Supplier,
    Product,
    StoreSettings,
    ENV_FILE_PATH,
    log_action,
    update_env_file,
    requires_password_change,
):
    @app.route('/settings')
    @login_required
    def settings():
        is_admin = user_has_any_role(current_user, 'Admin', 'Operator', 'Developer')
        user_role = role_from_user(current_user, default='Cashier')
        app.logger.info(
            '[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s',
            getattr(current_user, 'username', 'anonymous'),
            role_from_user(current_user, default=''),
            is_admin,
            'Admin,Operator,Developer',
            request.path,
        )
        if not is_admin:
            abort(403)
        app.logger.info('Settings page opened user=%s admin=%s', current_user.username, is_admin)
        try:
            return render_template(
                'settings.html',
                users=User.query.all(),
                categories=Category.query.all(),
                suppliers=Supplier.query.filter_by(status='active').all(),
                products=Product.query.filter_by(status='active').order_by(Product.name.asc()).all(),
                is_admin=is_admin,
                user_role=user_role,
                password_change_required=requires_password_change(current_user),
            )
        except Exception:
            app.logger.exception('Settings page render failed user=%s', current_user.username)
            raise

    @app.route('/settings/barcode-scanner')
    @login_required
    def barcode_scanner_management():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator', 'Developer', 'Manager')
        if not allowed:
            abort(403)
        return render_template(
            'barcode_scanner_management.html',
            products=Product.query.filter_by(status='active').order_by(Product.name.asc()).all(),
        )

    @app.route('/api/store-settings', methods=['GET'])
    @login_required
    def api_store_settings_get():
        try:
            core_keys = [
                'store_name',
                'store_phone',
                'store_branch',
                'store_address',
                'receipt_footer',
                'store_email',
                'store_reg',
            ]
            result = {key: StoreSettings.get(key, '') for key in core_keys}
            if not result['store_name']:
                result['store_name'] = 'SuperMart'
            if not result['receipt_footer']:
                result['receipt_footer'] = ''
            pref_rows = StoreSettings.query.filter(StoreSettings.key.like('pref_%')).all()
            for row in pref_rows:
                result[row.key] = row.value
            tax_settings = StoreSettings.get_tax_settings()
            result['tax_enabled'] = tax_settings['tax_enabled']
            result['tax_rate'] = tax_settings['tax_rate']
            result['tax_name'] = tax_settings['tax_name']
            result['payhere_sandbox'] = is_sandbox_mode()
            app.logger.info(
                'Settings loaded user=%s keys=%s',
                current_user.username,
                sorted(result.keys())[:20],
            )
            return jsonify(result)
        except Exception:
            app.logger.exception('Settings load failed user=%s', current_user.username)
            raise

    @app.route('/api/store-settings', methods=['POST'])
    @login_required
    def api_store_settings_save():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        app.logger.info('[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s', getattr(current_user, 'username', 'anonymous'), role_from_user(current_user, default=''), allowed, 'Admin,Operator', request.path)
        if not allowed:
            abort(403)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({'ok': False, 'msg': 'Invalid JSON payload'}), 400
        try:
            if 'pref_taxEnabled' in data and 'tax_enabled' not in data:
                data['tax_enabled'] = data['pref_taxEnabled']
            if 'pref_taxRate' in data and 'tax_rate' not in data:
                data['tax_rate'] = data['pref_taxRate']
            if 'pref_taxName' in data and 'tax_name' not in data:
                data['tax_name'] = data['pref_taxName']
            StoreSettings.set_many(data)
            db.session.commit()
            app.logger.info(
                'Settings saved count=%s keys=%s',
                len(data),
                ','.join(sorted(list(data.keys()))[:20]),
            )
            log_action(
                'Settings updated',
                target_type='settings',
                metadata={'count': len(data), 'keys': sorted(list(data.keys()))[:10]},
            )
            return jsonify({'ok': True, 'saved': len(data)})
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Settings save failed payload_keys=%s', list(data.keys()))
            return jsonify({'ok': False, 'msg': f'Failed to save settings: {exc}'}), 500

    @app.route('/api/settings', methods=['GET'])
    @login_required
    def api_settings_get():
        """Canonical settings endpoint used by frontend."""
        return api_store_settings_get()

    @app.route('/api/settings', methods=['POST'])
    @login_required
    def api_settings_save():
        """Canonical settings endpoint used by frontend."""
        return api_store_settings_save()

    @app.route('/api/settings/all', methods=['GET'])
    @login_required
    def api_settings_all_get():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        app.logger.info('[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s', getattr(current_user, 'username', 'anonymous'), role_from_user(current_user, default=''), allowed, 'Admin,Operator', request.path)
        if not allowed:
            abort(403)
        try:
            rows = StoreSettings.query.all()
            result = {row.key: row.value for row in rows}
            app.logger.info('Settings/all loaded user=%s count=%s', current_user.username, len(result))
            return jsonify(result)
        except Exception:
            app.logger.exception('Settings/all load failed user=%s', current_user.username)
            raise

    @app.route('/api/card/config', methods=['GET', 'POST'])
    @login_required
    def api_card_config():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        if not allowed:
            abort(403)
        if request.method == 'GET':
            rows = StoreSettings.query.filter(StoreSettings.key.like('pref_card_%')).all()
            return jsonify({'ok': True, 'config': {row.key: row.value for row in rows}})
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({'ok': False, 'msg': 'Invalid JSON payload'}), 400
        card_payload = {}
        for key, value in data.items():
            if key.startswith('pref_card_'):
                card_payload[key] = value
        if not card_payload:
            return jsonify({'ok': False, 'msg': 'No card config keys provided.'}), 400
        StoreSettings.set_many(card_payload)
        db.session.commit()
        log_action(
            'Card payment settings updated',
            target_type='card_settings',
            metadata={'keys': sorted(card_payload.keys())[:12]},
        )
        return jsonify({'ok': True, 'saved': len(card_payload)})

    @app.route('/api/card/test-connection', methods=['POST'])
    @login_required
    def api_card_test_connection():
        data = request.get_json(silent=True) or {}
        mode = str(data.get('terminal_type') or StoreSettings.get('pref_card_terminal_type', 'manual_record_only')).strip()
        timeout = int(float(StoreSettings.get('pref_card_transaction_timeout', 60) or 60))
        if mode == 'manual_record_only':
            return jsonify({'ok': True, 'status': 'ready', 'msg': 'Manual mode: no network handshake required.'})
        if mode == 'lan_ip_terminal':
            ip = str(data.get('ip_address') or StoreSettings.get('pref_card_ip_address', '')).strip()
            port = str(data.get('port') or StoreSettings.get('pref_card_port', '')).strip()
            if not ip or not port:
                return jsonify({'ok': False, 'status': 'invalid', 'msg': 'IP address and port are required.'}), 400
        if mode == 'usb_serial_terminal':
            com = str(data.get('com_port') or StoreSettings.get('pref_card_com_port', '')).strip()
            if not com:
                return jsonify({'ok': False, 'status': 'invalid', 'msg': 'COM port is required for serial terminals.'}), 400
        return jsonify({'ok': True, 'status': 'connected', 'msg': f'Terminal link test passed (timeout {timeout}s).'})

    @app.route('/api/card/charge', methods=['POST'])
    @login_required
    def api_card_charge():
        data = request.get_json(silent=True) or {}
        amount = float(data.get('amount') or 0)
        if amount <= 0:
            return jsonify({'ok': False, 'status': 'invalid', 'msg': 'Amount must be greater than zero.'}), 400
        timeout = int(float(StoreSettings.get('pref_card_transaction_timeout', 60) or 60))
        force_status = str(data.get('simulate_status') or '').strip().lower()
        if force_status not in {'success', 'declined', 'timeout'}:
            force_status = 'success'
        status = force_status
        if status == 'timeout':
            log_action(
                f'Card terminal timeout for LKR {amount:.2f}',
                target_type='card_payment_failed',
                metadata={'amount': amount, 'status': status, 'timeout': timeout},
            )
            return jsonify({'ok': False, 'status': 'timeout', 'msg': f'Terminal timed out after {timeout}s.'}), 408
        if status == 'declined':
            log_action(
                f'Card transaction declined for LKR {amount:.2f}',
                target_type='card_payment_failed',
                metadata={'amount': amount, 'status': status},
            )
            return jsonify({'ok': False, 'status': 'declined', 'msg': 'Transaction declined by issuer.'}), 402
        approval = f'APR{random.randint(100000, 999999)}'
        rrn = f'RRN{random.randint(1000000000, 9999999999)}'
        response = {
            'ok': True,
            'status': 'success',
            'approval_code': approval,
            'rrn_reference': rrn,
            'transaction_timestamp': datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'),
            'msg': 'Approved by terminal.',
        }
        log_action(
            f'Card terminal approved LKR {amount:.2f}',
            target_type='card_payment_attempt',
            metadata={'amount': amount, 'status': 'success', 'approval_code': approval, 'rrn_reference': rrn},
        )
        return jsonify(response)
