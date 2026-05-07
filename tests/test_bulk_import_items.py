import io

from models import Category, Product


def test_bulk_import_creates_updates_and_supplier(auth_client, flask_app):
    payload = (
        "name,buy_price,sell_price,qty,barcode,category\n"
        "Engine Oil,2500,3200,10,343423423,Lubricants\n"
        "Engine Oil,2600,3300,5,343423423,Lubricants\n"
        "Brake Pad,3500,4500,5,,Brake Parts\n"
        "Bad Row,foo,120,1,999,\n"
        "\n"
    )
    res = auth_client.post('/api/items/bulk-import', data={'file': (io.BytesIO(payload.encode('utf-8-sig')), 'items.csv')}, content_type='multipart/form-data')
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    assert body['created'] == 2
    assert body['updated'] == 1
    assert body['skipped'] == 1
    assert len(body['errors']) == 1
    assert body['errors'][0]['column'] == 'buy_price'
    assert 'numeric' in body['errors'][0]['error']
    assert body['summary']['error_count'] == 1

    with flask_app.app_context():
        oil = Product.query.filter_by(barcode='343423423').first()
        assert oil is not None
        assert float(oil.stock_qty) == 15.0
        brake = Product.query.filter_by(name='Brake Pad').first()
        assert brake is not None
        assert float(oil.buy_price) == 2600.0
        assert float(oil.sell_price) == 3300.0
        lubricants = Category.query.filter_by(name='Lubricants').first()
        assert lubricants is not None
        assert oil.category_id == lubricants.id


def test_bulk_import_duplicate_name_updates_qty(auth_client, flask_app):
    seed = "name,buy_price,sell_price,qty,barcode\nEngine Oil,2500,3200,4,\n"
    auth_client.post('/api/items/bulk-import', data={'file': (io.BytesIO(seed.encode()), 'a.csv')}, content_type='multipart/form-data')
    dup = "item_name,cost_price,selling_price,quantity,sku\nEngine Oil,0,0,6,\n"
    res = auth_client.post('/api/items/bulk-import', data={'file': (io.BytesIO(dup.encode()), 'b.csv')}, content_type='multipart/form-data')
    assert res.status_code == 200
    with flask_app.app_context():
        oil = Product.query.filter_by(name='Engine Oil').first()
        assert oil is not None
        assert float(oil.stock_qty) == 10.0


def test_products_import_sample_download(auth_client):
    res = auth_client.get('/api/products/import/sample')
    assert res.status_code == 200
    assert res.headers.get('Content-Type', '').startswith('text/csv')
    assert 'attachment; filename=products_import_sample.csv' == res.headers.get('Content-Disposition')
    body = res.data.decode('utf-8')
    assert body.splitlines()[0] == 'name,buy_price,sell_price,qty,barcode,category'


def test_bulk_import_sell_price_less_than_buy_price(auth_client):
    payload = "name,buy_price,sell_price,qty\nPart A,100,50,2\n"
    res = auth_client.post('/api/items/bulk-import', data={'file': (io.BytesIO(payload.encode('utf-8-sig')), 'items.csv')}, content_type='multipart/form-data')
    assert res.status_code == 200
    body = res.get_json()
    assert body['skipped'] == 1
    assert body['errors'][0]['column'] == 'sell_price'
    assert 'greater than or equal' in body['errors'][0]['error']
