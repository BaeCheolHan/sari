import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import List

_GITIGNORE_CACHE: dict[str, tuple[float, List[str]]] = {}


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
    key = str(gitignore)
    try:
        mtime = float(gitignore.stat().st_mtime)
    except Exception:
        mtime = -1.0
    cached = _GITIGNORE_CACHE.get(key)
    if cached and cached[0] == mtime:
        return list(cached[1])
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        _GITIGNORE_CACHE[key] = (mtime, list(lines))
        return lines
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
        self._cache: dict[tuple[str, bool], bool] = {}
        self._cache_order: list[tuple[str, bool]] = []
        self._cache_max = 4096

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
        key = (str(rel_posix), bool(is_dir))
        cached = self._cache.get(key)
        if cached is not None:
            return bool(cached)
        ignored = False
        for rule in self._rules:
            if self._match_rule(rule, rel_posix, is_dir):
                ignored = not rule.negated
        self._cache[key] = ignored
        self._cache_order.append(key)
        if len(self._cache_order) > self._cache_max:
            stale = self._cache_order.pop(0)
            self._cache.pop(stale, None)
        return ignored
