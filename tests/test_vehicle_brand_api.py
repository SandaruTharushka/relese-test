from datetime import datetime


def test_vehicle_brand_create_and_duplicate_guard(auth_client):
    payload = {'name': '  BAJAJ ', 'vehicle_type': 'bike'}
    first = auth_client.post('/api/vehicle-brands', json=payload)
    assert first.status_code == 201
    first_data = first.get_json()
    assert first_data['ok'] is True
    assert first_data['brand']['normalized_name'] == 'bajaj'

    second = auth_client.post('/api/vehicle-brands', json={'name': 'bajaj', 'vehicle_type': 'motorcycle'})
    assert second.status_code == 200
    second_data = second.get_json()
    assert second_data['ok'] is True
    assert second_data['brand']['id'] == first_data['brand']['id']


def test_vehicle_brand_search_filters_by_type_and_order(auth_client, flask_app):
    from models import VehicleBrand, db

    with flask_app.app_context():
        db.session.add_all([
            VehicleBrand(name='Toyota', normalized_name='toyota', vehicle_type='car', usage_count=4, is_active=True),
            VehicleBrand(name='Toyo', normalized_name='toyo', vehicle_type='car', usage_count=2, is_active=True),
            VehicleBrand(name='AutoToyota', normalized_name='autotoyota', vehicle_type='car', usage_count=1, is_active=True),
            VehicleBrand(name='Yamaha', normalized_name='yamaha', vehicle_type='bike', usage_count=50, is_active=True),
        ])
        db.session.commit()

    res = auth_client.get('/api/vehicle-brands?q=toyo&vehicle_type=car')
    assert res.status_code == 200
    data = res.get_json()
    assert data['ok'] is True
    names = [row['name'] for row in data['brands']]
    assert names[0] == 'Toyo'
    assert 'Yamaha' not in names


def test_repair_job_create_increments_brand_usage(auth_client, flask_app):
    from models import RepairJob, User, VehicleBrand, db

    with flask_app.app_context():
        admin = User.query.filter_by(username='admin').first()
        assert admin is not None

    create_res = auth_client.post('/api/repairs', json={
        'issue_reported': 'Engine noise',
        'vehicle_type': 'bike',
        'vehicle_make': 'Loncin',
        'customer_name': 'Brand User',
        'received_date': datetime.now().isoformat(),
    })
    assert create_res.status_code == 201

    with flask_app.app_context():
        brand = VehicleBrand.query.filter_by(normalized_name='loncin', vehicle_type='bike').first()
        assert brand is not None
        assert brand.usage_count == 1
        job = RepairJob.query.order_by(RepairJob.id.desc()).first()
        assert job.vehicle_brand_id == brand.id
        assert job.vehicle_type == 'bike'
