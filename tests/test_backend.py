import unittest
import sys
import os

# Include project root in PATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.sha512_crypt import hash_password
from backend.discovery import get_local_subnet_ips, probe_ip_ports
from backend.imager import DEFAULT_USERNAME, TIMEZONE_TO_COUNTRY, _get_country_from_timezone

class TestKaceBackend(unittest.TestCase):
    
    def test_sha512_crypt(self):
        """
        Tests that SHA-512 password hashing generates a valid Unix crypt structure ($6$).
        """
        pwd = "test_password_for_kace"
        hashed = hash_password(pwd)
        
        self.assertTrue(hashed.startswith("$6$"), f"Hash should start with $6$. Got: {hashed}")
        self.assertEqual(len(hashed.split("$")), 4, f"Hash components mismatched. Got: {hashed}")
        
    def test_subnet_ips_generation(self):
        """
        Tests that subnet scanning list produces valid /24 host IPs.
        """
        ips = get_local_subnet_ips()
        self.assertTrue(len(ips) > 0, "Subnet IP list should not be empty")
        
        # Verify first IP formatting
        first_ip = ips[0]
        octets = first_ip.split(".")
        self.assertEqual(len(octets), 4, f"Invalid IP format: {first_ip}")
        self.assertEqual(octets[-1], "1", f"First host in subnet should end in .1. Got: {first_ip}")
        
    def test_port_probe_timeout(self):
        """
        Tests that port probing functions handle closed/dead IP sockets gracefully and return False.
        """
        # Test probing an unroutable IP address
        results = probe_ip_ports("192.0.2.1", ports=[22, 7125], timeout=0.1)
        self.assertFalse(results.get(22, True), "SSH Port should report closed on unroutable IP")
        self.assertFalse(results.get(7125, True), "Moonraker Port should report closed on unroutable IP")

    def test_networkmanager_profile(self):
        """
        Tests that config injection creates valid wpa_supplicant AND NetworkManager 
        profiles for dual-format compatibility, along with hashed user credentials.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        # Create a mock boot directory structure
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test.local",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail"
                )
                self.assertTrue(success)
                
                # Check userconf
                userconf_path = os.path.join(temp_boot, "userconf.txt")
                self.assertTrue(os.path.exists(userconf_path))
                with open(userconf_path, "r") as f:
                    userconf_content = f.read()
                self.assertTrue(userconf_content.startswith("kace:$6$"))
                
                # Check legacy wpa_supplicant
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path))
                with open(wpa_path, "r") as f:
                    wpa_content = f.read()
                self.assertIn('ssid="MySSID"', wpa_content)
                self.assertIn('psk="MyPassword"', wpa_content)
                
                # Check modern NetworkManager profile
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path))
                with open(nm_path, "r") as f:
                    nm_content = f.read()
                self.assertIn("id=preconfigured-wifi", nm_content)
                self.assertIn("ssid=MySSID", nm_content)
                self.assertIn("psk=MyPassword", nm_content)
                self.assertIn("type=wifi", nm_content)
                
        finally:
            shutil.rmtree(temp_boot)

    # ── New Tests (Audit 4.18) ────────────────────────────────────────────

    def test_default_username_constant(self):
        """Tests that the DEFAULT_USERNAME constant is defined and equals 'kace'."""
        self.assertEqual(DEFAULT_USERNAME, "kace")

    def test_timezone_to_country_mapping(self):
        """Tests that common timezone entries resolve to correct country codes."""
        self.assertEqual(_get_country_from_timezone("America/Sao_Paulo"), "BR")
        self.assertEqual(_get_country_from_timezone("America/New_York"), "US")
        self.assertEqual(_get_country_from_timezone("Europe/London"), "GB")
        self.assertEqual(_get_country_from_timezone("Asia/Tokyo"), "JP")
        self.assertEqual(_get_country_from_timezone("Australia/Sydney"), "AU")
        self.assertEqual(_get_country_from_timezone("UTC"), "US")

    def test_timezone_to_country_fallback(self):
        """Tests that an unknown timezone falls back to 'US'."""
        self.assertEqual(_get_country_from_timezone("Mars/Olympus_Mons"), "US")
        self.assertEqual(_get_country_from_timezone(""), "US")

    def test_timezone_to_country_prefix_matching(self):
        """Tests that sub-timezone paths match by their prefix (e.g. America/Indiana/Indianapolis)."""
        result = _get_country_from_timezone("America/Indiana/Indianapolis")
        self.assertEqual(result, "US")

    def test_config_injection_uses_default_username(self):
        """Tests that config injection uses the DEFAULT_USERNAME constant in userconf.txt."""
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                inject_config(
                    disk_number=99,
                    hostname="test.local",
                    wifi_ssid="",
                    wifi_password="",
                    ssh_password="testpwd",
                    dashboard_ui="mainsail"
                )
                userconf_path = os.path.join(temp_boot, "userconf.txt")
                with open(userconf_path, "r") as f:
                    content = f.read()
                self.assertTrue(content.startswith(f"{DEFAULT_USERNAME}:$6$"))
        finally:
            shutil.rmtree(temp_boot)

    def test_config_injection_country_code_from_timezone(self):
        """Tests that wpa_supplicant.conf uses the correct country code derived from timezone."""
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                inject_config(
                    disk_number=99,
                    hostname="test.local",
                    wifi_ssid="TestSSID",
                    wifi_password="TestPass",
                    ssh_password="testpwd",
                    dashboard_ui="mainsail",
                    timezone="America/Sao_Paulo"
                )
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                with open(wpa_path, "r") as f:
                    wpa_content = f.read()
                self.assertIn("country=BR", wpa_content)
                
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                with open(custom_toml_path, "r") as f:
                    toml_content = f.read()
                self.assertIn('country = "BR"', toml_content)
                self.assertIn('ssid = "TestSSID"', toml_content)
                self.assertIn('password = "TestPass"', toml_content)
        finally:
            shutil.rmtree(temp_boot)

    def test_config_injection_default_country_when_no_timezone(self):
        """Tests that wpa_supplicant uses 'US' when no timezone is provided."""
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                inject_config(
                    disk_number=99,
                    hostname="test.local",
                    wifi_ssid="TestSSID",
                    wifi_password="TestPass",
                    ssh_password="testpwd",
                    dashboard_ui="mainsail",
                    timezone=""
                )
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                with open(wpa_path, "r") as f:
                    wpa_content = f.read()
                self.assertIn("country=US", wpa_content)
        finally:
            shutil.rmtree(temp_boot)

    def test_list_drives_returns_list(self):
        """Tests that list_drives() always returns a list (even if empty on non-Windows)."""
        from backend.imager import list_drives
        result = list_drives()
        self.assertIsInstance(result, list)

    def test_list_drives_structure(self):
        """Tests that each drive entry has expected keys."""
        from backend.imager import list_drives
        drives = list_drives()
        for drive in drives:
            self.assertIn("id", drive)
            self.assertIn("name", drive)
            self.assertIn("size", drive)
            self.assertIn("bus", drive)

    def test_ssh_session_initial_state(self):
        """Tests that SSHSession initializes with clean state."""
        from backend.ssh_client import SSHSession
        session = SSHSession()
        self.assertIsNone(session.client)
        self.assertIsNone(session.channel)
        self.assertFalse(session.running)

    def test_ssh_session_close_safe(self):
        """Tests that close() can be called safely on an unconnected session."""
        from backend.ssh_client import SSHSession
        session = SSHSession()
        # Should not raise
        session.close()
        self.assertIsNone(session.client)
        self.assertIsNone(session.channel)

    def test_ssh_session_send_input_no_channel(self):
        """Tests that send_input() is a no-op when no channel is open."""
        from backend.ssh_client import SSHSession
        session = SSHSession()
        # Should not raise
        session.send_input("test")

    def test_bootstrap_config_injection(self):
        """Tests that the kace-bootstrap.txt file is correctly generated."""
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                inject_config(
                    disk_number=99,
                    hostname="kace.local",
                    wifi_ssid="",
                    wifi_password="",
                    ssh_password="pwd",
                    dashboard_ui="fluidd",
                    timezone="Europe/London",
                    pi_model="pi4",
                    os_arch="64bit",
                    crowsnest=True
                )
                bootstrap_path = os.path.join(temp_boot, "kace-bootstrap.txt")
                self.assertTrue(os.path.exists(bootstrap_path))
                with open(bootstrap_path, "r") as f:
                    content = f.read()
                self.assertIn("DASHBOARD=fluidd", content)
                self.assertIn("CROWSNEST=true", content)
                self.assertIn("TIMEZONE=Europe/London", content)
                self.assertIn("PI_MODEL=pi4", content)
                self.assertIn("OS_ARCH=64bit", content)
                
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                with open(custom_toml_path, "r") as f:
                    toml_content = f.read()
                self.assertIn('hostname = "kace"', toml_content)
                self.assertIn('name = "kace"', toml_content)
                self.assertIn('password = "pwd"', toml_content)
        finally:
            shutil.rmtree(temp_boot)


if __name__ == "__main__":
    unittest.main()

