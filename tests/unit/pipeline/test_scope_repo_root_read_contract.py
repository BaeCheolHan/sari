"""scope_repo_root 기반 read 경로 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path
import zlib

import pytest

from sari.core.exceptions import CollectionError
from sari.core.models import CollectedFileBodyDTO, CollectedFileL1DTO, WorkspaceDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.service import FileCollectionService
from sari.services.pipeline.quality_service import MirrorGoldenBackend, PipelineQualityService


def _build_service(db_path: Path, repo_root: str) -> FileCollectionService:
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path=repo_root, name="scope-root", indexed_at=None, is_active=True))
    return FileCollectionService(
        workspace_repo=workspace_repo,
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineQualityService.default_collection_policy(),
        lsp_backend=MirrorGoldenBackend(),
        policy_repo=None,
        event_repo=None,
    )


def test_read_file_uses_module_repo_root_for_l2_lookup(tmp_path: Path) -> None:
    """scope 요청이어도 L2 본문 조회는 module repo_root를 사용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = str((tmp_path / "mono-root").resolve())
    module_root = str((tmp_path / "mono-root" / "mod-a").resolve())
    file_path = tmp_path / "mono-root" / "mod-a" / "src" / "main.py"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("fs fallback should not be used\n", encoding="utf-8")

    service = _build_service(db_path=db_path, repo_root=scope_root)
    file_repo = FileCollectionRepository(db_path)
    body_repo = FileBodyRepository(db_path)
    now_iso = "2026-02-25T00:00:00+00:00"

    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="repo-mod-a",
            repo_root=module_root,
            scope_repo_root=scope_root,
            relative_path="src/main.py",
            absolute_path=str(file_path.resolve()),
            repo_label="mono-root/mod-a",
            mtime_ns=1,
            size_bytes=file_path.stat().st_size,
            content_hash="hash-1",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="DONE",
        )
    )
    body_repo.upsert_body(
        CollectedFileBodyDTO(
            repo_id="repo-mod-a",
            repo_root=module_root,
            scope_repo_root=scope_root,
            relative_path="src/main.py",
            content_hash="hash-1",
            content_zlib=zlib.compress("from_l2_body_source\n".encode("utf-8")),
            content_len=len("from_l2_body_source\n".encode("utf-8")),
            normalized_text="from_l2_body_source",
            created_at=now_iso,
            updated_at=now_iso,
        )
    )

    result = service.read_file(repo_root=scope_root, relative_path="src/main.py", offset=0, limit=None)
    assert result.source == "l2"
    assert "from_l2_body_source" in result.content


def test_read_file_raises_ambiguous_when_same_relative_path_exists_in_scope(tmp_path: Path) -> None:
    """scope 내 동일 relative_path가 다수 repo_root에 있으면 모호성 에러를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = str((tmp_path / "mono-root").resolve())
    module_a = str((tmp_path / "mono-root" / "mod-a").resolve())
    module_b = str((tmp_path / "mono-root" / "mod-b").resolve())
    now_iso = "2026-02-25T00:00:00+00:00"

    file_repo = FileCollectionRepository(db_path)
    service = _build_service(db_path=db_path, repo_root=scope_root)

    for idx, module_root in enumerate((module_a, module_b), start=1):
        absolute = tmp_path / "mono-root" / f"mod-{idx}" / "src" / "main.py"
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(f"module-{idx}\n", encoding="utf-8")
        file_repo.upsert_file(
            CollectedFileL1DTO(
                repo_id=f"repo-{idx}",
                repo_root=module_root,
                scope_repo_root=scope_root,
                relative_path="src/main.py",
                absolute_path=str(absolute.resolve()),
                repo_label=f"mono-root/mod-{idx}",
                mtime_ns=idx,
                size_bytes=absolute.stat().st_size,
                content_hash=f"hash-{idx}",
                is_deleted=False,
                last_seen_at=now_iso,
                updated_at=now_iso,
                enrich_state="DONE",
            )
        )

    with pytest.raises(CollectionError) as exc:
        service.read_file(repo_root=scope_root, relative_path="src/main.py", offset=0, limit=None)
    assert exc.value.context.code == "ERR_AMBIGUOUS_PATH_IN_SCOPE"


def test_read_file_prefers_exact_repo_match_before_scope_ambiguity(tmp_path: Path) -> None:
    """scope 후보가 여러 개여도 요청 repo_root와 exact 일치가 있으면 우선 선택해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = str((tmp_path / "workspace").resolve())
    module_root = str((tmp_path / "workspace" / "mod-a").resolve())
    now_iso = "2026-02-25T00:00:00+00:00"
    relative_path = "README.md"

    root_file = tmp_path / "workspace" / "README.md"
    module_file = tmp_path / "workspace" / "mod-a" / "README.md"
    root_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.parent.mkdir(parents=True, exist_ok=True)
    root_file.write_text("root fallback\n", encoding="utf-8")
    module_file.write_text("module fallback\n", encoding="utf-8")

    service = _build_service(db_path=db_path, repo_root=scope_root)
    file_repo = FileCollectionRepository(db_path)
    body_repo = FileBodyRepository(db_path)

    # exact repo_root row (요청과 동일)
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="repo-root",
            repo_root=scope_root,
            scope_repo_root=scope_root,
            relative_path=relative_path,
            absolute_path=str(root_file.resolve()),
            repo_label="workspace",
            mtime_ns=1,
            size_bytes=root_file.stat().st_size,
            content_hash="h-root",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="DONE",
        )
    )
    # fanout module row (동일 relative_path)
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="repo-mod-a",
            repo_root=module_root,
            scope_repo_root=scope_root,
            relative_path=relative_path,
            absolute_path=str(module_file.resolve()),
            repo_label="workspace/mod-a",
            mtime_ns=2,
            size_bytes=module_file.stat().st_size,
            content_hash="h-mod",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="DONE",
        )
    )
    body_repo.upsert_body(
        CollectedFileBodyDTO(
            repo_id="repo-root",
            repo_root=scope_root,
            scope_repo_root=scope_root,
            relative_path=relative_path,
            content_hash="h-root",
            content_zlib=zlib.compress("from-root-body\n".encode("utf-8")),
            content_len=len("from-root-body\n".encode("utf-8")),
            normalized_text="from-root-body",
            created_at=now_iso,
            updated_at=now_iso,
        )
    )
    body_repo.upsert_body(
        CollectedFileBodyDTO(
            repo_id="repo-mod-a",
            repo_root=module_root,
            scope_repo_root=scope_root,
            relative_path=relative_path,
            content_hash="h-mod",
            content_zlib=zlib.compress("from-module-body\n".encode("utf-8")),
            content_len=len("from-module-body\n".encode("utf-8")),
            normalized_text="from-module-body",
            created_at=now_iso,
            updated_at=now_iso,
        )
    )

    result = service.read_file(repo_root=scope_root, relative_path=relative_path, offset=0, limit=None)
    assert result.source == "l2"
    assert "from-root-body" in result.content
