from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_smoke_is_distinct_from_resource_verification():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert '"--verify-package"' in source
    assert '"--smoke-test"' in source
    assert "run_pywebview_smoke_test" in source
    assert "smoke_mode=True" in source
    assert "window.hide()" in source
    assert "window.pywebview.api.get_preferences" in source


def test_ci_executes_the_real_packaged_pywebview_smoke():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "scripts/smoke_executable.py" in workflow
    assert "dist/KACE-studio.exe" in workflow or "dist\\KACE-studio.exe" in workflow


def test_smoke_harness_has_a_hard_process_timeout():
    harness = (ROOT / "scripts" / "smoke_executable.py").read_text(encoding="utf-8")

    assert "TimeoutExpired" in harness
    assert "process.wait(timeout=" in harness
    assert '"taskkill"' in harness


class _LoadedEvent:
    def wait(self, timeout):
        return True


class _FakeWindow:
    def __init__(self, observation):
        self.events = type(
            "Events", (), {"shown": _LoadedEvent(), "loaded": _LoadedEvent()}
        )()
        self.observation = observation
        self.destroyed = False
        self.hidden = False

    def evaluate_js(self, script):
        assert "window.pywebview.api.get_preferences" in script
        return self.observation

    def destroy(self):
        self.destroyed = True

    def hide(self):
        self.hidden = True


class _FakeWebView:
    def __init__(self, observation):
        self.window = _FakeWindow(observation)
        self.create_kwargs = None

    def create_window(self, **kwargs):
        self.create_kwargs = kwargs
        return self.window

    @staticmethod
    def start(function, args):
        function(*args)


def test_source_smoke_uses_hidden_real_application_contract(monkeypatch):
    import main

    runtime = _FakeWebView({
        "title": "KACE Studio", "ready": True, "root": True, "bridge": True,
    })
    monkeypatch.setattr(main, "webview", runtime)
    monkeypatch.setattr(main, "verify_runtime_resources", lambda: None)

    main.run_pywebview_smoke_test(timeout_seconds=1)

    assert runtime.create_kwargs["hidden"] is False
    assert runtime.create_kwargs["focus"] is False
    assert runtime.create_kwargs["x"] == -32_000
    assert runtime.create_kwargs["y"] == -32_000
    assert runtime.create_kwargs["url"].__class__ is main.KaceWsgiApp
    assert runtime.create_kwargs["js_api"].__class__ is main.Api
    assert runtime.window.hidden is True
    assert runtime.window.destroyed is True


def test_source_smoke_fails_closed_on_bridge_mismatch(monkeypatch):
    import main

    runtime = _FakeWebView({
        "title": "KACE Studio", "ready": True, "root": True, "bridge": False,
    })
    monkeypatch.setattr(main, "webview", runtime)
    monkeypatch.setattr(main, "verify_runtime_resources", lambda: None)

    with pytest.raises(RuntimeError, match="runtime contract mismatch"):
        main.run_pywebview_smoke_test(timeout_seconds=1)
    assert runtime.window.destroyed is True
