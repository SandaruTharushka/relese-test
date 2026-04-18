from datetime import datetime, timedelta, timezone


def test_repairs_analysis_returns_finance_and_receipt_metrics(auth_client, flask_app):
    from models import RepairJob, RepairPayment, User, db

    with flask_app.app_context():
        admin = User.query.filter_by(username='admin').first()
        assert admin is not None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delivered = RepairJob(
            job_number='JOB-20260409-0001',
            issue_reported='Engine tune-up',
            status='delivered',
            total_amount=10000,
            advance_paid=1000,
            payment_status='paid',
            created_by=admin.id,
            received_date=now - timedelta(days=2),
        )
        ready = RepairJob(
            job_number='JOB-20260409-0002',
            issue_reported='Brake inspection',
            status='ready',
            total_amount=8000,
            advance_paid=3000,
            payment_status='partial',
            created_by=admin.id,
            received_date=now - timedelta(days=1),
        )
        db.session.add_all([delivered, ready])
        db.session.flush()

        db.session.add(
            RepairPayment(
                job_id=delivered.id,
                amount=9000,
                method='cash',
                created_by=admin.id,
                payment_date=now - timedelta(days=1),
            )
        )
        db.session.commit()

    response = auth_client.get('/api/repairs/analysis')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['ok'] is True
    assert payload['jobs']['total'] == 2
    assert payload['jobs']['delivered'] == 1
    assert payload['jobs']['ready'] == 1
    assert payload['receipts']['printable'] == 1
    assert payload['receipts']['not_ready'] == 1
    assert payload['finance']['delivered_revenue'] == 10000.0
    assert payload['finance']['outstanding_balance'] == 5000.0
    assert payload['finance']['avg_ticket'] == 10000.0


def test_repairs_analysis_rejects_invalid_date_format(auth_client):
    response = auth_client.get('/api/repairs/analysis?start=2026/01/01')
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['error'] == 'Start date must be in YYYY-MM-DD format.'
