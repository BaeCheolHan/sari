from __future__ import annotations

from solidlsp.ls_config import Language
from pytest import MonkeyPatch

from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend


def test_extract_once_uses_symbol_normalizer_subinterp_path() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        backend._symbol_normalizer_executor_mode = "subinterp"
        backend._symbol_normalizer_subinterp_min_symbols = 1

        class _Executor:
            def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN003
                _ = (fn, args, kwargs)

                class _Future:
                    def result(self, timeout=None):  # noqa: ANN001
                        _ = timeout
                        return [{"name": "Sub", "kind": "class", "line": 1, "end_line": 1}]

                return _Future()

            def shutdown(self, wait=True):  # noqa: ANN001
                _ = wait

        backend._symbol_normalizer_subinterp_executor = _Executor()  # type: ignore[assignment]
        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, [{"name": "Inline", "kind": "class"}])
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("inline path should not be used"))
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="a.py")  # noqa: SLF001
        assert result.error_message is None
        assert len(result.symbols) == 1
        assert result.symbols[0]["name"] == "Sub"
    finally:
        backend.shutdown_probe_executor()


def test_symbol_normalizer_executor_settings_honor_constructor_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SARI_L5_SYMBOL_NORMALIZER_EXECUTOR_MODE", raising=False)
    monkeypatch.delenv("SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_WORKERS", raising=False)
    monkeypatch.delenv("SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_MIN_SYMBOLS", raising=False)

    backend = SolidLspExtractionBackend(
        hub=object(),  # type: ignore[arg-type]
        symbol_normalizer_executor_mode="subinterp",
        symbol_normalizer_subinterp_workers=3,
        symbol_normalizer_subinterp_min_symbols=77,
    )
    try:
        assert backend._symbol_normalizer_executor_mode in {"subinterp", "inline"}
        assert backend._symbol_normalizer_subinterp_workers == 3
        assert backend._symbol_normalizer_subinterp_min_symbols == 77
    finally:
        backend.shutdown_probe_executor()


def test_extract_once_falls_back_to_inline_when_subinterp_fails() -> None:
    backend = SolidLspExtractionBackend(hub=object())  # type: ignore[arg-type]
    try:
        backend._symbol_normalizer_executor_mode = "subinterp"
        backend._symbol_normalizer_subinterp_min_symbols = 1

        class _Executor:
            def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN003
                _ = (fn, args, kwargs)

                class _Future:
                    def result(self, timeout=None):  # noqa: ANN001
                        _ = timeout
                        raise RuntimeError("boom")

                return _Future()

            def shutdown(self, wait=True):  # noqa: ANN001
                _ = wait

        backend._symbol_normalizer_subinterp_executor = _Executor()  # type: ignore[assignment]
        backend._extract_request_runner_service.run_request = (  # type: ignore[method-assign]
            lambda **kwargs: (Language.PYTHON, [{"name": "Inline", "kind": "class"}])
        )
        backend._symbol_normalizer_service.normalize_symbols = (  # type: ignore[method-assign]
            lambda **kwargs: [{"name": "Inline", "kind": "class", "line": 0, "end_line": 0}]
        )

        result = backend._extract_once(repo_root="/repo", normalized_relative_path="a.py")  # noqa: SLF001
        assert result.error_message is None
        assert len(result.symbols) == 1
        assert result.symbols[0]["name"] == "Inline"
    finally:
        backend.shutdown_probe_executor()
