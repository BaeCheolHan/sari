from __future__ import annotations

from solidlsp.ls_exceptions import SolidLSPException

from sari.services.collection.lsp_extract_error_mapper import LspExtractErrorMapper


def test_maps_workspace_mismatch_error() -> None:
    mapper = LspExtractErrorMapper()
    err = mapper.map_solid_exception(
        repo_root="/repo",
        normalized_relative_path="a.py",
        exc=SolidLSPException("No workspace contains /repo/a.py"),
    )
    assert err.startswith("ERR_LSP_WORKSPACE_MISMATCH:")


def test_maps_sync_open_change_errors() -> None:
    mapper = LspExtractErrorMapper()
    open_err = mapper.map_solid_exception(
        repo_root="/repo",
        normalized_relative_path="a.py",
        exc=SolidLSPException("ERR_LSP_SYNC_OPEN_FAILED: boom"),
    )
    change_err = mapper.map_solid_exception(
        repo_root="/repo",
        normalized_relative_path="a.py",
        exc=SolidLSPException("ERR_LSP_SYNC_CHANGE_FAILED: boom"),
    )
    assert open_err.startswith("ERR_LSP_SYNC_OPEN_FAILED:")
    assert change_err.startswith("ERR_LSP_SYNC_CHANGE_FAILED:")


def test_maps_default_document_symbol_error() -> None:
    mapper = LspExtractErrorMapper()
    err = mapper.map_solid_exception(
        repo_root="/repo",
        normalized_relative_path="a.py",
        exc=SolidLSPException("random"),
    )
    assert err.startswith("ERR_LSP_DOCUMENT_SYMBOL_FAILED:")


def test_maps_generic_exception_to_korean_message() -> None:
    mapper = LspExtractErrorMapper()
    err = mapper.map_generic_exception(RuntimeError("x"))
    assert err == "LSP 추출 실패: x"
