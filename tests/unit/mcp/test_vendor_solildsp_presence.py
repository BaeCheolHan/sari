"""Serena solidlsp 벤더 이식 여부를 검증한다."""

from pathlib import Path


EXPECTED_LANGUAGE_SERVER_FILES = {
    "al_language_server.py",
    "bash_language_server.py",
    "ccls_language_server.py",
    "clangd_language_server.py",
    "clojure_lsp.py",
    "common.py",
    "csharp_language_server.py",
    "dart_language_server.py",
    "eclipse_jdtls.py",
    "elm_language_server.py",
    "erlang_language_server.py",
    "fortran_language_server.py",
    "fsharp_language_server.py",
    "gopls.py",
    "groovy_language_server.py",
    "haskell_language_server.py",
    "intelephense.py",
    "jedi_server.py",
    "julia_server.py",
    "kotlin_language_server.py",
    "lua_ls.py",
    "marksman.py",
    "matlab_language_server.py",
    "nixd_ls.py",
    "omnisharp.py",
    "pascal_server.py",
    "perl_language_server.py",
    "powershell_language_server.py",
    "pyright_server.py",
    "r_language_server.py",
    "regal_server.py",
    "ruby_lsp.py",
    "rust_analyzer.py",
    "scala_language_server.py",
    "solargraph.py",
    "sourcekit_lsp.py",
    "taplo_server.py",
    "terraform_ls.py",
    "typescript_language_server.py",
    "vts_language_server.py",
    "vue_language_server.py",
    "yaml_language_server.py",
    "zls.py",
}


def test_all_serena_language_server_files_are_vendored() -> None:
    """Serena LSP 서버 구현 파일이 모두 포함되었는지 확인한다."""
    base = Path(__file__).resolve().parents[3] / "src" / "solidlsp" / "language_servers"
    actual = {path.name for path in base.glob("*.py")}

    missing = EXPECTED_LANGUAGE_SERVER_FILES - actual
    assert not missing, f"누락된 LSP 서버 파일: {sorted(missing)}"
