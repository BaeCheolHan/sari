from __future__ import annotations

from contextlib import contextmanager

from solidlsp.ls_config import Language

from sari.services.collection.l5.lsp.extract_request_runner_service import LspExtractRequestRunnerService


class _DocSymbols:
    def __init__(self, symbols):
        self._symbols = symbols

    def iter_symbols(self):
        return iter(self._symbols)


class _Tracer:
    @contextmanager
    def span(self, *args, **kwargs):  # noqa: ANN002, ANN003
        yield


@contextmanager
def _slot():
    yield


def test_run_request_executes_pipeline_and_returns_language_and_symbols() -> None:
    calls = {"prewarm": 0, "requested": 0, "accepted": 0, "legacy": 0}

    def _resolve_scope(**kwargs):
        return ("/runtime", "src/a.py")

    def _request_doc(lsp, rel, sync_with_ls=False):
        _ = (lsp, rel, sync_with_ls)
        return _DocSymbols([{"name": "A", "kind": "class"}]), True

    service = LspExtractRequestRunnerService(
        resolve_language=lambda path: Language.PYTHON,
        resolve_lsp_runtime_scope=_resolve_scope,
        ensure_prewarm=lambda **kwargs: calls.__setitem__("prewarm", calls["prewarm"] + 1),
        get_or_start_with_broker_guard=lambda **kwargs: object(),
        consume_l3_scope_pending_hint=lambda **kwargs: 2,
        acquire_l1_probe_slot=_slot,
        request_document_symbols=_request_doc,
        perf_tracer=_Tracer(),
        increment_doc_sync_requested=lambda: calls.__setitem__("requested", calls["requested"] + 1),
        increment_doc_sync_accepted=lambda: calls.__setitem__("accepted", calls["accepted"] + 1),
        increment_doc_sync_legacy_fallback=lambda: calls.__setitem__("legacy", calls["legacy"] + 1),
    )

    lang, symbols = service.run_request(repo_root="/repo", normalized_relative_path="src/a.py")

    assert lang is Language.PYTHON
    assert len(symbols) == 1
    assert calls["prewarm"] == 1
    assert calls["requested"] == 1
    assert calls["accepted"] == 1
    assert calls["legacy"] == 0


def test_run_request_counts_legacy_when_sync_hint_not_accepted() -> None:
    calls = {"accepted": 0, "legacy": 0}

    service = LspExtractRequestRunnerService(
        resolve_language=lambda path: Language.PYTHON,
        resolve_lsp_runtime_scope=lambda **kwargs: ("/runtime", "src/a.py"),
        ensure_prewarm=lambda **kwargs: None,
        get_or_start_with_broker_guard=lambda **kwargs: object(),
        consume_l3_scope_pending_hint=lambda **kwargs: 1,
        acquire_l1_probe_slot=_slot,
        request_document_symbols=lambda lsp, rel, sync_with_ls=False: (_DocSymbols([]), False),
        perf_tracer=_Tracer(),
        increment_doc_sync_requested=lambda: None,
        increment_doc_sync_accepted=lambda: calls.__setitem__("accepted", calls["accepted"] + 1),
        increment_doc_sync_legacy_fallback=lambda: calls.__setitem__("legacy", calls["legacy"] + 1),
    )

    _, _symbols = service.run_request(repo_root="/repo", normalized_relative_path="src/a.py")

    assert calls["accepted"] == 0
    assert calls["legacy"] == 1
