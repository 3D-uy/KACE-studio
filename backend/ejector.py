import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import uuid

from backend.imager import SUBPROCESS_FLAGS, _normalize_disk_identity
from backend.kace_writer import _query_disk_identity


EJECT_IDENTITY_FIELDS = (
    "number",
    "friendly_name",
    "size_bytes",
    "bus_type",
    "is_system",
    "is_boot",
    "serial_number",
    "path",
)


def _status_directory() -> str:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile and os.path.exists(user_profile):
        directory = os.path.join(user_profile, ".kace", "temp")
    else:
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(directory, exist_ok=True)
    return os.path.realpath(directory)


def _validate_status_path(status_file: str) -> str:
    status_directory = _status_directory()
    resolved = os.path.realpath(status_file)
    try:
        contained = os.path.commonpath((status_directory, resolved)) == status_directory
    except ValueError:
        contained = False
    if not contained or not os.path.basename(resolved).startswith("kace_eject_"):
        raise ValueError("Invalid eject status path.")
    return resolved


def _write_status(status_file: str, result: dict) -> None:
    resolved = _validate_status_path(status_file)
    temporary = f"{resolved}.tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8", newline="\n") as output:
        json.dump(result, output, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, resolved)


def _powershell_eject_command(disk_number: int) -> str:
    return f"""
$ErrorActionPreference = 'Stop'
$diskNumber = {int(disk_number)}
$result = [ordered]@{{
    success = $false
    method = ''
    message = ''
    error = ''
}}

function Get-MountedLetters([int]$number) {{
    try {{
        return @(Get-Partition -DiskNumber $number -ErrorAction Stop |
            Where-Object {{ $_.DriveLetter -and $_.DriveLetter -ne [char]0 }} |
            ForEach-Object {{ [string]$_.DriveLetter }})
    }} catch {{
        return @()
    }}
}}

try {{
    $disk = Get-Disk -Number $diskNumber -ErrorAction Stop
    if ($disk.IsSystem -or $disk.IsBoot) {{
        throw 'Refusing to eject a system or boot disk.'
    }}

    $letters = @(Get-MountedLetters $diskNumber)
    $verbInvoked = $false
    if ($letters.Count -gt 0) {{
        try {{
            $shell = New-Object -ComObject Shell.Application
            $folder = $shell.Namespace(17)
            foreach ($letter in $letters) {{
                try {{
                    $item = $folder.ParseName("$($letter):")
                    if (-not $item) {{ continue }}
                    $verb = $item.Verbs() | Where-Object {{
                        $_.Name.Replace('&', '') -match '(?i)^(eject|expulsar|ejetar|éjecter|auswerfen|espelli|извлечь|uitwerpen)'
                    }} | Select-Object -First 1
                    if ($verb) {{
                        $verb.DoIt()
                    }} else {{
                        $item.InvokeVerb('Eject')
                    }}
                    $verbInvoked = $true
                }} catch {{}}
            }}
        }} catch {{}}
    }}

    if ($verbInvoked) {{
        for ($attempt = 0; $attempt -lt 10; $attempt++) {{
            Start-Sleep -Milliseconds 500
            $current = Get-Disk -Number $diskNumber -ErrorAction SilentlyContinue
            if (-not $current) {{
                $result.success = $true
                $result.method = 'ejected'
                $result.message = 'The device was ejected and is safe to remove.'
                break
            }}
            if (@(Get-MountedLetters $diskNumber).Count -eq 0) {{
                $result.success = $true
                $result.method = 'unmounted'
                $result.message = 'All volumes were unmounted. The device is safe to remove.'
                break
            }}
        }}
    }}

    if (-not $result.success) {{
        $disk = Get-Disk -Number $diskNumber -ErrorAction SilentlyContinue
        if (-not $disk) {{
            $result.success = $true
            $result.method = 'ejected'
            $result.message = 'The device was ejected and is safe to remove.'
        }} else {{
            Set-Disk -Number $diskNumber -IsOffline $true -ErrorAction Stop
            Start-Sleep -Milliseconds 500
            $current = Get-Disk -Number $diskNumber -ErrorAction SilentlyContinue
            if ($current -and -not $current.IsOffline) {{
                throw 'Windows did not leave the disk offline.'
            }}
            if (@(Get-MountedLetters $diskNumber).Count -gt 0) {{
                throw 'One or more volumes remain mounted.'
            }}
            $result.success = $true
            $result.method = 'offline'
            $result.message = 'The disk is offline and all volumes are unmounted. It is safe to remove.'
        }}
    }}
}} catch {{
    $result.success = $false
    $result.error = $_.Exception.Message
}}

$result | ConvertTo-Json -Compress
"""


def _perform_windows_eject(disk_number: int) -> dict:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _powershell_eject_command(disk_number)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr.strip() or "The elevated eject command failed.",
        }
    try:
        payload = json.loads(result.stdout.strip())
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "error": "The eject command returned an invalid result."}
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return {
            "success": False,
            "error": str(payload.get("error") or "Windows could not safely remove the disk."),
        }
    return {
        "success": True,
        "method": str(payload.get("method") or "verified"),
        "message": str(payload.get("message") or "The disk is safe to remove."),
    }


def _validate_eject_identity(disk_number: int, expected_identity: dict) -> dict | None:
    try:
        expected = _normalize_disk_identity(expected_identity)
        current = _query_disk_identity(disk_number)
        if current["bus_type"] not in ("USB", "SD", "MMC", "1394"):
            raise ValueError("Disk bus type is not removable.")
        if current["is_system"] or current["is_boot"]:
            raise ValueError("Disk is a system or boot device.")
        for field in EJECT_IDENTITY_FIELDS:
            if current[field] != expected[field]:
                raise ValueError(f"Disk identity changed for {field}.")
        return current
    except (KeyError, TypeError, ValueError, OSError):
        return None


def privileged_eject(disk_number: int, status_file: str, expected_identity: dict) -> bool:
    try:
        status_file = _validate_status_path(status_file)
        expected = _normalize_disk_identity(expected_identity)
        if expected["number"] != disk_number:
            raise ValueError("Disk number does not match the expected identity.")
        if _validate_eject_identity(disk_number, expected) is None:
            raise ValueError("Disk identity changed or the target is no longer safe.")
        result = _perform_windows_eject(disk_number)
    except Exception as error:
        result = {"success": False, "error": str(error)}
    _write_status(status_file, result)
    return result.get("success") is True


def _escape_windows_argument(argument: str) -> str:
    if not argument:
        return '""'
    if not any(character in argument for character in (' ', '\t', '"')):
        return argument
    escaped = []
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
        elif character == '"':
            escaped.append("\\" * (2 * backslashes + 1))
            escaped.append('"')
            backslashes = 0
        else:
            if backslashes:
                escaped.append("\\" * backslashes)
                backslashes = 0
            escaped.append(character)
    if backslashes:
        escaped.append("\\" * (2 * backslashes))
    return '"' + "".join(escaped) + '"'


def _launch_elevated_helper(arguments: list[str]) -> tuple[bool, str]:
    if hasattr(sys, "_MEIPASS") or not sys.executable.lower().endswith("python.exe"):
        executable = sys.executable
        command_arguments = arguments
    else:
        executable = sys.executable
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        command_arguments = [os.path.join(project_root, "main.py"), *arguments]

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

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040
    info.lpOperation = "runas"
    info.lpFile = executable
    info.lpParameters = " ".join(_escape_windows_argument(item) for item in command_arguments)
    info.nShow = 0

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return False, "Administrative privilege prompt was declined or failed to launch."
    if not info.hProcess:
        return False, "Failed to obtain the elevated eject process handle."

    try:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = wintypes.DWORD(1)
        if not ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            return False, "Could not read the elevated eject process result."
        return True, ""
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)


def request_safe_eject(disk_number: int, expected_identity: dict) -> dict:
    if sys.platform != "win32":
        return {"success": False, "error": "Eject is only supported on Windows."}
    try:
        expected = _normalize_disk_identity(expected_identity)
    except (TypeError, ValueError) as error:
        return {"success": False, "error": f"Invalid target disk identity: {error}"}
    if expected["number"] != disk_number:
        return {"success": False, "error": "Target disk identity does not match the selected disk."}

    status_file = os.path.join(_status_directory(), f"kace_eject_{uuid.uuid4().hex}.json")
    arguments = [
        "--eject-disk",
        str(disk_number),
        status_file,
        json.dumps(expected, separators=(",", ":")),
    ]
    launched, launch_error = _launch_elevated_helper(arguments)
    if not launched:
        return {"success": False, "error": launch_error}

    try:
        with open(status_file, "r", encoding="utf-8") as source:
            result = json.load(source)
    except (OSError, json.JSONDecodeError):
        return {"success": False, "error": "Elevated eject helper returned no valid status."}
    finally:
        try:
            os.remove(status_file)
        except OSError:
            pass
    if not isinstance(result, dict):
        return {"success": False, "error": "Elevated eject helper returned an invalid status."}
    return result


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(2)
    try:
        disk_number = int(sys.argv[1])
        status_file = sys.argv[2]
        expected_identity = json.loads(sys.argv[3])
    except (TypeError, ValueError, json.JSONDecodeError):
        sys.exit(2)
    sys.exit(0 if privileged_eject(disk_number, status_file, expected_identity) else 1)
