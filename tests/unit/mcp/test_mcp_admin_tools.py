"""MCP 운영 도구(doctor/rescan/repo_candidates) 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import RepoIdentityDTO
from sari.core.models import now_iso8601_utc
from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.models import WorkspaceDTO
from sari.core.repo.context_resolver import ERR_WORKSPACE_INACTIVE, WORKSPACE_INACTIVE_MESSAGE
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
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


def test_mcp_doctor_reports_repo_language_starvation_for_manual_hot_repo(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_state(
        repo_root=str(repo_dir.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=2,
        inflight_phase="manual_probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    server = McpServer(db_path=db_path)
    server._doctor_tool._repo_language_probe_repo = probe_repo  # type: ignore[attr-defined]
    server._doctor_tool._repo_hot_checker = lambda repo: repo == str(repo_dir.resolve())  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 23,
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
    starvation_items = [item for item in items if item["name"] == "repo_language_starvation"]
    assert len(starvation_items) == 1
    assert starvation_items[0]["passed"] is False
    assert "python" in starvation_items[0]["detail"]


def test_mcp_doctor_reports_manual_starvation_for_cold_repo(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_state(
        repo_root=str(repo_dir.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_LSP_SLOT_EXHAUSTED",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    server = McpServer(db_path=db_path)
    server._doctor_tool._repo_language_probe_repo = probe_repo  # type: ignore[attr-defined]
    server._doctor_tool._repo_hot_checker = lambda repo: False  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 230,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"repo": str(repo_dir.resolve()), "options": {"structured": 1}},
            },
        }
    )

    payload = response.to_dict()
    items = payload["result"]["structuredContent"]["items"]
    starvation_items = [item for item in items if item["name"] == "repo_language_starvation"]
    assert len(starvation_items) == 1


def test_mcp_doctor_uses_validated_repo_root_for_repo_id_inputs(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    RepoRegistryRepository(db_path).upsert(
        RepoIdentityDTO(
            repo_id="rid_repo_a",
            repo_label="repo-a",
            repo_root=str(repo_dir.resolve()),
            workspace_root=str(repo_dir.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )
    probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_state(
        repo_root=str(repo_dir.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    seen_repo_roots: list[str] = []
    server = McpServer(db_path=db_path)
    server._doctor_tool._repo_language_probe_repo = probe_repo  # type: ignore[attr-defined]
    server._doctor_tool._repo_hot_checker = lambda repo: seen_repo_roots.append(repo) or repo == str(repo_dir.resolve())  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"repo": "rid_repo_a", "options": {"structured": 1}},
            },
        }
    )

    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    assert seen_repo_roots == [str(repo_dir.resolve())]
    items = result["structuredContent"]["items"]
    starvation_items = [item for item in items if item["name"] == "repo_language_starvation"]
    assert len(starvation_items) == 1


def test_mcp_doctor_ignores_background_backpressure_rows_for_hot_repo(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_state(
        repo_root=str(repo_dir.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="background_probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="background",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    server = McpServer(db_path=db_path)
    server._doctor_tool._repo_language_probe_repo = probe_repo  # type: ignore[attr-defined]
    server._doctor_tool._repo_hot_checker = lambda repo: repo == str(repo_dir.resolve())  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 25,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"repo": str(repo_dir.resolve()), "options": {"structured": 1}},
            },
        }
    )

    payload = response.to_dict()
    items = payload["result"]["structuredContent"]["items"]
    starvation_items = [item for item in items if item["name"] == "repo_language_starvation"]
    assert starvation_items == []


def test_mcp_doctor_treats_force_trigger_as_manual_starvation(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_state(
        repo_root=str(repo_dir.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-03-06T00:10:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="force",
        last_seen_at="2026-03-06T00:00:00+00:00",
        updated_at="2026-03-06T00:00:01+00:00",
    )

    server = McpServer(db_path=db_path)
    server._doctor_tool._repo_language_probe_repo = probe_repo  # type: ignore[attr-defined]
    server._doctor_tool._repo_hot_checker = lambda repo: repo == str(repo_dir.resolve())  # type: ignore[attr-defined]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 26,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"repo": str(repo_dir.resolve()), "options": {"structured": 1}},
            },
        }
    )

    payload = response.to_dict()
    items = payload["result"]["structuredContent"]["items"]
    starvation_items = [item for item in items if item["name"] == "repo_language_starvation"]
    assert len(starvation_items) == 1


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


def test_validate_repo_argument_rejects_unresolvable_secondary_repo_id(tmp_path: Path) -> None:
    """주 인자는 유효해도 보조 repo_id가 미해석이면 충돌로 거부해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_a.resolve()))

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a", "repo_id": "repo-a-typo"}

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


def test_validate_repo_argument_accepts_mixed_repo_fields_for_same_repository(tmp_path: Path) -> None:
    """repo/repo_key/repo_id가 표현만 다르고 동일 저장소면 정상 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    service = WorkspaceService(WorkspaceRepository(db_path))
    service.add_workspace(str(repo_a.resolve()))
    repo_registry_repo = RepoRegistryRepository(db_path)
    repo_registry_repo.upsert(
        RepoIdentityDTO(
            repo_id="repo-a-id",
            repo_label="repo-a",
            repo_root=str(repo_a.resolve()),
            workspace_root=str(repo_a.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {
        "repo": str(repo_a.resolve()),
        "repo_key": "repo-a",
        "repo_id": "repo-a-id",
    }

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is None
    assert arguments["repo"] == str(repo_a.resolve())
    assert arguments["repo_id"] == "repo-a-id"
    assert arguments["repo_key"] == "repo-a"


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


def test_validate_repo_argument_falls_back_when_repo_label_registry_row_is_stale(tmp_path: Path) -> None:
    """stale repo_label row가 있어도 유효한 workspace fallback은 계속 동작해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_a.resolve()))

    stale_root = tmp_path / "removed" / "repo-a"
    stale_root.parent.mkdir(parents=True)
    RepoRegistryRepository(db_path).upsert(
        RepoIdentityDTO(
            repo_id="stale-repo-a-id",
            repo_label="repo-a",
            repo_root=str(stale_root.resolve()),
            workspace_root=str(stale_root.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {"repo": "repo-a"}

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is None
    assert arguments["repo"] == str(repo_a.resolve())
    assert str(arguments["repo_id"]).strip() != "stale-repo-a-id"


def test_validate_repo_argument_ignores_ambiguous_repo_label_in_conflict_check(tmp_path: Path) -> None:
    """repo_key label이 다중 workspace에 걸쳐 모호하면 임의 row 선택으로 충돌시키면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_a1 = (tmp_path / "ws1" / "repo-a").resolve()
    repo_a2 = (tmp_path / "ws2" / "repo-a").resolve()
    repo_a1.mkdir(parents=True)
    repo_a2.mkdir(parents=True)
    workspace_service = WorkspaceService(WorkspaceRepository(db_path))
    workspace_service.add_workspace(str(repo_a1))
    workspace_service.add_workspace(str(repo_a2))

    repo_registry_repo = RepoRegistryRepository(db_path)
    # 의도적으로 반대 순서로 삽입해 LIMIT 1 비결정성 경로를 자극한다.
    repo_registry_repo.upsert(
        RepoIdentityDTO(
            repo_id="repo-a-id-2",
            repo_label="repo-a",
            repo_root=str(repo_a2),
            workspace_root=str(repo_a2),
            updated_at=now_iso8601_utc(),
        )
    )
    repo_registry_repo.upsert(
        RepoIdentityDTO(
            repo_id="repo-a-id-1",
            repo_label="repo-a",
            repo_root=str(repo_a1),
            workspace_root=str(repo_a1),
            updated_at=now_iso8601_utc(),
        )
    )

    workspace_repo = WorkspaceRepository(db_path)
    arguments: dict[str, object] = {
        "repo": str(repo_a1),
        "repo_key": "repo-a",
        "repo_id": "repo-a-id-1",
    }

    result = validate_repo_argument(arguments=arguments, workspace_repo=workspace_repo)

    assert result.error is None
    assert arguments["repo"] == str(repo_a1)
    assert arguments["repo_id"] == "repo-a-id-1"


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
