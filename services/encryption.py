"""Fernet wrapper. Used for Modal token storage at rest."""
from __future__ import annotations

from cryptography.fernet import Fernet

from config import cfg

_fernet = Fernet(cfg.encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()
