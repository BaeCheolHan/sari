"""L3 skip/readiness 최근성 판정 런타임 서비스."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Callable

from solidlsp.ls_config import Language

from sari.core.models import FileEnrichJobDTO, ToolReadinessStateDTO

log = logging.getLogger(__name__)


class L3SkipRuntimeService:
    def __init__(
        self,
        *,
        l3_supported_languages: set[Language],
        l3_recent_success_ttl_sec: int,
        readiness_repo: object,
        lsp_backend: object,
        resolve_language_from_path_fn: Callable[[str], Language | None],
    ) -> None:
        self._l3_supported_languages = l3_supported_languages
        self._l3_recent_success_ttl_sec = int(l3_recent_success_ttl_sec)
        self._readiness_repo = readiness_repo
        self._lsp_backend = lsp_backend
        self._resolve_language_from_path_fn = resolve_language_from_path_fn

    def resolve_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
        """job이 L3 추출을 건너뛰어야 하는 사유를 반환한다."""
        language = self._resolve_language_from_path_fn(job.relative_path)
        if language is None:
            return "skip_unsupported_extension"
        if language not in self._l3_supported_languages:
            return "skip_unsupported_language"
        checker = getattr(self._lsp_backend, "is_l3_permanently_unavailable_for_file", None)
        if callable(checker):
            try:
                if bool(checker(repo_root=job.repo_root, relative_path=job.relative_path)):
                    return "skip_probe_unavailable"
            except (RuntimeError, OSError, ValueError, TypeError):
                log.warning(
                    "Failed to evaluate L3 permanent-unavailable probe guard (repo=%s, path=%s)",
                    job.repo_root,
                    job.relative_path,
                    exc_info=True,
                )
                return "skip_probe_check_error"
        return None

    def build_l3_skipped_readiness(self, *, job: FileEnrichJobDTO, reason: str, now_iso: str) -> ToolReadinessStateDTO:
        return ToolReadinessStateDTO(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=False,
            get_callers_ready=False,
            consistency_ready=False,
            quality_ready=False,
            tool_ready=False,
            last_reason=reason,
            updated_at=now_iso,
        )

    def is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
        if self._l3_recent_success_ttl_sec <= 0:
            return False
        state = self._readiness_repo.get_state(job.repo_root, job.relative_path)
        if state is None:
            return False
        if not state.tool_ready:
            return False
        if state.content_hash != job.content_hash:
            return False
        try:
            updated_at = datetime.fromisoformat(state.updated_at)
        except ValueError:
            log.debug(
                "Invalid readiness updated_at; treating as not recent (repo=%s, path=%s, updated_at=%s)",
                job.repo_root,
                job.relative_path,
                state.updated_at,
            )
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_sec <= float(self._l3_recent_success_ttl_sec)
