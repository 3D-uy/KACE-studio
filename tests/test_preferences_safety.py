import json

from main import Api


def test_preferences_are_filtered_and_atomically_published(tmp_path):
    api = Api()
    api._prefs_path = str(tmp_path / "prefs.json")

    assert api.set_preferences({
        "theme": "light",
        "kace_auto_scan": False,
        "form_state": {
            "hostname-input": "printer-one",
            "wifi-password": "must-not-persist",
        },
        "unexpected": "ignored",
    })

    stored = json.loads((tmp_path / "prefs.json").read_text(encoding="utf-8"))
    assert stored == {
        "theme": "light",
        "kace_auto_scan": False,
        "form_state": {"hostname-input": "printer-one"},
    }
    assert not (tmp_path / "prefs.json.part").exists()


def test_failed_preferences_publish_removes_partial_file(tmp_path, monkeypatch):
    api = Api()
    api._prefs_path = str(tmp_path / "prefs.json")

    def fail_replace(_source, _target):
        raise OSError("simulated publish failure")

    monkeypatch.setattr("main.os.replace", fail_replace)

    assert not api.set_preferences({"theme": "dark"})
    assert not (tmp_path / "prefs.json.part").exists()
