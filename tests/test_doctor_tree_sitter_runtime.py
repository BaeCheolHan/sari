from sari.mcp.tools import doctor


def test_check_tree_sitter_language_runtime_reports_partial_coverage(monkeypatch):
    class _FakeEngine:
        enabled = True

        @staticmethod
        def _get_language(name: str):
            return object() if name in {"python", "javascript", "go"} else None

    monkeypatch.setattr("sari.core.parsers.ast_engine.ASTEngine", _FakeEngine)
    monkeypatch.setattr(
        "sari.core.parsers.factory.ParserFactory._lang_map",
        {
            ".py": "python",
            ".js": "javascript",
            ".go": "go",
            ".java": "java",
            ".rs": "rust",
            ".kt": "kotlin",
        },
        raising=False,
    )

    res = doctor._check_tree_sitter_language_runtime()
    assert res["name"] == "Tree-sitter Language Runtime"
    assert res["passed"] is False
    assert "available=go, javascript, python" in str(res["error"])
    assert "missing=java, kotlin, rust" in str(res["error"])


def test_check_tree_sitter_language_runtime_reports_full_coverage(monkeypatch):
    class _FakeEngine:
        enabled = True

        @staticmethod
        def _get_language(_name: str):
            return object()

    monkeypatch.setattr("sari.core.parsers.ast_engine.ASTEngine", _FakeEngine)
    monkeypatch.setattr(
        "sari.core.parsers.factory.ParserFactory._lang_map",
        {
            ".py": "python",
            ".js": "javascript",
        },
        raising=False,
    )

    res = doctor._check_tree_sitter_language_runtime()
    assert res["name"] == "Tree-sitter Language Runtime"
    assert res["passed"] is True
    assert "all configured parser languages have runtime support" in str(res["error"])
