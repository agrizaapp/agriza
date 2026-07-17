import os
import hmac
import hashlib

def hpw(password):
    salt = os.urandom(16)
    rounds = 210_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def vpw(password, encoded):
    try:
        _, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            int(rounds),
        ).hex()
        return hmac.compare_digest(digest, digest_hex)
    except Exception:
        return False


