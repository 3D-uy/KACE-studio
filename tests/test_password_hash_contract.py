from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_password_hashing_does_not_depend_on_abandoned_pcrypt():
    runtime_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    locked_requirements = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    implementation = (ROOT / "backend" / "sha512_crypt.py").read_text(encoding="utf-8")

    assert "pcrypt" not in runtime_requirements.lower()
    assert "pcrypt" not in locked_requirements.lower()
    assert "pcrypt" not in implementation.lower()


def test_password_hash_is_sha512_crypt_and_verifies_both_outcomes():
    from backend.sha512_crypt import hash_password, verify_password

    password = "correct horse battery staple"
    password_hash = hash_password(password)

    assert password_hash.startswith("$6$")
    assert verify_password(password, password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_password_hash_uses_a_fresh_salt():
    from backend.sha512_crypt import hash_password

    password = "same-password"
    assert hash_password(password) != hash_password(password)
