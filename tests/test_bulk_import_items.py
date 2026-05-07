import io

from models import Product, Supplier


def test_bulk_import_creates_updates_and_supplier(auth_client, flask_app):
    payload = (
        "name,buy_price,sell_price,qty,barcode,supplier\n"
        "Engine Oil,2500,3200,10,343423423,ABC Supplier\n"
        "Engine Oil,2500,3200,5,343423423,ABC Supplier\n"
        "Brake Pad,3500,4500,5,,\n"
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

    with flask_app.app_context():
        oil = Product.query.filter_by(barcode='343423423').first()
        assert oil is not None
        assert float(oil.stock_qty) == 15.0
        brake = Product.query.filter_by(name='Brake Pad').first()
        assert brake is not None
        sup = Supplier.query.filter_by(name='ABC Supplier').first()
        assert sup is not None
        assert oil.supplier_id == sup.id


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
