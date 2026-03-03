"""AdminService version alignment 예외 경로를 검증한다."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from pytest import MonkeyPatch

from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.admin.service import AdminService


def _build_service(db_path: Path) -> AdminService:
    return AdminService(
        config=AppConfig.default(),
        workspace_repo=WorkspaceRepository(db_path),
        runtime_repo=RuntimeRepository(db_path),
        symbol_cache_repo=SymbolCacheRepository(db_path),
    )


def test_detect_version_alignment_handles_value_error_as_unavailable(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """메타데이터 버전 조회가 ValueError면 unavailable로 폴백해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = _build_service(db_path)

    def _raise_value_error(_: str) -> str:
        raise ValueError("invalid version string")

    monkeypatch.setattr(importlib.metadata, "version", _raise_value_error)
    passed, detail = service._detect_version_alignment()

    assert passed is True
    assert "metadata=unavailable" in detail

