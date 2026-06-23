import pcrypt

def hash_password(password: str) -> str:
    """
    Generates a Unix-compatible SHA-512 crypt hash (Modular Crypt Format, $6$)
    suitable for use in userconf.txt / /etc/shadow.

    Complexity & Salt details:
    - Uses `pcrypt.mksalt(pcrypt.METHOD_SHA512)` to generate a crypt-compatible salt.
    - The salt consists of 16 random characters from the set [a-zA-Z0-9./], providing
      96 bits of entropy (6 bits per character).
    - The final hash format matches Modular Crypt Format: $6$rounds=<rounds>$<salt>$<hash>
      (defaulting to 5000 rounds of SHA-512 crypt, which is the system standard).
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
