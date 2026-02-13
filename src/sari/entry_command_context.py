import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from sari.core.workspace import WorkspaceManager


@dataclass
class CommandContext:
    cwd: Path | str | None = None
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)

    def __post_init__(self) -> None:
        if self.cwd is None:
            self.cwd = Path.cwd()
        else:
            self.cwd = Path(self.cwd)

    def resolve_config_path(self) -> str:
        return WorkspaceManager.resolve_config_path(str(self.cwd))

    def resolve_workspace_root(self) -> str:
        return WorkspaceManager.resolve_workspace_root()

    def normalize_path(self, path: str) -> str:
        return WorkspaceManager.normalize_path(path)

    def normalize_existing_dir(self, path: str) -> str | None:
        candidate = self.normalize_path(path)
        expanded = Path(candidate).expanduser()
        normalized = self.normalize_path(str(expanded))
        if not expanded.exists() or not expanded.is_dir():
            return None
        return normalized

    def print_json(self, payload: dict | list) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=self.stdout)

    def print_line(self, text: str) -> None:
        print(text, file=self.stdout)

    def print_err(self, text: str) -> None:
        print(text, file=self.stderr)

    def env(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, default)
