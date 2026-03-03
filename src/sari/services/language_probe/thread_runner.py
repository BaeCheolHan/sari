"""Language probe 스레드/타임아웃 실행기."""

from __future__ import annotations

import threading
from typing import Callable

from sari.core.language.provision_policy import get_lsp_provision_policy
from sari.core.models import LanguageProbeStatusDTO
from sari.services.language_probe.error_classifier import (
    classify_lsp_error_code,
    extract_missing_dependency,
    is_timeout_error,
)
from solidlsp.ls_exceptions import SolidLSPException

_ProbeError = RuntimeError | OSError | ValueError | TypeError | AssertionError | AttributeError | SolidLSPException


class LanguageProbeThreadRunner:
    """단일 언어 probe를 스레드+타임아웃으로 감싼다."""

    def run_with_timeout(
        self,
        *,
        language: str,
        probe_at: str,
        timeout_sec: float,
        task: Callable[[], LanguageProbeStatusDTO],
    ) -> LanguageProbeStatusDTO:
        """task 실행 결과를 timeout/예외 매핑하여 반환한다."""
        result_box: list[LanguageProbeStatusDTO] = []
        error_box: list[_ProbeError] = []
        done = threading.Event()

        def _runner() -> None:
            try:
                result_box.append(task())
            except (RuntimeError, OSError, ValueError, TypeError, AssertionError, AttributeError, SolidLSPException) as exc:  # pragma: no cover - 경계 예외
                error_box.append(exc)
            finally:
                done.set()

        worker = threading.Thread(
            target=_runner,
            name=f"sari-language-probe-{language}",
            daemon=True,
        )
        worker.start()
        finished = done.wait(timeout=timeout_sec)
        policy = get_lsp_provision_policy(language)
        if not finished:
            return LanguageProbeStatusDTO(
                language=language,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_TIMEOUT",
                last_error_message=f"language probe timed out after {timeout_sec:.1f}s: {language}",
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=True,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        if len(error_box) > 0:
            first_error = error_box[0]
            error_message = str(first_error)
            if isinstance(first_error, AssertionError):
                normalized_message = error_message if error_message.strip() != "" else "assertion failed during language probe"
                classified_code = classify_lsp_error_code(code="ERR_LSP_DOCUMENT_SYMBOL_FAILED", message=normalized_message)
                return LanguageProbeStatusDTO(
                    language=language,
                    enabled=True,
                    available=False,
                    last_probe_at=probe_at,
                    last_error_code=classified_code,
                    last_error_message=normalized_message,
                    updated_at=probe_at,
                    symbol_extract_success=False,
                    document_symbol_count=0,
                    path_mapping_ok=False,
                    timeout_occurred=is_timeout_error(code=classified_code, message=normalized_message),
                    recovered_by_restart=False,
                    provisioning_mode=policy.provisioning_mode,
                    missing_dependency=extract_missing_dependency(normalized_message),
                    install_hint=policy.install_hint,
                )
            return LanguageProbeStatusDTO(
                language=language,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LSP_PROBE_INTERNAL",
                last_error_message=error_message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=False,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        return result_box[0]
