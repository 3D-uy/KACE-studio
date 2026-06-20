import pcrypt

def hash_password(password: str) -> str:
    """
    Generates a Unix-compatible SHA-512 crypt hash (Modular Crypt Format, $6$)
    suitable for use in userconf.txt / /etc/shadow.
    """
    salt = pcrypt.mksalt(pcrypt.METHOD_SHA512)
    return pcrypt.crypt(password, salt)

if __name__ == "__main__":
    # Self-test
    pwd = "kace_password_123"
    hashed = hash_password(pwd)
    print(f"Password: {pwd}")
    print(f"Hashed:   {hashed}")
    assert hashed.startswith("$6$")
    print("Self-test passed!")
