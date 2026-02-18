"""수집 저장소의 repo_label(repo_key) 저장 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectedFileL1DTO, CollectionPolicyDTO, WorkspaceDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.file_collection_service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO


class _NoopLspBackend(LspExtractionBackend):
    """L3 추출을 빈 결과로 처리하는 테스트 더블이다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


def _policy() -> CollectionPolicyDTO:
    """테스트용 최소 수집 정책을 생성한다."""
    return CollectionPolicyDTO(
        include_ext=(".py",),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )


def test_scan_persists_workspace_relative_repo_label(tmp_path: Path) -> None:
    """nested repo를 스캔하면 repo_label은 workspace-relative repo_key여야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    workspace_root = tmp_path / "workspace"
    nested_repo = workspace_root / "apps" / "repo-a"
    nested_repo.mkdir(parents=True)
    (nested_repo / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(workspace_root.resolve()),
            name=None,
            indexed_at=None,
            is_active=True,
        )
    )

    file_repo = FileCollectionRepository(db_path)
    service = FileCollectionService(
        workspace_repo=workspace_repo,
        file_repo=file_repo,
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )

    _ = service.scan_once(str(nested_repo.resolve()))
    row = file_repo.get_file(repo_root=str(nested_repo.resolve()), relative_path="main.py")

    assert row is not None
    assert row.repo_label == "apps/repo-a"


def test_sync_repo_label_updates_existing_rows(tmp_path: Path) -> None:
    """기존 행의 repo_label이 정책과 다르면 일괄 동기화되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    repo_root = str((tmp_path / "workspace" / "repo-a").resolve())
    (tmp_path / "workspace" / "repo-a").mkdir(parents=True)

    file_repo.upsert_file(
        file_row=CollectedFileL1DTO(
            repo_root=repo_root,
            relative_path="main.py",
            absolute_path=str((tmp_path / "workspace" / "repo-a" / "main.py").resolve()),
            repo_label="repo-a",
            mtime_ns=1,
            size_bytes=1,
            content_hash="hash",
            is_deleted=False,
            last_seen_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            enrich_state="PENDING",
        )
    )

    updated = file_repo.sync_repo_label(repo_root=repo_root, repo_label="apps/repo-a")
    row = file_repo.get_file(repo_root=repo_root, relative_path="main.py")

    assert updated == 1
    assert row is not None
    assert row.repo_label == "apps/repo-a"
