from __future__ import annotations

import json
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
    assert 'invalid' in bad['msg'].lower() or 'not valid' in bad['msg'].lower()


def test_first_run_activation_with_displayed_install_id_succeeds(tmp_path, monkeypatch):
    """Key generated for the displayed install_id must always activate successfully."""
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'aabbccdd11223344aabbccdd11223344')

    # Simulate what the activation page shows the user
    status = lic.activation_status()
    displayed_install_id = status['install_id']

    # Owner generates key for the displayed install_id
    key = lic._derive_activation_key(displayed_install_id)

    # First-run activation must succeed with that key
    result = lic.activate_offline(key)
    assert result['ok'] is True, f"First-run activation failed: {result['msg']}"

    status_after = lic.activation_status()
    assert status_after['activated'] is True


def test_stale_unactivated_activation_json_normalized_to_current(tmp_path, monkeypatch):
    """Stale unactivated activation.json with an old install_id is normalized to current machine."""
    import license as lic

    activation_file = str(Path(tmp_path) / 'activation.json')
    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', activation_file)

    # Write a stale activation file referencing an old machine
    stale_data = {
        "install_id": "SMPOS-OLDINST-OLDINST-OLDINST",
        "machine_id": "oldmachineidstale000000000000000",
        "trial_started_at": "2024-01-01",
        "trial_active": True,
        "trial_days_left": 7,
        "activated": False,
        "activation_key": "",
        "activated_at": None,
    }
    with open(activation_file, 'w') as f:
        json.dump(stale_data, f)

    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'newmachineid000000000000000000a')

    status = lic.activation_status()
    current_install_id = lic.get_install_id()

    # Displayed install_id must now reflect current machine, not stale value
    assert status['install_id'] == current_install_id
    assert status['install_id'] != "SMPOS-OLDINST-OLDINST-OLDINST"

    # A key for the now-correct install_id must succeed
    key = lic._derive_activation_key(status['install_id'])
    result = lic.activate_offline(key)
    assert result['ok'] is True, f"Activation after normalize failed: {result['msg']}"


def test_stale_activated_file_with_wrong_machine_becomes_deactivated(tmp_path, monkeypatch):
    """activation.json moved from another PC is automatically invalidated."""
    import license as lic

    activation_file = str(Path(tmp_path) / 'activation.json')
    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', activation_file)

    # Build an activation record for the original machine
    old_machine_id = 'oldmachineoriginal0000000000000a'
    old_install_id = (
        f"SMPOS-{old_machine_id[:8].upper()}-"
        f"{old_machine_id[8:16].upper()}-"
        f"{old_machine_id[16:24].upper()}"
    )
    old_key = lic._derive_activation_key(old_install_id)

    activated_data = {
        "install_id": old_install_id,
        "machine_id": old_machine_id,
        "trial_started_at": "2024-01-01",
        "trial_active": False,
        "trial_days_left": 0,
        "activated": True,
        "activation_key": old_key,
        "activated_at": "2024-01-01 10:00:00",
    }
    with open(activation_file, 'w') as f:
        json.dump(activated_data, f)

    # Now on a different machine
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'newdifferentmachine0000000000000b')

    status = lic.activation_status()
    # Machine mismatch must invalidate the activation
    assert status['activated'] is False


def test_api_activation_and_license_activate_use_same_backend(tmp_path, monkeypatch):
    """activate_offline() and activate() compat wrapper produce identical results."""
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'testmachineid0000000000000000ab')

    status = lic.activation_status()
    key = lic._derive_activation_key(status['install_id'])

    result1 = lic.activate_offline(key)
    assert result1['ok'] is True

    # Reset and try via the compat wrapper (used by /api/license/activate)
    lic.deactivate()

    result2 = lic.activate(key)
    assert result2['ok'] is True
    assert result1['ok'] == result2['ok']


def test_invalid_key_response_includes_install_id(tmp_path, monkeypatch):
    """Failed activation response includes install_id to help the user copy it correctly."""
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'testmachineid1111111111111111cd')

    result = lic.activate_offline('SMPOS-AAAAAAAA-BBBBBBBB-CCCCCCCC')
    assert result['ok'] is False
    assert 'install_id' in result, "Failed response must include install_id for UX"
    assert result['install_id'].startswith('SMPOS-')


def test_verify_uses_install_id_from_activation_data(tmp_path, monkeypatch):
    """verify_offline_activation_key uses the install_id parameter, not global get_install_id."""
    import license as lic

    monkeypatch.setattr(lic, 'LICENSE_DIR', str(tmp_path))
    monkeypatch.setattr(lic, 'ACTIVATION_FILE', str(Path(tmp_path) / 'activation.json'))
    monkeypatch.setattr(lic, '_get_machine_id', lambda: 'machineid00000000000000000000ab')

    install_id = lic.get_install_id()
    key = lic._derive_activation_key(install_id)

    # Should pass when install_id is provided explicitly
    assert lic.verify_offline_activation_key(key, install_id) is True

    # Should fail for a different install_id
    assert lic.verify_offline_activation_key(key, 'SMPOS-AAAAAAAA-BBBBBBBB-CCCCCCCC') is False


def test_helper_functions_present():
    """New helper functions required by the spec exist and are callable."""
    import license as lic

    state = lic.current_machine_state()
    assert 'install_id' in state
    assert 'machine_id' in state
    assert state['install_id'].startswith('SMPOS-')

    key = lic.expected_key_for_current_install()
    assert key.startswith('SMPOS-')

    custom_key = lic.generate_activation_key_for_install_id(state['install_id'])
    assert custom_key == key
