from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class LspScopeResolutionDTO:
    workspace_repo_root: str
    relative_path: str
    language: str
    lsp_scope_root: str
    strategy: str
    marker_file: str | None = None


class LspScopePlanner:
    """LSP runtime scope root를 계산하는 planner (Phase 1 baseline)."""

    def __init__(
        self,
        *,
        java_markers: tuple[str, ...] = (
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        ),
        ts_markers: tuple[str, ...] = ("tsconfig.json", "jsconfig.json", "package.json"),
        vue_markers: tuple[str, ...] = ("vue.config.js", "vite.config.ts", "package.json", "tsconfig.json"),
        top_level_fallback: bool = True,
        ignore_dirs: tuple[str, ...] = (
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            "target",
            ".next",
            "coverage",
        ),
    ) -> None:
        self._java_markers = tuple(java_markers)
        self._ts_markers = tuple(ts_markers)
        self._vue_markers = tuple(vue_markers)
        self._top_level_fallback = bool(top_level_fallback)
        self._ignore_dirs = set(ignore_dirs)
        self._lock = threading.Lock()
        self._resolution_cache: dict[tuple[str, str, str], LspScopeResolutionDTO] = {}
        self._marker_hit_cache: dict[tuple[str, str], tuple[Path | None, str | None]] = {}
        self._marker_index_cache: dict[tuple[str, str], dict[Path, str]] = {}
        self._marker_index_inflight: set[tuple[str, str]] = set()

    def resolve(
        self,
        *,
        workspace_repo_root: str,
        relative_path: str,
        language: Language,
    ) -> LspScopeResolutionDTO:
        repo_root = str(Path(workspace_repo_root).resolve())
        rel = str(Path(relative_path).as_posix()).lstrip("/")
        candidate_dir = self._candidate_dir(rel)
        cache_key = (repo_root, language.value, candidate_dir)
        with self._lock:
            cached = self._resolution_cache.get(cache_key)
            if cached is not None:
                return cached

        repo_path = Path(repo_root)
        marker_dir, marker_file, marker_lookup_status = self._find_nearest_marker_dir(
            repo_path=repo_path,
            candidate_dir=candidate_dir,
            language=language,
        )
        if marker_lookup_status == "index_building":
            result = LspScopeResolutionDTO(
                workspace_repo_root=repo_root,
                relative_path=rel,
                language=language.value,
                lsp_scope_root=repo_root,
                strategy="FALLBACK_INDEX_BUILDING",
                marker_file=None,
            )
        elif marker_dir is not None:
            result = LspScopeResolutionDTO(
                workspace_repo_root=repo_root,
                relative_path=rel,
                language=language.value,
                lsp_scope_root=str(marker_dir.resolve()),
                strategy="marker",
                marker_file=marker_file,
            )
        else:
            # Phase 1 baseline: top-level repo fallback == current repo root
            strategy = "top_level_repo" if self._top_level_fallback else "workspace_fallback"
            result = LspScopeResolutionDTO(
                workspace_repo_root=repo_root,
                relative_path=rel,
                language=language.value,
                lsp_scope_root=repo_root,
                strategy=strategy,
                marker_file=None,
            )
        with self._lock:
            self._resolution_cache[cache_key] = result
        return result

    def invalidate_path(self, path: str) -> int:
        target = Path(path).expanduser().resolve()
        removed = 0
        with self._lock:
            for key, value in list(self._resolution_cache.items()):
                del key
                scope_root_path = Path(value.lsp_scope_root)
                candidate_abs = (Path(value.workspace_repo_root) / self._candidate_dir(value.relative_path)).resolve()
                if self._paths_overlap(scope_root_path, target) or self._paths_overlap(candidate_abs, target):
                    self._resolution_cache.pop((value.workspace_repo_root, value.language, self._candidate_dir(value.relative_path)), None)
                    removed += 1
            for key, marker_data in list(self._marker_hit_cache.items()):
                marker_dir, _ = marker_data
                if marker_dir is None:
                    continue
                if self._paths_overlap(marker_dir.resolve(), target):
                    self._marker_hit_cache.pop(key, None)
                    removed += 1
            for key, marker_index in list(self._marker_index_cache.items()):
                if any(self._paths_overlap(marker_dir.resolve(), target) for marker_dir in marker_index.keys()):
                    self._marker_index_cache.pop(key, None)
                    removed += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._resolution_cache.clear()
            self._marker_hit_cache.clear()
            self._marker_index_cache.clear()
            self._marker_index_inflight.clear()

    def to_scope_relative_path(self, *, workspace_relative_path: str, scope_candidate_root: str) -> str:
        """workspace-relative path를 scope-root-relative path로 변환한다.

        scope root가 해당 파일 경로의 prefix가 아니면 원본을 반환한다.
        """
        rel = str(Path(workspace_relative_path).as_posix()).lstrip("/")
        scope = str(Path(scope_candidate_root).as_posix()).strip("/").replace("\\", "/")
        if scope in ("", "."):
            return rel
        rel_parts = Path(rel).parts
        scope_parts = Path(scope).parts
        if len(scope_parts) > len(rel_parts):
            return rel
        if tuple(rel_parts[: len(scope_parts)]) != tuple(scope_parts):
            return rel
        remainder = Path(*rel_parts[len(scope_parts) :]).as_posix() if len(rel_parts) > len(scope_parts) else "."
        return remainder if remainder != "." else rel

    def _candidate_dir(self, relative_path: str) -> str:
        parent = Path(relative_path).parent
        if str(parent) in ("", "."):
            return "."
        return str(parent).replace("\\", "/")

    def _markers_for_language(self, language: Language) -> tuple[str, ...]:
        if language == Language.JAVA:
            return self._java_markers
        if language == Language.TYPESCRIPT:
            return self._ts_markers
        if language == Language.VUE:
            return self._vue_markers
        return ()

    def _find_nearest_marker_dir(
        self,
        *,
        repo_path: Path,
        candidate_dir: str,
        language: Language,
    ) -> tuple[Path | None, str | None, str]:
        markers = self._markers_for_language(language)
        if not markers:
            return (None, None, "no_markers")
        marker_index = self._ensure_marker_index(repo_path=repo_path, language=language)
        if marker_index is None:
            return (None, None, "index_building")
        marker_cache_key = (str(repo_path), f"{language.value}:{candidate_dir}")
        with self._lock:
            cached = self._marker_hit_cache.get(marker_cache_key)
            if cached is not None:
                return (cached[0], cached[1], "cache")
        current = (repo_path / candidate_dir).resolve()
        repo_resolved = repo_path.resolve()
        while True:
            if current.name in self._ignore_dirs:
                break
            marker_name = marker_index.get(current)
            if marker_name is not None:
                hit = (current, marker_name)
                with self._lock:
                    self._marker_hit_cache[marker_cache_key] = hit
                return (hit[0], hit[1], "index")
            if current == repo_resolved:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        miss = (None, None)
        with self._lock:
            self._marker_hit_cache[marker_cache_key] = miss
        return (None, None, "miss")

    def _ensure_marker_index(self, *, repo_path: Path, language: Language) -> dict[Path, str] | None:
        index_key = (str(repo_path.resolve()), language.value)
        with self._lock:
            cached = self._marker_index_cache.get(index_key)
            if cached is not None:
                return cached
            if index_key in self._marker_index_inflight:
                return None
            self._marker_index_inflight.add(index_key)
        try:
            built = self._build_marker_index(repo_path=repo_path.resolve(), language=language)
            with self._lock:
                self._marker_index_cache[index_key] = built
            return built
        finally:
            with self._lock:
                self._marker_index_inflight.discard(index_key)

    def _build_marker_index(self, *, repo_path: Path, language: Language) -> dict[Path, str]:
        markers = set(self._markers_for_language(language))
        if not markers:
            return {}
        index: dict[Path, str] = {}
        try:
            for path in repo_path.rglob("*"):
                if not path.is_file():
                    continue
                if path.name not in markers:
                    continue
                parent = path.parent.resolve()
                if any(part in self._ignore_dirs for part in parent.parts):
                    continue
                if parent not in index:
                    index[parent] = path.name
        except (OSError, RuntimeError):
            return {}
        return index

    def _paths_overlap(self, a: Path, b: Path) -> bool:
        try:
            a.relative_to(b)
            return True
        except ValueError:
            pass
        try:
            b.relative_to(a)
            return True
        except ValueError:
            return False
