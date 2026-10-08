"""Vault tests: setup, unlock/lock, env override, encrypt/decrypt."""

from __future__ import annotations

import pytest

from vigilcultor.secrets import vault
from vigilcultor.secrets.vault import VaultError


def _setup_vault(password: str = "master-pass-123") -> None:
    vault.set_master_password(password)


def test_full_lock_unlock_cycle(tmp_env):
    _setup_vault()
    assert vault.is_configured()
    assert not vault.is_unlocked()

    assert vault.unlock("master-pass-123")
    assert vault.is_unlocked()
    blob = vault.encrypt("app-private-key")
    assert vault.decrypt(blob) == "app-private-key"

    vault.lock()
    assert not vault.is_unlocked()
    with pytest.raises(VaultError):
        vault.decrypt(blob)


def test_wrong_password_rejected(tmp_env):
    _setup_vault()
    assert not vault.unlock("wrong-password")
    assert not vault.is_unlocked()


def test_env_password_overrides_lock(tmp_env, monkeypatch):
    _setup_vault()
    vault.lock()
    monkeypatch.setenv(vault.ENV_MASTER_PASSWORD, "master-pass-123")
    assert vault.is_unlocked()
    blob = vault.encrypt("secret")
    assert vault.decrypt(blob) == "secret"


def test_stale_unlock_key_rejected(tmp_env):
    _setup_vault()
    assert vault.unlock("master-pass-123")
    stale = tmp_env / "unlock.key"
    stale.write_bytes(b"0" * 32)
    assert vault.active_key() is None
    assert not stale.exists()


def test_short_password_rejected(tmp_env):
    with pytest.raises(VaultError):
        _setup_vault("short")


def test_password_rotation_invalidates_old(tmp_env):
    _setup_vault("first-password-1")
    vault.unlock("first-password-1")
    blob = vault.encrypt("data")
    vault.set_master_password("second-password-2")
    vault.lock()
    assert vault.unlock("second-password-2")
    with pytest.raises(Exception):  # noqa: B017 - old ciphertext no longer decrypts
        vault.decrypt(blob)
