"""L3 런타임 보조 조율 서비스.

probe 스케줄링, scope learning 기록, L3 대기큐 획득, deletion-hold 조회를 담당한다.
"""

from __future__ import annotations

from collections.abc import Callable
import queue
import logging

from solidlsp.ls_config import Language

from sari.core.models import FileEnrichJobDTO

log = logging.getLogger(__name__)


class L3RuntimeCoordinationService:
    def __init__(
        self,
        *,
        lsp_backend: object,
        lsp_probe_l1_languages: set[Language],
        resolve_language_from_path_fn: Callable[[str], Language | None],
        l3_ready_queue: queue.Queue[FileEnrichJobDTO],
        enrich_queue_repo: object,
        now_iso_supplier: Callable[[], str],
        policy_repo: object | None,
    ) -> None:
        self._lsp_backend = lsp_backend
        self._lsp_probe_l1_languages = lsp_probe_l1_languages
        self._resolve_language_from_path_fn = resolve_language_from_path_fn
        self._l3_ready_queue = l3_ready_queue
        self._enrich_queue_repo = enrich_queue_repo
        self._now_iso_supplier = now_iso_supplier
        self._policy_repo = policy_repo

    def schedule_l1_probe_after_l3_fallback(self, job: FileEnrichJobDTO) -> None:
        language = self._resolve_language_from_path_fn(job.relative_path)
        if language is None or language not in self._lsp_probe_l1_languages:
            return
        inflight_checker = getattr(self._lsp_backend, "is_probe_inflight_for_file", None)
        if callable(inflight_checker):
            try:
                if bool(inflight_checker(repo_root=job.repo_root, relative_path=job.relative_path)):
                    return
            except (RuntimeError, OSError, ValueError, TypeError):
                return
        scheduler = getattr(self._lsp_backend, "schedule_probe_for_file", None)
        if not callable(scheduler):
            return
        try:
            scheduler(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                force=False,
                trigger="l3_fallback",
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            log.warning(
                "Failed to schedule l3_fallback probe (repo=%s, path=%s)",
                job.repo_root,
                job.relative_path,
                exc_info=True,
            )

    def record_scope_learning_after_l3_success(self, *, job: FileEnrichJobDTO) -> None:
        recorder = getattr(self._lsp_backend, "record_scope_override_success", None)
        if not callable(recorder):
            return
        scope_level = (getattr(job, "scope_level", None) or "module").strip().lower()
        scope_root = getattr(job, "scope_root", None) or job.repo_root
        try:
            recorder(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                scope_root=scope_root,
                scope_level=scope_level,
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            log.debug(
                "Failed to record scope learning after L3 success (repo=%s, path=%s)",
                job.repo_root,
                job.relative_path,
                exc_info=True,
            )

    def acquire_l3_jobs(self, limit: int) -> list[FileEnrichJobDTO]:
        jobs: list[FileEnrichJobDTO] = []
        while len(jobs) < limit:
            try:
                jobs.append(self._l3_ready_queue.get_nowait())
            except queue.Empty:
                break
        if len(jobs) < limit:
            now_iso = self._now_iso_supplier()
            jobs.extend(self._enrich_queue_repo.acquire_pending_for_l3(limit=limit - len(jobs), now_iso=now_iso))
        return jobs

    def is_deletion_hold_enabled(self) -> bool:
        if self._policy_repo is None:
            return False
        return bool(self._policy_repo.get_policy().deletion_hold)
