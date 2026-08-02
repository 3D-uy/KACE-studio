import json

from backend.imager import inject_config


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


def test_cloud_init_wifi_values_are_yaml_quoted(tmp_path, monkeypatch):
    ssid = 'office":\n  injected: true'
    password = 'secret\\"\n  another: value'
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))

    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid=ssid,
        wifi_password=password,
        ssh_password="local-password",
        dashboard_ui="mainsail",
    )

    network_config = (tmp_path / "network-config").read_text(encoding="utf-8")
    assert f"        {json.dumps(ssid)}:" in network_config
    assert f"          password: {json.dumps(password)}" in network_config
    assert "\n  injected: true\n" not in network_config
    assert "\n  another: value\n" not in network_config


def test_prebaked_headless_wifi_cannot_inject_lines(tmp_path, monkeypatch):
    (tmp_path / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=abc-02 rootwait\n", encoding="utf-8"
    )
    monkeypatch.setattr("backend.imager.get_boot_drive_letter", lambda _disk: str(tmp_path))

    assert inject_config(
        disk_number=99,
        hostname="kace-test",
        wifi_ssid='safe\nHIDDEN="true"',
        wifi_password='password\nREGDOMAIN="ZZ"',
        ssh_password="local-password",
        dashboard_ui="mainsail",
        is_prebaked=True,
    )

    lines = (tmp_path / "headless_nm.txt").read_text(encoding="utf-8").splitlines()
    assert lines == [
        'SSID="safeHIDDEN=\\"true\\""',
        'PASSWORD="passwordREGDOMAIN=\\"ZZ\\""',
        'HIDDEN="false"',
        'REGDOMAIN="US"',
    ]


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
        is_prebaked=True,
    )

    script = (tmp_path / "firstrun.sh").read_text(encoding="utf-8")
    assert "SSH_ENABLED='false'" in script
    assert "systemctl disable --now ssh" in script
    assert "Refusing to merge existing target user" in script
    assert not (tmp_path / "ssh").exists()
    assert not (tmp_path / "ssh.txt").exists()
