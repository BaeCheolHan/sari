"""Language probe LSP 호출/워밍업 본체."""

from __future__ import annotations

import logging
import threading

from sari.core.exceptions import DaemonError
from sari.core.language_registry import LanguageSupportEntry
from sari.core.lsp_provision_policy import get_lsp_provision_policy
from sari.core.models import LanguageProbeStatusDTO
from sari.lsp.document_symbols import request_document_symbols_with_optional_sync
from sari.lsp.hub import LspHub
from sari.services.language_probe_error_classifier import (
    classify_lsp_error_code,
    extract_error_code,
    extract_missing_dependency,
    is_recovered_by_restart,
    is_timeout_error,
)
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

log = logging.getLogger(__name__)


class LanguageProbeWorker:
    """단일 언어 probe 본체와 Go 워밍업을 담당한다."""

    def __init__(
        self,
        *,
        lsp_hub: LspHub,
        lsp_request_timeout_sec: float,
        go_warmup_enabled: bool,
        go_warmup_timeout_sec: float,
    ) -> None:
        self._lsp_hub = lsp_hub
        self._lsp_request_timeout_sec = max(0.1, float(lsp_request_timeout_sec))
        self._go_warmup_enabled = bool(go_warmup_enabled)
        self._go_warmup_timeout_sec = max(0.1, float(go_warmup_timeout_sec))
        self._go_warmed_repo_roots: set[str] = set()
        self._go_warmup_lock = threading.Lock()

    def probe_single_language_impl(
        self,
        *,
        repo_root: str,
        entry: LanguageSupportEntry,
        sample_path: str | None,
        probe_at: str,
    ) -> LanguageProbeStatusDTO:
        """단일 언어 probe 본체를 수행한다."""
        policy = get_lsp_provision_policy(entry.language.value)
        if sample_path is None:
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code="ERR_LANGUAGE_SAMPLE_NOT_FOUND",
                last_error_message=f"sample file not found for {entry.language.value}",
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
        try:
            lsp = self._lsp_hub.get_or_start(language=entry.language, repo_root=repo_root)
            if entry.language == Language.GO:
                self._warm_up_go_lsp_once(repo_root=repo_root, lsp=lsp, sample_path=sample_path)
            symbols_result, _sync_hint_accepted = request_document_symbols_with_optional_sync(
                lsp,
                sample_path,
                sync_with_ls=False,
            )
            symbol_items = list(symbols_result.iter_symbols())
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=True,
                last_probe_at=probe_at,
                last_error_code=None,
                last_error_message=None,
                updated_at=probe_at,
                symbol_extract_success=True,
                document_symbol_count=len(symbol_items),
                path_mapping_ok=True,
                timeout_occurred=False,
                recovered_by_restart=False,
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=None,
                install_hint=policy.install_hint,
            )
        except DaemonError as exc:
            classified_code = classify_lsp_error_code(code=exc.context.code, message=exc.context.message)
            timeout_occurred = is_timeout_error(code=classified_code, message=exc.context.message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=exc.context.message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=timeout_occurred,
                recovered_by_restart=is_recovered_by_restart(exc.context.message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=extract_missing_dependency(exc.context.message),
                install_hint=policy.install_hint,
            )
        except SolidLSPException as exc:
            error_message = str(exc)
            code = extract_error_code(error_message, default_code="ERR_LSP_DOCUMENT_SYMBOL_FAILED")
            classified_code = classify_lsp_error_code(code=code, message=error_message)
            timeout_occurred = is_timeout_error(code=classified_code, message=error_message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=error_message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=timeout_occurred,
                recovered_by_restart=is_recovered_by_restart(error_message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=extract_missing_dependency(error_message),
                install_hint=policy.install_hint,
            )
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            error_message = str(exc)
            classified_code = classify_lsp_error_code(code="ERR_LSP_DOCUMENT_SYMBOL_FAILED", message=error_message)
            return LanguageProbeStatusDTO(
                language=entry.language.value,
                enabled=True,
                available=False,
                last_probe_at=probe_at,
                last_error_code=classified_code,
                last_error_message=error_message,
                updated_at=probe_at,
                symbol_extract_success=False,
                document_symbol_count=0,
                path_mapping_ok=False,
                timeout_occurred=is_timeout_error(code=classified_code, message=error_message),
                recovered_by_restart=is_recovered_by_restart(error_message),
                provisioning_mode=policy.provisioning_mode,
                missing_dependency=extract_missing_dependency(error_message),
                install_hint=policy.install_hint,
            )

    def warmup_context(self) -> dict[str, object]:
        """스레드 러너가 사용할 워밍업 관련 문맥을 반환한다."""
        return {
            "lsp_request_timeout_sec": self._lsp_request_timeout_sec,
            "go_warmup_timeout_sec": self._go_warmup_timeout_sec,
        }

    def _warm_up_go_lsp_once(self, repo_root: str, lsp: SolidLanguageServer, sample_path: str) -> None:
        """Go LSP 첫 기동 비용을 흡수하기 위해 warm-up 요청을 1회 실행한다."""
        if not self._go_warmup_enabled:
            return
        should_warmup = False
        with self._go_warmup_lock:
            if repo_root not in self._go_warmed_repo_roots:
                self._go_warmed_repo_roots.add(repo_root)
                should_warmup = True
        if not should_warmup:
            return
        set_timeout = getattr(lsp, "set_request_timeout", None)
        try:
            if callable(set_timeout):
                set_timeout(self._go_warmup_timeout_sec)
            symbols_result, _sync_hint_accepted = request_document_symbols_with_optional_sync(
                lsp,
                sample_path,
                sync_with_ls=False,
            )
            _ = list(symbols_result.iter_symbols())
        except (RuntimeError, OSError, ValueError, TypeError, AssertionError, AttributeError, DaemonError, SolidLSPException) as exc:
            log.debug("Go warm-up failed(repo=%s): %s", repo_root, exc)
        finally:
            if callable(set_timeout):
                set_timeout(self._lsp_request_timeout_sec)

