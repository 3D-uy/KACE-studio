import os
import sys
import json
import hashlib
import re
import subprocess
import ctypes
from pathlib import Path
from ctypes import wintypes

# Subprocess flags to run silent processes on Windows (CREATE_NO_WINDOW)
SUBPROCESS_FLAGS = {}
if sys.platform == "win32":
    SUBPROCESS_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

REQUIRED_IDENTITY_FIELDS = (
    "number", "friendly_name", "size_bytes", "bus_type", "is_system",
    "is_boot", "serial_number", "unique_id", "path",
)


class Win32DiskWriter:
    def __init__(self, physical_path, disk_number=None):
        self.physical_path = physical_path
        self.handle = None
        self.volume_handles = []
        self.lock_failed = False
        
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        
        kernel32 = ctypes.windll.kernel32
        
        self._CreateFileW = kernel32.CreateFileW
        self._CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE
        ]
        self._CreateFileW.restype = wintypes.HANDLE
        
        self._WriteFile = kernel32.WriteFile
        self._WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p
        ]
        self._WriteFile.restype = wintypes.BOOL

        self._ReadFile = kernel32.ReadFile
        self._ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._ReadFile.restype = wintypes.BOOL

        self._SetFilePointerEx = kernel32.SetFilePointerEx
        self._SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        self._SetFilePointerEx.restype = wintypes.BOOL
        
        self._CloseHandle = kernel32.CloseHandle
        self._CloseHandle.argtypes = [wintypes.HANDLE]
        self._CloseHandle.restype = wintypes.BOOL
        
        self._FlushFileBuffers = kernel32.FlushFileBuffers
        self._FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self._FlushFileBuffers.restype = wintypes.BOOL

        self._DeviceIoControl = kernel32.DeviceIoControl
        self._DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p
        ]
        self._DeviceIoControl.restype = wintypes.BOOL

        # 1. Lock and dismount all volumes on this disk
        if disk_number is not None:
            volumes = self._get_disk_volumes(disk_number)
            safe_print_out(f"STATUS: Found volume access paths for locking: {volumes}")
            for vol_path in volumes:
                try:
                    h_vol = self._CreateFileW(
                        vol_path,
                        GENERIC_READ | GENERIC_WRITE,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None,
                        OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL,
                        None
                    )
                    # Fallback to GENERIC_READ
                    if self._is_invalid(h_vol):
                        h_vol = self._CreateFileW(
                            vol_path,
                            GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_WRITE,
                            None,
                            OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL,
                            None
                        )
                    
                    if not self._is_invalid(h_vol):
                        bytes_returned = wintypes.DWORD(0)
                        res_lock = self._DeviceIoControl(h_vol, 0x00090018, None, 0, None, 0, ctypes.byref(bytes_returned), None)
                        res_dismount = self._DeviceIoControl(h_vol, 0x00090020, None, 0, None, 0, ctypes.byref(bytes_returned), None)
                        self.volume_handles.append(h_vol)
                        if not res_lock:
                            self.lock_failed = True
                            safe_print_err(f"[WARNING] Failed to lock volume {vol_path}. Another process may be using the drive.")
                        safe_print_out(f"STATUS: Locked and dismounted volume {vol_path} (Lock: {res_lock}, Dismount: {res_dismount})")
                    else:
                        err_code = kernel32.GetLastError()
                        safe_print_err(f"Warning: Failed to open volume handle for {vol_path}: GetLastError {err_code}")
                except Exception as vol_err:
                    safe_print_err(f"Warning: Exception locking volume {vol_path}: {vol_err}")

        # 2. Open physical drive handle
        self.handle = self._CreateFileW(
            self.physical_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        
        if self._is_invalid(self.handle):
            err_code = kernel32.GetLastError()
            raise OSError(None, f"CreateFileW failed with GetLastError: {err_code}", self.physical_path, err_code)

    def _is_invalid(self, handle):
        return handle is None or handle == 0 or handle == -1 or handle == 0xFFFFFFFF or handle == 0xFFFFFFFFFFFFFFFF

    def _get_disk_volumes(self, disk_number):
        # SEC FIX: Runtime guard replacing assert (assert is disabled with -O).
        if not isinstance(disk_number, int):
            raise TypeError(f"disk_number must be an integer, got {type(disk_number).__name__}")
        try:
            res = subprocess.run(["powershell", "-Command", f"Get-Partition -DiskNumber {disk_number} | Select-Object -ExpandProperty AccessPaths"], capture_output=True, text=True, encoding="utf-8", **SUBPROCESS_FLAGS)
            if res.returncode == 0:
                paths = []
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if line:
                        if line.endswith("\\"):
                            line = line[:-1]
                        if len(line) == 2 and line.endswith(":"):
                            paths.append(f"\\\\.\\{line}")
                        else:
                            paths.append(line)
                return list(set(paths))
        except Exception as e:
            safe_print_err(f"Warning: Failed to query partitions for locking: {e}")
        return []
    def write(self, data: bytes):
        if self._is_invalid(self.handle):
            raise OSError("Handle is closed or invalid.")
        
        bytes_written = wintypes.DWORD(0)
        data_len = len(data)
        data_buffer = ctypes.create_string_buffer(data)
        
        res = self._WriteFile(
            self.handle,
            data_buffer,
            data_len,
            ctypes.byref(bytes_written),
            None
        )
        if not res:
            err_code = ctypes.windll.kernel32.GetLastError()
            raise OSError(None, f"WriteFile failed with GetLastError: {err_code}", self.physical_path, err_code)
        
        if bytes_written.value != data_len:
            raise OSError(f"WriteFile wrote {bytes_written.value} bytes instead of {data_len}.")
            
        return bytes_written.value

    def seek(self, offset: int):
        if self._is_invalid(self.handle):
            raise OSError("Handle is closed or invalid.")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Disk offset must be a non-negative integer.")
        new_position = ctypes.c_longlong(0)
        if not self._SetFilePointerEx(
            self.handle, ctypes.c_longlong(offset), ctypes.byref(new_position), 0
        ):
            err_code = ctypes.windll.kernel32.GetLastError()
            raise OSError(None, f"SetFilePointerEx failed with GetLastError: {err_code}", self.physical_path, err_code)
        if new_position.value != offset:
            raise OSError(f"Physical disk seek reached {new_position.value} instead of {offset}.")
        return new_position.value

    def read(self, size: int) -> bytes:
        if self._is_invalid(self.handle):
            raise OSError("Handle is closed or invalid.")
        if not isinstance(size, int) or size <= 0:
            raise ValueError("Disk read size must be a positive integer.")
        buffer = ctypes.create_string_buffer(size)
        bytes_read = wintypes.DWORD(0)
        if not self._ReadFile(
            self.handle, buffer, size, ctypes.byref(bytes_read), None
        ):
            err_code = ctypes.windll.kernel32.GetLastError()
            raise OSError(None, f"ReadFile failed with GetLastError: {err_code}", self.physical_path, err_code)
        return buffer.raw[:bytes_read.value]

    def flush(self):
        if not self._is_invalid(self.handle):
            if not self._FlushFileBuffers(self.handle):
                err_code = ctypes.windll.kernel32.GetLastError()
                raise OSError(None, f"FlushFileBuffers failed with GetLastError: {err_code}", self.physical_path, err_code)

    def close(self):
        if not self._is_invalid(self.handle):
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
        for h_vol in self.volume_handles:
            if not self._is_invalid(h_vol):
                ctypes.windll.kernel32.CloseHandle(h_vol)
        self.volume_handles = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def _verify_disk_readback(dest, image_size, expected_sha256, chunk_size=4 * 1024 * 1024, progress_callback=None):
    """Hash exactly the source-image byte range from the physical disk."""
    if not isinstance(image_size, int) or image_size <= 0:
        raise ValueError("Readback image size must be a positive integer.")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected_sha256 or "")):
        raise ValueError("Readback requires a valid expected SHA-256.")
    if not isinstance(chunk_size, int) or chunk_size <= 0 or chunk_size % 512 != 0:
        raise ValueError("Readback chunk size must be a positive multiple of 512 bytes.")

    dest.seek(0)
    digest = hashlib.sha256()
    bytes_read = 0
    while bytes_read < image_size:
        requested = min(chunk_size, image_size - bytes_read)
        physical_read_size = ((requested + 511) // 512) * 512
        chunk = dest.read(physical_read_size)
        if len(chunk) < requested:
            raise OSError(
                f"Physical disk readback ended early at {bytes_read} of {image_size} bytes."
            )
        digest.update(chunk[:requested])
        bytes_read += requested
        if progress_callback is not None:
            progress_callback(bytes_read, image_size)

    actual_sha256 = digest.hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise OSError(
            "Physical disk readback SHA-256 mismatch: "
            f"expected {expected_sha256.lower()}, calculated {actual_sha256}."
        )
    return actual_sha256

def safe_print_err(msg):
    try:
        print(msg, file=sys.stderr)
        sys.stderr.flush()
    except Exception:
        try:
            enc = sys.stderr.encoding or 'ascii'
            clean_msg = str(msg).encode(enc, errors='replace').decode(enc)
            print(clean_msg, file=sys.stderr)
            sys.stderr.flush()
        except Exception:
            pass

def safe_print_out(msg):
    try:
        print(msg)
        sys.stdout.flush()
    except Exception:
        try:
            enc = sys.stdout.encoding or 'ascii'
            clean_msg = str(msg).encode(enc, errors='replace').decode(enc)
            print(clean_msg)
            sys.stdout.flush()
        except Exception:
            pass

def write_status(file_path, status, progress=0, message=""):
    """
    Writes structured JSON status to the progress file.
    """
    if not file_path:
        return
    try:
        data = {
            "status": status,
            "progress": progress,
            "message": message
        }
        # Write to a temp file then atomically replace the target.
        # Path.replace() is atomic on both POSIX and Windows (unlike os.remove + os.rename).
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        Path(temp_path).replace(file_path)
    except Exception as e:
        safe_print_err(f"Failed to write progress status file: {e}")

def _normalize_identity(data: dict, *, powershell: bool = False) -> dict:
    names = {
        "number": "Number" if powershell else "number",
        "friendly_name": "FriendlyName" if powershell else "friendly_name",
        "size_bytes": "Size" if powershell else "size_bytes",
        "bus_type": "BusType" if powershell else "bus_type",
        "is_system": "IsSystem" if powershell else "is_system",
        "is_boot": "IsBoot" if powershell else "is_boot",
        "serial_number": "SerialNumber" if powershell else "serial_number",
        "unique_id": "UniqueId" if powershell else "unique_id",
        "path": "Path" if powershell else "path",
    }
    identity = {}
    for field, source_name in names.items():
        if source_name not in data:
            raise ValueError(f"missing property: {field}")
        identity[field] = data[source_name]

    if isinstance(identity["number"], bool):
        raise ValueError("invalid disk number")
    identity["number"] = int(identity["number"])
    identity["size_bytes"] = int(identity["size_bytes"])
    if identity["number"] < 0 or identity["size_bytes"] <= 0:
        raise ValueError("invalid disk number or size")
    if not isinstance(identity["is_system"], bool) or not isinstance(identity["is_boot"], bool):
        raise ValueError("invalid boot/system flags")

    for field in ("friendly_name", "bus_type", "serial_number", "unique_id", "path"):
        value = str(identity[field]).strip() if identity[field] is not None else ""
        if not value:
            raise ValueError(f"empty property: {field}")
        identity[field] = value
    identity["bus_type"] = identity["bus_type"].upper()
    return identity


def _query_disk_identity(disk_number: int):
    command = f"""
    $physicalById = @{{}}
    Get-PhysicalDisk -ErrorAction SilentlyContinue | ForEach-Object {{
        $physicalById[[string]$_.DeviceId] = $_
    }}
    Get-Disk -Number {disk_number} | ForEach-Object {{
        $disk = $_
        $physical = $physicalById[[string]$disk.Number]
        [PSCustomObject]@{{
            Number = $disk.Number
            FriendlyName = $disk.FriendlyName
            Size = $disk.Size
            BusType = [string]$disk.BusType
            IsSystem = $disk.IsSystem
            IsBoot = $disk.IsBoot
            SerialNumber = $disk.SerialNumber
            UniqueId = $disk.UniqueId
            Path = $disk.Path
            MediaType = if ($physical) {{ [string]$physical.MediaType }} else {{ "Unspecified" }}
        }}
    }} | ConvertTo-Json -Depth 3
    """
    result = subprocess.run(
        ["powershell", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        **SUBPROCESS_FLAGS,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("disk identity query failed")
    data = json.loads(result.stdout.strip())
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError("disk identity query returned an unexpected number of disks")
        data = data[0]
    return _normalize_identity(data, powershell=True)


def _validate_disk_identity(disk_number: int, expected_identity: dict = None):
    if not isinstance(disk_number, int):
        raise TypeError(f"disk_number must be an integer, got {type(disk_number).__name__}")
    if sys.platform != "win32":
        return None
    try:
        current = _query_disk_identity(disk_number)
        if current["bus_type"] not in ("USB", "SD", "MMC", "1394"):
            raise ValueError(f"bus type '{current['bus_type']}' is not allowed")
        if current["is_system"] or current["is_boot"]:
            raise ValueError("disk is a system/boot drive")
        if expected_identity is not None:
            expected = _normalize_identity(expected_identity)
            for field in REQUIRED_IDENTITY_FIELDS:
                if current[field] != expected[field]:
                    raise ValueError(
                        f"disk identity changed for {field}: expected {expected[field]!r}, got {current[field]!r}"
                    )
        return current
    except Exception as error:
        safe_print_err(f"[SECURITY] Disk {disk_number} identity validation failed: {error}")
        return None


def _expected_status_path(disk_number: int) -> str:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile and os.path.exists(user_profile):
        temp_dir = os.path.join(user_profile, ".kace", "temp")
    else:
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    return os.path.realpath(os.path.join(temp_dir, f"kace_flash_{disk_number}.json"))


def _open_verified_image(image_path: str, expected_size: int, expected_sha256: str):
    """Opens, verifies, rewinds, and returns the same handle used for writing."""
    source = open(image_path, "rb")
    try:
        actual_size = os.fstat(source.fileno()).st_size
        if actual_size != expected_size:
            raise ValueError(
                f"image size changed: expected {expected_size} bytes, got {actual_size}"
            )
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"image checksum changed: expected {expected_sha256}, got {actual_sha256}"
            )
        source.seek(0)
        return source
    except Exception:
        source.close()
        raise


def main():
    if len(sys.argv) < 5:
        safe_print_err(
            "ERROR: Missing arguments. Usage: kace_writer.py "
            "<disk_number> <image_path> <status_file_path> <disk_identity_json>"
        )
        sys.exit(1)
        
    try:
        disk_number = int(sys.argv[1])
    except ValueError:
        safe_print_err("ERROR: Invalid disk number.")
        sys.exit(1)
        
    image_path = sys.argv[2]
    status_file = sys.argv[3]
    expected_status = _expected_status_path(disk_number)
    if os.path.normcase(os.path.realpath(status_file)) != os.path.normcase(expected_status):
        safe_print_err("ERROR: Status file path is outside the application-owned flash directory.")
        sys.exit(1)
    try:
        write_contract = json.loads(sys.argv[4])
        expected_identity = write_contract["disk_identity"]
        _normalize_identity(expected_identity)
        expected_image_size = int(write_contract["image_size"])
        expected_image_sha256 = str(write_contract["image_sha256"])
        if expected_image_size <= 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_image_sha256):
            raise ValueError("invalid image size or SHA-256")
    except (KeyError, json.JSONDecodeError, TypeError, ValueError) as contract_error:
        safe_print_err(f"ERROR: Invalid write contract: {contract_error}")
        write_status(status_file, "error", message="Invalid or incomplete write contract.")
        sys.exit(1)

    if not os.path.exists(image_path):
        err_msg = f"Image file not found."
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        sys.exit(1)

    try:
        verified_source = _open_verified_image(
            image_path, expected_image_size, expected_image_sha256
        )
    except (OSError, ValueError) as image_error:
        err_msg = f"Image verification failed: {image_error}"
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        sys.exit(1)

    current_identity = _validate_disk_identity(disk_number, expected_identity)
    if current_identity is None:
        verified_source.close()
        err_msg = f"Security check failed: disk {disk_number} identity changed or is unsafe. Aborting write."
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        sys.exit(1)

    image_size = expected_image_size
    if image_size > current_identity["size_bytes"]:
        verified_source.close()
        err_msg = (
            f"Image is too large for disk {disk_number}: {image_size} bytes required, "
            f"{current_identity['size_bytes']} bytes available."
        )
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        sys.exit(1)

    physical_path = rf"\\.\PhysicalDrive{disk_number}"
    
    try:
        # 1. Take disk offline to release file system locks (non-fatal)
        safe_print_out("STATUS: Taking disk offline...")
        write_status(status_file, "taking_offline", progress=0, message="Taking disk offline to release volume locks...")
        try:
            subprocess.run(
                ["powershell", "-Command", f"Set-Disk -Number {disk_number} -IsOffline $true"],
                check=True, capture_output=True, **SUBPROCESS_FLAGS
            )
        except Exception as offline_err:
            # Non-fatal warning - log and continue to let raw file open handle sharing check
            warn_msg = f"Warning: Failed to offline disk {disk_number}: {offline_err}"
            safe_print_err(warn_msg)
            write_status(status_file, "taking_offline", progress=0, message="Disk offline warning (proceeding)...")
        
        # 2. Write blocks
        safe_print_out("STATUS: Starting physical block write...")
        write_status(status_file, "writing", progress=0, message="Writing blocks...")
        
        bytes_written = 0
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        
        last_pct = -1
        with verified_source as src, Win32DiskWriter(physical_path, disk_number) as dest:
            # Verify exclusive lock was obtained on all volumes
            if dest.lock_failed:
                raise OSError(
                    "Failed to obtain exclusive lock on one or more volumes. "
                    "Close any File Explorer windows or applications accessing "
                    "the SD card and try again."
                )
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                
                chunk_len = len(chunk)
                # Sector alignment validation & padding for Windows physical drive writing
                if chunk_len % 512 != 0:
                    padding_len = 512 - (chunk_len % 512)
                    chunk = chunk + b"\x00" * padding_len
                
                dest.write(chunk)
                try:
                    dest.flush()
                except OSError:
                    pass
                
                bytes_written += chunk_len
                
                pct = int((bytes_written / image_size) * 100)
                if pct != last_pct:
                    safe_print_out(f"PROGRESS: {pct}")
                    write_status(status_file, "writing", progress=pct, message=f"Writing blocks: {pct}%")
                    last_pct = pct

            dest.flush()
            safe_print_out("STATUS: Verifying physical disk readback...")
            write_status(
                status_file,
                "verifying",
                progress=0,
                message="Verifying physical disk readback...",
            )
            last_verify_pct = [-1]

            def report_readback(bytes_read, total_bytes):
                pct = int((bytes_read / total_bytes) * 100)
                if pct != last_verify_pct[0]:
                    safe_print_out(f"VERIFY_PROGRESS: {pct}")
                    write_status(
                        status_file,
                        "verifying",
                        progress=pct,
                        message=f"Verifying physical disk readback: {pct}%",
                    )
                    last_verify_pct[0] = pct

            _verify_disk_readback(
                dest,
                image_size,
                expected_image_sha256,
                chunk_size=chunk_size,
                progress_callback=report_readback,
            )

        # 3. Bring disk back online to allow Windows to mount partitions (non-fatal)
        safe_print_out("STATUS: Bringing disk back online...")
        write_status(status_file, "bringing_online", progress=95, message="Bringing disk online...")
        try:
            subprocess.run(
                ["powershell", "-Command", f"Set-Disk -Number {disk_number} -IsOffline $false"],
                check=True, capture_output=True, **SUBPROCESS_FLAGS
            )
        except Exception as online_err:
            warn_msg = f"Warning: Failed to online disk {disk_number}: {online_err}"
            safe_print_err(warn_msg)
        
        # 4. Request Windows to mount partition and refresh drive list (non-fatal)
        safe_print_out("STATUS: Refreshing host storage cache...")
        write_status(status_file, "bringing_online", progress=98, message="Refreshing host storage cache...")
        try:
            subprocess.run(
                ["powershell", "-Command", "Update-HostStorageCache"],
                capture_output=True, **SUBPROCESS_FLAGS
            )
        except Exception as cache_err:
            warn_msg = f"Warning: Failed to refresh storage cache: {cache_err}"
            safe_print_err(warn_msg)
        
        safe_print_out("SUCCESS: Flashing completed successfully.")
        write_status(status_file, "success", progress=100, message="Flashing completed successfully.")
        sys.exit(0)
        
    except subprocess.CalledProcessError as e:
        # L3 FIX: Redact the command path from user-facing error messages.
        # e.cmd contains the full executable path + image path which can leak
        # sensitive filesystem layout information.
        stderr_msg = e.stderr.decode('utf-8', errors='replace').strip() if e.stderr else ""
        err_msg = f"System command failed (exit {e.returncode}): {stderr_msg if stderr_msg else 'No output.'}"
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        # Attempt to online disk in case of failure
        try:
            subprocess.run(
                ["powershell", "-Command", f"Set-Disk -Number {disk_number} -IsOffline $false"],
                capture_output=True, **SUBPROCESS_FLAGS
            )
        except Exception as online_err:
            safe_print_err(f"Warning: Failed to re-online disk after error: {online_err}")
        sys.exit(2)
    except Exception as e:
        err_msg = str(e)
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        # Attempt to online disk in case of failure
        try:
            subprocess.run(
                ["powershell", "-Command", f"Set-Disk -Number {disk_number} -IsOffline $false"],
                capture_output=True, **SUBPROCESS_FLAGS
            )
        except Exception as online_err:
            safe_print_err(f"Warning: Failed to re-online disk after error: {online_err}")
        sys.exit(2)

if __name__ == "__main__":
    main()
