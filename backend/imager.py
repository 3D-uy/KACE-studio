import os
import sys
import json
import time
import subprocess
import uuid
import re
from pathlib import Path
from backend.sha512_crypt import hash_password

# Set KACE_DEBUG=1 in the environment to enable verbose path/status logging.
# Do NOT enable in packaged/production builds — logs leak filesystem paths.
_DEBUG = os.environ.get("KACE_DEBUG", "0") == "1"

def _dbg(msg: str):
    """Print a debug message to stderr only when KACE_DEBUG=1."""
    if _DEBUG:
        print(f"[DEBUG] {msg}", file=sys.stderr)

# Subprocess flags to run silent processes on Windows (CREATE_NO_WINDOW)
SUBPROCESS_FLAGS = {}
if sys.platform == "win32":
    SUBPROCESS_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW


# Default system username for the Pi provisioning
DEFAULT_USERNAME = "kace"

import pytz

# Maps timezone names to ISO 3166-1 alpha-2 WiFi regulatory country codes
# We build this dynamically using pytz to support ALL timezones in the world.
TIMEZONE_TO_COUNTRY = {}
try:
    for country_code, tzs in pytz.country_timezones.items():
        for tz in tzs:
            TIMEZONE_TO_COUNTRY[tz] = country_code
except Exception as e:
    print(f"Error building pytz country mapping: {e}", file=sys.stderr)

# Add custom/fallback overrides
TIMEZONE_TO_COUNTRY["America/Argentina"] = "AR"
TIMEZONE_TO_COUNTRY["America/Buenos_Aires"] = "AR"
TIMEZONE_TO_COUNTRY["UTC"] = "US"

def _get_country_from_timezone(timezone: str) -> str:
    """Resolves WiFi regulatory country code from a timezone string."""
    if timezone in TIMEZONE_TO_COUNTRY:
        return TIMEZONE_TO_COUNTRY[timezone]
    # Fallback: try matching timezone prefix (e.g. "America/Argentina/Buenos_Aires" → "America/Argentina")
    parts = timezone.split("/")
    if len(parts) >= 2:
        prefix = f"{parts[0]}/{parts[1]}"
        if prefix in TIMEZONE_TO_COUNTRY:
            return TIMEZONE_TO_COUNTRY[prefix]
    return "US"  # Safe default


def _compute_wpa_psk(ssid: str, password: str) -> str:
    """Computes a WPA-PSK hash from SSID and password using PBKDF2."""
    import hashlib
    return hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()



def list_drives() -> list:
    """
    Returns a list of removable/USB drives that are safe to flash.
    Excludes system, boot, and internal NVMe/SATA drives.
    """
    drives = []
    
    if sys.platform == "win32":
        try:
            # Query physical disks
            res = subprocess.run(["powershell", "-Command", "Get-Disk | Select-Object Number, FriendlyName, Size, BusType, IsSystem, IsBoot | ConvertTo-Json"], capture_output=True, text=True, encoding="utf-8", errors="replace", **SUBPROCESS_FLAGS)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                # If there's only one disk, ConvertTo-Json returns a dict instead of a list
                if isinstance(data, dict):
                    data = [data]
                
                for disk in data:
                    # Filter for USB, SD, or CardReader drives, and exclude system/boot drives
                    bus_type = disk.get("BusType", "").upper()
                    is_system = disk.get("IsSystem", False)
                    is_boot = disk.get("IsBoot", False)
                    
                    # We only show removable-type buses to prevent flashing the main OS drive
                    if bus_type in ("USB", "SD", "MMC", "1394") and not is_system and not is_boot:
                        size_gb = round(disk.get("Size", 0) / (1024**3), 2)
                        drives.append({
                            "id": disk.get("Number"),
                            "name": disk.get("FriendlyName", "Unknown Drive"),
                            "size": f"{size_gb} GB",
                            "bus": bus_type
                        })
        except Exception as e:
            print(f"Error listing Windows drives: {e}", file=sys.stderr)
            
    else:
        # Basic POSIX fallback: return empty list on non-Windows to avoid false assumptions in CI
        pass
        
    return drives

def _build_boot_path(letter: str) -> str:
    """
    Constructs the absolute path to the boot drive volume root from a letter.
    Allows easy unit testing in isolation by decoupling path formatting.
    """
    if not letter:
        return ""
    if len(letter) == 1 and letter.isalpha():
        return f"{letter}:\\"
    return letter

def get_boot_drive_letter(disk_number: int):
    """
    Finds the FAT32/FAT partition drive letter of the flashed SD card.
    """
    # SEC FIX: Use a proper runtime check instead of assert, which is silently
    # disabled when Python is invoked with the -O (optimize) flag used by PyInstaller.
    if not isinstance(disk_number, int):
        raise TypeError(f"disk_number must be an integer, got {type(disk_number).__name__}")
    if sys.platform != "win32":
        return None
        
    ps_cmd = f"""
$ErrorActionPreference = 'Stop'
try {{
    $part = Get-Partition -DiskNumber {disk_number} -PartitionNumber 1 -ErrorAction Stop
    if ($part) {{
        $letter = $part.DriveLetter
        if ($letter -eq [char]0 -or $letter -eq $null -or [string]::IsNullOrWhiteSpace($letter)) {{
            $freeLetter = (3..25 | ForEach-Object {{ [char]($_ + 65) }} | Where-Object {{ (Get-Volume -DriveLetter $_ -ErrorAction SilentlyContinue) -eq $null }} | Select-Object -First 1)
            if ($freeLetter) {{
                $part | Set-Partition -NewDriveLetter $freeLetter -ErrorAction Stop
                $part = Get-Partition -DiskNumber {disk_number} -PartitionNumber 1 -ErrorAction Stop
                $letter = $part.DriveLetter
            }}
        }}
        if ($letter -and $letter -ne [char]0) {{
            [PSCustomObject]@{{ DriveLetter = [string]$letter; FileSystem = (Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue).FileSystem }} | ConvertTo-Json
        }}
    }}
}} catch {{
    if ($_.Exception.Message -like "*Access is denied*" -or $_.CategoryInfo.Category -eq "PermissionDenied") {{
        Write-Output "ERROR: PRIVILEGE_REQUIRED"
    }} else {{
        Write-Error $_.Exception.Message
    }}
    exit 1
}}
"""
    for _ in range(5):  # Retry up to 5 times to let Windows mount the disk
        res = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", **SUBPROCESS_FLAGS)
        if "ERROR: PRIVILEGE_REQUIRED" in res.stdout or "PermissionDenied" in res.stdout or "PermissionDenied" in res.stderr:
            print("ERROR: Failed to assign drive letter to boot partition.", file=sys.stderr)
            print("Please re-run KACE Studio as Administrator.", file=sys.stderr)
            return None
            
        if res.returncode == 0 and res.stdout.strip():
            try:
                data = json.loads(res.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                for vol in data:
                    letter = vol.get("DriveLetter")
                    fs = vol.get("FileSystem", "").upper()
                    if letter and fs in ("FAT32", "FAT"):
                        boot_path = _build_boot_path(letter)
                        # Retry loop for OS to mount the volume path
                        for _ in range(10):
                            if os.path.exists(boot_path):
                                break
                            time.sleep(0.5)
                            
                        if os.path.exists(boot_path):
                            return boot_path
            except Exception as e:
                print(f"Error parsing partition volume: {e}", file=sys.stderr)
        time.sleep(1)
        
    return None

def flash_drive(disk_number: int, image_path: str, progress_callback=None) -> tuple:
    """
    Flashes the image block-by-block onto the target drive by spawning
    the elevated helper process kace_writer.py.
    """
    # SEC FIX: Runtime guard replacing assert (assert is disabled with -O).
    if not isinstance(disk_number, int):
        raise TypeError(f"disk_number must be an integer, got {type(disk_number).__name__}")
    if sys.platform != "win32":
        err = "Raw flashing is only fully supported on Windows in this MVP client."
        print(err, file=sys.stderr)
        return False, err
        
    if not os.path.exists(image_path):
        err = f"Image path does not exist: {image_path}"
        print(err, file=sys.stderr)
        return False, err

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    
    # Create a secure temp directory inside the user's home profile to avoid public temp vulnerabilities
    user_profile = os.environ.get("USERPROFILE")
    if user_profile and os.path.exists(user_profile):
        temp_dir = os.path.join(user_profile, ".kace", "temp")
    else:
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
        
    os.makedirs(temp_dir, exist_ok=True)
    status_file = os.path.join(temp_dir, f"kace_flash_{disk_number}.json")
    if os.path.exists(status_file):
        try:
            os.remove(status_file)
        except Exception:
            pass
            
    # Resolve executable and arguments
    if hasattr(sys, '_MEIPASS') or not sys.executable.lower().endswith("python.exe"):
        # Packaged mode
        arg_list = [
            "--write-disk",
            str(disk_number),
            str(image_path),
            str(status_file)
        ]
        exec_path = sys.executable
    else:
        # Dev mode
        main_py = os.path.join(project_root, "main.py")
        arg_list = [
            main_py,
            "--write-disk",
            str(disk_number),
            str(image_path),
            str(status_file)
        ]
        exec_path = sys.executable

    # Format arguments safely for Windows process creation (bypasses shell parser)
    def escape_windows_arg(arg: str) -> str:
        if not arg:
            return '""'
        if ' ' not in arg and '\t' not in arg and '"' not in arg:
            return arg
        escaped = []
        bs_count = 0
        for char in arg:
            if char == '\\':
                bs_count += 1
            elif char == '"':
                escaped.append('\\' * (2 * bs_count + 1))
                escaped.append('"')
                bs_count = 0
            else:
                if bs_count > 0:
                    escaped.append('\\' * bs_count)
                    bs_count = 0
                escaped.append(char)
        if bs_count > 0:
            escaped.append('\\' * (2 * bs_count))
        return '"' + ''.join(escaped) + '"'

    params_str = " ".join(escape_windows_arg(x) for x in arg_list)
    
    # Run using ctypes ShellExecuteExW to elevate securely
    import ctypes
    from ctypes import wintypes
    
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HANDLE),
            ("lpOperation", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HANDLE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]
        
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0
    
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.hwnd = None
    info.lpOperation = "runas"
    info.lpFile = exec_path
    info.lpParameters = params_str
    info.lpDirectory = None
    info.nShow = SW_HIDE
    
    shell32 = ctypes.windll.shell32
    res = shell32.ShellExecuteExW(ctypes.byref(info))
    if not res:
        return False, "Administrative privilege prompt was declined or failed to launch."
        
    hProcess = info.hProcess
    if not hProcess:
        return False, "Failed to get process handle for the elevated helper."
        
    error_msg = ""
    success = False
    try:
        kernel32 = ctypes.windll.kernel32
        STILL_ACTIVE = 0x00000103
        exit_code = wintypes.DWORD(STILL_ACTIVE)
        
        last_progress = 0
        
        while True:
            if not kernel32.GetExitCodeProcess(hProcess, ctypes.byref(exit_code)):
                break
            if exit_code.value != STILL_ACTIVE:
                break
                
            time.sleep(0.2)
            if os.path.exists(status_file):
                try:
                    with open(status_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    status = data.get("status")
                    progress = data.get("progress", 0)
                    message = data.get("message", "")
                    
                    if progress_callback and progress != last_progress:
                        progress_callback(progress)
                        last_progress = progress
                        
                    if status == "success":
                        success = True
                    elif status == "error":
                        error_msg = message
                        success = False
                except json.JSONDecodeError as decode_err:
                    print(f"Warning: progress status file was partially written: {decode_err}", file=sys.stderr)
                except Exception:
                    pass
                    
        # Final status check once process exits
        if os.path.exists(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") == "success":
                    success = True
                else:
                    success = False
                    error_msg = data.get("message", "Helper reported failure.")
                # Clean up status file
                os.remove(status_file)
            except Exception:
                pass
                
        if not success and not error_msg:
            # Read actual process exit code for diagnostics
            final_exit_code = wintypes.DWORD(0)
            kernel32.GetExitCodeProcess(hProcess, ctypes.byref(final_exit_code))
            error_msg = (
                f"Elevated helper exited without writing completion status "
                f"(exit code: {final_exit_code.value}). "
                f"This may indicate a crash or missing dependency in the writer process."
            )
            
        return success, error_msg
        
    finally:
        ctypes.windll.kernel32.CloseHandle(hProcess)

def inject_config(disk_number: int, hostname: str, wifi_ssid: str, wifi_password: str, ssh_password: str, dashboard_ui: str, timezone: str = "", pi_model: str = "", os_arch: str = "", ssh_enabled: bool = True, crowsnest: bool = False, username: str = "kace", password_auth: bool = True, is_prebaked: bool = False) -> bool:
    """
    Injects SSH enablement, User credentials, WiFi configuration (wpa_supplicant + NetworkManager),
    and hostname parameters directly to the FAT32 boot partition.
    """
    # SEC FIX: Runtime guard replacing assert (assert is disabled with -O).
    if not isinstance(disk_number, int):
        raise TypeError(f"disk_number must be an integer, got {type(disk_number).__name__}")
    # Pre-flight: server-side validation of username (mirrors client-side regex in app.js)
    _USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_-]*$')
    if not username or not _USERNAME_RE.match(username):
        raise ValueError(
            "Invalid username. Must start with a lowercase letter or underscore "
            "and contain only lowercase letters, numbers, hyphens, or underscores."
        )

    # Sanitize free-text fields: strip characters that could inject extra lines
    # into key=value config files or corrupt YAML structure.
    clean_timezone = re.sub(r'[\r\n=]', '', timezone) if timezone else ''
    clean_pi_model = re.sub(r'[\r\n=]', '', pi_model) if pi_model else ''
    clean_os_arch  = re.sub(r'[\r\n=]', '', os_arch)  if os_arch  else ''

    # Wait for OS mount
    boot_path = get_boot_drive_letter(disk_number)
    _dbg(f"get_boot_drive_letter({disk_number}) resolved to: '{boot_path}'")
    if not boot_path or not os.path.exists(boot_path):
        print(f"FAT32 boot partition not mounted or not found on physical disk {disk_number}.", file=sys.stderr)
        return False

    try:
        # A. Hostname validation and sanitization
        # Regex: must start/end with alphanumeric, no consecutive dots, only [a-zA-Z0-9.-]
        _HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$')
        if not hostname:
            clean_hostname = "kace"
        else:
            clean_hostname = hostname.replace(".local", "")
            # Reject spaces, consecutive dots, leading/trailing dots or hyphens, and any other invalid chars
            if (' ' in clean_hostname
                    or '..' in clean_hostname
                    or clean_hostname.startswith('.')
                    or clean_hostname.endswith('.')
                    or clean_hostname.startswith('-')
                    or clean_hostname.endswith('-')
                    or not _HOSTNAME_RE.match(clean_hostname)):
                raise ValueError("Invalid hostname. Hostname must contain only alphanumeric characters, dots, and hyphens, and cannot start or end with a dot or hyphen, and must not contain consecutive dots.")
            
        # B. WiFi input sanitization/escaping to prevent shell/ini/file structure injection
        clean_wpa_ssid = wifi_ssid.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '').replace('\r', '')
        clean_wpa_password = wifi_password.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '').replace('\r', '')
        
        clean_nm_ssid = wifi_ssid.replace('\n', '').replace('\r', '')
        clean_nm_password = wifi_password.replace('\n', '').replace('\r', '')
        
        clean_toml_ssid = wifi_ssid.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '').replace('\r', '')
        clean_toml_password = wifi_password.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '').replace('\r', '')

        # C. SSH Enablement
        if ssh_enabled:
            ssh_file = os.path.join(boot_path, "ssh")
            try:
                with open(ssh_file, "w") as f:
                    pass # Writes empty file to enable SSH
                if not os.path.exists(ssh_file):
                    raise IOError(f"SSH enablement file not found at: {ssh_file}")
            except Exception as e:
                print(f"[ERROR] Failed to verify SSH enablement file: {e}", file=sys.stderr)
                raise e
                
            ssh_txt_file = os.path.join(boot_path, "ssh.txt")
            try:
                with open(ssh_txt_file, "w") as f:
                    pass
                if not os.path.exists(ssh_txt_file):
                    raise IOError(f"SSH txt enablement file not found at: {ssh_txt_file}")
            except Exception as e:
                print(f"[ERROR] Failed to verify SSH txt enablement file: {e}", file=sys.stderr)
                raise e
            
        # D. User Credentials configuration (userconf.txt)
        hashed_pw = hash_password(ssh_password)
        userconf_file = os.path.join(boot_path, "userconf.txt")
        try:
            with open(userconf_file, "w", newline="\n") as f:
                f.write(f"{username}:{hashed_pw}\n")
            if not os.path.exists(userconf_file):
                raise IOError(f"userconf.txt file not found at: {userconf_file}")
            with open(userconf_file, "r", encoding="utf-8") as f_check:
                written_data = f_check.read()
            if not written_data.startswith(f"{username}:"):
                raise ValueError("userconf.txt content is malformed or corrupted.")
            _dbg(f"Successfully verified userconf.txt write at {userconf_file}")
        except Exception as e:
            print(f"[ERROR] Failed writing or verifying userconf.txt: {e}", file=sys.stderr)
            raise e
            
        # E. WiFi credentials
        # Ensure wpa_psk_hex is always computed before the NM profile section
        # (even if re-used later by network-config/cloud-init).
        if wifi_ssid:
            # Legacy: wpa_supplicant.conf (for Buster/Bullseye compatibility)
            country_code = _get_country_from_timezone(timezone) if timezone else "US"
            # C2: Use pre-computed PBKDF2 hex PSK — never store plain text password.
            # When psk= is a 64-char hex string (no quotes), wpa_supplicant treats it as the
            # raw WPA-PSK key, which is cryptographically equivalent but not reversible.
            wpa_psk_hex = _compute_wpa_psk(wifi_ssid, wifi_password)
            wpa_conf = os.path.join(boot_path, "wpa_supplicant.conf")
            wpa_content = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country={country_code}

network={{
    ssid="{clean_wpa_ssid}"
    psk={wpa_psk_hex}
    key_mgmt=WPA-PSK
}}
"""
            try:
                with open(wpa_conf, "w", newline="\n") as f:
                    f.write(wpa_content)
                if not os.path.exists(wpa_conf):
                    raise IOError(f"wpa_supplicant.conf not found at: {wpa_conf}")
                with open(wpa_conf, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if clean_wpa_ssid not in c or wpa_psk_hex not in c:
                    raise ValueError("wpa_supplicant.conf content is missing SSID or PSK credentials.")
                _dbg(f"Successfully verified wpa_supplicant.conf write at {wpa_conf}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying wpa_supplicant.conf: {e}", file=sys.stderr)
                raise e
                
            # Modern: NetworkManager connection profile (for Bookworm compatibility)
            nm_dir = os.path.join(boot_path, "system-connections")
            os.makedirs(nm_dir, exist_ok=True)
            nm_file = os.path.join(nm_dir, "preconfigured-wifi.nmconnection")

            # Generate a random UUID for the connection (uuid already imported at top of file)
            conn_uuid = str(uuid.uuid4())
            # C2 FIX: Use the pre-computed PBKDF2 hex PSK (same value as wpa_supplicant.conf)
            # instead of the plaintext password. NetworkManager treats a 64-char hex string
            # in the psk field as a raw WPA-PSK key, which is cryptographically equivalent
            # but not reversible to the original password.
            nm_content = f"""[connection]
id=preconfigured-wifi
uuid={conn_uuid}
type=wifi
interface-name=wlan0

[wifi]
mode=infrastructure
ssid={clean_nm_ssid}

[wifi-security]
auth-alg=open
key-mgmt=wpa-psk
psk={wpa_psk_hex}

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default-or-eui64
"""
            try:
                with open(nm_file, "w", newline="\n") as f:
                    f.write(nm_content)
                if not os.path.exists(nm_file):
                    raise IOError(f"preconfigured-wifi.nmconnection connection profile not found at: {nm_file}")
                with open(nm_file, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if clean_nm_ssid not in c or wpa_psk_hex not in c:
                    raise ValueError("preconfigured-wifi.nmconnection is missing SSID or PSK credentials.")
                _dbg(f"Successfully verified NetworkManager connection profile at {nm_file}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying NetworkManager connection profile: {e}", file=sys.stderr)
                raise e
                
        # F. Hostname configuration injection via cmdline.txt boot arguments
        cmdline_file = os.path.join(boot_path, "cmdline.txt")
        if os.path.exists(cmdline_file) and clean_hostname:
            try:
                with open(cmdline_file, "r") as f:
                    content = f.read().strip()
                # If systemd.hostname boot parameter is not already set
                if "systemd.hostname" not in content:
                    # Append parameter
                    content = f"{content} systemd.hostname={clean_hostname}"
                    with open(cmdline_file, "w", newline="\n") as f:
                        f.write(content + "\n")
                    # Verification check
                    with open(cmdline_file, "r", encoding="utf-8") as f_check:
                        c = f_check.read()
                    if f"systemd.hostname={clean_hostname}" not in c:
                        raise ValueError("cmdline.txt update verification failed.")
                    _dbg(f"Successfully verified cmdline.txt update at {cmdline_file}")
            except Exception as e:
                print(f"[ERROR] Failed updating or verifying cmdline.txt: {e}", file=sys.stderr)
                raise e
                    
        # G. Bootstrap Config injection
        bootstrap_cfg = os.path.join(boot_path, "kace-bootstrap.txt")
        try:
            with open(bootstrap_cfg, "w", newline="\n") as f:
                f.write(f"DASHBOARD={dashboard_ui}\n")
                f.write(f"CROWSNEST={'true' if crowsnest else 'false'}\n")
                f.write(f"PREBAKED={'true' if is_prebaked else 'false'}\n")
                if clean_timezone:
                    f.write(f"TIMEZONE={clean_timezone}\n")
                if clean_pi_model:
                    f.write(f"PI_MODEL={clean_pi_model}\n")
                if clean_os_arch:
                    f.write(f"OS_ARCH={clean_os_arch}\n")
            if not os.path.exists(bootstrap_cfg):
                raise IOError(f"kace-bootstrap.txt not found at: {bootstrap_cfg}")
            with open(bootstrap_cfg, "r", encoding="utf-8") as f_check:
                c = f_check.read()
            if f"DASHBOARD={dashboard_ui}" not in c:
                raise ValueError("kace-bootstrap.txt verification failed.")
            _dbg(f"Successfully verified kace-bootstrap.txt write at {bootstrap_cfg}")
        except Exception as e:
            print(f"[ERROR] Failed writing or verifying kace-bootstrap.txt: {e}", file=sys.stderr)
            raise e
                
        # H. Bookworm headless configuration (custom.toml)
        custom_toml_path = os.path.join(boot_path, "custom.toml")

        # SEC FIX (HIGH-01): The [wlan] section of custom.toml was previously storing
        # the WiFi password in plaintext. It is now omitted entirely.
        # WiFi connectivity is fully handled by:
        #   - wpa_supplicant.conf  (section E) — uses PBKDF2 hex PSK, non-reversible
        #   - nmconnection profile (section E) — uses PBKDF2 hex PSK, non-reversible
        # Both files are already written to the FAT32 boot partition above.
        # custom.toml retains hostname and SSH configuration only.
        toml_content = f"""config_version = 1

[system]
hostname = "{clean_hostname}"

[ssh]
enabled = {"true" if ssh_enabled else "false"}
password_authentication = {"true" if password_auth else "false"}
"""
        try:
            with open(custom_toml_path, "w", newline="\n") as f:
                f.write(toml_content)
            if not os.path.exists(custom_toml_path):
                raise IOError(f"custom.toml not found at: {custom_toml_path}")
            with open(custom_toml_path, "r", encoding="utf-8") as f_check:
                c = f_check.read()
            if f'hostname = "{clean_hostname}"' not in c:
                raise ValueError("custom.toml verification failed.")
            _dbg(f"Successfully verified custom.toml write at {custom_toml_path}")
        except Exception as e:
            print(f"[ERROR] Failed writing or verifying custom.toml: {e}", file=sys.stderr)
            raise e

        # ── Cloud-init provisioning (Sections I–L) ─────────────────────────────
        # Prebaked images (MainsailOS) use NetworkManager — NOT cloud-init/Netplan.
        # Injecting cloud-init artifacts (user-data, meta-data, network-config,
        # ds=nocloud) on a prebaked image causes cloud-init to re-provision on
        # reboot, generating a conflicting network profile that kills WiFi.
        #
        # For prebaked: skip cloud-init entirely and clean up any residual files
        #               from the base image.
        # For vanilla:  write cloud-init artifacts as before (the OS needs them).

        country_code = _get_country_from_timezone(clean_timezone) if clean_timezone else "US"

        if is_prebaked:
            # ── Prebaked: remove cloud-init artifacts ────────────────────────
            # MainsailOS already has the user, hostname, and network configured
            # via userconf.txt, custom.toml, systemd.hostname, and
            # system-connections/*.nmconnection.  Cloud-init files would only
            # cause a destructive re-provisioning cycle.
            _cloud_init_files = ["user-data", "meta-data", "network-config"]
            for ci_file in _cloud_init_files:
                ci_path = os.path.join(boot_path, ci_file)
                if os.path.exists(ci_path):
                    try:
                        os.remove(ci_path)
                        _dbg(f"Removed pre-existing cloud-init file: {ci_path}")
                    except Exception as rm_err:
                        print(f"[WARNING] Could not remove {ci_file}: {rm_err}", file=sys.stderr)

            # Strip any existing ds=nocloud from cmdline.txt (base image may have it)
            cmdline_path = os.path.join(boot_path, "cmdline.txt")
            if os.path.exists(cmdline_path):
                try:
                    with open(cmdline_path, "r") as f:
                        cmdline_content = f.read().strip()
                    # Remove ds=nocloud;i=... or ds=nocloud tokens
                    import re as _re
                    cmdline_content = _re.sub(r'\s*ds=nocloud[^\s]*', '', cmdline_content).strip()
                    # Ensure WiFi regulatory domain is set
                    if "cfg80211.ieee80211_regdom" not in cmdline_content:
                        cmdline_content = f"{cmdline_content} cfg80211.ieee80211_regdom={country_code}"
                    with open(cmdline_path, "w", newline="\n") as f:
                        f.write(cmdline_content + "\n")
                    _dbg(f"Cleaned cmdline.txt for prebaked image (removed ds=nocloud, ensured regdom)")
                except Exception as e:
                    print(f"[ERROR] Failed cleaning cmdline.txt for prebaked image: {e}", file=sys.stderr)
                    raise e

            # ── Prebaked: write headless_nm.txt for MainsailOS WiFi setup ────
            # MainsailOS uses a systemd service (headless_nm.service) that reads
            # headless_nm.txt from the boot partition on first boot, creates the
            # corresponding NetworkManager WiFi profile, and then auto-deletes
            # the file.  Without this file, MainsailOS never configures WiFi.
            if wifi_ssid:
                headless_nm_path = os.path.join(boot_path, "headless_nm.txt")
                # Escape double-quote characters in SSID/password for the
                # key="value" format used by headless_nm.
                esc_hl_ssid = wifi_ssid.replace('"', '\\"')
                esc_hl_password = wifi_password.replace('"', '\\"')
                headless_content = (
                    f'SSID="{esc_hl_ssid}"\n'
                    f'PASSWORD="{esc_hl_password}"\n'
                    f'HIDDEN="false"\n'
                    f'REGDOMAIN="{country_code}"\n'
                )
                try:
                    with open(headless_nm_path, "w", newline="\n") as f:
                        f.write(headless_content)
                    if not os.path.exists(headless_nm_path):
                        raise IOError(f"headless_nm.txt not found at: {headless_nm_path}")
                    with open(headless_nm_path, "r", encoding="utf-8") as f_check:
                        c = f_check.read()
                    if esc_hl_ssid not in c:
                        raise ValueError("headless_nm.txt verification failed (missing SSID).")
                    _dbg(f"Successfully wrote headless_nm.txt at {headless_nm_path}")
                except Exception as e:
                    print(f"[ERROR] Failed writing or verifying headless_nm.txt: {e}", file=sys.stderr)
                    raise e

            # ── Prebaked: write firstrun.sh to set credentials ──────────────
            # Strategy: rename the pre-existing printer user (pi/mainsail/fluidd)
            # to the target username, move their home directory, and create a
            # backward-compatibility symlink so Python venv shebangs continue to
            # resolve. Then patch User=/Group= in the known service unit files.
            # This mirrors what Raspberry Pi Imager does and avoids the 'create new
            # user alongside pi' approach that orphaned all pre-installed software.
            firstrun_path = os.path.join(boot_path, "firstrun.sh")
            pw_auth_str = "true" if password_auth else "false"

            firstrun_content = f"""#!/bin/bash
# KACE Studio prebaked firstboot credentials setup.
# Renames the pre-existing printer user to the target username, moves their
# home directory, creates a compatibility symlink so Python venv shebangs
# keep working, then patches User=/Group= in the known service unit files.

cleanup() {{
    local exit_code=$?
    local target_mnt="$BOOT_MNT"
    if [ -z "$target_mnt" ]; then
        if [ -f "/boot/firmware/cmdline.txt" ]; then
            target_mnt="/boot/firmware"
        elif [ -f "/boot/cmdline.txt" ]; then
            target_mnt="/boot"
        fi
    fi

    if [ -n "$target_mnt" ]; then
        if [ $exit_code -ne 0 ]; then
            echo "KACE prebaked firstrun failed at $(date) with exit code $exit_code" > "$target_mnt/kace-firstrun-error.log"
        fi
        CMDLINE="$target_mnt/cmdline.txt"
        if [ -f "$CMDLINE" ]; then
            # [^ ]* is safe here because cmdline.txt now uses a plain unquoted path
            # (no embedded spaces) so the pattern matches the full token correctly.
            sed -i 's| systemd[.]run=[^ ]*||g; s| systemd[.]run_success_action=[^ ]*||g; s| systemd[.]run_failure_action=[^ ]*||g; s| systemd[.]unit=kernel-command-line[.]target||g' "$CMDLINE"
        fi
        rm -f "$target_mnt/firstrun.sh"
        rm -f "$target_mnt/.kace-firstrun-done"
        sync
    fi
}}
trap cleanup EXIT

set -e

# Detect boot partition mount point (Bookworm+: /boot/firmware, legacy: /boot)
BOOT_MNT=""
if [ -d "/boot/firmware" ] && mountpoint -q /boot/firmware 2>/dev/null; then
    BOOT_MNT="/boot/firmware"
elif [ -d "/boot" ] && mountpoint -q /boot 2>/dev/null; then
    BOOT_MNT="/boot"
fi

# Idempotency guard
if [ -n "$BOOT_MNT" ] && [ -f "$BOOT_MNT/.kace-firstrun-done" ]; then
    exit 0
fi

HASHED_PW='{hashed_pw}'
TARGET_USER='{username}'

# ── 1. Detect the pre-existing printer user ──────────────────────────────────
# On pre-baked images (MainsailOS / FluiddPi) Klipper lives under the default
# user (pi, mainsail, or fluidd). Find that user before deciding what to do.
SOURCE_USER=""
for candidate in pi mainsail fluidd; do
    if id "$candidate" &>/dev/null && [ -d "/home/$candidate/klipper" ]; then
        SOURCE_USER="$candidate"
        break
    fi
done
# Fallback: glob any home dir that contains klipper/
if [ -z "$SOURCE_USER" ]; then
    for udir in /home/*/klipper; do
        [ -d "$udir" ] || continue
        candidate=$(echo "$udir" | cut -d/ -f3)
        if id "$candidate" &>/dev/null; then
            SOURCE_USER="$candidate"
            break
        fi
    done
fi

# ── 2. User setup ─────────────────────────────────────────────────────────────
if [ -z "$SOURCE_USER" ] || [ "$TARGET_USER" = "$SOURCE_USER" ]; then
    # No rename needed — just update the password for the existing/target user.
    EFFECTIVE_USER="${{SOURCE_USER:-$TARGET_USER}}"
    echo "$EFFECTIVE_USER:$HASHED_PW" | chpasswd -e
else
    # Rename SOURCE_USER → TARGET_USER (mirroring Raspberry Pi Imager behavior).

    # a. Rename the login name and primary group.
    groupmod -n "$TARGET_USER" "$SOURCE_USER" 2>/dev/null || true
    usermod -l "$TARGET_USER" -d "/home/$TARGET_USER" -m "$SOURCE_USER"

    # b. Backward-compatibility symlink: Python venv shebangs like
    #    #!/home/pi/klippy-env/bin/python are baked into pre-compiled binaries.
    #    The symlink lets them keep resolving without rewriting every file.
    if [ ! -e "/home/$SOURCE_USER" ]; then
        ln -s "/home/$TARGET_USER" "/home/$SOURCE_USER"
    fi

    # c. Set the target user's password.
    echo "$TARGET_USER:$HASHED_PW" | chpasswd -e

    # d. Systemd services (User=/Group= paths) will be patched during the bootstrap
    #    phase by bootstrap.sh (owned by KACE), keeping the OS imager decoupled from
    #    Klipper domain logic.
    echo "Renamed printer user '$SOURCE_USER' -> '$TARGET_USER', home /home/$TARGET_USER"
fi

# ── 3. Configure SSH Password Authentication ──────────────────────────────────
mkdir -p /etc/ssh/sshd_config.d
if [ "{pw_auth_str}" = "true" ]; then
    echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/00-kace.conf
    sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
elif [ "{pw_auth_str}" = "false" ]; then
    echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/00-kace.conf
    sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
fi

if systemctl is-active ssh &>/dev/null; then
    systemctl reload ssh || true
fi

if [ -n "$BOOT_MNT" ]; then
    touch "$BOOT_MNT/.kace-firstrun-done"
fi

exit 0
"""
            try:
                with open(firstrun_path, "w", newline="\n", encoding="utf-8") as f:
                    f.write(firstrun_content)
                if not os.path.exists(firstrun_path):
                    raise IOError(f"firstrun.sh not found at: {firstrun_path}")

                with open(firstrun_path, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if f"TARGET_USER='{username}'" not in c or f"HASHED_PW='{hashed_pw}'" not in c:
                    raise ValueError("firstrun.sh verification failed (content mismatch).")
                _dbg(f"Successfully verified firstrun.sh write at {firstrun_path}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying firstrun.sh: {e}", file=sys.stderr)
                raise e

            # Append systemd.run trigger to cmdline.txt
            if os.path.exists(cmdline_path):
                try:
                    with open(cmdline_path, "r") as f:
                        cmdline_content = f.read().strip()
                    if "systemd.run=" not in cmdline_content:
                        # Plain unquoted path — no /bin/sh -c dispatcher.
                        # The dispatcher caused [^ ]* cleanup regex truncation
                        # because the quoted string contained embedded spaces.
                        # Pre-baked images are Bookworm-based; boot is always /boot/firmware.
                        cmdline_content = (
                            f"{cmdline_content}"
                            f" systemd.run=/boot/firmware/firstrun.sh"
                            f" systemd.run_success_action=reboot"
                            f" systemd.run_failure_action=reboot"
                            f" systemd.unit=kernel-command-line.target"
                        )
                        with open(cmdline_path, "w", newline="\n") as f:
                            f.write(cmdline_content + "\n")
                        
                        # Verification check for cmdline.txt patch
                        with open(cmdline_path, "r", encoding="utf-8") as f_check:
                            c = f_check.read()
                        if "systemd.run=" not in c:
                            raise ValueError("cmdline.txt systemd.run verification failed.")
                        _dbg(f"Successfully patched and verified cmdline.txt for prebaked firstrun.sh")
                except Exception as e:
                    print(f"[ERROR] Failed appending or verifying systemd.run in cmdline.txt: {e}", file=sys.stderr)
                    raise e
        else:
            # ── Vanilla RPi OS: write cloud-init artifacts ───────────────────

            # I. cloud-init: user-data
            instance_uuid = str(uuid.uuid4())  # uuid imported at top of module
            userdata_path = os.path.join(boot_path, "user-data")

            # M6 FIX: use sanitized clean_timezone to prevent YAML injection via the timezone field.
            # The clean_timezone value has had \r, \n, and = stripped already.
            userdata_content = f"""#cloud-config
hostname: {clean_hostname}
manage_etc_hosts: true
packages:
- avahi-daemon
timezone: {clean_timezone or "UTC"}
users:
- name: {username}
  groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
  shell: /bin/bash
  lock_passwd: false
  passwd: "{hashed_pw}"
enable_ssh: true
ssh_pwauth: {"true" if password_auth else "false"}
"""
            try:
                with open(userdata_path, "w", newline="\n") as f:
                    f.write(userdata_content)
                if not os.path.exists(userdata_path):
                    raise IOError(f"user-data not found at: {userdata_path}")
                with open(userdata_path, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if f"hostname: {clean_hostname}" not in c or f'passwd: "{hashed_pw}"' not in c:
                    raise ValueError("user-data verification failed.")
                _dbg(f"Successfully verified user-data write at {userdata_path}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying user-data: {e}", file=sys.stderr)
                raise e

            # J. cloud-init: network-config (Netplan v2)
            # NOTE: Netplan's password field expects the plaintext passphrase,
            # NOT a pre-hashed hex PSK.  The hex PSK is only valid for
            # wpa_supplicant.conf and NetworkManager psk= fields.
            network_config_path = os.path.join(boot_path, "network-config")
            if wifi_ssid:
                network_content = f"""network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
  wifis:
    wlan0:
      dhcp4: true
      regulatory-domain: "{country_code}"
      access-points:
        "{wifi_ssid}":
          password: "{clean_wpa_password}"
      optional: true
"""
            else:
                network_content = """network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
"""
            try:
                with open(network_config_path, "w", newline="\n") as f:
                    f.write(network_content)
                if not os.path.exists(network_config_path):
                    raise IOError(f"network-config not found at: {network_config_path}")
                with open(network_config_path, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if "version: 2" not in c:
                    raise ValueError("network-config verification failed.")
                if wifi_ssid and wifi_ssid not in c:
                    raise ValueError("network-config verification failed (missing SSID).")
                _dbg(f"Successfully verified network-config write at {network_config_path}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying network-config: {e}", file=sys.stderr)
                raise e

            # K. cloud-init: meta-data
            metadata_path = os.path.join(boot_path, "meta-data")
            metadata_content = f"instance-id: kace-{instance_uuid}\n"
            try:
                with open(metadata_path, "w", newline="\n") as f:
                    f.write(metadata_content)
                if not os.path.exists(metadata_path):
                    raise IOError(f"meta-data not found at: {metadata_path}")
                with open(metadata_path, "r", encoding="utf-8") as f_check:
                    c = f_check.read()
                if f"kace-{instance_uuid}" not in c:
                    raise ValueError("meta-data verification failed.")
                _dbg(f"Successfully verified meta-data write at {metadata_path}")
            except Exception as e:
                print(f"[ERROR] Failed writing or verifying meta-data: {e}", file=sys.stderr)
                raise e

            # L. Patch cmdline.txt — add ds=nocloud and WiFi regulatory domain
            cmdline_path = os.path.join(boot_path, "cmdline.txt")
            if os.path.exists(cmdline_path):
                try:
                    with open(cmdline_path, "r") as f:
                        cmdline_content = f.read().strip()
                    if "ds=nocloud" not in cmdline_content:
                        cmdline_content = f"{cmdline_content} cfg80211.ieee80211_regdom={country_code} ds=nocloud;i=kace-{instance_uuid}"
                        with open(cmdline_path, "w", newline="\n") as f:
                            f.write(cmdline_content + "\n")
                        with open(cmdline_path, "r", encoding="utf-8") as f_check:
                            c = f_check.read()
                        if f"ds=nocloud;i=kace-{instance_uuid}" not in c:
                            raise ValueError("cmdline.txt verification failed.")
                        _dbg(f"Successfully verified cmdline.txt patch at {cmdline_path}")
                except Exception as e:
                    print(f"[ERROR] Failed patching or verifying cmdline.txt: {e}", file=sys.stderr)
                    raise e
            else:
                print(f"[WARNING] cmdline.txt not found at: {cmdline_path}. Skipping cmdline.txt patching.", file=sys.stderr)

            # L2. Write firstrun.sh — fallback for images without cloud-init
            # (e.g. Bookworm).  The script copies the pre-staged .nmconnection
            # from the boot partition into /etc/NetworkManager/system-connections/
            # with root-only permissions, reloads NM, then self-destructs.
            # On Trixie+ (which ships cloud-init), this is harmless: cloud-init
            # handles networking on the second boot after firstrun triggers a
            # reboot.
            if wifi_ssid:
                firstrun_path = os.path.join(boot_path, "firstrun.sh")
                firstrun_content = """#!/bin/bash
# KACE Studio first-run network configuration
# Copies the pre-configured NetworkManager WiFi profile from the boot
# partition into the system directory so NetworkManager can use it.
# This script runs once via systemd.run= in cmdline.txt and then
# removes itself and its cmdline.txt trigger.

cleanup() {
    local exit_code=$?
    local target_mnt="$BOOT_MNT"
    if [ -z "$target_mnt" ]; then
        if [ -f "/boot/firmware/cmdline.txt" ]; then
            target_mnt="/boot/firmware"
        elif [ -f "/boot/cmdline.txt" ]; then
            target_mnt="/boot"
        fi
    fi

    if [ -n "$target_mnt" ]; then
        if [ $exit_code -ne 0 ]; then
            echo "KACE vanilla firstrun failed at $(date) with exit code $exit_code" > "$target_mnt/kace-firstrun-error.log"
        fi
        CMDLINE="$target_mnt/cmdline.txt"
        if [ -f "$CMDLINE" ]; then
            # [^ ]* is safe here because cmdline.txt now uses a plain unquoted path
            # (no embedded spaces) so the pattern matches the full token correctly.
            sed -i 's| systemd[.]run=[^ ]*||g; s| systemd[.]run_success_action=[^ ]*||g; s| systemd[.]run_failure_action=[^ ]*||g; s| systemd[.]unit=kernel-command-line[.]target||g' "$CMDLINE"
        fi
        rm -f "$target_mnt/firstrun.sh"
        sync
    fi
}
trap cleanup EXIT

set -e

# Detect boot partition mount point (Bookworm+: /boot/firmware, legacy: /boot)
BOOT_MNT=""
if [ -d "/boot/firmware" ] && mountpoint -q /boot/firmware 2>/dev/null; then
    BOOT_MNT="/boot/firmware"
elif [ -d "/boot" ] && mountpoint -q /boot 2>/dev/null; then
    BOOT_MNT="/boot"
fi

if [ -n "$BOOT_MNT" ]; then
    NM_SRC="$BOOT_MNT/system-connections/preconfigured-wifi.nmconnection"
    NM_DST="/etc/NetworkManager/system-connections/preconfigured-wifi.nmconnection"

    if [ -f "$NM_SRC" ] && [ -d "/etc/NetworkManager/system-connections" ]; then
        cp "$NM_SRC" "$NM_DST"
        chmod 600 "$NM_DST"
        chown root:root "$NM_DST"
    fi
fi
"""
                try:
                    with open(firstrun_path, "w", newline="\n", encoding="utf-8") as f:
                        f.write(firstrun_content)
                    if not os.path.exists(firstrun_path):
                        raise IOError(f"firstrun.sh not found at: {firstrun_path}")
                    _dbg(f"Successfully wrote firstrun.sh at {firstrun_path}")
                except Exception as e:
                    print(f"[ERROR] Failed writing firstrun.sh: {e}", file=sys.stderr)
                    raise e

                # Append systemd.run trigger to cmdline.txt so firstrun.sh
                # executes on the very first boot.
                # The boot partition is mounted at /boot/firmware/ on
                # Bookworm/Trixie.
                if os.path.exists(cmdline_path):
                    try:
                        with open(cmdline_path, "r") as f:
                            cmdline_content = f.read().strip()
                        if "systemd.run=" not in cmdline_content:
                            # Plain unquoted path — no /bin/sh -c dispatcher.
                            # Vanilla Bookworm/Trixie always mounts boot at /boot/firmware.
                            cmdline_content = (
                                f"{cmdline_content}"
                                f" systemd.run=/boot/firmware/firstrun.sh"
                                f" systemd.run_success_action=reboot"
                                f" systemd.run_failure_action=reboot"
                                f" systemd.unit=kernel-command-line.target"
                            )
                            with open(cmdline_path, "w", newline="\n") as f:
                                f.write(cmdline_content + "\n")
                            _dbg(f"Appended systemd.run trigger for firstrun.sh to cmdline.txt")
                    except Exception as e:
                        print(f"[ERROR] Failed appending systemd.run to cmdline.txt: {e}", file=sys.stderr)
                        raise e

        # M. Copy bootstrap.sh with version comment and Unix line endings
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)
            if hasattr(sys, '_MEIPASS'):
                local_bootstrap_src = os.path.join(sys._MEIPASS, "bootstrap.sh")
            else:
                # Developer source run: prefer the authoritative script from the sibling
                # KACE repository if it is checked out alongside KACE-studio. This avoids
                # deploying the test-generated mock stub (which tests may leave on disk).
                sibling_path = os.path.abspath(
                    os.path.join(project_root, "..", "KACE", "scripts", "bootstrap.sh")
                )
                if os.path.isfile(sibling_path):
                    local_bootstrap_src = sibling_path
                else:
                    local_bootstrap_src = os.path.join(project_root, "bootstrap.sh")

            if os.path.exists(local_bootstrap_src):
                git_hash = ""
                try:
                    res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], 
                                         capture_output=True, text=True, cwd=project_root)
                    if res.returncode == 0:
                        git_hash = res.stdout.strip()
                except Exception:
                    pass

                if not git_hash:
                    import datetime
                    git_hash = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                version_line = f"# KACE Bootstrap Version: {git_hash}\n"
                dest_bootstrap_path = os.path.join(boot_path, "bootstrap.sh")

                with open(local_bootstrap_src, 'r', encoding='utf-8', errors='ignore') as f_in, \
                     open(dest_bootstrap_path, 'w', encoding='utf-8', newline='\n') as f_out:
                    f_out.write(version_line)
                    while True:
                        chunk = f_in.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)
                
                if not os.path.exists(dest_bootstrap_path):
                    raise IOError(f"bootstrap.sh not found at: {dest_bootstrap_path}")
                _dbg(f"Successfully verified bootstrap.sh local copy at {dest_bootstrap_path}")
            else:
                print(f"[WARNING] Local bootstrap.sh source not found at: {local_bootstrap_src}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Failed copying bootstrap.sh: {e}", file=sys.stderr)
            raise e

        try:
            time.sleep(1)
            subprocess.run(["powershell", "-Command", "Update-HostStorageCache"], capture_output=True, **SUBPROCESS_FLAGS)
        except Exception as flush_err:
            print(f"Warning: Update-HostStorageCache failed (non-fatal): {flush_err}", file=sys.stderr)

        return True
    except Exception as e:
        print(f"Error injecting boot configs: {e}", file=sys.stderr)
        if isinstance(e, ValueError):
            raise e
        return False

if __name__ == "__main__":
    # Drive query self-test
    print("Testing drive discovery:")
    for d in list_drives():
        print(f" - [{d['id']}] {d['name']} ({d['size']}) on {d['bus']}")
