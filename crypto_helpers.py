"""PIN-based symmetric encryption for secrets stored on disk.

Used to protect DNS-log API keys (e.g. the Pi-hole web/app password) at rest in
``connections.json`` so that physically inspecting the device's storage does
not reveal them.

The PIN itself is never stored; it is supplied by the user at runtime. A random
16-byte salt is generated per encrypted secret and stored alongside the
ciphertext. The key is derived using Scrypt (memory-hard) and the secret is
sealed with Fernet (AES-128-CBC + HMAC-SHA256), both from the ``cryptography``
library already used elsewhere in this project.
"""
import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_LENGTH = 16


def _derive_key(pin: str, salt: bytes) -> bytes:
    """Derive a URL-safe base64 Fernet key from the PIN and salt via Scrypt."""
    kdf = Scrypt(
        salt=salt,
        length=KEY_LENGTH,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    raw = kdf.derive(pin.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def encrypt_secret(pin: str, secret: str):
    """Encrypt ``secret`` with a key derived from ``pin``.

    Returns a ``(token_b64, salt_b64)`` tuple of urlsafe-base64 strings suitable
    for storing as JSON strings.
    """
    if not pin:
        raise ValueError("PIN is required for encryption")
    salt = os.urandom(SALT_LENGTH)
    key = _derive_key(pin, salt)
    token = Fernet(key).encrypt(secret.encode("utf-8"))
    return token.decode("ascii"), base64.urlsafe_b64encode(salt).decode("ascii")


def decrypt_secret(pin: str, token_b64: str, salt_b64: str) -> str:
    """Decrypt a secret previously produced by :func:`encrypt_secret`.

    Raises ``ValueError`` with a descriptive message if the PIN is wrong or the
    ciphertext has been tampered with.
    """
    if not pin:
        raise ValueError("PIN is required for decryption")
    try:
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        token = token_b64.encode("ascii")
    except Exception as exc:
        raise ValueError(f"corrupt encrypted secret: {exc}") from exc
    key = _derive_key(pin, salt)
    try:
        plaintext = Fernet(key).decrypt(token)
    except InvalidToken:
        raise ValueError("incorrect PIN or secret has been tampered with") from None
    return plaintext.decode("utf-8")