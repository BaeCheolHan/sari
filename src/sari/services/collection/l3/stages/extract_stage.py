"""LSP extract stage for L3 orchestrator."""

from __future__ import annotations

import inspect
from typing import Callable


class L3ExtractStage:
    """Thin stage wrapper around LSP backend extract call."""

    def __init__(self, *, extract_fn: Callable[[str, str, str], object]) -> None:
        self._extract_fn = extract_fn

    def execute(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        bypass_zero_relations_retry_pending: bool = False,
    ) -> object:
        if not bypass_zero_relations_retry_pending:
            return self._extract_fn(repo_root, relative_path, content_hash)
        if self._supports_bypass_keyword():
            return self._extract_fn(
                repo_root,
                relative_path,
                content_hash,
                bypass_zero_relations_retry_pending=True,
            )
        return self._extract_fn(repo_root, relative_path, content_hash)

    def _supports_bypass_keyword(self) -> bool:
        try:
            signature = inspect.signature(self._extract_fn)
        except (TypeError, ValueError):
            return False
        for parameter in signature.parameters.values():
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                return True
        return "bypass_zero_relations_retry_pending" in signature.parameters
