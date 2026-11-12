import json

from backend import ssh_diagnostics


def test_opt_in_records_cause_locations_without_secrets(tmp_path, monkeypatch):
    target = tmp_path / "ssh.jsonl"
    monkeypatch.setenv("KACE_STUDIO_SSH_DIAGNOSTICS", str(target))
    try:
        raise ConnectionResetError(10054, "password=secret-command-token")
    except ConnectionResetError as error:
        ssh_diagnostics.record("stream_error", session="test", phase="receive",
                               error=error, active=False)
    raw = target.read_text()
    item = json.loads(raw)
    assert "secret-command-token" not in raw
    assert "password=" not in raw
    assert item["error_type"] == "ConnectionResetError"
    assert item["errno"] == 10054
    assert item["frames"][0]["file"] == "test_ssh_diagnostics.py"
    assert item["active"] is False
    assert item["unix_ns"] > 0 and item["monotonic_ns"] > 0


def test_disabled_diagnostics_do_not_open_files(monkeypatch):
    monkeypatch.delenv("KACE_STUDIO_SSH_DIAGNOSTICS", raising=False)
    monkeypatch.setattr(ssh_diagnostics, "RotatingFileHandler",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("opened")))
    ssh_diagnostics.record("stream_start", session="test")


def test_storage_failure_does_not_interrupt_ssh(tmp_path, monkeypatch):
    target = tmp_path / "not-a-directory"
    target.write_text("preserve")
    monkeypatch.setenv("KACE_STUDIO_SSH_DIAGNOSTICS", str(target / "ssh.jsonl"))
    ssh_diagnostics.record("stream_error", session="test", error=TimeoutError())
    assert target.read_text() == "preserve"
