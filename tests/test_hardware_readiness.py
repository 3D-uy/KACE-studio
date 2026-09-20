"""No real storage: Win32 fakes and PowerShell with all storage commands replaced."""
import base64
import json
import shutil
import subprocess

import pytest

from backend import ejector, kace_writer
from tests.test_writer_volume_safety import storage, IDENTITY, PHYSICAL, VOLUME_A
from tests.test_audit_stabilization import handle_reply


@pytest.mark.parametrize("changed", ["disk", "volume", "missing"])
def test_no_dismount_or_write_before_actual_handle_identity(storage, monkeypatch, changed):
    kernel, query = storage
    query.return_value.stdout = json.dumps(["E:\\"])
    if changed == "missing":
        kernel.CreateFileW.side_effect = [101, -1]
    else:
        def reply(self, *args):
            replaced = changed == "disk" or self.handle == 101
            return handle_reply(IDENTITY, "OTHER" if replaced else None)(*args)
        monkeypatch.setattr(kace_writer.Win32DiskWriter, "_query_handle", reply)
    with pytest.raises(OSError):
        with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
            writer.verify_identity(IDENTITY)
    kernel.DeviceIoControl.assert_not_called()
    kernel.WriteFile.assert_not_called()
    assert 101 in [c.args[0] for c in kernel.CloseHandle.call_args_list]


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell unavailable")
@pytest.mark.parametrize("mode,success", [
    ("partition_error", False), ("disk_error", False), ("online", False),
    ("changed_identity", False), ("offline", True), ("removed", True),
])
def test_real_eject_script_never_converts_storage_errors_to_safe_removal(mode, success):
    identity = dict(IDENTITY, number=3)
    # All commands with access to storage/COM are replaced before executing the
    # production script. No physical device is enumerated, opened or modified.
    mocks = r'''
$script:diskReads=0
$script:partReads=0
function Get-CimInstance { [pscustomobject]@{PNPDeviceID=$null} }
function Get-Disk {
    param($Number)
    $script:diskReads++
    if ($script:diskReads -gt 2 -and $mode -eq 'disk_error') { throw 'disk query failed' }
    if ($script:diskReads -gt 2 -and $mode -eq 'removed') { return }
    $serial = $identity.serial_number
    if ($script:diskReads -gt 2 -and $mode -eq 'changed_identity') { $serial='OTHER' }
        [pscustomobject]@{Number=3; FriendlyName=$identity.friendly_name; IsSystem=$false; IsBoot=$false;
        IsOffline=($mode -ne 'online'); SerialNumber=$serial;
        UniqueId=$identity.unique_id; Path=$identity.path;
        Size=$identity.size_bytes; BusType=$identity.bus_type}
}
function Get-Partition {
    param($DiskNumber)
    $script:partReads++
    if ($script:partReads -gt 1 -and $mode -eq 'partition_error') { throw 'partition query failed' }
    if ($script:partReads -eq 1) { [pscustomobject]@{DriveLetter='R'} }
}
function Set-Disk { throw 'SIMULATED offline failure' }
function Start-Sleep { param($Milliseconds) }
function New-Object {
    param($ComObject)
    $item=[pscustomobject]@{}
    $item | Add-Member ScriptMethod Verbs { @() }
    $item | Add-Member ScriptMethod InvokeVerb { param($verb) }
    $folder=[pscustomobject]@{Item=$item}
    $folder | Add-Member ScriptMethod ParseName { param($name) $this.Item }
    $shell=[pscustomobject]@{Folder=$folder}
    $shell | Add-Member ScriptMethod Namespace { param($number) $this.Folder }
    return $shell
}
'''
    script = (f"$mode='{mode}'; $identity='{json.dumps(identity)}' | ConvertFrom-Json\n"
              + mocks + ejector._powershell_eject_command(3, identity))
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["success"] is success, result
    if not success:
        assert result["error"]
