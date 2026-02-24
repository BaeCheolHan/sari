"""File lookup and guard stage for L3 orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class L3FileGuardResult:
    file_row: object | None
    done_immediately: bool


class L3FileGuardStage:
    """Resolve file row and short-circuit when job is already stale."""

    def __init__(self, *, get_file: Callable[[str, str], object | None]) -> None:
        self._get_file = get_file

    def execute(self, *, repo_root: str, relative_path: str, content_hash: str) -> L3FileGuardResult:
        file_row = self._get_file(repo_root, relative_path)
        if file_row is None:
            return L3FileGuardResult(file_row=None, done_immediately=True)
        if bool(getattr(file_row, "is_deleted", False)):
            return L3FileGuardResult(file_row=file_row, done_immediately=True)
        row_hash = getattr(file_row, "content_hash", None)
        if row_hash != content_hash:
            return L3FileGuardResult(file_row=file_row, done_immediately=True)
        return L3FileGuardResult(file_row=file_row, done_immediately=False)

