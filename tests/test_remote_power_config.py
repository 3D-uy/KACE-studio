import json

import pytest

from backend.remote_power_config import (
    POWER_SCHEMA,
    RemotePowerConfigError,
    parse_remote_power_config,
)


def versioned(**overrides):
    data = {
        "schema": POWER_SCHEMA,
        "revision": 3,
        "enabled": True,
        "device": "main_psu",
        "pin": "gpiochip0/gpio20",
        "active_low": True,
        "initial_state": "on",
        "restart_klipper_when_powered": True,
        "off_when_shutdown": False,
    }
    data.update(overrides)
    return json.dumps(data)


def test_full_remote_schema_is_validated_and_normalized():
    config = parse_remote_power_config(versioned())
    assert config["schema"] == POWER_SCHEMA
    assert config["revision"] == 3
    assert config["device"] == "main_psu"
    assert config["legacy"] is False


def test_disabled_remote_schema_cannot_retain_device_or_pin():
    with pytest.raises(RemotePowerConfigError, match="disabled"):
        parse_remote_power_config(versioned(enabled=False))
    disabled = parse_remote_power_config(versioned(enabled=False, device=None, pin=None))
    assert disabled["enabled"] is False


def test_legacy_schema_preserves_only_authoritative_device_identity():
    config = parse_remote_power_config('{"schema":1,"enabled":true,"device":"printer"}')
    assert config["legacy"] is True
    assert config["device"] == "printer"
    assert config["pin"] is None


def test_malformed_legacy_enabled_does_not_silently_become_disabled():
    with pytest.raises(RemotePowerConfigError, match="boolean"):
        parse_remote_power_config('{"schema":1,"enabled":"true","device":"printer"}')


@pytest.mark.parametrize(
    "mutation",
    [
        {"revision": 0},
        {"pin": "!gpiochip0/gpio20"},
        {"active_low": "true"},
        {"initial_state": "maybe"},
        {"device": "bad name"},
    ],
)
def test_malformed_remote_contract_fails_closed(mutation):
    with pytest.raises(RemotePowerConfigError):
        parse_remote_power_config(versioned(**mutation))
