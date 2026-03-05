"""MCP 운영 도구(doctor/rescan/repo_candidates) 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import RepoIdentityDTO
from sari.core.models import now_iso8601_utc
from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.models import WorkspaceDTO
from sari.core.repo.context_resolver import ERR_WORKSPACE_INACTIVE, WORKSPACE_INACTIVE_MESSAGE
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.services.workspace.service import WorkspaceService


def test_mcp_repo_candidates_returns_registered_workspace(tmp_path: Path) -> None:
    """repo_candidates는 등록된 워크스페이스를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "repo_candidates",
                "arguments": {"repo": "repo-a", "options": {"structured": 1}},
            },
        }
    )
    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    items = result["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["repo"] == str(repo_dir.resolve())


def test_mcp_rescan_returns_invalidation_count(tmp_path: Path) -> None:
    """rescan은 invalidated_cache_rows를 구조화 응답에 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "rescan",
                "arguments": {"repo": "repo-a", "options": {"structured": 1}},
            },
        }
    )
    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert "invalidated_cache_rows" in structured


def test_mcp_rescan_returns_pack1_error_when_db_lock_busy(tmp_path: Path) -> None:
    """rescan은 DB lock busy를 pack1 에러로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    class _BusyAdminService:
        def index(self) -> dict[str, object]:
            raise DaemonError(
                ErrorContext(
                    code="ERR_DB_LOCK_BUSY",
                    message="database is locked during index/rescan operation",
                )
            )

    server._rescan_tool._admin_service = _BusyAdminService()  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "rescan",
                "arguments": {"repo": "repo-a", "options": {"structured": 1}},
            },
        }
    )

    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "ERR_DB_LOCK_BUSY" in text


def test_mcp_doctor_includes_repo_scope_root_item(tmp_path: Path) -> None:
    """doctor 응답은 요청 repo scope root 컨텍스트를 첫 항목으로 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"repo": str(repo_dir.resolve()), "options": {"structured": 1}},
            },
        }
    )
    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    items = result["structuredContent"]["items"]
    assert len(items) >= 1
    assert items[0]["name"] == "repo_scope_root"
    assert items[0]["detail"] == str(repo_dir.resolve())


def test_validate_repo_argument_rejects_inactive_workspace(tmp_path: Path) -> None:
    """validate_repo_argument는 비활성 workspace를 명시적으로 거부해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-inactive"
    repo_dir.mkdir()
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-inactive", indexed_at=None, is_active=False))

    arguments: dict[str, object] = {"repo": str(repo_dir.resolve())}
    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == ERR_WORKSPACE_INACTIVE
    assert result.error.message == WORKSPACE_INACTIVE_MESSAGE


def test_validate_repo_argument_returns_conflict_error_when_repo_and_repo_key_disagree(tmp_path: Path) -> None:
    """repo/repo_key가 서로 다른 루트를 가리키면 충돌 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    service = WorkspaceService(WorkspaceRepository(db_path))
    service.add_workspace(str(repo_a.resolve()))
    service.add_workspace(str(repo_b.resolve()))

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a", "repo_key": "repo-b"}

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == "ERR_REPO_ARGUMENT_CONFLICT"
    assert result.error.message == "repo/repo_key/repo_id must match"


def test_validate_repo_argument_accepts_matching_repo_and_repo_key(tmp_path: Path) -> None:
    """repo/repo_key가 같은 식별자를 가리키면 정상 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    service = WorkspaceService(WorkspaceRepository(db_path))
    service.add_workspace(str(repo_a.resolve()))
    repo_registry_repo = RepoRegistryRepository(db_path)
    repo_registry_repo.upsert(
        RepoIdentityDTO(
            repo_id="repo-a",
            repo_label="repo-a",
            repo_root=str(repo_a.resolve()),
            workspace_root=str(repo_a.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a", "repo_key": "repo-a"}

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is None
    assert arguments["repo"] == str(repo_a.resolve())
    assert arguments["repo_id"] == "repo-a"
    assert isinstance(arguments["repo_key"], str)
    assert str(arguments["repo_key"]).strip() == "repo-a"


def test_validate_repo_argument_warns_on_legacy_key_fallback(tmp_path: Path) -> None:
    """미등록 repo_id 입력이 legacy key로 해석되면 경고와 함께 성공해야 한다(v2.N)."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    service = WorkspaceService(WorkspaceRepository(db_path))
    service.add_workspace(str(repo_a.resolve()))

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a"}

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is None
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "WARN_REPO_LEGACY_KEY_FALLBACK"
    assert arguments["repo"] == str(repo_a.resolve())
    assert str(arguments["repo_id"]).startswith("r_")


def test_validate_repo_argument_rejects_unknown_repo_id(tmp_path: Path) -> None:
    """등록되지 않은 repo 식별자는 resolver 오류 코드를 그대로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_a.resolve()))

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "unknown-repo-id"}

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == "ERR_REPO_NOT_FOUND"


def test_validate_repo_argument_rejects_repo_id_when_workspace_removed(tmp_path: Path) -> None:
    """registry에 남아도 workspace에서 제거된 repo는 repo_id 조회 성공으로 처리하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    service = WorkspaceService(WorkspaceRepository(db_path))
    service.add_workspace(str(repo_a.resolve()))
    repo_registry_repo = RepoRegistryRepository(db_path)
    repo_registry_repo.upsert(
        RepoIdentityDTO(
            repo_id="repo-a",
            repo_label="repo-a",
            repo_root=str(repo_a.resolve()),
            workspace_root=str(repo_a.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )
    service.remove_workspace(str(repo_a.resolve()))

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a"}
    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == "ERR_REPO_NOT_REGISTERED"


def test_validate_repo_argument_rejects_inactive_workspace_for_absolute_path_registry_hit(tmp_path: Path) -> None:
    """absolute path가 registry에 있어도 workspace 비활성이면 ERR_WORKSPACE_INACTIVE를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path=str(repo_a.resolve()), name="repo-a", indexed_at=None, is_active=False))
    RepoRegistryRepository(db_path).upsert(
        RepoIdentityDTO(
            repo_id="repo-a",
            repo_label="repo-a",
            repo_root=str(repo_a.resolve()),
            workspace_root=str(repo_a.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )

    arguments: dict[str, object] = {"repo": str(repo_a.resolve())}
    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == ERR_WORKSPACE_INACTIVE


def test_validate_repo_argument_preserves_inactive_error_for_identifier_fallback(tmp_path: Path) -> None:
    """repo_id 미스 이후 fallback에서 발생한 ERR_WORKSPACE_INACTIVE는 NOT_REGISTERED로 마스킹하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path=str(repo_a.resolve()), name="repo-a", indexed_at=None, is_active=False))

    arguments: dict[str, object] = {"repo": "repo-a"}
    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is not None
    assert result.error.code == ERR_WORKSPACE_INACTIVE
