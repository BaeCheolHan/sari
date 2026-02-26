"""언어별 LSP readiness probe 서비스를 제공한다."""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Callable

from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.language.registry import LanguageSupportEntry, iter_language_support_entries
from sari.core.models import LanguageProbeStatusDTO, now_iso8601_utc
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.lsp.hub import LspHub
from sari.services.language_probe.file_sampler import LanguageProbeFileSampler
from sari.services.language_probe.thread_runner import LanguageProbeThreadRunner
from sari.services.language_probe.worker import LanguageProbeWorker


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
        self._probe_repo = probe_repo
        self._entries = iter_language_support_entries() if entries is None else entries
        self._now_provider = now_provider if now_provider is not None else now_iso8601_utc
        self._per_language_timeout_sec = max(0.1, float(per_language_timeout_sec))
        self._per_language_timeout_overrides: dict[str, float] = {}
        if per_language_timeout_overrides is not None:
            for language, timeout_sec in per_language_timeout_overrides.items():
                normalized = language.strip().lower()
                self._per_language_timeout_overrides[normalized] = max(0.1, float(timeout_sec))
        warmup_timeout = self._per_language_timeout_overrides.get("go", self._per_language_timeout_sec)
        if go_warmup_timeout_sec is not None:
            warmup_timeout = max(0.1, float(go_warmup_timeout_sec))
        self._file_sampler = LanguageProbeFileSampler(
            entries=self._entries,
            go_sample_candidates_max=go_sample_candidates_max,
        )
        self._thread_runner = LanguageProbeThreadRunner()
        self._probe_worker = LanguageProbeWorker(
            lsp_hub=lsp_hub,
            lsp_request_timeout_sec=lsp_request_timeout_sec,
            go_warmup_enabled=go_warmup_enabled,
            go_warmup_timeout_sec=warmup_timeout,
        )

    def run(self, repo_root: str) -> dict[str, object]:
        """전체 활성 언어에 대한 readiness probe를 실행한다."""
        normalized_repo = str(Path(repo_root).expanduser().resolve())
        self._ensure_registered_repo(normalized_repo)
        started_at = self._now_provider()
        sample_candidates_by_extension = self._file_sampler.collect_candidates_by_extension(normalized_repo)
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

    def _probe_single_language(
        self,
        *,
        repo_root: str,
        entry: LanguageSupportEntry,
        sample_candidates_by_extension: dict[str, list[tuple[str, int]]],
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 readiness를 probe하고 결과 DTO를 반환한다."""
        sample_path = self._file_sampler.pick_sample_path(
            entry=entry,
            sample_candidates_by_extension=sample_candidates_by_extension,
        )
        timeout_sec = self._probe_timeout_for(entry.language.value)
        return self._thread_runner.run_with_timeout(
            language=entry.language.value,
            probe_at=probe_at,
            timeout_sec=timeout_sec,
            task=lambda: self._probe_worker.probe_single_language_impl(
                repo_root=repo_root,
                entry=entry,
                sample_path=sample_path,
                probe_at=probe_at,
            ),
        )

    def _probe_timeout_for(self, language: str) -> float:
        """언어별 probe timeout을 계산한다."""
        normalized = language.strip().lower()
        return self._per_language_timeout_overrides.get(normalized, self._per_language_timeout_sec)
