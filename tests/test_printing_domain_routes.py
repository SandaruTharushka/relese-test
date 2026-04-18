def test_scanner_settings_clears_legacy_label_selection(auth_client, flask_app):
    with flask_app.app_context():
        from models import StoreSettings, db

        StoreSettings.set('label_printer_selection', 'legacy-printer')
        db.session.commit()

    response = auth_client.post('/api/scanner/settings', json={'scanner_prefix': 'AA', 'scanner_target': 'product'})
    assert response.status_code == 200
    assert response.get_json()['ok'] is True

    with flask_app.app_context():
        from models import StoreSettings

        assert (StoreSettings.get('label_printer_selection', '') or '') == ''


def test_printing_history_unified_endpoint_returns_label_and_receipt_logs(auth_client, flask_app):
    with flask_app.app_context():
        from models import UserLog, db

        db.session.add(UserLog(action='Receipt print dispatched', target_type='receipt_print'))
        db.session.add(UserLog(action='Label print dispatched', target_type='label_print'))
        db.session.commit()

    response = auth_client.get('/api/printing/history?limit=10')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    target_types = {entry['target_type'] for entry in payload['history']}
    assert 'receipt_print' in target_types
    assert 'label_print' in target_types
