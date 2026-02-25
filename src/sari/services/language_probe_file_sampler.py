"""Language probe용 파일 샘플 수집/선택 유틸리티."""

from __future__ import annotations

import os
from pathlib import Path

from sari.core.language_registry import LanguageSupportEntry
from solidlsp.ls_config import Language


class LanguageProbeFileSampler:
    """레포 내 언어 샘플 파일 후보를 수집/선택한다."""

    def __init__(self, *, entries: tuple[LanguageSupportEntry, ...], go_sample_candidates_max: int) -> None:
        self._entries = entries
        self._go_sample_candidates_max = max(1, int(go_sample_candidates_max))

    def collect_candidates_by_extension(self, repo_root: str) -> dict[str, list[tuple[str, int]]]:
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

    def pick_sample_path(self, *, entry: LanguageSupportEntry, sample_candidates_by_extension: dict[str, list[tuple[str, int]]]) -> str | None:
        """언어 엔트리 확장자 목록에서 probe 샘플 파일을 고른다."""
        if entry.language == Language.GO:
            return self._pick_go_sample_path(entry=entry, sample_candidates_by_extension=sample_candidates_by_extension)
        for extension in entry.extensions:
            candidates = sample_candidates_by_extension.get(extension.lower(), [])
            if len(candidates) > 0:
                return candidates[0][0]
        return None

    def _sample_candidate_cap_for_extension(self, extension: str) -> int:
        """확장자별 후보 수집 상한을 반환한다."""
        if extension.lower() == ".go":
            return self._go_sample_candidates_max
        return 1

    def _pick_go_sample_path(self, *, entry: LanguageSupportEntry, sample_candidates_by_extension: dict[str, list[tuple[str, int]]]) -> str | None:
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

