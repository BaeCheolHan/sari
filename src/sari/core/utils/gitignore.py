import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class _GitignoreRule:
    pattern: str
    negated: bool
    anchored: bool
    dir_only: bool


def load_gitignore(root: Path) -> List[str]:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return []
    try:
        return gitignore.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def _parse_lines(lines: List[str]) -> List[_GitignoreRule]:
    rules: List[_GitignoreRule] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(r"\#"):
            line = line[1:]
        elif line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        if not line:
            continue
        rules.append(_GitignoreRule(pattern=line, negated=negated, anchored=anchored, dir_only=dir_only))
    return rules


class GitignoreMatcher:
    def __init__(self, lines: List[str]):
        self._rules = _parse_lines(lines or [])

    def _match_rule(self, rule: _GitignoreRule, rel_posix: str, is_dir: bool) -> bool:
        if rule.dir_only and not is_dir:
            return False
        if rule.anchored:
            return fnmatch.fnmatch(rel_posix, rule.pattern)
        if "/" in rule.pattern:
            if fnmatch.fnmatch(rel_posix, rule.pattern):
                return True
            if fnmatch.fnmatch(rel_posix, f"*/{rule.pattern}"):
                return True
            return False
        name = rel_posix.rsplit("/", 1)[-1]
        return fnmatch.fnmatch(name, rule.pattern)

    def is_ignored(self, rel_posix: str, is_dir: bool = False) -> bool:
        ignored = False
        for rule in self._rules:
            if self._match_rule(rule, rel_posix, is_dir):
                ignored = not rule.negated
        return ignored
