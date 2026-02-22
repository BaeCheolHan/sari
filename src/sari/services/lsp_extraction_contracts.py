from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LspExtractionResultDTO:
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]
    error_message: str | None


class LspExtractionBackend(Protocol):

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        ...
