from pathlib import Path
from types import SimpleNamespace
import os

from sari.core.indexer.scanner import Scanner


def test_scanner_builds_gitignore_matcher_once_per_scan(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "a" / "b").mkdir(parents=True)
    (ws / "a" / "b" / "f.py").write_text("print('x')\n", encoding="utf-8")

    cfg = SimpleNamespace(
        include_ext=[".py"],
        include_files=[],
        exclude_dirs=[],
        exclude_globs=[],
        gitignore_lines=["*.skip"],
        settings=SimpleNamespace(MAX_DEPTH=10, FOLLOW_SYMLINKS=False),
    )

    calls = {"n": 0}

    class _CountMatcher:
        def __init__(self, _lines):
            calls["n"] += 1

        def is_ignored(self, _path, is_dir=False):
            return False

    monkeypatch.setattr("sari.core.indexer.scanner.GitignoreMatcher", _CountMatcher)
    scanner = Scanner(cfg, active_workspaces=[])
    files = list(scanner.iter_file_entries(Path(ws), apply_exclude=True))
    assert files
    assert calls["n"] == 1


def test_scanner_excludes_dot_idea_glob_directory_without_descending(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src" / ".idea").mkdir(parents=True)
    (ws / "src" / ".idea" / "workspace.xml").write_text("<xml/>", encoding="utf-8")
    (ws / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    cfg = SimpleNamespace(
        include_ext=[".py", ".xml"],
        include_files=[],
        exclude_dirs=[".idea/**"],
        exclude_globs=[],
        gitignore_lines=[],
        settings=SimpleNamespace(MAX_DEPTH=10, FOLLOW_SYMLINKS=False),
    )

    scanner = Scanner(cfg, active_workspaces=[])
    paths = [str(p) for p, _st, _excluded in scanner.iter_file_entries(Path(ws), apply_exclude=True)]
    assert str(ws / "src" / "main.py") in paths
    assert str(ws / "src" / ".idea" / "workspace.xml") not in paths


def test_scanner_excludes_venv_and_site_packages_without_descending(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".venv" / "lib").mkdir(parents=True)
    (ws / "pkg" / "site-packages" / "x").mkdir(parents=True)
    (ws / "src").mkdir(parents=True)
    (ws / ".venv" / "lib" / "a.py").write_text("print('venv')\n", encoding="utf-8")
    (ws / "pkg" / "site-packages" / "x" / "y.py").write_text("print('pkg')\n", encoding="utf-8")
    (ws / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    cfg = SimpleNamespace(
        include_ext=[".py"],
        include_files=[],
        exclude_dirs=[".venv/**", "site-packages/**"],
        exclude_globs=[],
        gitignore_lines=[],
        settings=SimpleNamespace(MAX_DEPTH=10, FOLLOW_SYMLINKS=False),
    )

    scan_calls: list[str] = []
    real_scandir = os.scandir

    def _count_scandir(path):
        scan_calls.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr("sari.core.indexer.scanner.os.scandir", _count_scandir)

    scanner = Scanner(cfg, active_workspaces=[])
    paths = [str(p) for p, _st, _excluded in scanner.iter_file_entries(Path(ws), apply_exclude=True)]

    assert str(ws / "src" / "main.py") in paths
    assert str(ws / ".venv" / "lib" / "a.py") not in paths
    assert str(ws / "pkg" / "site-packages" / "x" / "y.py") not in paths
    assert str(ws / ".venv") not in scan_calls
    assert str(ws / "pkg" / "site-packages") not in scan_calls
