"""First-use approval is bound to one identity, selection and attempt."""
import time

import pytest

import main
from backend.ssh_client import UnknownHostKey, paramiko


@pytest.fixture
def scenario(monkeypatch):
    key = paramiko.RSAKey.generate(1024)
    sessions = []

    class Candidate:
        def __init__(self):
            self.approved_host_key = None
            sessions.append(self)

        def connect(self, *_args):
            raise UnknownHostKey("pi.local", key)

        def close(self):
            pass

        def is_connected(self):
            return False

    api = main.Api()
    monkeypatch.setattr(main, "SSHSession", Candidate)
    monkeypatch.setattr(api, "set_device_state", lambda *_args: None)
    assert api.select_power_target("pi.local", 1)
    return api, key, sessions


def challenge(api):
    result = api.connect_ssh("pi.local", "pi", "password", power_selection=1)
    assert result["status"] == "host_key_confirmation_required"
    assert result["fingerprint"].startswith("SHA256:")
    return result


def test_approval_is_single_use_and_binds_exact_identity(scenario):
    api, key, sessions = scenario
    result = challenge(api)
    assert api.respond_ssh_host_key(result["token"], True)
    assert not api.respond_ssh_host_key(result["token"], True)
    # A replay invalidates the pending approval rather than extending it.
    challenge(api)
    assert sessions[-1].approved_host_key is None

    result = challenge(api)
    assert api.respond_ssh_host_key(result["token"], True)
    challenge(api)
    assert sessions[-1].approved_host_key == ("pi.local", key.get_name(), key.get_base64())
    challenge(api)
    assert sessions[-1].approved_host_key is None


@pytest.mark.parametrize("change", ["reject", "selection", "disconnect", "expired", "new_attempt"])
def test_old_confirmation_cannot_authorize_another_attempt(scenario, change):
    api, _key, _sessions = scenario
    result = challenge(api)
    if change == "reject":
        assert not api.respond_ssh_host_key(result["token"], False)
    elif change == "selection":
        assert api.select_power_target("other.local", 2)
    elif change == "disconnect":
        assert api.disconnect_ssh(1)
    elif change == "expired":
        api._ssh_host_key_challenge["expires"] = time.monotonic() - 1
    else:
        challenge(api)
    assert not api.respond_ssh_host_key(result["token"], True)


def test_automatic_recovery_does_not_approve_an_unknown_key(scenario):
    api, _key, _sessions = scenario
    result = api.connect_ssh("pi.local", "pi", "password", power_selection=1,
                             _reconnect_attempt=api._ssh_attempt_gen)
    assert result == {"status": "host_key_confirmation_required"}
    assert api._ssh_host_key_challenge is None


def test_invalid_token_is_rejected(scenario):
    api, _key, _sessions = scenario
    challenge(api)
    assert not api.respond_ssh_host_key("ñ" * 43, True)
