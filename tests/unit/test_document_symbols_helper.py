"""document_symbols helper 호환 계약을 검증한다."""

from __future__ import annotations

from sari.lsp.document_symbols import request_document_symbols_with_optional_sync


class _Result:
    def __init__(self, token: str) -> None:
        self.token = token


def test_request_document_symbols_uses_sync_kwarg_when_supported() -> None:
    calls: list[tuple[str, bool]] = []

    class _Server:
        def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _Result:
            calls.append((relative_path, bool(sync_with_ls)))
            return _Result("ok")

    result, accepted = request_document_symbols_with_optional_sync(
        _Server(),
        "a.py",
        sync_with_ls=False,
    )
    assert accepted is True
    assert result.token == "ok"
    assert calls == [("a.py", False)]


def test_request_document_symbols_falls_back_without_sync_for_legacy_signature() -> None:
    calls: list[str] = []

    class _Server:
        def request_document_symbols(self, relative_path: str) -> _Result:
            calls.append(relative_path)
            return _Result("legacy")

    result, accepted = request_document_symbols_with_optional_sync(
        _Server(),
        "b.py",
        sync_with_ls=False,
    )
    assert accepted is False
    assert result.token == "legacy"
    assert calls == ["b.py"]


def test_request_document_symbols_falls_back_on_runtime_typeerror_message() -> None:
    calls: list[str] = []

    class _Server:
        def request_document_symbols(self, relative_path: str, **kwargs: object) -> _Result:
            if "sync_with_ls" in kwargs:
                raise TypeError("CSharpLanguageServer.request_document_symbols: `sync_with_ls` is not present.")
            calls.append(relative_path)
            return _Result("fallback")

    result, accepted = request_document_symbols_with_optional_sync(
        _Server(),
        "c.py",
        sync_with_ls=False,
    )
    assert accepted is False
    assert result.token == "fallback"
    assert calls == ["c.py"]
