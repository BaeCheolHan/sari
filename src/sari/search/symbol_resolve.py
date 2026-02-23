"""LSP 기반 심볼 해석 레이어를 구현한다."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import CandidateFileDTO, SearchErrorDTO, SearchItemDTO
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.lsp.document_symbols import request_document_symbols_with_optional_sync
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_location_to_repo_relative, normalize_repo_relative_path
from sari.search.error_policy import classify_search_error
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class SymbolInfoStats:
    """심볼 상세 정보 조회 통계를 표현한다."""

    include_info_requested: bool = False
    symbol_info_budget_sec: float = 0.0
    symbol_info_requested_count: int = 0
    symbol_info_budget_exceeded_count: int = 0
    symbol_info_skipped_count: int = 0


class LspQueryBackend(Protocol):
    """심볼 조회 백엔드 프로토콜을 정의한다."""

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
        include_info: bool = False,
        symbol_info_budget_sec: float = 0.0,
    ) -> list[SearchItemDTO]:
        """파일 단위 documentSymbol 조회 결과를 반환한다."""


class SolidLspQueryBackend:
    """solidlsp를 이용한 실제 심볼 조회 백엔드다."""

    def __init__(self, hub: LspHub) -> None:
        """LSP Hub 의존성을 주입한다."""
        self._hub = hub
        self._last_query_stats = SymbolInfoStats()

    @property
    def last_query_stats(self) -> SymbolInfoStats:
        """직전 query_document_symbols 호출 통계를 반환한다."""
        return self._last_query_stats

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
        include_info: bool = False,
        symbol_info_budget_sec: float = 0.0,
    ) -> list[SearchItemDTO]:
        """documentSymbol 결과를 SearchItem DTO로 변환한다."""
        normalized_budget = max(0.0, float(symbol_info_budget_sec))
        symbol_info_requested_count = 0
        symbol_info_budget_exceeded_count = 0
        symbol_info_skipped_count = 0
        info_start_monotonic = time.monotonic()
        budget_exceeded = False
        try:
            self._hub.ensure_healthy(language=language, repo_root=repo_root)  # type: ignore[attr-defined]
        except DaemonError as exc:
            if exc.context.code != "ERR_LSP_UNHEALTHY":
                raise
            self._hub.restart_if_unhealthy(language=language, repo_root=repo_root)  # type: ignore[attr-defined]
        try:
            lsp = self._hub.get_or_start(language=language, repo_root=repo_root, request_kind="interactive")
        except TypeError:
            # 이전 테스트 더블/구현체 호환을 위한 positional fallback이다.
            lsp = self._hub.get_or_start(language=language, repo_root=repo_root)  # type: ignore[call-arg]
        normalized_query = query.strip().lower()
        items: list[SearchItemDTO] = []
        for relative_path in relative_paths:
            normalized_relative_path = normalize_repo_relative_path(relative_path)
            try:
                document_symbols_result, _sync_hint_accepted = request_document_symbols_with_optional_sync(
                    lsp,
                    normalized_relative_path,
                    sync_with_ls=False,
                )
                raw_symbols = list(document_symbols_result.iter_symbols())
            except SolidLSPException as exc:
                message = str(exc)
                if "ERR_LSP_INTERACTIVE_TIMEOUT" in message:
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_LSP_INTERACTIVE_TIMEOUT",
                            message=f"인터랙티브 LSP 타임아웃(path={normalized_relative_path}): {message}",
                        )
                    ) from exc
                if "ERR_LSP_SYNC_OPEN_FAILED" in message:
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_LSP_SYNC_OPEN_FAILED",
                            message=f"LSP 문서 open 동기화 실패(path={normalized_relative_path}): {message}",
                        )
                    ) from exc
                if "ERR_LSP_SYNC_CHANGE_FAILED" in message:
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_LSP_SYNC_CHANGE_FAILED",
                            message=f"LSP 문서 change 동기화 실패(path={normalized_relative_path}): {message}",
                        )
                    ) from exc
                raise DaemonError(
                    ErrorContext(
                        code="ERR_LSP_DOCUMENT_SYMBOL_FAILED",
                        message=f"documentSymbol 요청 실패(path={normalized_relative_path}): {message}",
                    )
                ) from exc
            for symbol in raw_symbols:
                if not isinstance(symbol, dict):
                    continue
                symbol_name = symbol.get("name")
                if not isinstance(symbol_name, str):
                    continue
                if normalized_query != "" and normalized_query not in symbol_name.lower():
                    continue
                resolved_relative_path = normalized_relative_path
                location = symbol.get("location")
                if isinstance(location, dict):
                    try:
                        resolved_relative_path = normalize_location_to_repo_relative(
                            location=location,
                            fallback_relative_path=normalized_relative_path,
                            repo_root=repo_root,
                        )
                    except ValidationError:
                        resolved_relative_path = normalized_relative_path
                symbol_kind = symbol.get("kind")
                symbol_key = symbol.get("symbol_key")
                parent_symbol_key = symbol.get("parent_symbol_key")
                depth = symbol.get("depth")
                container_name = symbol.get("container_name")
                symbol_info: str | None = None
                if include_info:
                    if budget_exceeded:
                        symbol_info_skipped_count += 1
                    else:
                        now_monotonic = time.monotonic()
                        if normalized_budget > 0.0 and (now_monotonic - info_start_monotonic) >= normalized_budget:
                            budget_exceeded = True
                            symbol_info_budget_exceeded_count += 1
                            symbol_info_skipped_count += 1
                        else:
                            symbol_info_requested_count += 1
                            symbol_info = self._request_symbol_info(
                                lsp=lsp,
                                fallback_relative_path=normalized_relative_path,
                                symbol=symbol,
                            )
                            if normalized_budget > 0.0 and (time.monotonic() - info_start_monotonic) >= normalized_budget:
                                budget_exceeded = True
                                symbol_info_budget_exceeded_count += 1
                items.append(
                    SearchItemDTO(
                        item_type="symbol",
                        repo=repo_root,
                        relative_path=str(resolved_relative_path),
                        score=1.0,
                        source="lsp",
                        name=symbol_name,
                        kind=str(symbol_kind) if symbol_kind is not None else None,
                        symbol_info=symbol_info,
                        symbol_key=str(symbol_key) if isinstance(symbol_key, str) and symbol_key.strip() != "" else None,
                        parent_symbol_key=(
                            str(parent_symbol_key)
                            if isinstance(parent_symbol_key, str) and parent_symbol_key.strip() != ""
                            else None
                        ),
                        depth=int(depth) if isinstance(depth, int) else 0,
                        container_name=(
                            str(container_name) if isinstance(container_name, str) and container_name.strip() != "" else None
                        ),
                    )
                )
                if len(items) >= limit:
                    self._last_query_stats = SymbolInfoStats(
                        include_info_requested=bool(include_info),
                        symbol_info_budget_sec=normalized_budget,
                        symbol_info_requested_count=symbol_info_requested_count,
                        symbol_info_budget_exceeded_count=symbol_info_budget_exceeded_count,
                        symbol_info_skipped_count=symbol_info_skipped_count,
                    )
                    return items[:limit]
        self._last_query_stats = SymbolInfoStats(
            include_info_requested=bool(include_info),
            symbol_info_budget_sec=normalized_budget,
            symbol_info_requested_count=symbol_info_requested_count,
            symbol_info_budget_exceeded_count=symbol_info_budget_exceeded_count,
            symbol_info_skipped_count=symbol_info_skipped_count,
        )
        return items

    def _request_symbol_info(
        self,
        *,
        lsp: object,
        fallback_relative_path: str,
        symbol: dict[str, object],
    ) -> str | None:
        request_hover = getattr(lsp, "request_hover", None)
        if not callable(request_hover):
            return None
        location = symbol.get("location")
        if not isinstance(location, dict):
            return None
        relative_path = fallback_relative_path
        relative_from_location = location.get("relativePath")
        if isinstance(relative_from_location, str) and relative_from_location.strip() != "":
            relative_path = normalize_repo_relative_path(relative_from_location)
        range_data = location.get("range")
        if not isinstance(range_data, dict):
            return None
        start_data = range_data.get("start")
        if not isinstance(start_data, dict):
            return None
        line = int(start_data.get("line", 0))
        column = int(start_data.get("character", 0))
        try:
            hover = request_hover(relative_path, line, column)
        except (SolidLSPException, RuntimeError, OSError, ValueError, TypeError):
            return None
        return self._extract_hover_text(hover)

    @staticmethod
    def _extract_hover_text(hover: object) -> str | None:
        if not isinstance(hover, dict):
            return None
        contents = hover.get("contents")
        if isinstance(contents, str):
            text = contents.strip()
            return text if text != "" else None
        if isinstance(contents, dict):
            value = contents.get("value")
            if isinstance(value, str):
                text = value.strip()
                return text if text != "" else None
        if isinstance(contents, list):
            collected: list[str] = []
            for item in contents:
                if isinstance(item, str) and item.strip() != "":
                    collected.append(item.strip())
                    continue
                if isinstance(item, dict):
                    value = item.get("value")
                    if isinstance(value, str) and value.strip() != "":
                        collected.append(value.strip())
            if len(collected) > 0:
                return "\n".join(collected)
        return None


class SymbolResolveService:
    """후보 파일을 LSP 심볼로 해석하는 서비스다."""

    def __init__(
        self,
        hub: LspHub,
        cache_repo: SymbolCacheRepository,
        backend: LspQueryBackend | None = None,
        lsp_fallback_mode: str = "normal",
        include_info_default: bool = False,
        symbol_info_budget_sec: float = 10.0,
        lsp_pressure_guard_enabled: bool = True,
        lsp_pressure_pending_threshold: int = 1,
        lsp_pressure_timeout_threshold: int = 1,
        lsp_pressure_rejected_threshold: int = 1,
        lsp_recent_failure_cooldown_sec: float = 5.0,
    ) -> None:
        """LSP Hub/캐시 저장소/백엔드를 주입한다."""
        self._hub = hub
        self._cache_repo = cache_repo
        self._backend = backend or SolidLspQueryBackend(hub)
        normalized_mode = lsp_fallback_mode.strip().lower()
        self._lsp_fallback_mode = "strict" if normalized_mode == "strict" else "normal"
        self._include_info_default = bool(include_info_default)
        self._symbol_info_budget_sec = max(0.0, float(symbol_info_budget_sec))
        self._lsp_pressure_guard_enabled = bool(lsp_pressure_guard_enabled)
        self._lsp_pressure_pending_threshold = max(0, int(lsp_pressure_pending_threshold))
        self._lsp_pressure_timeout_threshold = max(0, int(lsp_pressure_timeout_threshold))
        self._lsp_pressure_rejected_threshold = max(0, int(lsp_pressure_rejected_threshold))
        self._lsp_recent_failure_cooldown_sec = max(0.0, float(lsp_recent_failure_cooldown_sec))
        self._recent_failure_until_by_scope: dict[tuple[str, Language], float] = {}
        self._last_fallback_reason: str | None = None
        self._last_info_stats = SymbolInfoStats(
            include_info_requested=self._include_info_default,
            symbol_info_budget_sec=self._symbol_info_budget_sec,
        )

    @property
    def last_info_stats(self) -> SymbolInfoStats:
        """직전 resolve 호출의 상세 정보 조회 통계를 반환한다."""
        return self._last_info_stats

    @property
    def last_fallback_reason(self) -> str | None:
        """직전 resolve 호출에서 적용된 fallback 차단 이유를 반환한다."""
        return self._last_fallback_reason

    def resolve(
        self,
        candidates: list[CandidateFileDTO],
        query: str,
        limit: int,
        include_info: bool | None = None,
        symbol_info_budget_sec: float | None = None,
    ) -> tuple[list[SearchItemDTO], list[SearchErrorDTO]]:
        """후보 파일 기반으로 LSP 심볼 결과를 생성한다."""
        include_info_effective = self._include_info_default if include_info is None else bool(include_info)
        budget_effective = self._symbol_info_budget_sec if symbol_info_budget_sec is None else max(0.0, float(symbol_info_budget_sec))
        cache_variant = "detail" if include_info_effective else "list"
        self._last_fallback_reason = None
        stats_requested_count = 0
        stats_budget_exceeded_count = 0
        stats_skipped_count = 0
        items: list[SearchItemDTO] = []
        errors: list[SearchErrorDTO] = []

        miss_candidates: list[CandidateFileDTO] = []
        grouped_languages: dict[tuple[str, Language], list[str]] = {}

        for candidate in candidates:
            cached_items = self._cache_repo.get_cached_items(
                repo_root=candidate.repo_root,
                relative_path=candidate.relative_path,
                query=query,
                file_hash=candidate.file_hash,
                cache_variant=cache_variant,
            )
            if cached_items is not None:
                for cached in cached_items:
                    items.append(cached)
                    if len(items) >= limit:
                        self._last_info_stats = SymbolInfoStats(
                            include_info_requested=include_info_effective,
                            symbol_info_budget_sec=budget_effective,
                            symbol_info_requested_count=stats_requested_count,
                            symbol_info_budget_exceeded_count=stats_budget_exceeded_count,
                            symbol_info_skipped_count=stats_skipped_count,
                        )
                        return items[:limit], errors
                continue
            if self._lsp_fallback_mode == "strict":
                self._last_fallback_reason = "strict_cache_only"
                errors.append(
                    SearchErrorDTO(
                        code="ERR_LSP_FALLBACK_BLOCKED",
                        message=(
                            "캐시 미스에서 LSP fallback이 차단되었습니다: "
                            f"repo={candidate.repo_root}, path={candidate.relative_path}"
                        ),
                        severity=classify_search_error("ERR_LSP_FALLBACK_BLOCKED"),
                        origin="symbol_resolve",
                    )
                )
                continue

            miss_candidates.append(candidate)
            try:
                language = self._hub.resolve_language(candidate.relative_path)
            except DaemonError as exc:
                log.warning(
                    "LSP 언어 매핑 실패(repo=%s,path=%s,query=%s): %s",
                    candidate.repo_root,
                    candidate.relative_path,
                    query,
                    exc.context.message,
                )
                errors.append(
                    SearchErrorDTO(
                        code=exc.context.code,
                        message=exc.context.message,
                        severity=classify_search_error(exc.context.code),
                        origin="symbol_resolve",
                    )
                )
                continue
            grouped_languages.setdefault((candidate.repo_root, language), []).append(candidate.relative_path)

        if len(miss_candidates) == 0:
            self._last_info_stats = SymbolInfoStats(
                include_info_requested=include_info_effective,
                symbol_info_budget_sec=budget_effective,
                symbol_info_requested_count=stats_requested_count,
                symbol_info_budget_exceeded_count=stats_budget_exceeded_count,
                symbol_info_skipped_count=stats_skipped_count,
            )
            return items[:limit], errors

        miss_path_set = {(candidate.repo_root, candidate.relative_path) for candidate in miss_candidates}
        miss_map = {(candidate.repo_root, candidate.relative_path): candidate for candidate in miss_candidates}

        per_path_items: dict[tuple[str, str], list[SearchItemDTO]] = {}
        for grouped_key, relative_paths in grouped_languages.items():
            repo_root, language = grouped_key
            if self._is_recent_failure_cooldown_active(repo_root=repo_root, language=language):
                self._last_fallback_reason = "recent_failure_cooldown"
                errors.append(
                    SearchErrorDTO(
                        code="ERR_LSP_FALLBACK_BLOCKED",
                        message=(
                            "최근 LSP 실패 cooldown으로 fallback이 차단되었습니다: "
                            f"repo={repo_root}, language={language.value}"
                        ),
                        severity=classify_search_error("ERR_LSP_FALLBACK_BLOCKED"),
                        origin="symbol_resolve",
                    )
                )
                continue
            if self._is_interactive_pressure_high():
                self._last_fallback_reason = "interactive_pressure"
                errors.append(
                    SearchErrorDTO(
                        code="ERR_LSP_FALLBACK_BLOCKED",
                        message=(
                            "interactive 압력 보호모드로 fallback이 차단되었습니다: "
                            f"repo={repo_root}, language={language.value}"
                        ),
                        severity=classify_search_error("ERR_LSP_FALLBACK_BLOCKED"),
                        origin="symbol_resolve",
                    )
                )
                continue
            try:
                resolved = self._query_symbols(
                    repo_root=repo_root,
                    language=language,
                    relative_paths=relative_paths,
                    query=query,
                    limit=limit,
                    include_info=include_info_effective,
                    symbol_info_budget_sec=budget_effective,
                )
                backend_stats = getattr(self._backend, "last_query_stats", None)
                if isinstance(backend_stats, SymbolInfoStats):
                    stats_requested_count += int(backend_stats.symbol_info_requested_count)
                    stats_budget_exceeded_count += int(backend_stats.symbol_info_budget_exceeded_count)
                    stats_skipped_count += int(backend_stats.symbol_info_skipped_count)
                self._clear_recent_failure_cooldown(repo_root=repo_root, language=language)
            except DaemonError as exc:
                self._mark_recent_failure_cooldown(repo_root=repo_root, language=language, error_code=exc.context.code)
                log.warning("LSP 심볼 조회 도메인 실패(repo=%s,language=%s,query=%s): %s", repo_root, language.value, query, exc.context.message)
                errors.append(
                    SearchErrorDTO(
                        code=exc.context.code,
                        message=exc.context.message,
                        severity=classify_search_error(exc.context.code),
                        origin="symbol_resolve",
                    )
                )
                continue
            except ValidationError as exc:
                log.warning("LSP URI 파싱 실패(repo=%s,language=%s,query=%s): %s", repo_root, language.value, query, exc.context.message)
                errors.append(
                    SearchErrorDTO(
                        code=exc.context.code,
                        message=exc.context.message,
                        severity=classify_search_error(exc.context.code),
                        origin="symbol_resolve",
                    )
                )
                continue
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                self._mark_recent_failure_cooldown(repo_root=repo_root, language=language, error_code="ERR_LSP_QUERY_FAILED")
                # 운영에서 원인 추적이 가능하도록 예외 메시지를 명시적으로 포함한다.
                log.exception("LSP 심볼 조회 실패(repo=%s, language=%s, query=%s): %s", repo_root, language.value, query, exc)
                code = "ERR_LSP_QUERY_FAILED"
                errors.append(
                    SearchErrorDTO(
                        code=code,
                        message=f"LSP 심볼 조회 중 오류가 발생했습니다: {exc}",
                        severity=classify_search_error(code),
                        origin="symbol_resolve",
                    )
                )
                continue

            for item in resolved:
                path_key = (item.repo, item.relative_path)
                if path_key not in miss_path_set:
                    continue
                bucket = per_path_items.setdefault(path_key, [])
                bucket.append(item)

        for path_key, candidate in miss_map.items():
            path_items = per_path_items.get(path_key, [])
            materialized_items = [
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=item.score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    symbol_info=item.symbol_info,
                    content_hash=candidate.file_hash,
                    rrf_score=item.rrf_score,
                    importance_score=item.importance_score,
                    hierarchy_score=item.hierarchy_score,
                    hierarchy_norm_score=item.hierarchy_norm_score,
                    symbol_key=item.symbol_key,
                    parent_symbol_key=item.parent_symbol_key,
                    depth=item.depth,
                    container_name=item.container_name,
                    ranking_components=item.ranking_components,
                    vector_score=item.vector_score,
                    final_score=item.final_score,
                )
                for item in path_items
            ]
            if len(materialized_items) > 0:
                self._cache_repo.upsert_items(
                    repo_root=candidate.repo_root,
                    relative_path=candidate.relative_path,
                    query=query,
                    file_hash=candidate.file_hash,
                    items=materialized_items,
                    cache_variant=cache_variant,
                )
            for item in materialized_items:
                items.append(item)
                if len(items) >= limit:
                    self._last_info_stats = SymbolInfoStats(
                        include_info_requested=include_info_effective,
                        symbol_info_budget_sec=budget_effective,
                        symbol_info_requested_count=stats_requested_count,
                        symbol_info_budget_exceeded_count=stats_budget_exceeded_count,
                        symbol_info_skipped_count=stats_skipped_count,
                    )
                    return items[:limit], errors

        self._last_info_stats = SymbolInfoStats(
            include_info_requested=include_info_effective,
            symbol_info_budget_sec=budget_effective,
            symbol_info_requested_count=stats_requested_count,
            symbol_info_budget_exceeded_count=stats_budget_exceeded_count,
            symbol_info_skipped_count=stats_skipped_count,
        )
        return items[:limit], errors

    def _is_interactive_pressure_high(self) -> bool:
        if not self._lsp_pressure_guard_enabled:
            return False
        get_pressure = getattr(self._hub, "get_interactive_pressure", None)
        if not callable(get_pressure):
            return False
        try:
            pressure = get_pressure()
        except (RuntimeError, OSError, ValueError, TypeError):
            return False
        pending = int(pressure.get("pending_interactive", 0))
        timeout_count = int(pressure.get("interactive_timeout_count", 0))
        rejected_count = int(pressure.get("interactive_rejected_count", 0))
        if self._lsp_pressure_pending_threshold > 0 and pending >= self._lsp_pressure_pending_threshold:
            return True
        if self._lsp_pressure_timeout_threshold > 0 and timeout_count >= self._lsp_pressure_timeout_threshold:
            return True
        if self._lsp_pressure_rejected_threshold > 0 and rejected_count >= self._lsp_pressure_rejected_threshold:
            return True
        return False

    def _is_recent_failure_cooldown_active(self, *, repo_root: str, language: Language) -> bool:
        if self._lsp_recent_failure_cooldown_sec <= 0.0:
            return False
        key = (repo_root, language)
        expires_at = self._recent_failure_until_by_scope.get(key)
        if expires_at is None:
            return False
        now = time.monotonic()
        if now < expires_at:
            return True
        self._recent_failure_until_by_scope.pop(key, None)
        return False

    def _mark_recent_failure_cooldown(self, *, repo_root: str, language: Language, error_code: str) -> None:
        if self._lsp_recent_failure_cooldown_sec <= 0.0:
            return
        if error_code not in {
            "ERR_LSP_INTERACTIVE_TIMEOUT",
            "ERR_LSP_START_TIMEOUT",
            "ERR_LSP_QUERY_FAILED",
            "ERR_LSP_DOCUMENT_SYMBOL_FAILED",
            "ERR_LSP_SYNC_OPEN_FAILED",
            "ERR_LSP_SYNC_CHANGE_FAILED",
            "ERR_LSP_UNAVAILABLE",
        }:
            return
        key = (repo_root, language)
        self._recent_failure_until_by_scope[key] = time.monotonic() + self._lsp_recent_failure_cooldown_sec

    def _clear_recent_failure_cooldown(self, *, repo_root: str, language: Language) -> None:
        key = (repo_root, language)
        self._recent_failure_until_by_scope.pop(key, None)

    def _query_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
        include_info: bool = False,
        symbol_info_budget_sec: float = 0.0,
    ) -> list[SearchItemDTO]:
        """백엔드 호환 모드(document/workspace)를 모두 지원해 심볼을 조회한다."""
        if hasattr(self._backend, "query_document_symbols"):
            try:
                return self._backend.query_document_symbols(
                    repo_root=repo_root,
                    language=language,
                    relative_paths=relative_paths,
                    query=query,
                    limit=limit,
                    include_info=include_info,
                    symbol_info_budget_sec=symbol_info_budget_sec,
                )
            except TypeError:
                return self._backend.query_document_symbols(
                    repo_root=repo_root,
                    language=language,
                    relative_paths=relative_paths,
                    query=query,
                    limit=limit,
                )
        # 이전 테스트 더블 호환을 위한 workspace 쿼리 fallback이다.
        if hasattr(self._backend, "query_workspace_symbol"):
            queried = self._backend.query_workspace_symbol(  # type: ignore[attr-defined]
                repo_root=repo_root,
                language=language,
                query=query,
            )
            return queried[:limit]
        raise ValueError("symbol backend contract is invalid")
