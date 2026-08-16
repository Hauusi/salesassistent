"""Symmetric encryption for secrets at rest (OAuth tokens).

Uses Fernet (AES-128-CBC + HMAC) with a key from ``TOKEN_ENCRYPTION_KEY``.
Never store OAuth access/refresh tokens in plaintext - always go through
``encrypt``/``decrypt`` here before persisting or after reading them.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenEncryptionNotConfigured(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenEncryptionNotConfigured(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" and put it in your .env."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored token - key mismatch or corrupted data.") from exc
