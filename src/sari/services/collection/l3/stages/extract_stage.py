"""LSP extract stage for L3 orchestrator."""

from __future__ import annotations

from typing import Callable


class L3ExtractStage:
    """Thin stage wrapper around LSP backend extract call."""

    def __init__(self, *, extract_fn: Callable[[str, str, str], object]) -> None:
        self._extract_fn = extract_fn

    def execute(self, *, repo_root: str, relative_path: str, content_hash: str) -> object:
        return self._extract_fn(repo_root, relative_path, content_hash)

