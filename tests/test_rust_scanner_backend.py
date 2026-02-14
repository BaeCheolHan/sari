from pathlib import Path
from types import SimpleNamespace

from sari.core.indexer.scanner import Scanner


def _cfg():
    return SimpleNamespace(
        include_ext=[".py", ".txt"],
        include_files=[],
        exclude_dirs=[".idea/**"],
        exclude_globs=[],
        gitignore_lines=[],
        settings=SimpleNamespace(MAX_DEPTH=10, FOLLOW_SYMLINKS=False),
    )


def test_scanner_rust_backend_yields_entries(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = ws / "main.py"
    p.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setenv("SARI_SCANNER_BACKEND", "rust")
    monkeypatch.setattr(
        "sari.core.indexer.scanner.iter_rust_scan_entries",
        lambda root, **_k: iter([(p, 1, 12)]),
    )

    scanner = Scanner(_cfg(), active_workspaces=[])
    got = list(scanner.iter_file_entries(ws, apply_exclude=True))
    assert any(str(x[0]) == str(p) for x in got)


def test_scanner_rust_backend_fallbacks_to_python_on_failure(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = ws / "main.py"
    p.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setenv("SARI_SCANNER_BACKEND", "rust")

    def _boom(*_a, **_k):
        raise RuntimeError("rust failed")

    monkeypatch.setattr("sari.core.indexer.scanner.iter_rust_scan_entries", _boom)

    scanner = Scanner(_cfg(), active_workspaces=[])
    got = list(scanner.iter_file_entries(ws, apply_exclude=True))
    assert any(str(x[0]) == str(p) for x in got)


def test_scanner_rust_backend_respects_exclude_filters(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "src" / ".idea").mkdir(parents=True)
    a = ws / "src" / ".idea" / "x.py"
    b = ws / "src" / "main.py"
    a.write_text("x=1\n", encoding="utf-8")
    b.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setenv("SARI_SCANNER_BACKEND", "rust")
    monkeypatch.setattr(
        "sari.core.indexer.scanner.iter_rust_scan_entries",
        lambda root, **_k: iter([(a, 1, 1), (b, 1, 1)]),
    )

    scanner = Scanner(_cfg(), active_workspaces=[])
    got = [str(p) for p, _st, _ex in scanner.iter_file_entries(ws, apply_exclude=True)]
    assert str(b) in got
    assert str(a) not in got
