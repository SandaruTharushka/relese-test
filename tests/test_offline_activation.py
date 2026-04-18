from __future__ import annotations

from pathlib import Path


def test_offline_activation_trial_and_manual_key(tmp_path, monkeypatch):
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'cafebabedeadbeef0011223344556677')

    status = lic.activation_status()
    assert status['activated'] is False
    assert status['trial_days_total'] == 7
    assert status['trial_days_left'] == 7
    assert status['install_id'].startswith('SMPOS-')

    expected_key = lic._derive_activation_key(status['install_id'])
    result = lic.activate_offline(expected_key)
    assert result['ok'] is True

    status_after = lic.activation_status()
    assert status_after['activated'] is True
    assert status_after['requires_activation'] is False
    assert status_after['trial_days_left'] == 0
    assert status_after['trial_active'] is False
    assert status_after['license_type'] == 'Lifetime'
    assert status_after['activated_at']


def test_invalid_activation_key_is_rejected(tmp_path, monkeypatch):
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: '00112233445566778899aabbccddeeff')

    bad = lic.activate_offline('SMPOS-AAAAAAAA-BBBBBBBB-CCCCCCCC')
    assert bad['ok'] is False
    assert 'invalid' in bad['msg'].lower()
