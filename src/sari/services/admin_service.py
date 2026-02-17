"""운영 보조 서비스(doctor/index/install/engine)를 제공한다."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository

@dataclass(frozen=True)
class DoctorCheckDTO:
    """doctor 항목 결과를 표현한다."""

    name: str
    passed: bool
    detail: str


class AdminService:
    """운영 명령의 실제 동작을 담당한다."""

    def __init__(
        self,
        config: AppConfig,
        workspace_repo: WorkspaceRepository,
        runtime_repo: RuntimeRepository,
        symbol_cache_repo: SymbolCacheRepository,
    ) -> None:
        """서비스에 필요한 저장소와 설정을 주입한다."""
        self._config = config
        self._workspace_repo = workspace_repo
        self._runtime_repo = runtime_repo
        self._symbol_cache_repo = symbol_cache_repo

    def doctor(self) -> list[DoctorCheckDTO]:
        """핵심 런타임 상태를 점검한다."""
        checks: list[DoctorCheckDTO] = []
        checks.append(
            DoctorCheckDTO(
                name="db_path",
                passed=self._config.db_path.exists(),
                detail=str(self._config.db_path),
            )
        )
        runtime = self._runtime_repo.get_runtime()
        checks.append(
            DoctorCheckDTO(
                name="daemon_runtime",
                passed=runtime is not None,
                detail="running" if runtime is not None else "stopped",
            )
        )
        workspace_count = len(self._workspace_repo.list_all())
        checks.append(
            DoctorCheckDTO(
                name="workspace_count",
                passed=workspace_count > 0,
                detail=str(workspace_count),
            )
        )
        return checks

    def index(self) -> dict[str, object]:
        """캐시 무효화 기반 재색인 트리거를 수행한다."""
        invalidated = self._symbol_cache_repo.invalidate_all()
        return {"invalidated_cache_rows": invalidated}

    def install_host_config(self, host: str) -> dict[str, object]:
        """호스트별 MCP 설정 스니펫을 생성한다."""
        args = ["mcp", "stdio"]
        if host == "codex":
            return {
                "host": host,
                "snippet": {
                    "mcp_servers": {
                        "sari": {
                            "command": "sari",
                            "args": args,
                        }
                    }
                },
            }
        if host == "gemini":
            return {
                "host": host,
                "snippet": {
                    "mcpServers": {
                        "sari": {
                            "command": "sari",
                            "args": args,
                        }
                    }
                },
            }
        return {
            "host": host,
            "error": {"code": "ERR_UNSUPPORTED_HOST", "message": "지원하지 않는 host입니다"},
        }

    def engine_status(self) -> dict[str, object]:
        """엔진 관련 의존성과 모드를 조회한다."""
        def _module_available(name: str) -> bool:
            try:
                importlib.import_module(name)
                return True
            except ImportError:
                # 의존성 미설치/로드 실패는 False로 처리한다.
                return False

        return {
            "search_mode": "lsp_pipeline",
            "dependencies": {
                "solidlsp": _module_available("solidlsp"),
                "tantivy": _module_available("tantivy"),
                "requests": _module_available("requests"),
            },
        }

    def engine_install(self) -> dict[str, object]:
        """엔진 설치 확인 동작을 수행한다."""
        return {
            "installed": True,
            "details": self.engine_status(),
        }

    def engine_rebuild(self) -> dict[str, object]:
        """엔진 재빌드 동작으로 캐시를 정리한다."""
        invalidated = self._symbol_cache_repo.invalidate_all()
        return {"rebuild": "done", "invalidated_cache_rows": invalidated}

    def engine_verify(self) -> dict[str, object]:
        """엔진 검증 결과를 반환한다."""
        status = self.engine_status()
        deps = status["dependencies"]
        ok = bool(isinstance(deps, dict) and deps.get("solidlsp"))
        return {"verified": ok, "status": status}

    def daemon_list(self) -> list[dict[str, object]]:
        """현재 등록된 데몬 목록을 반환한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return []
        return [
            {
                "pid": runtime.pid,
                "host": runtime.host,
                "port": runtime.port,
                "state": runtime.state,
                "started_at": runtime.started_at,
                "session_count": runtime.session_count,
                "last_heartbeat_at": runtime.last_heartbeat_at,
                "last_exit_reason": runtime.last_exit_reason,
            }
        ]

    def repo_candidates(self) -> list[dict[str, object]]:
        """등록된 워크스페이스를 후보 저장소 목록으로 반환한다."""
        return [{"repo": ws.path, "name": ws.name} for ws in self._workspace_repo.list_all()]
