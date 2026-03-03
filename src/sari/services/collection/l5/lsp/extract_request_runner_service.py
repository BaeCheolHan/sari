"""extract_once 요청 실행 블록을 담당하는 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language


class LspExtractRequestRunnerService:
    """scope/pwarm/broker/doc-symbol 요청의 실행 흐름을 캡슐화한다."""

    def __init__(
        self,
        *,
        resolve_language,
        resolve_lsp_runtime_scope,
        ensure_prewarm,
        get_or_start_with_broker_guard,
        consume_l3_scope_pending_hint,
        acquire_l1_probe_slot,
        request_document_symbols,
        perf_tracer,
        increment_doc_sync_requested,
        increment_doc_sync_accepted,
        increment_doc_sync_legacy_fallback,
    ) -> None:
        self._resolve_language = resolve_language
        self._resolve_lsp_runtime_scope = resolve_lsp_runtime_scope
        self._ensure_prewarm = ensure_prewarm
        self._get_or_start_with_broker_guard = get_or_start_with_broker_guard
        self._consume_l3_scope_pending_hint = consume_l3_scope_pending_hint
        self._acquire_l1_probe_slot = acquire_l1_probe_slot
        self._request_document_symbols = request_document_symbols
        self._perf_tracer = perf_tracer
        self._increment_doc_sync_requested = increment_doc_sync_requested
        self._increment_doc_sync_accepted = increment_doc_sync_accepted
        self._increment_doc_sync_legacy_fallback = increment_doc_sync_legacy_fallback

    def run_request(self, *, repo_root: str, normalized_relative_path: str) -> tuple[Language, list[object]]:
        language = self._resolve_language(normalized_relative_path)
        runtime_scope_root, runtime_relative_path = self._resolve_lsp_runtime_scope(
            repo_root=repo_root,
            normalized_relative_path=normalized_relative_path,
            language=language,
        )
        with self._perf_tracer.span(
            "extract_once.ensure_prewarm",
            phase="l3_extract",
            repo_root=runtime_scope_root,
            language=language.value,
        ):
            self._ensure_prewarm(language=language, repo_root=runtime_scope_root)
        lsp = self._get_or_start_with_broker_guard(
            language=language,
            runtime_scope_root=runtime_scope_root,
            lane="backlog",
            pending_jobs_in_scope=max(
                1,
                self._consume_l3_scope_pending_hint(language=language, runtime_scope_root=runtime_scope_root),
            ),
            request_kind="indexing",
            trace_name="extract_once.get_or_start",
            trace_phase="l3_extract",
        )
        with self._acquire_l1_probe_slot():
            with self._perf_tracer.span(
                "extract_once.document_symbol_request",
                phase="l3_extract",
                repo_root=repo_root,
                language=language.value,
            ):
                self._increment_doc_sync_requested()
                document_symbols_result, sync_hint_accepted = self._request_document_symbols(
                    lsp,
                    runtime_relative_path,
                    sync_with_ls=False,
                )
                if sync_hint_accepted:
                    self._increment_doc_sync_accepted()
                else:
                    self._increment_doc_sync_legacy_fallback()
                return language, list(document_symbols_result.iter_symbols())
