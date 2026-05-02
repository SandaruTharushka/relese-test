from __future__ import annotations

import secrets

from models import User, db


def test_login_with_correct_credentials_succeeds(client):
    response = client.post(
        '/login',
        json={'username': 'admin', 'password': 'AdminPass123!'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    assert payload['role'] == 'Admin'


def test_login_with_wrong_password_fails(client):
    response = client.post(
        '/login',
        json={'username': 'admin', 'password': 'wrong-password'},
    )

    assert response.status_code == 401
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['code'] == 'INVALID_CREDENTIALS'


def test_login_with_email_succeeds(client):
    response = client.post(
        '/login',
        json={'username': 'admin@example.com', 'password': 'AdminPass123!'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    assert payload['role'] == 'Admin'


def test_reset_password_flow_without_otp(client):
    verify_response = client.post(
        '/forgot-password',
        json={'email': 'admin@example.com', 'username': 'admin'},
    )
    assert verify_response.status_code == 200
    verify_payload = verify_response.get_json()
    assert verify_payload['ok'] is True

    # Password must not contain identity tokens from username ('admin'), email ('example'),
    # or full_name ('Root Admin') — use a neutral password.
    reset_response = client.post(
        '/reset-password',
        json={'password': 'Bz7kQp4nXm2W!', 'confirm_password': 'Bz7kQp4nXm2W!'},
    )
    assert reset_response.status_code == 200
    reset_payload = reset_response.get_json()
    assert reset_payload['ok'] is True

    login_response = client.post(
        '/login',
        json={'username': 'admin', 'password': 'Bz7kQp4nXm2W!'},
    )
    assert login_response.status_code == 200
    login_payload = login_response.get_json()
    assert login_payload['ok'] is True


def test_reset_password_rejects_mismatch_identity(client):
    response = client.post(
        '/forgot-password',
        json={'email': 'admin@example.com', 'username': 'operator'},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['ok'] is False
    # The response intentionally does not disclose whether the email or
    # username existed — both failure modes return the same generic message
    # so attackers cannot enumerate accounts via the reset flow.
    assert payload['msg'] == (
        'Identity verification failed. Check your username and email and try again.'
    )


def test_reset_password_rejects_unknown_email_with_same_message(client):
    """Unknown email must produce the same response as a mismatched pair."""
    response = client.post(
        '/forgot-password',
        json={'email': 'noone@example.com', 'username': 'admin'},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['msg'] == (
        'Identity verification failed. Check your username and email and try again.'
    )


def test_operator_cannot_create_admin_user(client, flask_app):
    with flask_app.app_context():
        operator = User(
            username='operator',
            full_name='Operator User',
            email='operator@example.com',
            role='Operator',
            status='active',
            force_password_change=False,
        )
        operator.set_password('OperatorPass123!')
        operator.session_token = secrets.token_urlsafe(32)
        db.session.add(operator)
        db.session.commit()

        with client.session_transaction() as session:
            session['_user_id'] = str(operator.id)
            session['_fresh'] = True
            session['_id'] = secrets.token_urlsafe(24)
            session['_session_token'] = operator.session_token

    response = client.post(
        '/api/users',
        json={
            'username': 'evil-admin',
            'full_name': 'Evil Admin',
            'email': 'evil@example.com',
            'password': 'VeryStrongPass123!',
            'role': 'Admin',
        },
    )

    assert response.status_code == 403


def test_barcode_scan_requires_login_redirects(client):
    # Flask-Login's @login_required redirects unauthenticated browser requests (302).
    # The endpoint is only accessed from within the authenticated app session.
    response = client.get('/api/barcode/scan/1234567890123')
    assert response.status_code in (302, 401)
