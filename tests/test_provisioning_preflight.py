from dataclasses import FrozenInstanceError

import pytest

import main
from backend.provisioning import (
    EXPECTED_BOOTSTRAP_SHA256,
    ImageType,
    ProvisioningValidationError,
    WifiSecurity,
    validate_provisioning,
)


def valid_request(**overrides):
    values = {
        "image_type": ImageType.MAINSAILOS_PREBAKED,
        "image_path": "default_prebaked",
        "hostname": "Printer-One.local",
        "wifi_ssid": "Workshop",
        "wifi_password": "wifi-pass-123",
        "wifi_security": WifiSecurity.WPA2,
        "ssh_password": "ssh-pass-123",
        "dashboard_ui": "mainsail",
        "timezone": "America/Montevideo",
        "pi_model": "pi4",
        "os_arch": "64bit",
        "ssh_enabled": True,
        "crowsnest": False,
        "username": "kace",
        "password_auth": True,
        "power_relay": False,
        "power_device": "",
        "power_gpio": None,
        "power_active_low": False,
        "restart_klipper_when_powered": True,
        "verify_write": True,
        "bootstrap_exists": True,
        "bootstrap_sha256": EXPECTED_BOOTSTRAP_SHA256,
        "validate_media": True,
        "image_exists": True,
        "image_size_bytes": None,
        "target_size_bytes": 32 * 1024**3,
        "cache_free_bytes": 6 * 1024**3,
    }
    values.update(overrides)
    return values


def test_validator_returns_normalized_immutable_data():
    result = validate_provisioning(**valid_request())
    assert result.hostname == "printer-one"
    assert result.image_type is ImageType.MAINSAILOS_PREBAKED
    assert result.wifi_security is WifiSecurity.WPA2
    with pytest.raises(FrozenInstanceError):
        result.hostname = "changed"


@pytest.mark.parametrize(
    ("image_type", "image_path", "dashboard"),
    [
        (ImageType.RASPIOS_VANILLA, "default_lite", "mainsail"),
        (ImageType.MAINSAILOS_PREBAKED, "default_prebaked", "both"),
        (ImageType.FLUIDDPI_PREBAKED, "default_prebaked", "fluidd"),
        (ImageType.CUSTOM_VANILLA, "custom.img", "mainsail"),
        (ImageType.CUSTOM_PREBAKED, "custom.img", "mainsail"),
    ],
)
def test_every_explicit_image_family_has_a_supported_contract(
    image_type, image_path, dashboard
):
    custom = image_type.is_custom
    result = validate_provisioning(**valid_request(
        image_type=image_type,
        image_path=image_path,
        dashboard_ui=dashboard,
        os_arch="32bit" if image_type is ImageType.FLUIDDPI_PREBAKED else "64bit",
        image_size_bytes=1024 * 1024 if custom else None,
    ))
    assert result.image_type is image_type


@pytest.mark.parametrize(
    ("updates", "field"),
    [
        ({"hostname": "bad host"}, "hostname"),
        ({"username": "Root"}, "username"),
        ({"ssh_password": "short"}, "ssh_password"),
        ({"wifi_ssid": "x" * 33}, "wifi_ssid"),
        ({"wifi_password": "short"}, "wifi_password"),
        ({"dashboard_ui": "other"}, "dashboard_ui"),
        ({"pi_model": "pizerow", "os_arch": "64bit"}, "os_arch"),
        ({"image_type": ImageType.FLUIDDPI_PREBAKED, "dashboard_ui": "fluidd", "os_arch": "64bit"}, "os_arch"),
        ({"bootstrap_exists": False}, "bootstrap"),
        ({"bootstrap_sha256": "0" * 64}, "bootstrap"),
        ({"cache_free_bytes": 1024}, "free_space"),
        ({"target_size_bytes": 1024}, "target_drive"),
        ({"verify_write": "false"}, "verify_write"),
    ],
)
def test_invalid_provisioning_facts_are_rejected(updates, field):
    with pytest.raises(ProvisioningValidationError) as error:
        validate_provisioning(**valid_request(**updates))
    assert error.value.field == field


def test_open_wifi_is_explicit_and_rejects_a_password():
    result = validate_provisioning(**valid_request(
        wifi_security="open",
        wifi_password="",
    ))
    assert result.wifi_security is WifiSecurity.OPEN

    with pytest.raises(ProvisioningValidationError) as error:
        validate_provisioning(**valid_request(wifi_security="open"))
    assert error.value.field == "wifi_password"


def test_write_verification_is_enabled_by_default_but_can_be_skipped():
    assert validate_provisioning(**valid_request()).verify_write is True
    assert validate_provisioning(**valid_request(verify_write=False)).verify_write is False


def _drive_snapshot():
    return {
        "number": 7,
        "friendly_name": "Test SD",
        "size_bytes": 32 * 1024**3,
        "bus_type": "USB",
        "is_system": False,
        "is_boot": False,
        "serial_number": "SERIAL-7",
        "unique_id": "UNIQUE-7",
        "path": r"\\?\usbstor#test-7",
        "media_type": "Unspecified",
    }


@pytest.mark.parametrize(
    "case",
    [
        "hostname",
        "username",
        "ssh_password",
        "wifi_ssid",
        "wifi_password",
        "dashboard",
        "architecture",
        "power_device",
        "power_gpio",
        "image_type",
        "image_source",
        "bootstrap_missing",
        "bootstrap_hash",
        "free_space",
        "target_size",
    ],
)
def test_every_invalid_start_flash_case_stops_before_worker_and_writer(monkeypatch, case):
    api = main.Api()
    snapshot = _drive_snapshot()
    arguments = {
        "image_path": "default_lite",
        "hostname": "printer-one",
        "wifi_ssid": "Workshop",
        "wifi_password": "wifi-pass-123",
        "ssh_password": "ssh-pass-123",
        "dashboard_ui": "mainsail",
        "pi_model": "pi4",
        "os_arch": "64bit",
        "username": "kace",
        "power_relay": False,
        "power_device": "",
        "power_gpio": None,
        "image_type": ImageType.RASPIOS_VANILLA.value,
        "wifi_security": "wpa2",
    }
    if case == "hostname":
        arguments["hostname"] = "bad host"
    elif case == "username":
        arguments["username"] = "Root"
    elif case == "ssh_password":
        arguments["ssh_password"] = "short"
    elif case == "wifi_ssid":
        arguments["wifi_ssid"] = "x" * 33
    elif case == "wifi_password":
        arguments["wifi_password"] = "short"
    elif case == "dashboard":
        arguments["dashboard_ui"] = "unknown"
    elif case == "architecture":
        arguments.update(pi_model="pizerow", os_arch="64bit")
    elif case == "power_device":
        arguments.update(power_relay=True, power_device="bad name", power_gpio=20)
    elif case == "power_gpio":
        arguments.update(power_relay=True, power_device="printer", power_gpio=None)
    elif case == "image_type":
        arguments["image_type"] = "unknown"
    elif case == "image_source":
        arguments["image_path"] = "wrong.img"
    elif case == "target_size":
        snapshot["size_bytes"] = 1024

    api._drive_snapshots[7] = dict(snapshot)
    touched = []

    bootstrap_exists = case != "bootstrap_missing"
    bootstrap_hash = "0" * 64 if case == "bootstrap_hash" else EXPECTED_BOOTSTRAP_SHA256
    monkeypatch.setattr(
        main,
        "bootstrap_preflight_facts",
        lambda: ("bootstrap.sh", bootstrap_exists, bootstrap_hash),
    )
    free_bytes = 1024 if case == "free_space" else 6 * 1024**3
    monkeypatch.setattr(
        main.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": free_bytes})(),
    )
    monkeypatch.setattr(main.threading, "Thread", lambda *_a, **_k: touched.append("thread"))
    monkeypatch.setattr(main, "flash_drive", lambda *_a, **_k: touched.append("writer"))
    monkeypatch.setattr(api, "_resolve_default_image", lambda *_a: touched.append("resolver"))

    result = api.start_flash(
        7,
        arguments["image_path"],
        arguments["hostname"],
        arguments["wifi_ssid"],
        arguments["wifi_password"],
        arguments["ssh_password"],
        arguments["dashboard_ui"],
        pi_model=arguments["pi_model"],
        os_arch=arguments["os_arch"],
        username=arguments["username"],
        drive_identity=snapshot,
        power_relay=arguments["power_relay"],
        power_device=arguments["power_device"],
        power_gpio=arguments["power_gpio"],
        image_type=arguments["image_type"],
        wifi_security=arguments["wifi_security"],
    )
    assert result is False
    assert touched == []


def test_frontend_sends_explicit_image_family_and_wifi_security():
    web_dir = main.os.path.join(main.os.path.dirname(main.__file__), "web")
    app_js = open(main.os.path.join(web_dir, "app.js"), encoding="utf-8").read()
    index_html = open(main.os.path.join(web_dir, "index.html"), encoding="utf-8").read()
    assert "custom_prebaked" in index_html
    assert "mainsailos_prebaked" in app_js
    assert "fluiddpi_prebaked" in app_js
    assert "imageType,\n            wifiSecurity" in app_js
