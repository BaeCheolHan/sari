"""언어별 LSP readiness probe 서비스를 제공한다."""

from __future__ import annotations

import logging
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
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

log = logging.getLogger(__name__)


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
        per_language_timeout_overrides: dict[str, float] | None = None,
        go_sample_candidates_max: int = 5,
        go_warmup_enabled: bool = True,
        lsp_request_timeout_sec: float = 20.0,
        go_warmup_timeout_sec: float | None = None,
    ) -> None:
        """필요 의존성을 저장한다."""
        self._workspace_repo = workspace_repo
        self._lsp_hub = lsp_hub
        self._probe_repo = probe_repo
        self._entries = iter_language_support_entries() if entries is None else entries
        self._now_provider = now_provider if now_provider is not None else now_iso8601_utc
        self._per_language_timeout_sec = max(0.1, float(per_language_timeout_sec))
        self._per_language_timeout_overrides: dict[str, float] = {}
        if per_language_timeout_overrides is not None:
            for language, timeout_sec in per_language_timeout_overrides.items():
                normalized = language.strip().lower()
                self._per_language_timeout_overrides[normalized] = max(0.1, float(timeout_sec))
        self._go_sample_candidates_max = max(1, int(go_sample_candidates_max))
        self._go_warmup_enabled = bool(go_warmup_enabled)
        self._lsp_request_timeout_sec = max(0.1, float(lsp_request_timeout_sec))
        warmup_timeout = self._per_language_timeout_overrides.get("go", self._per_language_timeout_sec)
        if go_warmup_timeout_sec is not None:
            warmup_timeout = max(0.1, float(go_warmup_timeout_sec))
        self._go_warmup_timeout_sec = max(0.1, warmup_timeout)
        self._go_warmed_repo_roots: set[str] = set()
        self._go_warmup_lock = threading.Lock()

    def run(self, repo_root: str) -> dict[str, object]:
        """전체 활성 언어에 대한 readiness probe를 실행한다."""
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        self._ensure_registered_repo(normalized_repo)
        started_at = self._now_provider()
        sample_candidates_by_extension = self._collect_sample_candidates_by_extension(normalized_repo)
        items: list[LanguageProbeStatusDTO] = []
        for entry in self._entries:
            item = self._probe_single_language(
                repo_root=normalized_repo,
                entry=entry,
                sample_candidates_by_extension=sample_candidates_by_extension,
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

    def _collect_sample_candidates_by_extension(self, repo_root: str) -> dict[str, list[tuple[str, int]]]:
        """레포 내 확장자별 샘플 후보(상대경로, 파일크기)를 수집한다."""
        required_extensions: set[str] = set()
        for entry in self._entries:
            for extension in entry.extensions:
                required_extensions.add(extension.lower())
        found: dict[str, list[tuple[str, int]]] = {}
        skip_dirs = {".git", "node_modules", "dist", "build", ".venv", "__pycache__"}
        for current_root, dirs, files in os.walk(repo_root):
            dirs[:] = [item for item in dirs if item not in skip_dirs]
            for name in files:
                suffix = Path(name).suffix.lower()
                if suffix == "" or suffix not in required_extensions:
                    continue
                current_items = found.get(suffix, [])
                if len(current_items) >= self._sample_candidate_cap_for_extension(suffix):
                    continue
                absolute_path = Path(current_root) / name
                relative_path = str(absolute_path.resolve().relative_to(Path(repo_root).resolve()).as_posix())
                try:
                    size = int(absolute_path.stat().st_size)
                except OSError:
                    size = 0
                current_items.append((relative_path, max(0, size)))
                found[suffix] = current_items
            if all(len(found.get(ext, [])) >= self._sample_candidate_cap_for_extension(ext) for ext in required_extensions):
                break
        return found

    def _sample_candidate_cap_for_extension(self, extension: str) -> int:
        """확장자별 후보 수집 상한을 반환한다."""
        if extension.lower() == ".go":
            return self._go_sample_candidates_max
        return 1

    def _probe_single_language(
        self,
        repo_root: str,
        entry: LanguageSupportEntry,
        sample_candidates_by_extension: dict[str, list[tuple[str, int]]],
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 readiness를 probe하고 결과 DTO를 반환한다."""
        result_box: list[LanguageProbeStatusDTO] = []
        error_box: list[RuntimeError | OSError | ValueError | TypeError | AssertionError | AttributeError | DaemonError | SolidLSPException] = []
        done = threading.Event()

        def _runner() -> None:
            """단일 언어 probe를 별도 스레드에서 실행한다."""
            try:
                result_box.append(
                    self._probe_single_language_impl(
                        repo_root=repo_root,
                        entry=entry,
                        sample_candidates_by_extension=sample_candidates_by_extension,
                        probe_at=probe_at,
                    )
                )
            except (RuntimeError, OSError, ValueError, TypeError, AssertionError, AttributeError, DaemonError, SolidLSPException) as exc:  # pragma: no cover - 경계 예외
                error_box.append(exc)
            finally:
                done.set()

        worker = threading.Thread(
            target=_runner,
            name=f"sari-language-probe-{entry.language.value}",
            daemon=True,
        )
        worker.start()
        timeout_sec = self._probe_timeout_for(entry.language.value)
        finished = done.wait(timeout=timeout_sec)
        if not finished:
            policy = get_lsp_provision_policy(entry.language.value)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_TIMEOUT",
                last_error_message=(
                    f"language probe timed out after {timeout_sec:.1f}s: "
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
            first_error = error_box[0]
            error_message = str(first_error)
            if isinstance(first_error, AssertionError):
                normalized_message = error_message if error_message.strip() != "" else "assertion failed during language probe"
                classified_code = _classify_lsp_error_code(code="ERR_LSP_DOCUMENT_SYMBOL_FAILED", message=normalized_message)
                return LanguageProbeStatusDTO(
                    language=entry.language.value,
                    enabled=True,
                    available=False,
                    last_probe_at=probe_at,
                    last_error_code=classified_code,
                    last_error_message=normalized_message,
                    updated_at=probe_at,
                    symbol_extract_success=False,
                    document_symbol_count=0,
                    path_mapping_ok=False,
                    timeout_occurred=_is_timeout_error(code=classified_code, message=normalized_message),
                    recovered_by_restart=False,
                    provisioning_mode=policy.provisioning_mode,
                    missing_dependency=_extract_missing_dependency(normalized_message),
                    install_hint=policy.install_hint,
                )
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_PROBE_INTERNAL",
                last_error_message=error_message,
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
        sample_candidates_by_extension: dict[str, list[tuple[str, int]]],
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 probe 본체를 수행한다."""
        sample_path = self._pick_sample_path(entry=entry, sample_candidates_by_extension=sample_candidates_by_extension)
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
            if entry.language == Language.GO:
                self._warm_up_go_lsp_once(repo_root=repo_root, lsp=lsp, sample_path=sample_path)
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

    def _pick_sample_path(self, entry: LanguageSupportEntry, sample_candidates_by_extension: dict[str, list[tuple[str, int]]]) -> str | None:
        """언어 엔트리 확장자 목록에서 probe 샘플 파일을 고른다."""
        if entry.language == Language.GO:
            return self._pick_go_sample_path(entry=entry, sample_candidates_by_extension=sample_candidates_by_extension)
        for extension in entry.extensions:
            candidates = sample_candidates_by_extension.get(extension.lower(), [])
            if len(candidates) > 0:
                return candidates[0][0]
        return None

    def _pick_go_sample_path(self, entry: LanguageSupportEntry, sample_candidates_by_extension: dict[str, list[tuple[str, int]]]) -> str | None:
        """Go 샘플은 작은/비테스트/비서드파티 파일을 우선한다."""
        ranked: list[tuple[int, int, int, str]] = []
        for extension in entry.extensions:
            for relative_path, size in sample_candidates_by_extension.get(extension.lower(), []):
                lowered = relative_path.lower()
                is_test = 1 if lowered.endswith("_test.go") else 0
                path_tokens = tuple(lowered.split("/"))
                is_noisy_path = 1 if any(token in {"vendor", "third_party"} or "generated" in token for token in path_tokens) else 0
                ranked.append((is_test, is_noisy_path, max(0, size), relative_path))
        if len(ranked) == 0:
            return None
        ranked.sort()
        return ranked[0][3]

    def _probe_timeout_for(self, language: str) -> float:
        """언어별 probe timeout을 계산한다."""
        normalized = language.strip().lower()
        return self._per_language_timeout_overrides.get(normalized, self._per_language_timeout_sec)

    def _warm_up_go_lsp_once(self, repo_root: str, lsp: SolidLanguageServer, sample_path: str) -> None:
        """Go LSP 첫 기동 비용을 흡수하기 위해 warm-up 요청을 1회 실행한다."""
        if not self._go_warmup_enabled:
            return
        should_warmup = False
        with self._go_warmup_lock:
            if repo_root not in self._go_warmed_repo_roots:
                self._go_warmed_repo_roots.add(repo_root)
                should_warmup = True
        if not should_warmup:
            return
        set_timeout = getattr(lsp, "set_request_timeout", None)
        try:
            if callable(set_timeout):
                set_timeout(self._go_warmup_timeout_sec)
            _ = list(lsp.request_document_symbols(sample_path).iter_symbols())
        except (RuntimeError, OSError, ValueError, TypeError, AssertionError, AttributeError, DaemonError, SolidLSPException) as exc:
            log.debug("Go warm-up failed(repo=%s): %s", repo_root, exc)
        finally:
            if callable(set_timeout):
                set_timeout(self._lsp_request_timeout_sec)


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
