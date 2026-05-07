from datetime import datetime, timezone

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from payhere import is_sandbox_mode
from shared_helpers import role_from_user, user_has_any_role
from version import APP_VERSION


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
    from services.settings_service import get_settings_service
    from services.card_terminal_service import get_card_terminal_service, CardConfig
    from decimal import Decimal, InvalidOperation

    _settings_svc = get_settings_service()
    _card_svc = get_card_terminal_service()

    def _safe_amount(raw, label='Amount') -> Decimal:
        if raw in (None, ''):
            raise ValueError(f'{label} is required.')
        try:
            v = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f'{label} must be a valid number.')
        if v <= 0:
            raise ValueError(f'{label} must be greater than zero.')
        return v

    # ── Pages ─────────────────────────────────────────────────────────────────

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
                users=User.query.order_by(User.username.asc()).all(),
                categories=Category.query.order_by(Category.name.asc()).all(),
                suppliers=Supplier.query.filter_by(status='active').order_by(Supplier.name.asc()).limit(500).all(),
                products=Product.query.filter_by(status='active').order_by(Product.name.asc()).limit(500).all(),
                is_admin=is_admin,
                user_role=user_role,
                password_change_required=requires_password_change(current_user),
                app_version=APP_VERSION,
                card_terminal_status=_card_svc.status_dict(),
            )
        except Exception:
            app.logger.exception('Settings page render failed user=%s', current_user.username)
            raise

    # ── Store settings API ────────────────────────────────────────────────────

    @app.route('/api/store-settings', methods=['GET'])
    @login_required
    def api_store_settings_get():
        try:
            all_settings = _settings_svc.get_all()
            core_keys = [
                'store_name', 'store_phone', 'store_branch', 'store_address',
                'receipt_footer', 'store_email', 'store_reg',
            ]
            result = {k: all_settings.get(k, '') for k in core_keys}
            if not result['store_name']:
                result['store_name'] = 'Garage'
            # Include all pref_ keys
            for k, v in all_settings.items():
                if k.startswith('pref_'):
                    result[k] = v
            tax_settings = _settings_svc.get_tax_settings()
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
        app.logger.info(
            '[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s',
            getattr(current_user, 'username', 'anonymous'),
            role_from_user(current_user, default=''),
            allowed, 'Admin,Operator', request.path,
        )
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
            _settings_svc.invalidate()
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
        return api_store_settings_get()

    @app.route('/api/settings', methods=['POST'])
    @login_required
    def api_settings_save():
        return api_store_settings_save()

    @app.route('/api/settings/all', methods=['GET'])
    @login_required
    def api_settings_all_get():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        app.logger.info(
            '[RBAC] user=%s role=%s allowed=%s allowed_roles=%s path=%s',
            getattr(current_user, 'username', 'anonymous'),
            role_from_user(current_user, default=''),
            allowed, 'Admin,Operator', request.path,
        )
        if not allowed:
            abort(403)
        try:
            result = _settings_svc.get_all()
            app.logger.info('Settings/all loaded user=%s count=%s', current_user.username, len(result))
            return jsonify(result)
        except Exception:
            app.logger.exception('Settings/all load failed user=%s', current_user.username)
            raise

    # ── Card terminal API ─────────────────────────────────────────────────────

    @app.route('/api/card/config', methods=['GET', 'POST'])
    @login_required
    def api_card_config():
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        if not allowed:
            abort(403)
        if request.method == 'GET':
            card_settings = _settings_svc.get_all(prefix='pref_card_')
            return jsonify({'ok': True, 'config': card_settings})
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({'ok': False, 'msg': 'Invalid JSON payload'}), 400
        card_payload = {k: v for k, v in data.items() if k.startswith('pref_card_')}
        if not card_payload:
            return jsonify({'ok': False, 'msg': 'No card config keys provided.'}), 400
        StoreSettings.set_many(card_payload)
        db.session.commit()
        _settings_svc.invalidate()
        # Reconfigure card terminal with updated settings
        fresh_cfg = _settings_svc.get_all(prefix='pref_card_')
        _card_svc.configure(CardConfig.from_settings(fresh_cfg))
        log_action(
            'Card payment settings updated',
            target_type='card_settings',
            metadata={'keys': sorted(card_payload.keys())[:12]},
        )
        return jsonify({'ok': True, 'saved': len(card_payload)})

    @app.route('/api/card/status', methods=['GET'])
    @login_required
    def api_card_status():
        """Real-time card terminal connection status."""
        return jsonify({'ok': True, **_card_svc.status_dict()})

    @app.route('/api/card/connect', methods=['POST'])
    @login_required
    def api_card_connect():
        """Manually trigger card terminal connection."""
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        if not allowed:
            abort(403)
        fresh_cfg = _settings_svc.get_all(prefix='pref_card_')
        _card_svc.configure(CardConfig.from_settings(fresh_cfg))
        ok, msg = _card_svc.connect()
        return jsonify({'ok': ok, 'status': _card_svc.status.value, 'msg': msg})

    @app.route('/api/card/disconnect', methods=['POST'])
    @login_required
    def api_card_disconnect():
        """Disconnect the card terminal."""
        allowed = user_has_any_role(current_user, 'Admin', 'Operator')
        if not allowed:
            abort(403)
        _card_svc.disconnect()
        return jsonify({'ok': True, 'status': 'disconnected', 'msg': 'Terminal disconnected.'})

    @app.route('/api/card/test-connection', methods=['POST'])
    @login_required
    def api_card_test_connection():
        data = request.get_json(silent=True) or {}
        # Build a temporary config from request overrides + stored settings
        stored = _settings_svc.get_all(prefix='pref_card_')
        merged = {**stored}
        # Allow request to override individual fields for test
        for field in ('terminal_type', 'ip_address', 'port', 'com_port'):
            req_val = data.get(field)
            if req_val:
                merged[f'pref_card_{field}'] = req_val
        test_cfg = CardConfig.from_settings(merged)
        ok, msg = test_cfg and _build_test_connection(test_cfg)
        return jsonify({'ok': ok, 'status': 'connected' if ok else 'error', 'msg': msg})

    def _build_test_connection(cfg: 'CardConfig'):
        from services.card_terminal_service import TerminalType
        import socket
        if cfg.terminal_type == TerminalType.MANUAL:
            return True, 'Manual mode: no physical connection test needed.'
        if cfg.terminal_type == TerminalType.LAN:
            if not cfg.ip_address:
                return False, 'IP address is required for LAN terminal.'
            if cfg.tcp_port <= 0:
                return False, 'Valid TCP port is required.'
            try:
                with socket.create_connection((cfg.ip_address, cfg.tcp_port), timeout=5):
                    pass
                return True, f'Successfully connected to {cfg.ip_address}:{cfg.tcp_port}'
            except socket.timeout:
                return False, f'Connection timed out to {cfg.ip_address}:{cfg.tcp_port}'
            except ConnectionRefusedError:
                return False, f'Connection refused by {cfg.ip_address}:{cfg.tcp_port}'
            except OSError as exc:
                return False, f'Network error: {exc}'
        if cfg.terminal_type == TerminalType.SERIAL:
            if not cfg.com_port:
                return False, 'COM port is required for serial terminal.'
            try:
                import serial  # type: ignore
                s = serial.Serial(port=cfg.com_port, baudrate=cfg.baud_rate, timeout=2)
                s.close()
                return True, f'Successfully opened {cfg.com_port}'
            except ImportError:
                return False, 'pyserial not installed. Run: pip install pyserial'
            except Exception as exc:
                return False, f'Serial error: {exc}'
        return False, 'Unknown terminal type.'

    @app.route('/api/card/charge', methods=['POST'])
    @login_required
    def api_card_charge():
        data = request.get_json(silent=True) or {}
        try:
            amount = _safe_amount(data.get('amount'), 'Amount')
        except ValueError as exc:
            return jsonify({'ok': False, 'status': 'invalid', 'msg': str(exc)}), 400

        # Ensure terminal is configured with latest settings
        fresh_cfg = _settings_svc.get_all(prefix='pref_card_')
        _card_svc.configure(CardConfig.from_settings(fresh_cfg))

        result = _card_svc.send_payment(amount)

        if result.status == 'timeout':
            log_action(
                f'Card terminal timeout for LKR {float(amount):.2f}',
                target_type='card_payment_failed',
                metadata={'amount': float(amount), 'status': result.status},
            )
            return jsonify({'ok': False, 'status': 'timeout', 'msg': result.message}), 408

        if result.status == 'declined':
            log_action(
                f'Card transaction declined for LKR {float(amount):.2f}',
                target_type='card_payment_failed',
                metadata={'amount': float(amount), 'status': result.status},
            )
            return jsonify({'ok': False, 'status': 'declined', 'msg': result.message}), 402

        if not result.ok:
            log_action(
                f'Card terminal error for LKR {float(amount):.2f}',
                target_type='card_payment_failed',
                metadata={'amount': float(amount), 'status': result.status, 'msg': result.message},
            )
            return jsonify({'ok': False, 'status': result.status, 'msg': result.message}), 500

        log_action(
            f'Card terminal approved LKR {float(amount):.2f}',
            target_type='card_payment_attempt',
            metadata={
                'amount': float(amount), 'status': result.status,
                'approval_code': result.approval_code,
                'rrn_reference': result.rrn_reference,
            },
        )
        return jsonify({
            'ok': True,
            'status': result.status,
            'approval_code': result.approval_code,
            'rrn_reference': result.rrn_reference,
            'card_type': result.card_type,
            'card_last4': result.card_last4,
            'terminal_id': result.terminal_id,
            'transaction_timestamp': result.transaction_timestamp,
            'msg': result.message,
        })

    @app.route('/api/migrations/status', methods=['GET'])
    @login_required
    def api_migration_status():
        """Return database migration status (Admin only)."""
        if not user_has_any_role(current_user, 'Admin', 'Developer'):
            abort(403)
        try:
            from services.migrations import migration_status
            status = migration_status(db.session)
            return jsonify({'ok': True, 'migrations': status})
        except Exception as exc:
            app.logger.exception('Migration status check failed')
            return jsonify({'ok': False, 'msg': str(exc)}), 500
