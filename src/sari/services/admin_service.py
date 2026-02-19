"""운영 보조 서비스(doctor/index/install/engine)를 제공한다."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
from typing import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sari import __version__ as SARI_RUNTIME_VERSION
from sari.core.config import AppConfig
from sari.core.models import now_iso8601_utc
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
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
        queue_repo: FileEnrichQueueRepository | None = None,
        registry_repo: DaemonRegistryRepository | None = None,
        lsp_reconciler: Callable[[], int] | None = None,
    ) -> None:
        """서비스에 필요한 저장소와 설정을 주입한다."""
        self._config = config
        self._workspace_repo = workspace_repo
        self._runtime_repo = runtime_repo
        self._symbol_cache_repo = symbol_cache_repo
        self._queue_repo = queue_repo
        self._registry_repo = registry_repo
        self._lsp_reconciler = lsp_reconciler

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
        checks.append(
            DoctorCheckDTO(
                name="run_mode",
                passed=self._config.run_mode in {"dev", "prod"},
                detail=self._config.run_mode,
            )
        )
        checks.append(
            DoctorCheckDTO(
                name="orm_backend",
                passed=True,
                detail=self._detect_orm_backend(),
            )
        )
        version_alignment_passed, version_alignment_detail = self._detect_version_alignment()
        checks.append(
            DoctorCheckDTO(
                name="version_alignment",
                passed=version_alignment_passed,
                detail=version_alignment_detail,
            )
        )
        return checks

    def _detect_orm_backend(self) -> str:
        """저장소 계층 ORM 백엔드 상태를 탐지한다."""
        repository_root = Path(__file__).resolve().parents[1] / "db" / "repositories"
        repository_files = sorted(repository_root.glob("*_repository.py"))
        legacy_count = 0
        for file_path in repository_files:
            try:
                source = file_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "from sari.db.schema import connect" in source:
                legacy_count += 1
        if legacy_count == 0:
            return "sqlalchemy_only"
        return f"mixed(sqlalchemy+sqlite):legacy_repositories={legacy_count}"

    def _detect_version_alignment(self) -> tuple[bool, str]:
        """실행중 코드 버전과 설치 메타데이터 버전 정합성을 점검한다."""
        runtime_version = SARI_RUNTIME_VERSION.strip()
        try:
            metadata_version = importlib.metadata.version("sari").strip()
        except importlib.metadata.PackageNotFoundError:
            metadata_version = "unavailable"
        except (importlib.metadata.InvalidVersion, ValueError):
            metadata_version = "unavailable"
        if metadata_version == "unavailable":
            return True, f"runtime={runtime_version}, metadata=unavailable"
        if runtime_version == metadata_version:
            return True, f"runtime={runtime_version}, metadata={metadata_version}"
        return False, f"runtime={runtime_version}, metadata={metadata_version}, mismatch=true"

    def run_mode(self) -> str:
        """현재 유효 실행 모드를 반환한다."""
        return self._config.run_mode

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

    def apply_host_config(self, host: str) -> dict[str, object]:
        """호스트 설정 파일을 직접 갱신한다."""
        snippet_payload = self.install_host_config(host=host)
        if "error" in snippet_payload:
            return snippet_payload
        if host == "gemini":
            return self._apply_gemini_config(snippet_payload)
        if host == "codex":
            return self._apply_codex_config(snippet_payload)
        return {
            "host": host,
            "error": {"code": "ERR_UNSUPPORTED_HOST", "message": "지원하지 않는 host입니다"},
        }

    def _apply_gemini_config(self, snippet_payload: dict[str, object]) -> dict[str, object]:
        """Gemini 설정 파일에 sari 서버 블록을 병합한다."""
        target_path = Path.home() / ".gemini" / "settings.json"
        backup_path = self._backup_if_exists(target_path)
        data = self._load_json_file(target_path)
        if data is None:
            return {
                "host": "gemini",
                "error": {"code": "ERR_CONFIG_INVALID_JSON", "message": f"잘못된 JSON 설정: {target_path}"},
            }
        mcp_servers_obj = data.get("mcpServers")
        mcp_servers = mcp_servers_obj if isinstance(mcp_servers_obj, dict) else {}
        snippet = snippet_payload.get("snippet")
        snippet_obj = snippet if isinstance(snippet, dict) else {}
        snippet_servers_obj = snippet_obj.get("mcpServers")
        snippet_servers = snippet_servers_obj if isinstance(snippet_servers_obj, dict) else {}
        sari_obj = snippet_servers.get("sari")
        if not isinstance(sari_obj, dict):
            return {
                "host": "gemini",
                "error": {"code": "ERR_INSTALL_SNIPPET_INVALID", "message": "sari install snippet 형식이 잘못되었습니다"},
            }
        mcp_servers["sari"] = sari_obj
        data["mcpServers"] = mcp_servers
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "host": "gemini",
            "applied": True,
            "path": str(target_path),
            "backup_path": str(backup_path) if backup_path is not None else None,
            "snippet": snippet_obj,
        }

    def _apply_codex_config(self, snippet_payload: dict[str, object]) -> dict[str, object]:
        """Codex TOML 설정에 sari 블록을 병합한다."""
        target_path = Path.home() / ".codex" / "config.toml"
        backup_path = self._backup_if_exists(target_path)
        existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        snippet = snippet_payload.get("snippet")
        snippet_obj = snippet if isinstance(snippet, dict) else {}
        snippet_servers_obj = snippet_obj.get("mcp_servers")
        snippet_servers = snippet_servers_obj if isinstance(snippet_servers_obj, dict) else {}
        sari_obj = snippet_servers.get("sari")
        if not isinstance(sari_obj, dict):
            return {
                "host": "codex",
                "error": {"code": "ERR_INSTALL_SNIPPET_INVALID", "message": "sari install snippet 형식이 잘못되었습니다"},
            }
        command_obj = sari_obj.get("command")
        args_obj = sari_obj.get("args")
        command = str(command_obj).strip() if isinstance(command_obj, str) else ""
        if command == "" or not isinstance(args_obj, list):
            return {
                "host": "codex",
                "error": {"code": "ERR_INSTALL_SNIPPET_INVALID", "message": "codex snippet 필수 필드가 누락되었습니다"},
            }
        args_line = json.dumps([str(item) for item in args_obj], ensure_ascii=False)
        block = [
            "[mcp_servers.sari]",
            f'command = "{command}"',
            f"args = {args_line}",
        ]
        merged = self._replace_toml_section(existing_text=existing, section_header="[mcp_servers.sari]", replacement_lines=block)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(merged, encoding="utf-8")
        return {
            "host": "codex",
            "applied": True,
            "path": str(target_path),
            "backup_path": str(backup_path) if backup_path is not None else None,
            "snippet": snippet_obj,
        }

    def _backup_if_exists(self, target_path: Path) -> Path | None:
        """설정 파일이 있으면 타임스탬프 백업을 생성한다."""
        if not target_path.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = target_path.with_name(f"{target_path.name}.bak.{stamp}")
        backup_path.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")
        return backup_path

    def _load_json_file(self, path: Path) -> dict[str, object] | None:
        """JSON 파일을 읽고 object 형태만 허용한다."""
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        return loaded

    def _replace_toml_section(self, existing_text: str, section_header: str, replacement_lines: list[str]) -> str:
        """기존 TOML 섹션을 교체하거나 신규 추가한다."""
        lines = existing_text.splitlines()
        output: list[str] = []
        index = 0
        replaced = False
        while index < len(lines):
            current = lines[index]
            if current.strip() == section_header:
                replaced = True
                output.extend(replacement_lines)
                index += 1
                while index < len(lines):
                    next_line = lines[index]
                    if next_line.startswith("[") and next_line.strip().endswith("]"):
                        break
                    index += 1
                continue
            output.append(current)
            index += 1
        if not replaced:
            if len(output) > 0 and output[-1].strip() != "":
                output.append("")
            output.extend(replacement_lines)
        return "\n".join(output) + "\n"

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
            "run_mode": self._config.run_mode,
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

    def runtime_reconcile(self) -> dict[str, int]:
        """런타임/레지스트리 불일치를 정리하고 정리 건수를 반환한다."""
        reconciled_daemons = 0
        stale_registry_cleaned = 0
        reaped_lsp = 0
        orphan_workers_stopped = 0
        runtime = self._runtime_repo.get_runtime()
        if runtime is not None and not self._is_pid_alive(runtime.pid):
            self._runtime_repo.clear_runtime()
            reconciled_daemons += 1
            if self._registry_repo is not None:
                self._registry_repo.remove_by_pid(runtime.pid)
                stale_registry_cleaned += 1
        if self._queue_repo is not None:
            orphan_workers_stopped = self._queue_repo.reset_running_to_failed(now_iso=now_iso8601_utc())
        if self._lsp_reconciler is not None:
            reaped_lsp = max(0, int(self._lsp_reconciler()))
        return {
            "reconciled_daemons": reconciled_daemons,
            "reaped_lsp": reaped_lsp,
            "orphan_workers_stopped": orphan_workers_stopped,
            "stale_registry_cleaned": stale_registry_cleaned,
        }

    def _is_pid_alive(self, pid: int) -> bool:
        """PID가 살아있는지 확인한다."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        stat = self._read_process_stat(pid)
        if stat.startswith("Z"):
            return False
        return True

    def _read_process_stat(self, pid: int) -> str:
        """프로세스 상태 문자열(ps stat)을 조회한다."""
        process = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        if process.returncode != 0:
            return ""
        return process.stdout.strip()
