from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope='session')
def flask_app():
    flask = pytest.importorskip('flask')
    assert flask is not None

    from app import app

    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    return app


@pytest.fixture()
def client(flask_app):
    from models import User, db

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        admin = User(
            username='admin',
            full_name='Test Admin',
            email='admin@example.com',
            role='Admin',
            status='active',
            force_password_change=False,
        )
        admin.set_password('AdminPass123!')
        admin.session_token = secrets.token_urlsafe(32)
        db.session.add(admin)
        db.session.commit()

    with flask_app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def auth_client(client, flask_app):
    from models import User, db

    with flask_app.app_context():
        admin = User.query.filter_by(username='admin').first()
        assert admin is not None

        if not admin.session_token:
            admin.session_token = secrets.token_urlsafe(32)
            db.session.commit()

        with client.session_transaction() as session:
            session['_user_id'] = str(admin.id)
            session['_fresh'] = True
            session['_id'] = secrets.token_urlsafe(24)
            session['_session_token'] = admin.session_token

    return client
