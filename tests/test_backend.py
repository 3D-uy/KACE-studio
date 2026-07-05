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
                
                # Check legacy wpa_supplicant — password is stored as PBKDF2 hex PSK, NOT plain text
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path))
                with open(wpa_path, "r") as f:
                    wpa_content = f.read()
                self.assertIn('ssid="MySSID"', wpa_content)
                # Verify the hex PSK (64 char hex, no quotes) is present instead of plain text password
                from backend.imager import _compute_wpa_psk
                expected_hex_psk = _compute_wpa_psk("MySSID", "MyPassword")
                self.assertIn(f'psk={expected_hex_psk}', wpa_content)
                self.assertNotIn('psk="MyPassword"', wpa_content, "Plain text password must NOT appear in wpa_supplicant.conf")
                
                # Check modern NetworkManager profile
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path))
                with open(nm_path, "r") as f:
                    nm_content = f.read()
                self.assertIn("id=preconfigured-wifi", nm_content)
                self.assertIn("ssid=MySSID", nm_content)
                self.assertIn(f"psk={expected_hex_psk}", nm_content)
                self.assertNotIn("psk=MyPassword", nm_content)
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
        """Tests that wpa_supplicant.conf uses the correct country code derived from timezone.
        Also verifies that custom.toml does NOT contain a [wlan] section (HIGH-01 fix —
        plaintext WiFi passwords are no longer written to custom.toml)."""
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
                # wpa_supplicant.conf must use the correct country code
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                with open(wpa_path, "r") as f:
                    wpa_content = f.read()
                self.assertIn("country=BR", wpa_content)
                
                # custom.toml must exist but must NOT contain WiFi credentials
                # (HIGH-01 FIX: plaintext password removed from custom.toml)
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                with open(custom_toml_path, "r") as f:
                    toml_content = f.read()
                self.assertNotIn('[wlan]', toml_content,
                    "custom.toml must not contain a [wlan] section (HIGH-01 security fix)")
                self.assertNotIn('password =', toml_content,
                    "custom.toml must not contain a plaintext WiFi password")
                # Hostname and SSH sections must still be present
                self.assertIn('hostname = "test"', toml_content)
                self.assertIn('[ssh]', toml_content)

                # wpa_supplicant.conf must use hex PSK, not plain text
                from backend.imager import _compute_wpa_psk
                expected_psk = _compute_wpa_psk("TestSSID", "TestPass")
                with open(wpa_path, "r") as f:
                    wpa_content2 = f.read()
                self.assertIn(f'psk={expected_psk}', wpa_content2)
                self.assertNotIn('psk="TestPass"', wpa_content2)
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
        finally:
            shutil.rmtree(temp_boot)

    def test_wifi_credentials_injection_escaping(self):
        r"""
        Test that wifi_ssid and wifi_password containing shell metacharacters (", ', \n, ;, $, \)
        are safely written to config files without breaking file structure or enabling injection.
        Assert the output files remain valid and parseable.
        """
        import tempfile
        import shutil
        import configparser
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            wifi_ssid = 'My"SSID"\n;$\\'
            wifi_password = 'My\'Password\n;$\\'
            
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid=wifi_ssid,
                    wifi_password=wifi_password,
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail"
                )
                self.assertTrue(success)
                
                # Check wpa_supplicant.conf — must use hex PSK, never plain text
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path))
                with open(wpa_path, "r", encoding="utf-8") as f:
                    wpa_content = f.read()
                # SSID is still quoted in wpa_supplicant (with special chars escaped)
                self.assertIn('ssid="My\\"SSID\\";$\\\\"', wpa_content)
                # Plain text password must NOT appear — verify hex PSK is present
                self.assertNotIn('psk="', wpa_content, "Plain text password must NOT appear in wpa_supplicant.conf")
                
                # Check NetworkManager profile
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path))
                
                config = configparser.ConfigParser(inline_comment_prefixes=None)
                config.read(nm_path, encoding="utf-8")
                self.assertEqual(config.get("wifi", "ssid"), 'My"SSID";$\\')
                from backend.imager import _compute_wpa_psk
                expected_escaped_psk = _compute_wpa_psk(wifi_ssid, wifi_password)
                self.assertEqual(config.get("wifi-security", "psk"), expected_escaped_psk)
                
                # Check custom.toml — must NOT contain WiFi credentials (HIGH-01 fix)
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                with open(custom_toml_path, "r", encoding="utf-8") as f:
                    toml_content = f.read()
                self.assertNotIn('[wlan]', toml_content,
                    "custom.toml must not contain a [wlan] section (HIGH-01 security fix)")
                self.assertNotIn('password =', toml_content,
                    "custom.toml must not contain a plaintext WiFi password")
                # Hostname and SSH must still be present
                self.assertIn('hostname = "kace-test"', toml_content)
                self.assertIn('[ssh]', toml_content)
                
                try:
                    import tomllib
                    with open(custom_toml_path, "rb") as f:
                        data = tomllib.load(f)
                    # [wlan] section must be absent from the TOML structure
                    self.assertNotIn('wlan', data,
                        "custom.toml must not have a wlan key (HIGH-01 security fix)")
                except ImportError:
                    self.assertIn('hostname = "kace-test"', toml_content)
                    self.assertIn('password_authentication = true', toml_content)
        finally:
            shutil.rmtree(temp_boot)

    def test_hostname_invalid_rejection(self):
        """
        Test that hostname with invalid characters (spaces, dots beyond subdomain,
        special chars) is rejected gracefully by raising a ValueError.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            invalid_hostnames = [
                "kace space",
                "kace$special",
                ".kace",
                "kace.",
                "-kace",
                "kace-",
                "kace..local",
            ]
            
            for hn in invalid_hostnames:
                with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                    with self.assertRaises(ValueError, msg=f"Hostname '{hn}' should be rejected"):
                        inject_config(
                            disk_number=99,
                            hostname=hn,
                            wifi_ssid="ssid",
                            wifi_password="pwd",
                            ssh_password="pwd",
                            dashboard_ui="mainsail"
                        )
        finally:
            shutil.rmtree(temp_boot)

    def test_password_hash_crypt_verification(self):
        """
        Extend the SHA-512 test to verify the hash is actually valid against the original
        plaintext using crypt.crypt() or equivalent (pcrypt.crypt).
        """
        import pcrypt
        pwd = "my_secure_kace_password_123"
        hashed = hash_password(pwd)
        
        self.assertTrue(hashed.startswith("$6$"))
        verification = pcrypt.crypt(pwd, hashed)
        self.assertEqual(verification, hashed)
        
        wrong_verification = pcrypt.crypt("wrong_password", hashed)
        self.assertNotEqual(wrong_verification, hashed)

    def test_dashboard_ui_both_injection(self):
        """
        Verify that when dashboard_ui="both" is passed, the kace-bootstrap.txt
        correctly reflects DASHBOARD=both and that no files are missing or malformed.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-both",
                    wifi_ssid="ssid",
                    wifi_password="pwd",
                    ssh_password="pwd",
                    dashboard_ui="both"
                )
                self.assertTrue(success)
                
                # Check kace-bootstrap.txt
                bootstrap_path = os.path.join(temp_boot, "kace-bootstrap.txt")
                self.assertTrue(os.path.exists(bootstrap_path))
                with open(bootstrap_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("DASHBOARD=both\n", content)
                
                # Check userconf.txt
                userconf_path = os.path.join(temp_boot, "userconf.txt")
                self.assertTrue(os.path.exists(userconf_path))
                
                # Check custom.toml
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                
                # Check legacy wpa_supplicant
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path))
                
                # Check modern NetworkManager profile
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path))
                
        finally:
            shutil.rmtree(temp_boot)

    def test_bootstrap_argument_priority(self):
        """
        Simulate a scenario where both CLI arguments and a kace-bootstrap.txt file
        are present with conflicting values. Assert that the explicitly passed argument
        always wins.
        """
        import subprocess
        import tempfile
        import shutil
        
        try:
            subprocess.run(["bash", "--version"], capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            raise unittest.SkipTest("bash not available on this platform")
            
        temp_dir = tempfile.mkdtemp()
        try:
            mock_boot_cfg = os.path.join(temp_dir, "kace-bootstrap.txt")
            with open(mock_boot_cfg, "w") as f:
                f.write("DASHBOARD=fluidd\n")
                f.write("CROWSNEST=true\n")
                f.write("TIMEZONE=Europe/London\n")
                
            bootstrap_sh_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bootstrap.sh")
            with open(bootstrap_sh_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            posix_boot_cfg_path = mock_boot_cfg.replace("\\", "/")
            
            modified_content = content.replace(
                '/boot/firmware/kace-bootstrap.txt', posix_boot_cfg_path
            ).replace(
                '/boot/kace-bootstrap.txt', posix_boot_cfg_path
            ).replace(
                'exec > >(tee -i "$LOG_FILE") 2>&1', '# exec > >(tee -i "$LOG_FILE") 2>&1'
            )
            
            target_str = 'echo "--------------------------------------------------------"'
            idx = modified_content.find(target_str)
            if idx != -1:
                next_idx = modified_content.find(target_str, idx + len(target_str))
                if next_idx != -1:
                    insert_pos = next_idx + len(target_str)
                    modified_content = modified_content[:insert_pos] + "\nexit 0\n" + modified_content[insert_pos:]
            
            temp_script = os.path.join(temp_dir, "bootstrap_test.sh")
            with open(temp_script, "w", newline="\n", encoding="utf-8") as f:
                f.write(modified_content)
                
            res = subprocess.run([
                "bash", temp_script,
                "--dashboard", "mainsail",
                "--crowsnest", "false",
                "--timezone", "America/New_York"
            ], capture_output=True, text=True)
            
            self.assertEqual(res.returncode, 0)
            self.assertIn("Dashboard UI : mainsail", res.stdout)
            self.assertIn("Webcam Stream: false", res.stdout)
            self.assertIn("Timezone     : America/New_York", res.stdout)
            
        finally:
            shutil.rmtree(temp_dir)

    def test_unwritable_boot_partition_handling(self):
        """
        Mock the boot path as a read-only or non-existent directory.
        Assert that inject_config fails gracefully with a clear return value (False)
        and not a raw unhandled exception.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        with patch("backend.imager.get_boot_drive_letter", return_value="/nonexistent/boot/path"):
            success = inject_config(
                disk_number=99,
                hostname="kace-test",
                wifi_ssid="ssid",
                wifi_password="pwd",
                ssh_password="pwd",
                dashboard_ui="mainsail"
            )
            self.assertFalse(success)
            
        temp_boot = tempfile.mkdtemp()
        try:
            original_open = open
            def mock_open(file, mode="r", *args, **kwargs):
                if temp_boot in str(file) and "w" in mode:
                    raise PermissionError("[Errno 13] Permission denied (Mocked read-only)")
                return original_open(file, mode, *args, **kwargs)
                
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                with patch("builtins.open", side_effect=mock_open):
                    success = inject_config(
                        disk_number=99,
                        hostname="kace-test",
                        wifi_ssid="ssid",
                        wifi_password="pwd",
                        ssh_password="pwd",
                        dashboard_ui="mainsail"
                    )
                    self.assertFalse(success)
        finally:
            shutil.rmtree(temp_boot)

    def test_probe_ip_ports_known_open_port(self):
        """
        Test probe_ip_ports against 127.0.0.1 on a port you open temporarily
        with socket.socket in the test setup. Assert it correctly returns True for that port.
        """
        import socket
        from backend.discovery import probe_ip_ports
        
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]
        
        try:
            results = probe_ip_ports("127.0.0.1", ports=[port], timeout=0.1)
            self.assertTrue(results.get(port), f"Port {port} should report open")
        finally:
            server_sock.close()

    def test_get_boot_drive_letter_exception_safety(self):
        """
        Add at least one test that calls the real (non-mocked) get_boot_drive_letter
        and asserts it returns either a valid path string or None/"" — never raises an exception,
        regardless of OS.
        """
        from backend.imager import get_boot_drive_letter
        try:
            result = get_boot_drive_letter(9999)
            self.assertTrue(isinstance(result, str) or result is None)
        except Exception as e:
            self.fail(f"get_boot_drive_letter raised an exception: {e}")

    def test_list_drives_non_windows_empty(self):
        """
        Assert that on non-Windows platforms list_drives() returns an empty list.
        """
        from backend.imager import list_drives
        from unittest.mock import patch
        
        with patch("sys.platform", "linux"):
            result = list_drives()
            self.assertEqual(result, [])

    def test_build_boot_path_formatting(self):
        """
        Unit test for the path construction helper _build_boot_path.
        """
        from backend.imager import _build_boot_path
        self.assertEqual(_build_boot_path("E"), "E:\\")
        self.assertEqual(_build_boot_path("Z"), "Z:\\")
        self.assertEqual(_build_boot_path(""), "")
        self.assertEqual(_build_boot_path(None), "")
        self.assertEqual(_build_boot_path("/mock/path/dir"), "/mock/path/dir")
        self.assertEqual(_build_boot_path("C:\\temp\\dir"), "C:\\temp\\dir")

    def test_inject_config_integration_real_write(self):
        """
        Verify inject_config end-to-end using a real temp directory.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-integration",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail",
                    timezone="America/Sao_Paulo"
                )
                self.assertTrue(success)
                
                # Check that all expected files were actually written to the disk partition
                userconf_path = os.path.join(temp_boot, "userconf.txt")
                self.assertTrue(os.path.exists(userconf_path))
                with open(userconf_path, "r", encoding="utf-8") as f:
                    userconf_content = f.read()
                self.assertTrue(userconf_content.startswith("kace:$6$"))
                
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path))
                
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path))
                
                bootstrap_cfg = os.path.join(temp_boot, "kace-bootstrap.txt")
                self.assertTrue(os.path.exists(bootstrap_cfg))
                
                custom_toml_path = os.path.join(temp_boot, "custom.toml")
                self.assertTrue(os.path.exists(custom_toml_path))
                with open(custom_toml_path, "r", encoding="utf-8") as f:
                    toml_content = f.read()
                
                ssh_path = os.path.join(temp_boot, "ssh")
                self.assertTrue(os.path.exists(ssh_path))
                ssh_txt_path = os.path.join(temp_boot, "ssh.txt")
                self.assertTrue(os.path.exists(ssh_txt_path))
                
                # Verify that kace-bootstrap.txt has no leading whitespace on any line
                with open(bootstrap_cfg, "r", encoding="utf-8") as f:
                    for line in f:
                        self.assertEqual(line, line.lstrip(), f"Line '{line}' has leading whitespace")
                        
                # Verify that custom.toml has no leading whitespace on keys
                with open(custom_toml_path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped and not stripped.startswith("["):
                            self.assertEqual(line.lstrip(), line, f"Line '{line}' has leading whitespace")
        finally:
            shutil.rmtree(temp_boot)

    def test_userconf_hash_verification(self):
        """
        Verify that userconf.txt contains a valid SHA-512 crypt hash (Modular Crypt Format)
        and verifies successfully against the plaintext using pcrypt.
        """
        import tempfile
        import shutil
        import pcrypt
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-hash-test",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="mysecretpassword",
                    dashboard_ui="mainsail"
                )
                self.assertTrue(success)
                
                userconf_path = os.path.join(temp_boot, "userconf.txt")
                self.assertTrue(os.path.exists(userconf_path))
                with open(userconf_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                
                # Expected format: username:hash
                parts = content.split(":")
                self.assertEqual(len(parts), 2)
                self.assertEqual(parts[0], "kace")
                
                hashed = parts[1]
                self.assertTrue(hashed.startswith("$6$"))
                
                # Cryptographic verification against plaintext
                verification = pcrypt.crypt("mysecretpassword", hashed)
                self.assertEqual(verification, hashed)
                
                # Negative check
                self.assertNotEqual(pcrypt.crypt("wrong_password", hashed), hashed)
        finally:
            shutil.rmtree(temp_boot)

    def test_compute_wpa_psk(self):
        """
        Verify that _compute_wpa_psk produces correct hex output for known SSID/password pairs.
        """
        from backend.imager import _compute_wpa_psk
        result = _compute_wpa_psk("Root", "SalociN20")
        self.assertEqual(result, "096995fe921ae6d8e570d57a8dcb56c14f11d7841a4e20843b4b5d5bc3b44021")

    def test_cloud_init_injection(self):
        """
        Verify that user-data, network-config, meta-data are written with correct values,
        and cmdline.txt is patched correctly.
        """
        import tempfile
        import shutil
        import re
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            # Create a mock cmdline.txt in the temp dir to simulate a real image
            cmdline_path = os.path.join(temp_boot, "cmdline.txt")
            with open(cmdline_path, "w") as f:
                f.write("console=serial0,115200 root=/dev/mmcblk0p2 rootwait\n")
                
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-cloud",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail",
                    timezone="America/Sao_Paulo"
                )
                self.assertTrue(success)
                
                # 1. Verify user-data
                userdata_path = os.path.join(temp_boot, "user-data")
                self.assertTrue(os.path.exists(userdata_path))
                with open(userdata_path, "r", encoding="utf-8") as f:
                    userdata = f.read()
                self.assertIn("hostname: kace-cloud", userdata)
                self.assertIn("timezone: America/Sao_Paulo", userdata)
                self.assertIn("name: kace", userdata)
                self.assertIn('passwd: "$6$', userdata)  # Verify SHA-512 hash format
                self.assertIn("enable_ssh: true", userdata)
                self.assertIn("ssh_pwauth: true", userdata)
                
                # 2. Verify network-config
                network_path = os.path.join(temp_boot, "network-config")
                self.assertTrue(os.path.exists(network_path))
                with open(network_path, "r", encoding="utf-8") as f:
                    network_data = f.read()
                self.assertIn('version: 2', network_data)
                self.assertIn('wifis:', network_data)
                self.assertIn('regulatory-domain: "BR"', network_data)
                self.assertIn('"MySSID":', network_data)
                # Netplan's password field expects plaintext, NOT hex PSK
                self.assertIn('password: "MyPassword"', network_data)
                
                # 3. Verify meta-data
                metadata_path = os.path.join(temp_boot, "meta-data")
                self.assertTrue(os.path.exists(metadata_path))
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = f.read().strip()
                match = re.match(r"^instance-id: kace-([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$", metadata)
                self.assertIsNotNone(match, f"Invalid meta-data instance-id format: {metadata}")
                instance_uuid = match.group(1)
                
                # 4. Verify cmdline.txt is patched correctly
                with open(cmdline_path, "r", encoding="utf-8") as f:
                    cmdline = f.read().strip()
                self.assertIn("console=serial0,115200 root=/dev/mmcblk0p2 rootwait", cmdline)
                self.assertIn("cfg80211.ieee80211_regdom=BR", cmdline)
                self.assertIn(f"ds=nocloud;i=kace-{instance_uuid}", cmdline)
                
        finally:
            shutil.rmtree(temp_boot)

    def test_cloud_init_empty_wifi(self):
        """
        Verify that the wifis section is omitted from network-config when wifi_ssid is empty.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-cloud",
                    wifi_ssid="",
                    wifi_password="",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail"
                )
                self.assertTrue(success)
                
                network_path = os.path.join(temp_boot, "network-config")
                self.assertTrue(os.path.exists(network_path))
                with open(network_path, "r", encoding="utf-8") as f:
                    network_data = f.read()
                self.assertIn('version: 2', network_data)
                self.assertIn('eth0:', network_data)
                self.assertNotIn('wifis:', network_data)
                self.assertNotIn('wlan0:', network_data)
                
        finally:
            shutil.rmtree(temp_boot)

    def test_cloud_init_missing_cmdline_handling(self):
        """
        Verify that missing cmdline.txt is handled gracefully (skips patch without failing inject_config).
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            # cmdline.txt is deliberately NOT created in temp_boot
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-cloud",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail"
                )
                # Should return True because cmdline.txt missing is skipped gracefully
                self.assertTrue(success)
                
                # Check other cloud-init files are still written
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "user-data")))
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "network-config")))
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "meta-data")))
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "cmdline.txt")))
                
        finally:
            shutil.rmtree(temp_boot)

    def test_bootstrap_local_copy_and_version_injection(self):
        """
        Verify that inject_config copies bootstrap.sh to the boot partition,
        injects a version comment at the top, and uses Unix line endings (\n).
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-local-test",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail"
                )
                self.assertTrue(success)
                
                # Check that bootstrap.sh was copied to temp_boot
                dest_bootstrap = os.path.join(temp_boot, "bootstrap.sh")
                self.assertTrue(os.path.exists(dest_bootstrap))
                
                # Read content as binary to verify Unix line endings and check the first line version comment
                with open(dest_bootstrap, "rb") as f:
                    content_bytes = f.read()
                    
                # Ensure no \r\n (CRLF) exists in the file
                self.assertNotIn(b"\r\n", content_bytes)
                
                # Verify that it starts with the KACE Bootstrap Version comment
                lines = content_bytes.split(b"\n")
                self.assertTrue(lines[0].startswith(b"# KACE Bootstrap Version:"))
                
        finally:
            shutil.rmtree(temp_boot)

    def test_sftp_list_directory_returns_empty_when_not_connected(self):
        from backend.ssh_client import SSHSession
        session = SSHSession()
        self.assertEqual(session.list_directory("/home/kace"), [])

    def test_sftp_download_file_returns_false_when_not_connected(self):
        from backend.ssh_client import SSHSession
        session = SSHSession()
        self.assertFalse(session.download_file("/home/kace/file.txt", "/tmp/file.txt"))

    def test_sftp_list_directory_mocked_sftp(self):
        from backend.ssh_client import SSHSession
        from unittest.mock import MagicMock, patch
        import stat
        
        session = SSHSession()
        session.client = MagicMock()
        mock_sftp = MagicMock()
        
        mock_attr1 = MagicMock()
        mock_attr1.filename = "file.txt"
        mock_attr1.st_mode = stat.S_IFREG | 0o644
        mock_attr1.st_size = 123
        mock_attr1.st_mtime = 1600000000
        
        mock_attr2 = MagicMock()
        mock_attr2.filename = "folder"
        mock_attr2.st_mode = stat.S_IFDIR | 0o755
        mock_attr2.st_size = 4096
        mock_attr2.st_mtime = 1600000001
        
        mock_sftp.listdir_attr.return_value = [mock_attr1, mock_attr2]
        
        with patch.object(session, "get_sftp", return_value=mock_sftp):
            res = session.list_directory("/home/kace")
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0]["name"], "file.txt")
            self.assertFalse(res[0]["is_dir"])
            self.assertEqual(res[0]["size"], 123)
            self.assertEqual(res[0]["modified"], 1600000000)
            
            self.assertEqual(res[1]["name"], "folder")
            self.assertTrue(res[1]["is_dir"])
            self.assertEqual(res[1]["size"], 4096)
            self.assertEqual(res[1]["modified"], 1600000001)
            
            mock_sftp.close.assert_called_once()

    def test_api_sftp_list_connected(self):
        from main import Api, KaceWsgiApp
        from unittest.mock import MagicMock, patch
        import json
        
        api = Api()
        api._ssh = MagicMock()
        api._ssh.list_directory.return_value = [
            {"name": "file.txt", "is_dir": False, "size": 123, "modified": 1600000000}
        ]
        
        app = KaceWsgiApp(".", api)
        
        environ = {
            "PATH_INFO": "/api/sftp/list",
            "QUERY_STRING": "path=/home/kace"
        }
        
        headers = []
        def start_response(status, response_headers):
            headers.append(status)
            headers.extend(response_headers)
            
        response = app(environ, start_response)
        
        self.assertIn("200 OK", headers[0])
        response_body = b"".join(response).decode("utf-8")
        data = json.loads(response_body)
        self.assertEqual(data["path"], "/home/kace")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["name"], "file.txt")

    def test_inject_config_prebaked_flag(self):
        """
        Tests that inject_config properly writes PREBAKED=true or PREBAKED=false
        to the kace-bootstrap.txt config file depending on is_prebaked parameter.
        Also verifies that cloud-init files are NOT created for prebaked images
        and ARE created for non-prebaked images.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch
        
        temp_boot = tempfile.mkdtemp()
        try:
            # Create a cmdline.txt fixture (required by the prebaked path)
            cmdline_path = os.path.join(temp_boot, "cmdline.txt")
            with open(cmdline_path, "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootfstype=ext4 rootwait\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                # Test with is_prebaked=True
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test.local",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=True
                )
                self.assertTrue(success)
                
                cfg_path = os.path.join(temp_boot, "kace-bootstrap.txt")
                self.assertTrue(os.path.exists(cfg_path))
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("PREBAKED=true", content)

                # Cloud-init files must NOT exist for prebaked images
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "user-data")),
                                 "user-data must NOT be created for prebaked images")
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "meta-data")),
                                 "meta-data must NOT be created for prebaked images")
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "network-config")),
                                 "network-config must NOT be created for prebaked images")

                # cmdline.txt must NOT contain ds=nocloud
                with open(cmdline_path, "r", encoding="utf-8") as f:
                    cmdline = f.read()
                self.assertNotIn("ds=nocloud", cmdline,
                                 "cmdline.txt must NOT contain ds=nocloud for prebaked images")
                # But should have the regulatory domain
                self.assertIn("cfg80211.ieee80211_regdom=", cmdline)
                
                # Clean up for next test
                for fname in os.listdir(temp_boot):
                    fpath = os.path.join(temp_boot, fname)
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                    elif os.path.isdir(fpath):
                        shutil.rmtree(fpath)

                # Re-create cmdline.txt fixture for non-prebaked test
                with open(cmdline_path, "w") as f:
                    f.write("console=tty1 root=PARTUUID=abc-02 rootfstype=ext4 rootwait\n")

                # Test with is_prebaked=False
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test.local",
                    wifi_ssid="MySSID",
                    wifi_password="MyPassword",
                    ssh_password="kacepwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=False
                )
                self.assertTrue(success)
                self.assertTrue(os.path.exists(cfg_path))
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("PREBAKED=false", content)

                # Cloud-init files MUST exist for non-prebaked (vanilla RPi OS)
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "user-data")),
                                "user-data must be created for non-prebaked images")
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "meta-data")),
                                "meta-data must be created for non-prebaked images")
                self.assertTrue(os.path.exists(os.path.join(temp_boot, "network-config")),
                                "network-config must be created for non-prebaked images")

                # cmdline.txt must contain ds=nocloud for non-prebaked
                with open(cmdline_path, "r", encoding="utf-8") as f:
                    cmdline = f.read()
                self.assertIn("ds=nocloud", cmdline,
                              "cmdline.txt must contain ds=nocloud for non-prebaked images")
                
        finally:
            shutil.rmtree(temp_boot)

    def test_prebaked_cleans_existing_cloud_init_files(self):
        """
        Verify that when is_prebaked=True, any pre-existing cloud-init files
        on the boot partition (from the base MainsailOS image) are removed.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch

        temp_boot = tempfile.mkdtemp()
        try:
            # Simulate pre-existing cloud-init files from the base image
            for ci_file in ["user-data", "meta-data", "network-config"]:
                with open(os.path.join(temp_boot, ci_file), "w") as f:
                    f.write(f"dummy {ci_file} content\n")

            # Simulate a cmdline.txt with ds=nocloud already present
            cmdline_path = os.path.join(temp_boot, "cmdline.txt")
            with open(cmdline_path, "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootwait ds=nocloud;i=old-uuid\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid="TestNet",
                    wifi_password="TestPass",
                    ssh_password="pwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=True
                )
                self.assertTrue(success)

                # All cloud-init files must be removed
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "user-data")))
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "meta-data")))
                self.assertFalse(os.path.exists(os.path.join(temp_boot, "network-config")))

                # ds=nocloud must be stripped from cmdline.txt
                with open(cmdline_path, "r", encoding="utf-8") as f:
                    cmdline = f.read()
                self.assertNotIn("ds=nocloud", cmdline)
                self.assertNotIn("old-uuid", cmdline)
                # But the rest of cmdline should be preserved
                self.assertIn("console=tty1", cmdline)
                self.assertIn("rootwait", cmdline)
        finally:
            shutil.rmtree(temp_boot)

    def test_non_prebaked_network_config_uses_plaintext_password(self):
        """
        Verify that for non-prebaked images, the network-config file uses the
        plaintext WiFi password (Netplan expects plaintext), NOT the hex PSK.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config, _compute_wpa_psk
        from unittest.mock import patch

        temp_boot = tempfile.mkdtemp()
        try:
            # Create cmdline.txt fixture
            with open(os.path.join(temp_boot, "cmdline.txt"), "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootwait\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid="TestNet",
                    wifi_password="MySecretPass",
                    ssh_password="pwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=False
                )
                self.assertTrue(success)

                # network-config should have the plaintext password
                nc_path = os.path.join(temp_boot, "network-config")
                self.assertTrue(os.path.exists(nc_path))
                with open(nc_path, "r", encoding="utf-8") as f:
                    nc_content = f.read()
                self.assertIn("MySecretPass", nc_content,
                              "network-config must use plaintext password for Netplan")

                # wpa_supplicant.conf should still use hex PSK (not plaintext)
                hex_psk = _compute_wpa_psk("TestNet", "MySecretPass")
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                with open(wpa_path, "r", encoding="utf-8") as f:
                    wpa_content = f.read()
                self.assertIn(f"psk={hex_psk}", wpa_content)
                self.assertNotIn('psk="MySecretPass"', wpa_content,
                                 "wpa_supplicant.conf must NOT contain plaintext password")
        finally:
            shutil.rmtree(temp_boot)

    def test_prebaked_preserves_nm_and_wpa_configs(self):
        """
        Verify that prebaked mode still writes wpa_supplicant.conf and
        system-connections/preconfigured-wifi.nmconnection (these are the
        correct WiFi config mechanisms for MainsailOS).
        """
        import tempfile
        import shutil
        from backend.imager import inject_config, _compute_wpa_psk
        from unittest.mock import patch

        temp_boot = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_boot, "cmdline.txt"), "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootwait\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid="TestNet",
                    wifi_password="TestPass",
                    ssh_password="pwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=True
                )
                self.assertTrue(success)

                # wpa_supplicant.conf must still be created
                wpa_path = os.path.join(temp_boot, "wpa_supplicant.conf")
                self.assertTrue(os.path.exists(wpa_path),
                                "wpa_supplicant.conf must be created even for prebaked images")
                with open(wpa_path, "r", encoding="utf-8") as f:
                    wpa_content = f.read()
                hex_psk = _compute_wpa_psk("TestNet", "TestPass")
                self.assertIn(f"psk={hex_psk}", wpa_content)

                # NM connection profile must still be created
                nm_path = os.path.join(temp_boot, "system-connections", "preconfigured-wifi.nmconnection")
                self.assertTrue(os.path.exists(nm_path),
                                "NM connection profile must be created even for prebaked images")
                with open(nm_path, "r", encoding="utf-8") as f:
                    nm_content = f.read()
                self.assertIn("ssid=TestNet", nm_content)
                self.assertIn(f"psk={hex_psk}", nm_content)
        finally:
            shutil.rmtree(temp_boot)

    def test_prebaked_writes_headless_nm_txt(self):
        """
        Verify that prebaked mode writes headless_nm.txt to the boot partition
        so that MainsailOS's headless_nm.service can configure WiFi on first boot.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch

        temp_boot = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_boot, "cmdline.txt"), "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootwait\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid="MyNetwork",
                    wifi_password="MySecretPass",
                    ssh_password="pwd123",
                    dashboard_ui="mainsail",
                    timezone="America/New_York",
                    is_prebaked=True
                )
                self.assertTrue(success)

                # headless_nm.txt must exist
                hl_path = os.path.join(temp_boot, "headless_nm.txt")
                self.assertTrue(os.path.exists(hl_path),
                                "headless_nm.txt must be created for prebaked images")
                with open(hl_path, "r", encoding="utf-8") as f:
                    hl_content = f.read()
                self.assertIn('SSID="MyNetwork"', hl_content)
                self.assertIn('PASSWORD="MySecretPass"', hl_content)
                self.assertIn('HIDDEN="false"', hl_content)
                self.assertIn('REGDOMAIN="US"', hl_content)
        finally:
            shutil.rmtree(temp_boot)

    def test_vanilla_writes_firstrun_sh(self):
        """
        Verify that vanilla (non-prebaked) mode writes firstrun.sh to the boot
        partition and appends systemd.run trigger to cmdline.txt, providing a
        fallback WiFi configuration mechanism for images without cloud-init.
        """
        import tempfile
        import shutil
        from backend.imager import inject_config
        from unittest.mock import patch

        temp_boot = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_boot, "cmdline.txt"), "w") as f:
                f.write("console=tty1 root=PARTUUID=abc-02 rootwait\n")

            with patch("backend.imager.get_boot_drive_letter", return_value=temp_boot):
                success = inject_config(
                    disk_number=99,
                    hostname="kace-test",
                    wifi_ssid="TestNet",
                    wifi_password="TestPass",
                    ssh_password="pwd123",
                    dashboard_ui="mainsail",
                    is_prebaked=False
                )
                self.assertTrue(success)

                # firstrun.sh must exist
                fr_path = os.path.join(temp_boot, "firstrun.sh")
                self.assertTrue(os.path.exists(fr_path),
                                "firstrun.sh must be created for vanilla images with WiFi")
                with open(fr_path, "r", encoding="utf-8") as f:
                    fr_content = f.read()
                self.assertIn("#!/bin/bash", fr_content)
                self.assertIn("preconfigured-wifi.nmconnection", fr_content)
                self.assertIn("chmod 600", fr_content)

                # cmdline.txt must contain systemd.run trigger
                with open(os.path.join(temp_boot, "cmdline.txt"), "r", encoding="utf-8") as f:
                    cmdline = f.read()
                self.assertIn("systemd.run=/boot/firmware/firstrun.sh", cmdline)
                self.assertIn("systemd.run_success_action=reboot", cmdline)
                self.assertIn("systemd.unit=kernel-command-line.target", cmdline)

                # headless_nm.txt must NOT exist for vanilla images
                hl_path = os.path.join(temp_boot, "headless_nm.txt")
                self.assertFalse(os.path.exists(hl_path),
                                 "headless_nm.txt must NOT be created for vanilla images")
        finally:
            shutil.rmtree(temp_boot)

if __name__ == "__main__":
    unittest.main()

