"""수집 파이프라인의 repo 식별/정책 보조 컴포넌트를 제공한다."""

from __future__ import annotations

from contextlib import contextmanager
import fnmatch
from pathlib import Path
from typing import Callable

from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern
from solidlsp.ls_config import Language

from sari.core.language_registry import resolve_language_from_path
from sari.core.models import CollectionPolicyDTO, RepoIdentityDTO, now_iso8601_utc
from sari.core.repo_identity import compute_repo_id, resolve_workspace_root
from sari.core.repo_resolver import resolve_repo_key
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository


class CollectionRepoSupport:
    """repo 식별/수집 정책/LSP prewarm 보조 책임을 담당한다."""

    def __init__(
        self,
        *,
        workspace_repo: WorkspaceRepository,
        policy: CollectionPolicyDTO,
        policy_repo: PipelinePolicyRepository | None,
        lsp_backend: object,
        repo_registry_repo: RepoRegistryRepository | None,
        lsp_prewarm_min_language_files: int,
        lsp_prewarm_top_language_count: int,
    ) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._policy = policy
        self._policy_repo = policy_repo
        self._lsp_backend = lsp_backend
        self._repo_registry_repo = repo_registry_repo
        self._lsp_prewarm_min_language_files = lsp_prewarm_min_language_files
        self._lsp_prewarm_top_language_count = lsp_prewarm_top_language_count
        self._extra_exclude_globs_stack: list[tuple[str, ...]] = []

    def resolve_lsp_language(self, relative_path: str) -> Language | None:
        """파일 상대 경로로 LSP 언어를 해석한다."""
        return resolve_language_from_path(file_path=relative_path)

    def configure_lsp_prewarm_languages(self, repo_root: str, language_counts: dict[Language, int]) -> None:
        """스캔 통계를 바탕으로 repo별 hot language prewarm 대상을 설정한다."""
        configure_func = getattr(self._lsp_backend, "configure_hot_languages", None)
        if not callable(configure_func):
            return
        candidates = [
            (language, count)
            for language, count in language_counts.items()
            if count >= self._lsp_prewarm_min_language_files
        ]
        candidates.sort(key=lambda item: item[1], reverse=True)
        selected = {language for language, _ in candidates[: self._lsp_prewarm_top_language_count]}
        configure_func(repo_root=repo_root, languages=selected)

    def schedule_lsp_probe_for_file(self, repo_root: str, relative_path: str) -> None:
        """파일 경로를 기준으로 비동기 LSP probe를 스케줄한다."""
        scheduler = getattr(self._lsp_backend, "schedule_probe_for_file", None)
        if not callable(scheduler):
            return
        scheduler(repo_root=repo_root, relative_path=relative_path, force=False, trigger="background")

    def force_lsp_probe_for_file(self, repo_root: str, relative_path: str) -> None:
        """실사용 경로에서 즉시 probe를 강제 수행한다."""
        scheduler = getattr(self._lsp_backend, "schedule_probe_for_file", None)
        if not callable(scheduler):
            return
        scheduler(repo_root=repo_root, relative_path=relative_path, force=True, trigger="force")

    def invalidate_lsp_probe_ready(self, repo_root: str, relative_path: str) -> None:
        """READY/WARMING 상태를 무효화한다."""
        resolver = getattr(self._lsp_backend, "invalidate_probe_ready_for_file", None)
        if not callable(resolver):
            return
        resolver(repo_root=repo_root, relative_path=relative_path)

    def shutdown_probe_executor(self) -> None:
        """비동기 probe executor를 종료한다."""
        shutdown = getattr(self._lsp_backend, "shutdown_probe_executor", None)
        if not callable(shutdown):
            return
        shutdown()

    def resolve_repo_label(self, repo_root: str) -> str:
        """저장소 절대경로를 workspace-relative repo_key로 변환한다."""
        try:
            workspace_paths = [item.path for item in self._workspace_repo.list_all()]
            return resolve_repo_key(repo_root=repo_root, workspace_paths=workspace_paths)
        except (RuntimeError, ValueError):
            return Path(repo_root).name

    def resolve_repo_identity(self, repo_root: str) -> RepoIdentityDTO:
        """repo 라벨/ID를 계산하고 레지스트리에 동기화한다."""
        workspace_paths = [workspace.path for workspace in self._workspace_repo.list_all()]
        resolved_label = self.resolve_repo_label(repo_root)
        workspace_root = resolve_workspace_root(repo_root=repo_root, workspace_paths=workspace_paths)
        resolved_repo_id = compute_repo_id(repo_label=resolved_label, workspace_root=workspace_root)
        identity = RepoIdentityDTO(
            repo_id=resolved_repo_id,
            repo_label=resolved_label,
            repo_root=repo_root,
            workspace_root=workspace_root,
            updated_at=now_iso8601_utc(),
        )
        if self._repo_registry_repo is not None:
            self._repo_registry_repo.upsert(identity)
        return identity

    def load_gitignore_spec(self, repo_root: Path) -> PathSpec:
        """repo root의 .gitignore를 pathspec으로 로드한다."""
        gitignore_path = repo_root / ".gitignore"
        patterns: list[str] = []
        if gitignore_path.exists():
            patterns = gitignore_path.read_text(encoding="utf-8").splitlines()
        return PathSpec.from_lines(GitWildMatchPattern, patterns)

    def is_collectible(self, file_path: Path, repo_root: Path, gitignore_spec: PathSpec) -> bool:
        """파일이 수집 정책(include/exclude/gitignore/hidden)에 부합하는지 판정한다."""
        if file_path.stat().st_size > self._policy.max_file_size_bytes:
            return False
        relative_posix = str(file_path.relative_to(repo_root).as_posix())
        if gitignore_spec.match_file(relative_posix):
            return False
        if any((part.startswith(".") for part in file_path.parts)):
            return False
        suffix = file_path.suffix.lower()
        if suffix not in self._policy.include_ext:
            return False
        for pattern in self._policy.exclude_globs:
            if fnmatch.fnmatch(relative_posix, pattern):
                return False
        if len(self._extra_exclude_globs_stack) > 0:
            for extra_globs in self._extra_exclude_globs_stack:
                for pattern in extra_globs:
                    if fnmatch.fnmatch(relative_posix, pattern):
                        return False
        return True

    @contextmanager
    def temporary_extra_exclude_globs(self, globs: tuple[str, ...]):
        """일시적으로 추가 exclude globs를 적용한다 (perf 측정 전용)."""
        normalized = tuple(item.strip() for item in globs if item.strip() != "")
        if len(normalized) == 0:
            yield
            return
        self._extra_exclude_globs_stack.append(normalized)
        try:
            yield
        finally:
            if len(self._extra_exclude_globs_stack) > 0:
                self._extra_exclude_globs_stack.pop()

    def is_deletion_hold_enabled(self) -> bool:
        """삭제 보류 정책 활성화 여부를 반환한다."""
        if self._policy_repo is None:
            return False
        return bool(self._policy_repo.get_policy().deletion_hold)


class WorkspaceFanoutResolver:
    """workspace root 하위 top-level repo fan-out 대상을 판정한다."""

    def __init__(
        self,
        *,
        workspace_repo: WorkspaceRepository,
        load_gitignore_spec: Callable[[Path], PathSpec],
        is_collectible: Callable[[Path, Path, PathSpec], bool],
        build_markers: tuple[str, ...],
    ) -> None:
        """필요 의존성과 판정 함수를 주입한다."""
        self._workspace_repo = workspace_repo
        self._load_gitignore_spec = load_gitignore_spec
        self._is_collectible = is_collectible
        self._build_markers = build_markers

    def resolve_targets(self, root_path: Path) -> list[Path]:
        """workspace 경로인 경우 top-level repo 후보를 계산한다."""
        if not root_path.exists() or not root_path.is_dir():
            return []
        registered_paths = {Path(item.path).expanduser().resolve() for item in self._workspace_repo.list_all()}
        if root_path not in registered_paths:
            return []
        targets: list[Path] = []
        for child in root_path.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if self._is_top_level_repo_candidate(child):
                targets.append(child.resolve())
        # 단일 후보만 있으면 workspace fan-out이 아니라 단일 repo 스캔으로 본다.
        if len(targets) <= 1:
            return []
        targets.sort(key=lambda item: item.name)
        return targets

    def _is_top_level_repo_candidate(self, candidate: Path) -> bool:
        """top-level 하위 디렉터리가 repo 후보인지 판정한다."""
        if (candidate / ".git").exists():
            return True
        for marker in self._build_markers:
            if (candidate / marker).exists():
                return True
        return self._contains_collectible_file(candidate)

    def _contains_collectible_file(self, repo_root: Path) -> bool:
        """빌드 마커가 없어도 수집 대상 파일이 존재하면 repo 후보로 간주한다."""
        gitignore_spec = self._load_gitignore_spec(repo_root)
        for file_path in repo_root.rglob("*"):
            if not file_path.is_file():
                continue
            if self._is_collectible(file_path=file_path, repo_root=repo_root, gitignore_spec=gitignore_spec):
                return True
        return False
