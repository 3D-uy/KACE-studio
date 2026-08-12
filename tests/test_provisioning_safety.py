import pytest

from backend.imager import inject_config
from backend.provisioning import ImageType, ProvisioningValidationError


def test_disabling_ssh_removes_markers_and_disables_cloud_init(tmp_path, monkeypatch):
    (tmp_path / "ssh").touch()
    (tmp_path / "ssh.txt").touch()
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))

    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid="",
        wifi_password="",
        ssh_password="local-password",
        dashboard_ui="mainsail",
        ssh_enabled=False,
        password_auth=True,
    )

    assert not (tmp_path / "ssh").exists()
    assert not (tmp_path / "ssh.txt").exists()
    user_data = (tmp_path / "user-data").read_text(encoding="utf-8")
    assert "enable_ssh: false" in user_data
    assert "ssh_pwauth: false" in user_data


def test_cloud_init_rejects_multiline_wifi_before_mount_access(tmp_path, monkeypatch):
    ssid = 'office":\n  injected: true'
    password = 'secret\\"\n  another: value'
    mount_called = False

    def forbidden_mount(_disk):
        nonlocal mount_called
        mount_called = True
        return str(tmp_path)

    monkeypatch.setattr("backend.imager.get_boot_drive_letter", forbidden_mount)

    with pytest.raises(ProvisioningValidationError, match="SSID"):
        inject_config(
            disk_number=99,
            hostname="kace-test",
            wifi_ssid=ssid,
            wifi_password=password,
            ssh_password="local-password",
            dashboard_ui="mainsail",
        )
    assert mount_called is False
    assert list(tmp_path.iterdir()) == []


def test_prebaked_headless_wifi_rejects_line_injection_before_mount(tmp_path, monkeypatch):
    (tmp_path / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=abc-02 rootwait\n", encoding="utf-8"
    )
    mount_called = False

    def forbidden_mount(_disk):
        nonlocal mount_called
        mount_called = True
        return str(tmp_path)

    monkeypatch.setattr("backend.imager.get_boot_drive_letter", forbidden_mount)

    with pytest.raises(ProvisioningValidationError, match="SSID"):
        inject_config(
            disk_number=99,
            hostname="kace-test",
            wifi_ssid='safe\nHIDDEN="true"',
            wifi_password='password\nREGDOMAIN="ZZ"',
            ssh_password="local-password",
            dashboard_ui="mainsail",
            image_type=ImageType.MAINSAILOS_PREBAKED,
        )
    assert mount_called is False


def test_prebaked_ssh_disable_is_enforced_on_first_boot(tmp_path, monkeypatch):
    (tmp_path / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=abc-02 rootwait\n", encoding="utf-8"
    )
    (tmp_path / "ssh").touch()
    (tmp_path / "ssh.txt").touch()
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))

    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid="",
        wifi_password="",
        ssh_password="local-password",
        dashboard_ui="mainsail",
        ssh_enabled=False,
        password_auth=True,
        image_type=ImageType.MAINSAILOS_PREBAKED,
    )

    script = (tmp_path / "firstrun.sh").read_text(encoding="utf-8")
    assert "SSH_ENABLED='false'" in script
    assert "systemctl disable --now ssh" in script
    assert "Refusing to merge existing target user" in script
    assert not (tmp_path / "ssh").exists()
    assert not (tmp_path / "ssh.txt").exists()


def test_open_wifi_generates_passwordless_network_profiles(tmp_path, monkeypatch):
    (tmp_path / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=abc-02 rootwait\n", encoding="utf-8"
    )
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))

    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid="Guest Network",
        wifi_password="",
        wifi_security="open",
        ssh_password="local-password",
        dashboard_ui="mainsail",
    )

    wpa = (tmp_path / "wpa_supplicant.conf").read_text(encoding="utf-8")
    nm = (tmp_path / "system-connections" / "preconfigured-wifi.nmconnection").read_text(
        encoding="utf-8"
    )
    network = (tmp_path / "network-config").read_text(encoding="utf-8")
    assert "key_mgmt=NONE" in wpa
    assert "psk=" not in wpa
    assert "[wifi-security]" not in nm
    assert "password:" not in network


def test_raw_64_hex_wifi_psk_is_not_hashed_again(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))
    raw_psk = "A1" * 32
    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid="Workshop",
        wifi_password=raw_psk,
        ssh_password="local-password",
        dashboard_ui="mainsail",
    )
    wpa = (tmp_path / "wpa_supplicant.conf").read_text(encoding="utf-8")
    assert f"psk={raw_psk.lower()}" in wpa
