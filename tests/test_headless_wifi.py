"""Compatibility with the pinned consumer, not a producer-side parser."""
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.provisioning import ImageType, ProvisioningValidationError, validate_provisioning
from backend import imager
from tests.test_provisioning_preflight import valid_request


@pytest.mark.parametrize("value", ['Lab"Printer', "Lab'Printer", " both'\" ", r"printer\wifi123", "  spaces  ", "#lab=work", "Taller ñ", "", "a'\"`´b"])
def test_prebaked_wifi_round_trips_through_exact_consumer(tmp_path, monkeypatch, value):
    boot = tmp_path / "boot"
    boot.mkdir()
    monkeypatch.setattr(imager, "get_boot_drive_letter", lambda *_args, **_kwargs: str(boot))
    monkeypatch.setattr(imager, "_verified_boot_volume", lambda path, _identity: path)
    identity = {"number": 7, "friendly_name": "Test SD", "size_bytes": 32 * 1024**3, "bus_type": "USB", "is_system": False, "is_boot": False, "serial_number": "SERIAL-7", "unique_id": "UNIQUE-7", "path": r"\\?\usbstor#test-7", "media_type": "Unspecified"}
    # Use the same value in password when its length is valid; open WiFi otherwise.
    password = value if 8 <= len(value) <= 63 else ""
    security = "wpa2" if password else "open"
    assert imager.inject_config(7, "printer-one", value or "Workshop", password, "ssh-pass-123", "mainsail", image_type=ImageType.MAINSAILOS_PREBAKED, wifi_security=security, drive_identity=identity)
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash is required to execute the upstream WiFi parser")
    parser = Path(__file__).parent / "fixtures/headless_nm/parser.sh"
    script = parser.read_text(encoding="utf-8") + '\nSETUPFILE="$1"\nprintf "%s\\0%s" "$(get_value SSID)" "$(get_value PASSWORD)"\n'
    result = subprocess.run([bash, "-c", script, "fixture", str(boot / "headless_nm.txt")], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    ssid, recovered_password = result.stdout.decode().split("\x00")
    assert ssid == (value or "Workshop")
    assert recovered_password == password


@pytest.mark.parametrize("value", [' \"\'`´', '-n', '-e', '-En'])
def test_unrepresentable_prebaked_values_are_rejected_by_preflight(value):
    with pytest.raises(ProvisioningValidationError) as error:
        validate_provisioning(**valid_request(wifi_ssid=value))
    assert error.value.field == "wifi_ssid"
