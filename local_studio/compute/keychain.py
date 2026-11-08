from __future__ import annotations

import subprocess
from pathlib import Path


class KeychainError(RuntimeError):
    """Sanitized error from the macOS Keychain helper."""


class KeychainStore:
    """macOS generic-password store; secrets travel to Swift over stdin only."""

    MAX_SECRET_BYTES = 8192
    ALLOWED_PROVIDERS = frozenset({"vast", "hf"})

    def __init__(self, *, swift_path: str = "/usr/bin/swift", helper_path: Path | None = None) -> None:
        self._swift_path = swift_path
        self._helper_path = helper_path or Path(__file__).with_name("keychain.swift")

    @classmethod
    def _validate_provider(cls, provider: str) -> None:
        if provider not in cls.ALLOWED_PROVIDERS:
            raise ValueError("Unsupported compute provider")

    def _run(self, action: str, provider: str, secret: bytes | None = None) -> bytes:
        self._validate_provider(provider)
        if action not in {"set", "get", "delete"}:
            raise ValueError("Unsupported Keychain action")
        try:
            result = subprocess.run(
                [self._swift_path, str(self._helper_path), action, provider],
                input=secret,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise KeychainError("macOS Keychain is unavailable") from None

        if action == "get" and result.returncode == 2:
            return b""
        if result.returncode != 0:
            raise KeychainError("macOS Keychain could not complete the request")
        return result.stdout

    def set(self, provider: str, secret: str) -> None:
        self._validate_provider(provider)
        if not isinstance(secret, str):
            raise ValueError("API key must be text")
        encoded = secret.strip().encode("utf-8")
        if not encoded or len(encoded) > self.MAX_SECRET_BYTES:
            raise ValueError("API key is empty or too large")
        self._run("set", provider, encoded)

    def get(self, provider: str) -> str | None:
        raw = self._run("get", provider)
        if not raw:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise KeychainError("Stored API key is invalid") from None

    def delete(self, provider: str) -> None:
        self._run("delete", provider)

    def has(self, provider: str) -> bool:
        return self.get(provider) is not None
