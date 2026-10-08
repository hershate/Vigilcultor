"""Vault implementation: key derivation, check blob, unlock cache, encrypt/decrypt."""

from __future__ import annotations

import base64
import os
import secrets as py_secrets
import sys

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vigilcultor.config import data_dir
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Kv

ENV_MASTER_PASSWORD = "VIGILCULTOR_MASTER_PASSWORD"

_SALT_KEY = "vault_salt"
_CHECK_KEY = "vault_check"
_CHECK_PLAINTEXT = b"vigilcultor-vault-check"

# Argon2id parameters for key derivation (interactive UX, not password storage).
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536
_ARGON2_PARALLELISM = 2
_ARGON2_HASH_LEN = 32


class VaultError(RuntimeError):
    pass


def _derive(password: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=Type.ID,
    )


def _kv_get(key: str) -> str | None:
    with get_session_maker()() as session:
        row = session.get(Kv, key)
        return row.value if row else None


def _kv_set(key: str, value: str) -> None:
    with get_session_maker()() as session:
        row = session.get(Kv, key)
        if row:
            row.value = value
        else:
            session.add(Kv(key=key, value=value))
        session.commit()


def _encode(key: bytes, plaintext: bytes) -> str:
    nonce = os.urandom(12)
    blob = nonce + AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.b64encode(blob).decode("ascii")


def _decode(key: bytes, value: str) -> bytes:
    blob = base64.b64decode(value.encode("ascii"))
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def _unlock_key_path():
    return data_dir() / "unlock.key"


def is_configured() -> bool:
    return _kv_get(_SALT_KEY) is not None


def set_master_password(password: str) -> None:
    """Initialize (or reinitialize) the vault with a new master password."""
    if len(password) < 8:
        raise VaultError("master password must be at least 8 characters")
    salt = os.urandom(16)
    key = _derive(password, salt)
    _kv_set(_SALT_KEY, base64.b64encode(salt).decode("ascii"))
    _kv_set(_CHECK_KEY, _encode(key, _CHECK_PLAINTEXT))


def _stored_key_for(password: str) -> bytes | None:
    salt_b64 = _kv_get(_SALT_KEY)
    check = _kv_get(_CHECK_KEY)
    if salt_b64 is None or check is None:
        return None
    key = _derive(password, base64.b64decode(salt_b64.encode("ascii")))
    try:
        _decode(key, check)
    except Exception:  # noqa: BLE001 - wrong password surfaces as decrypt failure
        return None
    return key


def unlock(password: str) -> bool:
    """Verify the master password and cache the derived key for local processes."""
    key = _stored_key_for(password)
    if key is None:
        return False
    path = _unlock_key_path()
    path.write_bytes(key)
    path.chmod(0o600)
    return True


def lock() -> None:
    _unlock_key_path().unlink(missing_ok=True)


def _env_key() -> bytes | None:
    password = os.environ.get(ENV_MASTER_PASSWORD)
    if not password:
        return None
    return _stored_key_for(password)


def active_key() -> bytes | None:
    """Current usable vault key: env password first, then the unlock cache."""
    key = _env_key()
    if key is not None:
        return key
    path = _unlock_key_path()
    if not path.exists():
        return None
    key = path.read_bytes()
    # Validate against the check blob so stale/foreign key files are rejected.
    check = _kv_get(_CHECK_KEY)
    if check is None:
        return None
    try:
        _decode(key, check)
    except Exception:  # noqa: BLE001
        path.unlink(missing_ok=True)
        return None
    return key


def is_unlocked() -> bool:
    return active_key() is not None


def encrypt(plaintext: str) -> str:
    key = active_key()
    if key is None:
        raise VaultError("vault is locked; unlock it before storing secrets")
    return _encode(key, plaintext.encode("utf-8"))


def decrypt(value: str) -> str:
    key = active_key()
    if key is None:
        raise VaultError("vault is locked; unlock it before reading secrets")
    return _decode(key, value).decode("utf-8")


def generate_secret(nbytes: int = 24) -> str:
    return py_secrets.token_urlsafe(nbytes)


def _self_test() -> None:  # pragma: no cover - manual smoke helper
    os.environ.setdefault("VIGILCULTOR_DATA_DIR", "/tmp/vgc_vault_smoke")
    set_master_password("master-pass-123")
    assert unlock("master-pass-123")
    blob = encrypt("hello")
    assert decrypt(blob) == "hello"
    lock()
    try:
        decrypt(blob)
    except VaultError:
        print("vault smoke: ok")
        return
    print("vault smoke: FAILED", file=sys.stderr)
    raise SystemExit(1)
