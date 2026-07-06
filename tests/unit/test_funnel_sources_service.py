from __future__ import annotations

from pathlib import Path

import pytest

from src.services import funnel_sources_service as service


def test_resolve_appstore_private_key_prefers_env_value(monkeypatch):
    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY", "env-key")
    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY_PATH", None)

    assert service._resolve_appstore_private_key() == "env-key"


def test_resolve_appstore_private_key_reads_from_file(monkeypatch, tmp_path: Path):
    key_path = tmp_path / "AuthKey_TEST.p8"
    key_path.write_text("file-key\n", encoding="utf-8")

    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY", None)
    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY_PATH", str(key_path))

    assert service._resolve_appstore_private_key() == "file-key"


def test_resolve_appstore_private_key_raises_for_missing_file(monkeypatch):
    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY", None)
    monkeypatch.setattr(service.config, "APPSTORE_PRIVATE_KEY_PATH", "/tmp/does-not-exist.p8")

    with pytest.raises(FileNotFoundError):
        service._resolve_appstore_private_key()
