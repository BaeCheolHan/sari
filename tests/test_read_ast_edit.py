import json
from pathlib import Path

import pytest

from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.read import execute_read
import sari.mcp.tools.read as read_tool


def _payload(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _hash12(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


class _DummyDB:
    def __init__(self) -> None:
        self._read = None


def test_ast_edit_requires_expected_version_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    db = _DummyDB()

    resp = execute_read(
        {"mode": "ast_edit", "target": str(f), "old_text": "x = 1", "new_text": "x = 2"},
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "INVALID_ARGS"


def test_ast_edit_rejects_version_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    db = _DummyDB()

    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": "deadbeef0000",
            "old_text": "x = 1",
            "new_text": "x = 2",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "VERSION_CONFLICT"


def test_ast_edit_updates_file_and_emits_test_next_call(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    src_dir = ws / "src"
    src_dir.mkdir()
    tests_dir = ws / "tests"
    tests_dir.mkdir()
    f = src_dir / "calc.py"
    f.write_text("def calc():\n    return 1\n", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text("def test_calc():\n    assert True\n", encoding="utf-8")

    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    rid = WorkspaceManager.root_id_for_workspace(str(ws))

    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "old_text": "return 1",
            "new_text": "return 2",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert payload["mode"] == "ast_edit"
    assert payload["path"] == f"{rid}/src/calc.py"
    assert payload["updated"] is True
    assert "return 2" in f.read_text(encoding="utf-8")

    stabilization = payload["meta"]["stabilization"]
    assert stabilization["next_calls"]
    assert stabilization["next_calls"][0]["tool"] == "execute_shell_command"
    assert "pytest -q" in stabilization["next_calls"][0]["arguments"]["command"]
    assert payload["focus_indexing"] == "deferred"
    assert stabilization["warnings"]


def test_ast_edit_triggers_focus_indexing_when_indexer_is_available(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "x.py"
    f.write_text("v = 1\n", encoding="utf-8")
    db = _DummyDB()

    class _Indexer:
        def __init__(self):
            self.called = None

        def index_file(self, path: str):
            self.called = path

    indexer = _Indexer()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "old_text": "v = 1",
            "new_text": "v = 2",
            "__indexer__": indexer,
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert payload["focus_indexing"] == "triggered"
    assert indexer.called


def test_ast_edit_focus_indexing_marks_failed_when_indexer_returns_not_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "x.py"
    f.write_text("v = 1\n", encoding="utf-8")
    db = _DummyDB()

    class _Indexer:
        @staticmethod
        def index_file(_path: str):
            return {"ok": False, "message": "queue full"}

    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "old_text": "v = 1",
            "new_text": "v = 2",
            "__indexer__": _Indexer(),
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert payload["focus_indexing"] == "failed"
    warnings = payload["meta"]["stabilization"]["warnings"]
    assert any("queue full" in w for w in warnings)


def test_ast_edit_symbol_mode_replaces_python_symbol_block(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "mod.py"
    f.write_text(
        "def keep():\n    return 0\n\n"
        "def target_fn():\n    return 1\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "target_fn",
            "new_text": "def target_fn():\n    return 2",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    after = f.read_text(encoding="utf-8")
    assert "def keep():" in after
    assert "return 2" in after
    assert "return 1" not in after


def test_ast_edit_next_calls_prioritizes_symbol_related_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    src = ws / "src"
    src.mkdir()
    tests = ws / "tests"
    tests.mkdir()
    f = src / "service.py"
    f.write_text("def target_fn():\n    return 1\n", encoding="utf-8")
    (tests / "test_target.py").write_text("from src.service import target_fn\n", encoding="utf-8")
    (tests / "test_other.py").write_text("def test_other():\n    assert True\n", encoding="utf-8")

    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "target_fn",
            "new_text": "def target_fn():\n    return 3",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    cmd = payload["meta"]["stabilization"]["next_calls"][0]["arguments"]["command"]
    assert "pytest -q" in cmd
    assert "test_target.py" in cmd


def test_ast_edit_invalid_python_syntax_does_not_write_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "broken.py"
    original = "def f():\n    return 1\n"
    f.write_text(original, encoding="utf-8")
    db = _DummyDB()

    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "f",
            "new_text": "def f(:\n    return 2",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "INVALID_ARGS"
    assert "invalid syntax" in payload["error"]["message"]
    assert f.read_text(encoding="utf-8") == original


def test_ast_edit_next_calls_uses_db_callers_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    src = ws / "src"
    src.mkdir()
    tests = ws / "tests"
    tests.mkdir()
    f = src / "service.py"
    f.write_text("def target_fn():\n    return 1\n", encoding="utf-8")
    caller_test = tests / "test_from_caller.py"
    caller_test.write_text("def test_from_caller():\n    assert True\n", encoding="utf-8")
    db_path = f"{WorkspaceManager.root_id_for_workspace(str(ws))}/tests/test_from_caller.py"

    class _DBWithCallers(_DummyDB):
        class _Conn:
            @staticmethod
            def execute(_sql: str, _params: tuple):
                class _Res:
                    @staticmethod
                    def fetchall():
                        return [(db_path,)]

                return _Res()

        def get_read_connection(self):
            return self._Conn()

    db = _DBWithCallers()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "target_fn",
            "new_text": "def target_fn():\n    return 4",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    cmd = payload["meta"]["stabilization"]["next_calls"][0]["arguments"]["command"]
    assert "test_from_caller.py" in cmd


def test_ast_edit_symbol_mode_replaces_javascript_function_block(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "mod.js"
    f.write_text(
        "function keep() {\n  return 0;\n}\n\n"
        "function targetFn() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "new_text": "function targetFn() {\n  return 2;\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    after = f.read_text(encoding="utf-8")
    assert "function keep()" in after
    assert "return 2;" in after
    assert "return 1;" not in after


def test_ast_edit_focus_indexing_reports_sync_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "sync.py"
    f.write_text("n = 1\n", encoding="utf-8")
    db = _DummyDB()

    class _Indexer:
        def __init__(self):
            self._ticks = 0

        def index_file(self, _path: str):
            return {"ok": True}

        def get_queue_depths(self):
            self._ticks += 1
            if self._ticks < 3:
                return {"fair_queue": 1, "priority_queue": 0, "db_writer": 1}
            return {"fair_queue": 0, "priority_queue": 0, "db_writer": 0}

    indexer = _Indexer()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "old_text": "n = 1",
            "new_text": "n = 2",
            "__indexer__": indexer,
            "sync_timeout_ms": 500,
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert payload["focus_indexing"] == "triggered"
    assert payload["focus_sync_state"] == "complete"


def test_ast_edit_symbol_mode_replaces_go_function_block_via_tree_sitter(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "svc.go"
    f.write_text(
        "package svc\n\n"
        "func keep() int {\n    return 0\n}\n\n"
        "func targetFn() int {\n    return 1\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "sari.mcp.tools.read._tree_sitter_symbol_span",
        lambda _source, _path, sym, symbol_qualname="", symbol_kind="": (7, 9) if sym == "targetFn" else None,
        raising=False,
    )

    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "new_text": "func targetFn() int {\n    return 2\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    after = f.read_text(encoding="utf-8")
    assert "func keep() int" in after
    assert "return 2" in after
    assert "return 1" not in after


@pytest.mark.parametrize(
    ("filename", "before", "symbol", "replacement", "target_span", "keep_probe", "old_probe", "new_probe"),
    [
        (
            "svc.java",
            "class Svc {\n  int keep() { return 0; }\n  int targetFn() { return 1; }\n}\n",
            "targetFn",
            "int targetFn() { return 2; }",
            (3, 3),
            "keep()",
            "return 1;",
            "return 2;",
        ),
        (
            "svc.rs",
            "fn keep() -> i32 {\n    0\n}\n\nfn target_fn() -> i32 {\n    1\n}\n",
            "target_fn",
            "fn target_fn() -> i32 {\n    2\n}",
            (5, 7),
            "fn keep()",
            "\n    1\n",
            "\n    2\n",
        ),
        (
            "svc.kt",
            "class Svc {\n    fun keep(): Int = 0\n    fun targetFn(): Int = 1\n}\n",
            "targetFn",
            "fun targetFn(): Int = 2",
            (3, 3),
            "keep()",
            "Int = 1",
            "Int = 2",
        ),
    ],
)
def test_ast_edit_symbol_mode_replaces_tree_sitter_languages(
    monkeypatch,
    tmp_path,
    filename,
    before,
    symbol,
    replacement,
    target_span,
    keep_probe,
    old_probe,
    new_probe,
):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / filename
    f.write_text(before, encoding="utf-8")
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")

    def _fake_span(_source: str, path: str, sym: str, symbol_qualname: str = "", symbol_kind: str = ""):
        if path.endswith(filename) and sym == symbol:
            return target_span
        return None

    monkeypatch.setattr("sari.mcp.tools.read._tree_sitter_symbol_span", _fake_span, raising=False)

    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": symbol,
            "new_text": replacement,
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    after = f.read_text(encoding="utf-8")
    assert keep_probe in after
    assert new_probe in after
    assert old_probe not in after


def test_tree_sitter_symbol_span_prefers_qualified_name(monkeypatch):
    source = (
        "class A {\n"
        "  int targetFn() { return 1; }\n"
        "}\n"
        "class B {\n"
        "  int targetFn() { return 2; }\n"
        "}\n"
    )

    class _Sym:
        def __init__(self, name, qualname, line, end_line, kind="method"):
            self.name = name
            self.qualname = qualname
            self.line = line
            self.end_line = end_line
            self.kind = kind

    monkeypatch.setattr(
        "sari.mcp.tools.read._extract_tree_sitter_symbols",
        lambda _source, _path: [
            _Sym("targetFn", "A.targetFn", 2, 2, "method"),
            _Sym("targetFn", "B.targetFn", 5, 5, "method"),
        ],
        raising=False,
    )
    span = read_tool._tree_sitter_symbol_span(source, "/tmp/svc.java", "targetFn", symbol_qualname="B.targetFn")
    assert span == (5, 5)


def test_tree_sitter_symbol_span_prefers_kind_hint(monkeypatch):
    source = "fn target() -> i32 { 1 }\nstruct target {}\n"

    class _Sym:
        def __init__(self, name, qualname, line, end_line, kind):
            self.name = name
            self.qualname = qualname
            self.line = line
            self.end_line = end_line
            self.kind = kind

    monkeypatch.setattr(
        "sari.mcp.tools.read._extract_tree_sitter_symbols",
        lambda _source, _path: [
            _Sym("target", "mod.target", 1, 1, "function"),
            _Sym("target", "mod.target", 2, 2, "class"),
        ],
        raising=False,
    )
    span = read_tool._tree_sitter_symbol_span(source, "/tmp/svc.rs", "target", symbol_kind="function")
    assert span == (1, 1)


def test_ast_edit_symbol_mode_forwards_qualname_and_kind_hints(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "svc.go"
    f.write_text(
        "package svc\n\n"
        "func targetFn() int {\n    return 1\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    captured = {}

    def _capture(_source: str, _path: str, symbol: str, symbol_qualname: str = "", symbol_kind: str = ""):
        captured["symbol"] = symbol
        captured["symbol_qualname"] = symbol_qualname
        captured["symbol_kind"] = symbol_kind
        return (3, 5)

    monkeypatch.setattr("sari.mcp.tools.read._tree_sitter_symbol_span", _capture, raising=False)
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "symbol_qualname": "svc.targetFn",
            "symbol_kind": "function",
            "new_text": "func targetFn() int {\n    return 2\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert captured["symbol"] == "targetFn"
    assert captured["symbol_qualname"] == "svc.targetFn"
    assert captured["symbol_kind"] == "function"


def test_ast_edit_symbol_mode_rejects_when_old_text_not_in_selected_symbol_block(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "svc.go"
    f.write_text(
        "package svc\n\n"
        "func targetFn() int {\n    return 1\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "sari.mcp.tools.read._tree_sitter_symbol_span",
        lambda _source, _path, sym, symbol_qualname="", symbol_kind="": (3, 5) if sym == "targetFn" else None,
        raising=False,
    )
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "old_text": "return 999",
            "new_text": "func targetFn() int {\n    return 2\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "SYMBOL_BLOCK_MISMATCH"
    assert "old_text was not found in selected symbol block" in payload["error"]["message"]


@pytest.mark.parametrize(
    ("ext", "lang_module", "source", "symbol"),
    [
        (".java", "tree_sitter_java", "class Svc {\n  int target() { return 1; }\n}\n", "target"),
        (".kt", "tree_sitter_kotlin", "class Svc {\n  fun target(): Int = 1\n}\n", "target"),
        (".rs", "tree_sitter_rust", "fn target() -> i32 {\n    1\n}\n", "target"),
    ],
)
def test_tree_sitter_symbol_span_runtime_smoke_for_languages(ext, lang_module, source, symbol):
    pytest.importorskip("tree_sitter")
    pytest.importorskip(lang_module)
    span = read_tool._tree_sitter_symbol_span(source, f"/tmp/svc{ext}", symbol)
    assert span is not None


def test_ast_edit_rejects_invalid_symbol_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "svc.go"
    f.write_text(
        "package svc\n\n"
        "func targetFn() int {\n    return 1\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "symbol_kind": "banana",
            "new_text": "func targetFn() int {\n    return 2\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "SYMBOL_KIND_INVALID"


def test_ast_edit_symbol_resolution_failure_uses_specific_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "svc.go"
    f.write_text(
        "package svc\n\n"
        "func targetFn() int {\n    return 1\n}\n",
        encoding="utf-8",
    )
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "sari.mcp.tools.read._tree_sitter_symbol_span",
        lambda _source, _path, sym, symbol_qualname="", symbol_kind="": None,
        raising=False,
    )
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": "targetFn",
            "new_text": "func targetFn() int {\n    return 2\n}",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload["isError"] is True
    assert payload["error"]["code"] == "SYMBOL_RESOLUTION_FAILED"


@pytest.mark.parametrize(
    ("ext", "lang_module", "before", "symbol", "replacement", "old_probe", "new_probe"),
    [
        (".java", "tree_sitter_java", "class Svc {\n  int target() { return 1; }\n}\n", "target", "int target() { return 2; }", "return 1;", "return 2;"),
        (".kt", "tree_sitter_kotlin", "class Svc {\n  fun target(): Int = 1\n}\n", "target", "fun target(): Int = 2", "Int = 1", "Int = 2"),
        (".rs", "tree_sitter_rust", "fn target() -> i32 {\n    1\n}\n", "target", "fn target() -> i32 {\n    2\n}", "\n    1\n", "\n    2\n"),
    ],
)
def test_ast_edit_execute_read_runtime_e2e_tree_sitter_languages(
    monkeypatch, tmp_path, ext, lang_module, before, symbol, replacement, old_probe, new_probe
):
    pytest.importorskip("tree_sitter")
    pytest.importorskip(lang_module)
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / f"svc{ext}"
    f.write_text(before, encoding="utf-8")
    db = _DummyDB()
    original = f.read_text(encoding="utf-8")
    resp = execute_read(
        {
            "mode": "ast_edit",
            "target": str(f),
            "expected_version_hash": _hash12(original),
            "symbol": symbol,
            "new_text": replacement,
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    after = f.read_text(encoding="utf-8")
    assert new_probe in after
    assert old_probe not in after
