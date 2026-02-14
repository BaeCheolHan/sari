from sari.mcp.tools import doctor


def test_check_lsp_runtime_enabled_by_default(monkeypatch):
    monkeypatch.delenv("SARI_LSP_ON_DEMAND", raising=False)
    res = doctor._check_lsp_runtime()
    assert res["name"] == "LSP Runtime"
    assert res["passed"] is True
    assert "enabled" in str(res["error"])


def test_check_lsp_runtime_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SARI_LSP_ON_DEMAND", "0")
    res = doctor._check_lsp_runtime()
    assert res["name"] == "LSP Runtime"
    assert res["passed"] is False
    assert "disabled" in str(res["error"])

