"""LSP parallelism/bulk-mode 제어 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language


class LspParallelismService:
    """parallelism 관련 분기 로직을 backend 본체에서 분리한다."""

    def __init__(
        self,
        *,
        hub: object,
        is_profiled_language,
        ensure_prewarm,
        increment_broker_parallelism_guard_skip,
    ) -> None:
        self._hub = hub
        self._is_profiled_language = is_profiled_language
        self._ensure_prewarm = ensure_prewarm
        self._increment_broker_parallelism_guard_skip = increment_broker_parallelism_guard_skip

    def get_parallelism(self, *, repo_root: str, language: Language) -> int:
        if self._is_profiled_language(language):
            self._increment_broker_parallelism_guard_skip()
            return 1
        running = self._hub.get_running_instance_count(language=language, repo_root=repo_root)
        if running > 0:
            return running
        self._ensure_prewarm(language=language, repo_root=repo_root)
        return max(1, self._hub.get_running_instance_count(language=language, repo_root=repo_root))

    def get_parallelism_for_batch(self, *, repo_root: str, language: Language, batch_size: int) -> int:
        if self._is_profiled_language(language):
            self._increment_broker_parallelism_guard_skip()
            return 1
        desired = max(1, int(batch_size))
        servers = self._hub.acquire_pool(language=language, repo_root=repo_root, desired=desired, request_kind="indexing")
        return max(1, len(servers))

    def set_bulk_mode(self, *, repo_root: str, language: Language, enabled: bool) -> None:
        if self._is_profiled_language(language):
            self._increment_broker_parallelism_guard_skip()
            return
        self._hub.set_bulk_mode(language=language, repo_root=repo_root, enabled=enabled)
