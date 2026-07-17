"""Secret storage behind a small protocol.

Production uses the OS credential store via ``keyring`` (macOS Keychain,
Windows Credential Locker, Linux Secret Service); tests use the in-memory
store. OAuth tokens and anything secret go here — never into SQLite, config
files, or the repository.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)

SERVICE_NAME = "Hearth"


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeychainSecretStore:
    """OS credential-store-backed secrets (via keyring)."""

    def __init__(self, service: str = SERVICE_NAME):
        self._service = service

    def get(self, key: str) -> str | None:
        import keyring
        import keyring.errors

        try:
            return keyring.get_password(self._service, key)
        except keyring.errors.KeyringError as exc:
            # Locked/unavailable store: treat as "not connected", don't crash.
            log.warning("Credential store unavailable: %s", exc)
            return None

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self._service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(self._service, key)
        except keyring.errors.KeyringError:
            pass


class InMemorySecretStore:
    """Test double. Never used in the packaged app."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
