"""Secret storage.

Secrets (OAuth tokens, website passwords...) are stored as one encrypted blob per
secret under ``<data_dir>/secrets``. Windows Credential Manager is not used
because of its ~2.5 KB limit, which is too small for OAuth token bundles.

- Windows: DPAPI (``CryptProtectData``), bound to the current Windows user.
- Other OSes (dev/tests): Fernet with a key file readable only by the user.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path


class SecretStoreError(Exception):
    pass


class SecretStore(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def set(self, name: str, value: str) -> None: ...

    @abstractmethod
    def get(self, name: str) -> str | None: ...

    @abstractmethod
    def delete(self, name: str) -> bool: ...

    @abstractmethod
    def list_names(self) -> list[str]: ...


class EncryptedFileSecretStore(SecretStore):
    """Shared file layout; subclasses provide ``_encrypt`` / ``_decrypt``."""

    suffix = ".secret"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def _encrypt(self, data: bytes) -> bytes: ...

    @abstractmethod
    def _decrypt(self, data: bytes) -> bytes: ...

    def _path(self, name: str) -> Path:
        if not name:
            raise ValueError("secret name must not be empty")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:40]
        return self.directory / f"{digest}{self.suffix}"

    def set(self, name: str, value: str) -> None:
        blob = self._encrypt(json.dumps({"name": name, "value": value}).encode("utf-8"))
        path = self._path(name)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)

    def _read(self, path: Path) -> dict[str, str]:
        try:
            return json.loads(self._decrypt(path.read_bytes()).decode("utf-8"))
        except SecretStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(f"Cannot decrypt {path.name}: {exc}") from exc

    def get(self, name: str) -> str | None:
        path = self._path(name)
        if not path.is_file():
            return None
        return self._read(path)["value"]

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_names(self) -> list[str]:
        names = []
        for path in sorted(self.directory.glob(f"*{self.suffix}")):
            try:
                names.append(self._read(path)["name"])
            except SecretStoreError:
                continue
        return sorted(names)


class DPAPISecretStore(EncryptedFileSecretStore):
    backend_name = "windows-dpapi"
    _entropy = b"VideoStudio.SecretStore.v1"

    def __init__(self, directory: Path) -> None:
        if sys.platform != "win32":
            raise SecretStoreError("DPAPI is only available on Windows")
        super().__init__(directory)

    def _encrypt(self, data: bytes) -> bytes:
        return _dpapi_call(data, self._entropy, protect=True)

    def _decrypt(self, data: bytes) -> bytes:
        return _dpapi_call(data, self._entropy, protect=False)


class FernetFileSecretStore(EncryptedFileSecretStore):
    """Fallback for non-Windows dev machines and CI. The key file is chmod 600."""

    backend_name = "fernet-file"

    def __init__(self, directory: Path) -> None:
        from cryptography.fernet import Fernet

        super().__init__(directory)
        key_path = self.directory / ".fernet.key"
        if not key_path.is_file():
            key_path.write_bytes(Fernet.generate_key())
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
        self._fernet = Fernet(key_path.read_bytes().strip())

    def _encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def _decrypt(self, data: bytes) -> bytes:
        return self._fernet.decrypt(data)


def _dpapi_call(data: bytes, entropy: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):  # noqa: N801 - Win32 name
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(raw: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    in_blob, _in_buf = to_blob(data)
    ent_blob, _ent_buf = to_blob(entropy)
    out_blob = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    description = ctypes.c_wchar_p("VideoStudio") if protect else None
    ok = fn(
        ctypes.byref(in_blob),
        description,
        ctypes.byref(ent_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStoreError(f"DPAPI call failed (error {ctypes.GetLastError()})")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def create_secret_store(directory: Path) -> SecretStore:
    if sys.platform == "win32":
        return DPAPISecretStore(directory)
    return FernetFileSecretStore(directory)
