"""언어별 LSP readiness probe 서비스를 제공한다."""

from __future__ import annotations

import os
from pathlib import Path
import threading
import uuid
from typing import Callable

from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.language_registry import LanguageSupportEntry, iter_language_support_entries
from sari.core.lsp_provision_policy import get_lsp_provision_policy
from sari.core.models import LanguageProbeStatusDTO, now_iso8601_utc
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.lsp.hub import LspHub
from solidlsp.ls_exceptions import SolidLSPException


class LanguageProbeService:
    """레포 단위 언어 readiness를 점검하고 결과를 저장한다."""

    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        lsp_hub: LspHub,
        probe_repo: LanguageProbeRepository | None = None,
        entries: tuple[LanguageSupportEntry, ...] | None = None,
        now_provider: Callable[[], str] | None = None,
        per_language_timeout_sec: float = 20.0,
    ) -> None:
        """필요 의존성을 저장한다."""
        self._workspace_repo = workspace_repo
        self._lsp_hub = lsp_hub
        self._probe_repo = probe_repo
        self._entries = iter_language_support_entries() if entries is None else entries
        self._now_provider = now_provider if now_provider is not None else now_iso8601_utc
        self._per_language_timeout_sec = max(0.1, float(per_language_timeout_sec))

    def run(self, repo_root: str) -> dict[str, object]:
        """전체 활성 언어에 대한 readiness probe를 실행한다."""
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        self._ensure_registered_repo(normalized_repo)
        started_at = self._now_provider()
        sample_by_extension = self._collect_first_sample_by_extension(normalized_repo)
        items: list[LanguageProbeStatusDTO] = []
        for entry in self._entries:
            item = self._probe_single_language(
                repo_root=normalized_repo,
                entry=entry,
                sample_by_extension=sample_by_extension,
                probe_at=started_at,
            )
            items.append(item)
            if self._probe_repo is not None:
                self._probe_repo.upsert_result(
                    language=item.language,
                    enabled=item.enabled,
                    available=item.available,
                    last_probe_at=item.last_probe_at,
                    last_error_code=item.last_error_code,
                    last_error_message=item.last_error_message,
                )
        total_languages = len(items)
        available_languages = len([item for item in items if item.available])
        return {
            "run_id": str(uuid.uuid4()),
            "repo_root": normalized_repo,
            "started_at": started_at,
            "finished_at": self._now_provider(),
            "summary": {
                "total_languages": total_languages,
                "available_languages": available_languages,
                "unavailable_languages": total_languages - available_languages,
            },
            "languages": [item.to_dict() for item in items],
        }

    def _ensure_registered_repo(self, repo_root: str) -> None:
        """등록되지 않은 repo 입력을 명시적으로 차단한다."""
        workspace = self._workspace_repo.get_by_path(repo_root)
        if workspace is None:
            raise DaemonError(ErrorContext(code="ERR_REPO_NOT_REGISTERED", message=f"repo is not registered: {repo_root}"))

    def _collect_first_sample_by_extension(self, repo_root: str) -> dict[str, str]:
        """레포 내 확장자별 첫 샘플 파일 상대경로를 수집한다."""
        required_extensions: set[str] = set()
        for entry in self._entries:
            for extension in entry.extensions:
                required_extensions.add(extension.lower())
        found: dict[str, str] = {}
        skip_dirs = {".git", "node_modules", "dist", "build", ".venv", "__pycache__"}
        for current_root, dirs, files in os.walk(repo_root):
            dirs[:] = [item for item in dirs if item not in skip_dirs]
            for name in files:
                suffix = Path(name).suffix.lower()
                if suffix == "" or suffix in found or suffix not in required_extensions:
                    continue
                absolute_path = Path(current_root) / name
                relative_path = str(absolute_path.resolve().relative_to(Path(repo_root).resolve()).as_posix())
                found[suffix] = relative_path
            if len(found) == len(required_extensions):
                break
        return found

    def _probe_single_language(
        self,
        repo_root: str,
        entry: LanguageSupportEntry,
        sample_by_extension: dict[str, str],
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 readiness를 probe하고 결과 DTO를 반환한다."""
        result_box: list[LanguageProbeStatusDTO] = []
        error_box: list[RuntimeError | OSError | ValueError | TypeError | DaemonError | SolidLSPException] = []
        done = threading.Event()

        def _runner() -> None:
            """단일 언어 probe를 별도 스레드에서 실행한다."""
            try:
                result_box.append(
                    self._probe_single_language_impl(
                        repo_root=repo_root,
                        entry=entry,
                        sample_by_extension=sample_by_extension,
                        probe_at=probe_at,
                    )
                )
            except (RuntimeError, OSError, ValueError, TypeError, DaemonError, SolidLSPException) as exc:  # pragma: no cover - 경계 예외
                error_box.append(exc)
            finally:
                done.set()

        worker = threading.Thread(
            target=_runner,
            name=f"sari-language-probe-{entry.language.value}",
            daemon=True,
        )
        worker.start()
        finished = done.wait(timeout=self._per_language_timeout_sec)
        if not finished:
            policy = get_lsp_provision_policy(entry.language.value)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_TIMEOUT",
                last_error_message=(
                    f"language probe timed out after {self._per_language_timeout_sec:.1f}s: "
                    f"{entry.language.value}"
                ),
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=True,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        if len(error_box) > 0:
            policy = get_lsp_provision_policy(entry.language.value)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_PROBE_INTERNAL",
                last_error_message=str(error_box[0]),
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=False,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        return result_box[0]

    def _probe_single_language_impl(
        self,
        repo_root: str,
        entry: LanguageSupportEntry,
        sample_by_extension: dict[str, str],
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 probe 본체를 수행한다."""
        sample_path = self._pick_sample_path(entry=entry, sample_by_extension=sample_by_extension)
        policy = get_lsp_provision_policy(entry.language.value)
        if sample_path is None:
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LANGUAGE_SAMPLE_NOT_FOUND",
                last_error_message=f"sample file not found for {entry.language.value}",
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=False,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        try:
            lsp = self._lsp_hub.get_or_start(language=entry.language, repo_root=repo_root)
            symbol_items = list(lsp.request_document_symbols(sample_path).iter_symbols())
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=True,
                last_probe_at=probe_at,
                last_error_code=None,
                last_error_message=None,
                updated_at=probe_at,
                symbol_extract_success=True,
                document_symbol_count=len(symbol_items),
                path_mapping_ok=True,
                timeout_occurred=False,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        except DaemonError as exc:
            classified_code = _classify_lsp_error_code(code=exc.context.code, message=exc.context.message)
            timeout_occurred = _is_timeout_error(code=classified_code, message=exc.context.message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=exc.context.message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=timeout_occurred,
                recovered_by_restart=_is_recovered_by_restart(exc.context.message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=_extract_missing_dependency(exc.context.message),
                install_hint=policy.install_hint,
            )
        except SolidLSPException as exc:
            error_message = str(exc)
            code = _extract_error_code(error_message, default_code="ERR_LSP_DOCUMENT_SYMBOL_FAILED")
            classified_code = _classify_lsp_error_code(code=code, message=error_message)
            timeout_occurred = _is_timeout_error(code=classified_code, message=error_message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=error_message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=timeout_occurred,
                recovered_by_restart=_is_recovered_by_restart(error_message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=_extract_missing_dependency(error_message),
                install_hint=policy.install_hint,
            )
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            error_message = str(exc)
            classified_code = _classify_lsp_error_code(code="ERR_LSP_DOCUMENT_SYMBOL_FAILED", message=error_message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=error_message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=_is_timeout_error(code=classified_code, message=error_message),
                recovered_by_restart=_is_recovered_by_restart(error_message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=_extract_missing_dependency(error_message),
                install_hint=policy.install_hint,
            )

    def _pick_sample_path(self, entry: LanguageSupportEntry, sample_by_extension: dict[str, str]) -> str | None:
        """언어 엔트리 확장자 목록에서 사용 가능한 첫 샘플 파일을 고른다."""
        for extension in entry.extensions:
            candidate = sample_by_extension.get(extension.lower())
            if candidate is not None:
                return candidate
        return None


def _extract_error_code(message: str, default_code: str) -> str:
    """예외 메시지 선두의 ERR_* 코드를 추출한다."""
    trimmed = message.strip()
    if trimmed.startswith("ERR_"):
        code = trimmed.split(":", 1)[0].strip()
        if code != "":
            return code
    return default_code


def _is_timeout_error(code: str, message: str) -> bool:
    """오류 코드/메시지가 타임아웃 성격인지 판별한다."""
    timeout_codes = {
        "ERR_LSP_TIMEOUT",
        "ERR_LSP_REQUEST_TIMEOUT",
        "ERR_LSP_DOCUMENT_SYMBOL_TIMEOUT",
    }
    normalized_message = message.strip().lower()
    return (code in timeout_codes) or ("timeout" in normalized_message) or ("timed out" in normalized_message)


def _is_recovered_by_restart(message: str) -> bool:
    """메시지에서 재시작 복구 여부 플래그를 탐지한다."""
    normalized_message = message.strip().lower()
    return ("recovered_by_restart" in normalized_message) and ("true" in normalized_message)


def _classify_lsp_error_code(code: str, message: str) -> str:
    """LSP 오류를 정책 코드로 정규화한다."""
    normalized_message = message.strip().lower()
    missing_server_tokens = (
        "command not found",
        "no such file",
        "file not found",
        "not installed",
        "missing executable",
        "failed to spawn",
        "failed to start",
        "cannot find",
        "filenotfounderror",
    )
    if any(token in normalized_message for token in missing_server_tokens):
        return "ERR_LSP_SERVER_MISSING"
    if _is_timeout_error(code=code, message=message):
        return "ERR_LSP_TIMEOUT"
    return code


def _extract_missing_dependency(message: str) -> str | None:
    """예외 메시지에서 누락 의존성 토큰을 추출한다."""
    normalized_message = message.strip()
    if normalized_message == "":
        return None
    lowered = normalized_message.lower()
    if "pyright" in lowered:
        return "pyright"
    if "node" in lowered:
        return "node"
    if "npm" in lowered:
        return "npm"
    if "dotnet" in lowered:
        return "dotnet"
    if "java" in lowered:
        return "java"
    if "no such file" in lowered or "command not found" in lowered or "missing required commands" in lowered:
        return "server_binary"
    return None
