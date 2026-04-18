from datetime import datetime


def _create_repair_job(flask_app):
    from models import RepairJob, RepairPayment, User, db

    with flask_app.app_context():
        admin = User.query.filter_by(username='admin').first()
        assert admin is not None
        job = RepairJob(
            job_number='JOB-20260410-0099',
            customer_name='Nimal Perera',
            customer_phone='0771234567',
            vehicle_make='Toyota',
            vehicle_model='Aqua',
            vehicle_reg_no='WP CAB-1234',
            issue_reported='Brake pad replacement and oil change',
            status='delivered',
            total_amount=15000,
            labour_charge=5000,
            advance_paid=3000,
            payment_status='partial',
            created_by=admin.id,
            received_date=datetime(2026, 4, 10, 9, 30, 0),
        )
        db.session.add(job)
        db.session.flush()
        db.session.add(
            RepairPayment(
                job_id=job.id,
                amount=7000,
                method='cash',
                created_by=admin.id,
            )
        )
        db.session.commit()
        return job.id


def test_repair_receipt_text_endpoint_returns_printer_ready_payload(auth_client, flask_app):
    jid = _create_repair_job(flask_app)

    response = auth_client.get(f'/api/repairs/{jid}/receipt-text')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['ok'] is True
    assert payload['job_number'] == 'JOB-20260410-0099'
    receipt_text = payload['receipt_text']
    assert isinstance(receipt_text, str)
    assert 'Job No' in receipt_text
    assert 'Customer' in receipt_text
    assert 'Vehicle' in receipt_text
    assert 'Reg No' in receipt_text
    assert 'TOTAL' in receipt_text
    assert 'PAID' in receipt_text
    assert 'BALANCE' in receipt_text


def test_receipt_print_endpoint_fails_when_receipt_printer_not_configured(auth_client):
    response = auth_client.post(
        '/api/printer/print/receipt',
        json={
            'title': 'Service Job JOB-20260410-0099',
            'receipt_text': 'Hello printer',
            'copies': 1,
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['code'] in {'MISSING_PRINTER', 'PRINTER_UNAVAILABLE', 'PRINTER_NOT_INSTALLED'}


def test_job_receipt_direct_print_endpoint_uses_thermal_dispatch(auth_client, flask_app):
    jid = _create_repair_job(flask_app)

    response = auth_client.post(
        '/api/printer/print/job-receipt',
        json={
            'job_id': jid,
            'copies': 1,
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['ok'] is False
    assert payload['job_id'] == jid
    assert payload['job_number'] == 'JOB-20260410-0099'
    assert payload['code'] in {'MISSING_PRINTER', 'PRINTER_UNAVAILABLE', 'PRINTER_NOT_INSTALLED'}
