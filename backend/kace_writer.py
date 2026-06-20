import os
import sys
import json
import subprocess
import ctypes
from ctypes import wintypes

class Win32DiskWriter:
    def __init__(self, physical_path, disk_number=None):
        self.physical_path = physical_path
        self.handle = None
        self.volume_handles = []
        
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
        try:
            cmd = f"powershell -Command \"Get-Partition -DiskNumber {disk_number} | Select-Object -ExpandProperty AccessPaths\""
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
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

    def flush(self):
        if not self._is_invalid(self.handle):
            self._FlushFileBuffers(self.handle)

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
        # Write to a temporary file first, then rename it to ensure atomic writes
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if os.path.exists(file_path):
            os.remove(file_path)
        os.rename(temp_path, file_path)
    except Exception as e:
        safe_print_err(f"Failed to write progress status file: {e}")

def main():
    if len(sys.argv) < 3:
        safe_print_err("ERROR: Missing arguments. Usage: kace_writer.py <disk_number> <image_path> [status_file_path]")
        sys.exit(1)
        
    try:
        disk_number = int(sys.argv[1])
    except ValueError:
        safe_print_err("ERROR: Invalid disk number.")
        sys.exit(1)
        
    image_path = sys.argv[2]
    status_file = sys.argv[3] if len(sys.argv) > 3 else None
    
    if not os.path.exists(image_path):
        err_msg = f"Image file not found: {image_path}"
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
                f"powershell -Command \"Set-Disk -Number {disk_number} -IsOffline $true\"",
                shell=True, check=True, capture_output=True
            )
        except Exception as offline_err:
            # Non-fatal warning - log and continue to let raw file open handle sharing check
            warn_msg = f"Warning: Failed to offline disk {disk_number}: {offline_err}"
            safe_print_err(warn_msg)
            write_status(status_file, "taking_offline", progress=0, message="Disk offline warning (proceeding)...")
        
        # 2. Write blocks
        safe_print_out("STATUS: Starting physical block write...")
        write_status(status_file, "writing", progress=0, message="Writing blocks...")
        
        image_size = os.path.getsize(image_path)
        bytes_written = 0
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        
        last_pct = -1
        with open(image_path, "rb") as src, Win32DiskWriter(physical_path, disk_number) as dest:
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
                    
        # 3. Bring disk back online to allow Windows to mount partitions (non-fatal)
        safe_print_out("STATUS: Bringing disk back online...")
        write_status(status_file, "bringing_online", progress=95, message="Bringing disk online...")
        try:
            subprocess.run(
                f"powershell -Command \"Set-Disk -Number {disk_number} -IsOffline $false\"",
                shell=True, check=True, capture_output=True
            )
        except Exception as online_err:
            warn_msg = f"Warning: Failed to online disk {disk_number}: {online_err}"
            safe_print_err(warn_msg)
        
        # 4. Request Windows to mount partition and refresh drive list (non-fatal)
        safe_print_out("STATUS: Refreshing host storage cache...")
        write_status(status_file, "bringing_online", progress=98, message="Refreshing host storage cache...")
        try:
            subprocess.run(
                "powershell -Command \"Update-HostStorageCache\"",
                shell=True, capture_output=True
            )
        except Exception as cache_err:
            warn_msg = f"Warning: Failed to refresh storage cache: {cache_err}"
            safe_print_err(warn_msg)
        
        safe_print_out("SUCCESS: Flashing completed successfully.")
        write_status(status_file, "success", progress=100, message="Flashing completed successfully.")
        sys.exit(0)
        
    except subprocess.CalledProcessError as e:
        stderr_msg = e.stderr.decode('utf-8', errors='replace').strip() if e.stderr else ""
        err_msg = f"System command failed.\nCommand: {e.cmd}\nExit code: {e.returncode}\nDetail: {stderr_msg if stderr_msg else 'No output description.'}"
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        # Attempt to online disk in case of failure
        try:
            subprocess.run(
                f"powershell -Command \"Set-Disk -Number {disk_number} -IsOffline $false\"",
                shell=True, capture_output=True
            )
        except:
            pass
        sys.exit(2)
    except Exception as e:
        err_msg = str(e)
        safe_print_err(f"ERROR: {err_msg}")
        write_status(status_file, "error", message=err_msg)
        # Attempt to online disk in case of failure
        try:
            subprocess.run(
                f"powershell -Command \"Set-Disk -Number {disk_number} -IsOffline $false\"",
                shell=True, capture_output=True
            )
        except:
            pass
        sys.exit(2)

if __name__ == "__main__":
    main()
