"""LSP runtime scope resolution/override 전담 서비스."""

from __future__ import annotations

import logging
from pathlib import Path
from solidlsp.ls_config import Language

log = logging.getLogger(__name__)
_KNOWN_SCOPE_MARKER_FILES: tuple[str, ...] = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "vue.config.js",
    "vite.config.ts",
)


class LspScopeRuntimeService:
    """scope planner/override/hint 소비를 backend 본체에서 분리한다."""

    def __init__(
        self,
        *,
        get_scope_override,
        to_scope_relative_path_or_fallback,
        get_lsp_scope_planner,
        is_lsp_scope_planner_enabled,
        get_scope_active_languages,
        perf_tracer,
        on_scope_override_hit,
        on_scope_planner_applied,
        on_scope_planner_fallback_index_building,
        l3_scope_pending_hints: dict[tuple[str, str], int],
        l3_scope_pending_hint_lock,
        normalize_repo_relative_path,
    ) -> None:
        self._get_scope_override = get_scope_override
        self._to_scope_relative_path_or_fallback = to_scope_relative_path_or_fallback
        self._get_lsp_scope_planner = get_lsp_scope_planner
        self._is_lsp_scope_planner_enabled = is_lsp_scope_planner_enabled
        self._get_scope_active_languages = get_scope_active_languages
        self._perf_tracer = perf_tracer
        self._on_scope_override_hit = on_scope_override_hit
        self._on_scope_planner_applied = on_scope_planner_applied
        self._on_scope_planner_fallback_index_building = on_scope_planner_fallback_index_building
        self._l3_scope_pending_hints = l3_scope_pending_hints
        self._l3_scope_pending_hint_lock = l3_scope_pending_hint_lock
        self._normalize_repo_relative_path = normalize_repo_relative_path

    def resolve_lsp_runtime_scope(
        self,
        *,
        repo_root: str,
        normalized_relative_path: str,
        language: Language,
    ) -> tuple[str, str]:
        override = self._get_scope_override(repo_root=repo_root, relative_path=normalized_relative_path)
        if override is not None:
            override_scope_root, _override_scope_level = override
            self._on_scope_override_hit()
            runtime_relative_path = self._to_scope_relative_path_or_fallback(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative_path,
                runtime_root=override_scope_root,
            )
            return (override_scope_root, runtime_relative_path)

        planner = self._get_lsp_scope_planner()
        if planner is None or not self._is_lsp_scope_planner_enabled():
            return (repo_root, normalized_relative_path)
        active_languages = self._get_scope_active_languages()
        if active_languages is not None and language.value.lower() not in active_languages:
            return (repo_root, normalized_relative_path)
        try:
            with self._perf_tracer.span(
                "scope_planner.resolve",
                phase="l3_extract",
                repo_root=repo_root,
                language=language.value,
            ):
                resolution = planner.resolve(
                    workspace_repo_root=repo_root,
                    relative_path=normalized_relative_path,
                    language=language,
                )
        except (RuntimeError, OSError, ValueError, TypeError):
            log.debug(
                "scope planner resolve failed, fallback to workspace scope (repo=%s, path=%s, lang=%s)",
                repo_root,
                normalized_relative_path,
                language.value,
                exc_info=True,
            )
            return (repo_root, normalized_relative_path)
        if getattr(resolution, "strategy", "") == "FALLBACK_INDEX_BUILDING":
            self._on_scope_planner_fallback_index_building()
        self._on_scope_planner_applied()
        runtime_root = str(resolution.lsp_scope_root)
        runtime_root_path = Path(runtime_root).expanduser().resolve()
        if runtime_root_path.name in _KNOWN_SCOPE_MARKER_FILES:
            return (repo_root, normalized_relative_path)
        runtime_relative_path = self._to_scope_relative_path_or_fallback(
            repo_root=repo_root,
            normalized_relative_path=normalized_relative_path,
            runtime_root=runtime_root,
            planner=planner,
        )
        return (runtime_root, runtime_relative_path)

    def resolve_probe_runtime_scope(
        self,
        *,
        repo_root: str,
        sample_relative_path: str,
        language: Language,
    ) -> tuple[str, str]:
        return self.resolve_lsp_runtime_scope(
            repo_root=repo_root,
            normalized_relative_path=self._normalize_repo_relative_path(sample_relative_path),
            language=language,
        )

    def consume_l3_scope_pending_hint(self, *, language: Language, runtime_scope_root: str) -> int:
        key = (language.value, runtime_scope_root)
        with self._l3_scope_pending_hint_lock:
            current = int(self._l3_scope_pending_hints.get(key, 0))
            if current <= 1:
                self._l3_scope_pending_hints.pop(key, None)
                return max(current, 0)
            self._l3_scope_pending_hints[key] = current - 1
            return current
