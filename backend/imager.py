import os
import sys
import json
import time
import subprocess
import uuid
from pathlib import Path
from backend.sha512_crypt import hash_password

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



def list_drives() -> list:
    """
    Returns a list of removable/USB drives that are safe to flash.
    Excludes system, boot, and internal NVMe/SATA drives.
    """
    drives = []
    
    if sys.platform == "win32":
        try:
            # Query physical disks
            cmd = "powershell -Command \"Get-Disk | Select-Object Number, FriendlyName, Size, BusType, IsSystem, IsBoot | ConvertTo-Json\""
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
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
        # Basic POSIX fallback for structure/testing
        drives.append({
            "id": "/dev/sdb",
            "name": "Mock Removable Drive (Non-Windows)",
            "size": "16.0 GB",
            "bus": "USB"
        })
        
    return drives

def get_boot_drive_letter(disk_number: int) -> str:
    """
    Finds the FAT32/FAT partition drive letter of the flashed SD card.
    """
    if sys.platform != "win32":
        return ""
        
    # Query partitions and find their drive letters
    cmd = f"powershell -Command \"Get-Partition -DiskNumber {disk_number} | Get-Volume | Select-Object DriveLetter, FileSystem | ConvertTo-Json\""
    for _ in range(5):  # Retry up to 5 times to let Windows mount the disk
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
        if res.returncode == 0 and res.stdout.strip():
            try:
                data = json.loads(res.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                for vol in data:
                    letter = vol.get("DriveLetter")
                    fs = vol.get("FileSystem", "").upper()
                    # We are looking for the FAT32 boot partition
                    if letter and fs in ("FAT32", "FAT"):
                        return f"{letter}:\\"
            except Exception as e:
                print(f"Error parsing partition volume: {e}", file=sys.stderr)
        time.sleep(1)
        
    return ""

def flash_drive(disk_number: int, image_path: str, progress_callback=None) -> tuple:
    """
    Flashes the image block-by-block onto the target drive by spawning
    the elevated helper process kace_writer.py.
    """
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
    
    # Create a temp file to track progress updates
    temp_dir = os.environ.get("TEMP", os.environ.get("TMP", "C:\\Temp"))
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
    status_file = os.path.join(temp_dir, f"kace_flash_{disk_number}.json")
    if os.path.exists(status_file):
        try:
            os.remove(status_file)
        except:
            pass
            
    # Resolve executable and arguments
    if hasattr(sys, '_MEIPASS') or not sys.executable.lower().endswith("python.exe"):
        # Packaged mode
        arg_list = [
            "'--write-disk'",
            f"'{disk_number}'",
            "'" + str(image_path).replace("'", "''") + "'",
            "'" + str(status_file).replace("'", "''") + "'"
        ]
        exec_path = sys.executable
    else:
        # Dev mode
        main_py = os.path.join(project_root, "main.py")
        arg_list = [
            "'" + str(main_py).replace("'", "''") + "'",
            "'--write-disk'",
            f"'{disk_number}'",
            "'" + str(image_path).replace("'", "''") + "'",
            "'" + str(status_file).replace("'", "''") + "'"
        ]
        exec_path = sys.executable

    args_str = ", ".join(arg_list)
    # Spawn elevated process via PowerShell Start-Process with -Verb RunAs
    cmd = f"powershell -Command \"Start-Process -FilePath '{exec_path}' -ArgumentList {args_str} -Verb RunAs -Wait -WindowStyle Hidden\""
    
    error_msg = ""
    try:
        proc = subprocess.Popen(cmd, shell=True)
        
        last_progress = 0
        success = False
        
        while proc.poll() is None:
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
            except:
                pass
                
        if not success and not error_msg:
            error_msg = "Administrative privilege prompt was declined or PowerShell failed to execute."
            
        return success, error_msg
        
    except Exception as e:
        err = f"Failed to execute elevated helper: {e}"
        print(err, file=sys.stderr)
        return False, err
def inject_config(disk_number: int, hostname: str, wifi_ssid: str, wifi_password: str, ssh_password: str, dashboard_ui: str, timezone: str = "", pi_model: str = "", os_arch: str = "", ssh_enabled: bool = True, crowsnest: bool = False) -> bool:
    """
    Injects SSH enablement, User credentials, WiFi configuration (wpa_supplicant + NetworkManager),
    and hostname parameters directly to the FAT32 boot partition.
    """
    # Wait for OS mount
    boot_path = get_boot_drive_letter(disk_number)
    if not boot_path or not os.path.exists(boot_path):
        print(f"FAT32 boot partition not mounted or not found on physical disk {disk_number}.", file=sys.stderr)
        return False
        
    try:
        # A. SSH Enablement
        if ssh_enabled:
            ssh_file = os.path.join(boot_path, "ssh")
            with open(ssh_file, "w") as f:
                pass # Writes empty file to enable SSH
                
            ssh_txt_file = os.path.join(boot_path, "ssh.txt")
            with open(ssh_txt_file, "w") as f:
                pass
            
        # B. User Credentials configuration (userconf.txt)
        hashed_pw = hash_password(ssh_password)
        userconf_file = os.path.join(boot_path, "userconf.txt")
        with open(userconf_file, "w", newline="\n") as f:
            f.write(f"{DEFAULT_USERNAME}:{hashed_pw}\n")
            
        # C. WiFi credentials
        if wifi_ssid:
            # Legacy: wpa_supplicant.conf (for Buster/Bullseye compatibility)
            country_code = _get_country_from_timezone(timezone) if timezone else "US"
            wpa_conf = os.path.join(boot_path, "wpa_supplicant.conf")
            wpa_content = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country={country_code}

network={{
    ssid="{wifi_ssid}"
    psk="{wifi_password}"
    key_mgmt=WPA-PSK
}}
"""
            with open(wpa_conf, "w", newline="\n") as f:
                f.write(wpa_content)
                
            # Modern: NetworkManager connection profile (for Bookworm compatibility)
            nm_dir = os.path.join(boot_path, "system-connections")
            os.makedirs(nm_dir, exist_ok=True)
            nm_file = os.path.join(nm_dir, "preconfigured-wifi.nmconnection")
            
            # Generate a random UUID for the connection
            conn_uuid = str(uuid.uuid4())
            nm_content = f"""[connection]
id=preconfigured-wifi
uuid={conn_uuid}
type=wifi
interface-name=wlan0

[wifi]
mode=infrastructure
ssid={wifi_ssid}

[wifi-security]
auth-alg=open
key-mgmt=wpa-psk
psk={wifi_password}

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default-or-eui64
"""
            with open(nm_file, "w", newline="\n") as f:
                f.write(nm_content)
                
        # D. Hostname configuration injection via cmdline.txt boot arguments
        cmdline_file = os.path.join(boot_path, "cmdline.txt")
        if os.path.exists(cmdline_file) and hostname:
            with open(cmdline_file, "r") as f:
                content = f.read().strip()
            # If systemd.hostname boot parameter is not already set
            if "systemd.hostname" not in content:
                # Append parameter
                content = f"{content} systemd.hostname={hostname.replace('.local', '')}"
                with open(cmdline_file, "w", newline="\n") as f:
                    f.write(content + "\n")
                    
        # E. Bootstrap Config injection
        bootstrap_cfg = os.path.join(boot_path, "kace-bootstrap.txt")
        with open(bootstrap_cfg, "w", newline="\n") as f:
            f.write(f"DASHBOARD={dashboard_ui}\n")
            f.write(f"CROWSNEST={'true' if crowsnest else 'false'}\n")
            if timezone:
                f.write(f"TIMEZONE={timezone}\n")
            if pi_model:
                f.write(f"PI_MODEL={pi_model}\n")
            if os_arch:
                f.write(f"OS_ARCH={os_arch}\n")
                    
        # F. Bookworm headless configuration (custom.toml)
        custom_toml_path = os.path.join(boot_path, "custom.toml")
        clean_hostname = hostname.replace(".local", "") if hostname else "kace"
        
        esc_hostname = clean_hostname.replace('\\', '\\\\').replace('"', '\\"')
        esc_username = DEFAULT_USERNAME.replace('\\', '\\\\').replace('"', '\\"')
        esc_ssh_password = ssh_password.replace('\\', '\\\\').replace('"', '\\"')
        
        toml_content = f"""config_version = 1

[system]
hostname = "{esc_hostname}"

[user]
name = "{esc_username}"
password = "{esc_ssh_password}"
password_encrypted = false

[ssh]
enabled = {"true" if ssh_enabled else "false"}
password_authentication = true
"""
        if wifi_ssid:
            country_code = _get_country_from_timezone(timezone) if timezone else "US"
            esc_wifi_ssid = wifi_ssid.replace('\\', '\\\\').replace('"', '\\"')
            esc_wifi_password = wifi_password.replace('\\', '\\\\').replace('"', '\\"')
            toml_content += f"""
[wlan]
ssid = "{esc_wifi_ssid}"
password = "{esc_wifi_password}"
password_encrypted = false
country = "{country_code}"
"""
        with open(custom_toml_path, "w", newline="\n") as f:
            f.write(toml_content)
        try:
            time.sleep(1)
            subprocess.run("powershell -Command \"Update-HostStorageCache\"", shell=True, capture_output=True)
        except:
            pass
            
        return True
    except Exception as e:
        print(f"Error injecting boot configs: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    # Drive query self-test
    print("Testing drive discovery:")
    for d in list_drives():
        print(f" - [{d['id']}] {d['name']} ({d['size']}) on {d['bus']}")
