from __future__ import annotations

import secrets


# Password used for the post-reset first admin account.
# Must NOT contain any substring from: username ('newadmin'), email parts
# ('setup', 'pos', 'test'), or full_name parts ('setup', 'admin') — because
# the server-side identity-token check in validate_password_strength() rejects
# passwords that include the user's own username / email / name.
_FIRST_ADMIN_PASSWORD = 'Bz4kQp7nXm1W!'


def _create_admin_user(db, User, *, username='rootadmin', password='AdminPass123!'):
    admin = User(
        username=username,
        full_name='Root Admin',
        email=f'{username}@example.com',
        role='Admin',
        is_admin=True,
        is_owner=True,
        is_primary_admin=True,
        status='active',
        force_password_change=False,
        session_token=secrets.token_urlsafe(32),
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return admin


def _authenticate_session(client, user):
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
        session['_id'] = secrets.token_urlsafe(24)
        session['_session_token'] = user.session_token


def test_factory_reset_first_user_becomes_owner_and_full_admin(flask_app):
    from models import StoreSettings, User, db

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        owner = _create_admin_user(db, User)
        db.session.add(StoreSettings(key='security_first_run_pending', value='0'))
        db.session.add(StoreSettings(key='primary_admin_user_id', value=str(owner.id)))
        db.session.commit()

    original_destructive = flask_app.config.get('ENABLE_DESTRUCTIVE_ADMIN_TOOLS')
    flask_app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = True
    try:
        with flask_app.test_client() as client:
            with flask_app.app_context():
                owner = User.query.filter_by(username='rootadmin').first()
                assert owner is not None
            _authenticate_session(client, owner)

            # Test case 1: factory reset -> first user becomes admin
            reset_response = client.post('/api/admin/factory-reset', json={'confirm': 'FACTORY_RESET'})
            assert reset_response.status_code == 200
            payload = reset_response.get_json()
            assert payload['ok'] is True

            with flask_app.app_context():
                assert User.query.count() == 0
                assert StoreSettings.get('security_first_run_pending', '0') == '1'
                assert StoreSettings.get('primary_admin_user_id', 'x') == ''

        # After reset the setup page must be reachable by an unauthenticated visitor
            setup_page = client.get('/setup-admin')
            assert setup_page.status_code == 200

        # Welcome message must appear on the first-run setup page
            assert b'first setup' in setup_page.data or b'first account' in setup_page.data or b'FIRST-RUN' in setup_page.data

            setup_response = client.post(
            '/setup-admin',
            json={
                'shop_name': 'Test Shop',
                'full_name': 'New Admin',
                'email': 'setup@pos.test',
                'username': 'newadmin',
                'password': _FIRST_ADMIN_PASSWORD,
                'confirm_password': _FIRST_ADMIN_PASSWORD,
            },
        )
            assert setup_response.status_code == 200, setup_response.get_data(as_text=True)
            assert setup_response.get_json()['ok'] is True

            with flask_app.app_context():
                first_user = User.query.filter_by(username='newadmin').first()
                assert first_user is not None
                assert first_user.role == 'Admin'
                assert first_user.is_admin is True
                assert first_user.is_owner is True
                assert first_user.is_primary_admin is True
                assert StoreSettings.get('primary_admin_user_id', '') == str(first_user.id)
                # First-run flag must be cleared after setup completes
                assert StoreSettings.get('security_first_run_pending', '1') == '0'

            # Re-authenticate as the newly created admin (factory reset cleared the old session)
            with flask_app.app_context():
                new_admin = User.query.filter_by(username='newadmin').first()
                assert new_admin is not None
            _authenticate_session(client, new_admin)

        # Data safety: duplicate "original admin" setup must be blocked
        duplicate_setup = client.post(
            '/setup-admin',
            json={
                'full_name': 'Duplicate Admin',
                'email': 'dup@pos.test',
                'username': 'dupadmin',
                'password': _FIRST_ADMIN_PASSWORD,
                'confirm_password': _FIRST_ADMIN_PASSWORD,
            },
        )
        assert duplicate_setup.status_code == 409
        duplicate_payload = duplicate_setup.get_json()
        assert duplicate_payload['ok'] is False
        assert duplicate_payload['code'] in ('SETUP_ALREADY_COMPLETED', 'PRIMARY_ADMIN_ALREADY_EXISTS')

        # Test case 2: admin can save settings
        save_settings = client.post('/api/settings', json={'store_name': 'Fresh POS'})
        assert save_settings.status_code == 200
        assert save_settings.get_json()['ok'] is True

        # Test case 3: admin can create users
        # Note: password must not contain identity tokens derived from username,
        # email, or full_name — 'Cashier One' would inject 'cashier' as a token
        # which is present in 'CashierPass123!', so use a neutral full_name.
        create_user = client.post(
            '/api/users',
            json={
                'username': 'cashier1',
                'full_name': 'Till Operator',
                'email': 'till1@pos.test',
                'password': 'Vx3mKz8pRn6!',
                'role': 'Cashier',
            },
        )
        assert create_user.status_code == 201, create_user.get_data(as_text=True)

        # Test case 4: second user does not automatically become admin
        with flask_app.app_context():
            second_user = User.query.filter_by(username='cashier1').first()
            assert second_user is not None
            assert second_user.role == 'Cashier'
            assert second_user.is_admin is False
            assert second_user.is_owner is False
            assert second_user.is_primary_admin is False

        # Test case 5: permissions persist after restart
        with flask_app.test_client() as restarted_client:
            login_again = restarted_client.post(
                '/login', json={'username': 'newadmin', 'password': _FIRST_ADMIN_PASSWORD}
            )
            assert login_again.status_code == 200, login_again.get_data(as_text=True)
            assert login_again.get_json()['ok'] is True

            settings_after_restart = restarted_client.post('/api/settings', json={'store_phone': '555-0000'})
            assert settings_after_restart.status_code == 200
            assert settings_after_restart.get_json()['ok'] is True

            with flask_app.app_context():
                admin = User.query.filter_by(username='newadmin').first()
                assert admin is not None
                assert admin.is_primary_admin is True
                assert admin.role == 'Admin'
    finally:
        flask_app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = original_destructive


def test_restart_after_factory_reset_does_not_create_bootstrap_admin(flask_app):
    """ensure_admin_user_exists() must NOT auto-create a bootstrap admin when
    security_first_run_pending='1'.  After factory reset the operator must go
    through /setup-admin — a silent bootstrap account would bypass that gate."""
    from app import ensure_admin_user_exists, is_initial_security_setup_pending
    from models import StoreSettings, User, db

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        # Simulate the state left by factory reset: no users, first-run pending.
        db.session.add(StoreSettings(key='security_first_run_pending', value='1'))
        db.session.add(StoreSettings(key='primary_admin_user_id', value=''))
        db.session.commit()

        assert User.query.count() == 0
        assert is_initial_security_setup_pending() is True

        result = ensure_admin_user_exists()

        # Must return None and must NOT have inserted a bootstrap admin.
        assert result is None
        assert User.query.count() == 0, (
            'ensure_admin_user_exists() created a bootstrap admin during first-run mode'
        )

    # The setup page must still be reachable and first-run must still be active.
    with flask_app.test_client() as client:
        resp = client.get('/setup-admin')
        assert resp.status_code == 200

        login_resp = client.get('/login')
        # GET /login redirects to /setup-admin when no users exist
        assert login_resp.status_code in (200, 302)


def test_reset_business_data_preserves_users(flask_app):
    """The /api/admin/reset-business-data endpoint wipes sales/products but preserves users and settings."""
    from models import Product, StoreSettings, User, db

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        owner = _create_admin_user(db, User, username='prodadmin', password='ProdAdmin123!')
        db.session.add(StoreSettings(key='security_first_run_pending', value='0'))
        db.session.add(StoreSettings(key='primary_admin_user_id', value=str(owner.id)))
        db.session.add(Product(name='Demo Item', sell_price=100))
        db.session.commit()
        owner_id = owner.id

    try:
        with flask_app.test_client() as client:
            with flask_app.app_context():
                owner = User.query.filter_by(username='prodadmin').first()
                assert owner is not None
            _authenticate_session(client, owner)

            # reset-business-data preserves users, settings, and license — only wipes transactions/inventory
            reset_response = client.post('/api/admin/reset-business-data')
            assert reset_response.status_code == 200, reset_response.get_data(as_text=True)
            payload = reset_response.get_json()
            assert payload['ok'] is True
            assert payload['activation_preserved'] is True

            with flask_app.app_context():
                # Users and settings must be intact
                assert User.query.filter_by(username='prodadmin').count() == 1
                assert Product.query.count() == 0
                assert StoreSettings.get('security_first_run_pending', '1') == '0'
                assert StoreSettings.get('primary_admin_user_id', '') == str(owner_id)
    finally:
        pass


def test_full_factory_reset_access_for_admin_depends_on_destructive_tools_flag(flask_app):
    from models import User, db

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        admin = _create_admin_user(db, User, username='fullresetadmin', password='FullReset123!')

    original_destructive = flask_app.config.get('ENABLE_DESTRUCTIVE_ADMIN_TOOLS')
    try:
        # Locked in production-safe mode for Admin role.
        flask_app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = False
        with flask_app.test_client() as client:
            with flask_app.app_context():
                admin = User.query.filter_by(username='fullresetadmin').first()
                assert admin is not None
            _authenticate_session(client, admin)
            forbidden = client.post('/api/admin/full-factory-reset', json={'confirm': 'NOT_IT'})
            assert forbidden.status_code == 403

        # Unlocked in support mode for Admin role, then route-level confirm validation runs.
        flask_app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = True
        with flask_app.test_client() as client:
            with flask_app.app_context():
                admin = User.query.filter_by(username='fullresetadmin').first()
                assert admin is not None
            _authenticate_session(client, admin)
            bad_confirm = client.post('/api/admin/full-factory-reset', json={'confirm': 'NOT_IT'})
            assert bad_confirm.status_code == 400
            payload = bad_confirm.get_json()
            assert payload['ok'] is False
            assert 'FULL_FACTORY_RESET' in payload['msg']
    finally:
        flask_app.config['ENABLE_DESTRUCTIVE_ADMIN_TOOLS'] = original_destructive
