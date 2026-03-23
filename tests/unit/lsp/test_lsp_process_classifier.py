from __future__ import annotations

from sari.lsp.process_classifier import classify_language_from_command, is_residual_lsp_command


def test_is_residual_lsp_command_matches_known_signatures() -> None:
    assert is_residual_lsp_command("/tmp/.solidlsp/jdtls/bin/java") is True
    assert is_residual_lsp_command("typescript-language-server --stdio") is False
    assert is_residual_lsp_command("pyrefly lsp") is False
    assert is_residual_lsp_command("/usr/bin/python worker.py") is False


def test_classify_language_from_command_maps_known_lsp_commands() -> None:
    assert classify_language_from_command("/tmp/.solidlsp/jdtls/bin/java") == "java"
    assert classify_language_from_command("typescript-language-server --stdio") == "typescript"
    assert classify_language_from_command("pyrefly lsp") == "python"
    assert classify_language_from_command("gopls serve") == "go"
    assert classify_language_from_command("rust-analyzer") == "rust"
    assert classify_language_from_command("node some-random.js") == "unknown"
