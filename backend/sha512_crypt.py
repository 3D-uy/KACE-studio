from passlib.hash import sha512_crypt


# Keep the work factor explicit so generated images do not silently change their
# password-hash cost when the implementation is upgraded.
SHA512_CRYPT_ROUNDS = 656_000

def hash_password(password: str) -> str:
    """
    Generates a Unix-compatible SHA-512 crypt hash (Modular Crypt Format, $6$)
    suitable for use in userconf.txt / /etc/shadow.

    The final hash uses Modular Crypt Format and an independently generated salt.
    The work factor is fixed here instead of inheriting a dependency default.
    """
    return sha512_crypt.using(rounds=SHA512_CRYPT_ROUNDS).hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without depending on the removed ``crypt``-style API."""
    return sha512_crypt.verify(password, password_hash)

if __name__ == "__main__":
    # Self-test
    pwd = "kace_password_123"
    hashed = hash_password(pwd)
    print(f"Password: {pwd}")
    print(f"Hashed:   {hashed}")
    assert hashed.startswith("$6$")
    print("Self-test passed!")
