"""AdminService runtime_reconcile 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.config import AppConfig
from sari.core.models import DaemonRuntimeDTO, WorkspaceDTO
from sari.db.migration import ensure_migrated
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.admin_service import AdminService


def _build_service(
    db_path: Path,
    *,
    queue_repo: FileEnrichQueueRepository | None = None,
    lsp_count: int = 0,
) -> AdminService:
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(db_path.parent),
            name="repo",
            indexed_at=None,
            is_active=True,
        )
    )
    runtime_repo = RuntimeRepository(db_path)
    symbol_cache_repo = SymbolCacheRepository(db_path)
    return AdminService(
        config=AppConfig.default(),
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=symbol_cache_repo,
        queue_repo=queue_repo,
        lsp_reconciler=(lambda: lsp_count),
    )


def test_runtime_reconcile_resets_running_jobs_and_lsp_count(tmp_path: Path) -> None:
    """reconcile은 RUNNING 작업 복구와 LSP 정리 카운트를 함께 반영해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    ensure_migrated(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    _ = queue_repo.enqueue(
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        now_iso="2026-02-19T00:00:00+00:00",
    )
    _ = queue_repo.acquire_pending(limit=1, now_iso="2026-02-19T00:00:01+00:00")

    service = _build_service(db_path, queue_repo=queue_repo, lsp_count=2)
    payload = service.runtime_reconcile()
    assert payload["orphan_workers_stopped"] == 1
    assert payload["reaped_lsp"] == 2

    counts = queue_repo.get_status_counts()
    assert counts["FAILED"] == 1
    assert counts["RUNNING"] == 0


def test_runtime_reconcile_clears_stale_runtime_pid(tmp_path: Path) -> None:
    """존재하지 않는 runtime pid는 reconcile 시 정리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    ensure_migrated(db_path)
    service = _build_service(db_path)
    runtime_repo = RuntimeRepository(db_path)
    runtime_repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=999_999,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at="2026-02-19T00:00:00+00:00",
            session_count=0,
            last_heartbeat_at="2026-02-19T00:00:00+00:00",
            last_exit_reason=None,
        )
    )

    payload = service.runtime_reconcile()
    assert payload["reconciled_daemons"] == 1
