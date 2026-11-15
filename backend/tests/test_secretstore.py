from __future__ import annotations

import sys
from pathlib import Path

import pytest

from videostudio.secretstore import (
    DPAPISecretStore,
    FernetFileSecretStore,
    SecretStoreError,
    create_secret_store,
)

STORES = [FernetFileSecretStore]
if sys.platform == "win32":
    STORES.append(DPAPISecretStore)


@pytest.fixture(params=STORES, ids=lambda c: c.backend_name)
def store_cls(request):
    return request.param


def test_roundtrip_unicode_and_large(store_cls, tmp_path: Path) -> None:
    store = store_cls(tmp_path / "bí mật")
    store.set("youtube.oauth", "token-ứng-dụng")
    big = "x" * 20_000  # bigger than Credential Manager's ~2.5 KB limit
    store.set("big", big)
    assert store.get("youtube.oauth") == "token-ứng-dụng"
    assert store.get("big") == big
    assert store.get("missing") is None
    assert store.list_names() == ["big", "youtube.oauth"]


def test_overwrite_and_delete(store_cls, tmp_path: Path) -> None:
    store = store_cls(tmp_path)
    store.set("a", "1")
    store.set("a", "2")
    assert store.get("a") == "2"
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.get("a") is None


def test_values_are_encrypted_on_disk(store_cls, tmp_path: Path) -> None:
    store = store_cls(tmp_path)
    store.set("facebook.page", "SIÊU-BÍ-MẬT")
    blobs = b"".join(p.read_bytes() for p in tmp_path.glob("*.secret"))
    assert b"facebook.page" not in blobs
    assert "SIÊU-BÍ-MẬT".encode() not in blobs


def test_persists_across_instances(store_cls, tmp_path: Path) -> None:
    store_cls(tmp_path).set("k", "v")
    assert store_cls(tmp_path).get("k") == "v"


def test_fernet_wrong_key_fails(tmp_path: Path) -> None:
    store = FernetFileSecretStore(tmp_path)
    store.set("k", "v")
    (tmp_path / ".fernet.key").unlink()
    with pytest.raises(SecretStoreError):
        FernetFileSecretStore(tmp_path).get("k")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_fernet_key_file_private(tmp_path: Path) -> None:
    FernetFileSecretStore(tmp_path)
    assert (tmp_path / ".fernet.key").stat().st_mode & 0o077 == 0


def test_factory_picks_platform_backend(tmp_path: Path) -> None:
    expected = "windows-dpapi" if sys.platform == "win32" else "fernet-file"
    assert create_secret_store(tmp_path).backend_name == expected


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_dpapi_unavailable_off_windows(tmp_path: Path) -> None:
    with pytest.raises(SecretStoreError):
        DPAPISecretStore(tmp_path)
