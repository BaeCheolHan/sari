"""LSP 기반 심볼 해석 레이어를 구현한다."""

from __future__ import annotations

import logging
from typing import Protocol

from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import CandidateFileDTO, SearchErrorDTO, SearchItemDTO
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_location_to_repo_relative, normalize_repo_relative_path
from sari.search.error_policy import classify_search_error
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

log = logging.getLogger(__name__)


class LspQueryBackend(Protocol):
    """심볼 조회 백엔드 프로토콜을 정의한다."""

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
    ) -> list[SearchItemDTO]:
        """파일 단위 documentSymbol 조회 결과를 반환한다."""


class SolidLspQueryBackend:
    """solidlsp를 이용한 실제 심볼 조회 백엔드다."""

    def __init__(self, hub: LspHub) -> None:
        """LSP Hub 의존성을 주입한다."""
        self._hub = hub

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
    ) -> list[SearchItemDTO]:
        """documentSymbol 결과를 SearchItem DTO로 변환한다."""
        try:
            self._hub.ensure_healthy(language=language, repo_root=repo_root)  # type: ignore[attr-defined]
        except DaemonError as exc:
            if exc.context.code != "ERR_LSP_UNHEALTHY":
                raise
            self._hub.restart_if_unhealthy(language=language, repo_root=repo_root)  # type: ignore[attr-defined]
        lsp = self._hub.get_or_start(language=language, repo_root=repo_root)
        normalized_query = query.strip().lower()
        items: list[SearchItemDTO] = []
        for relative_path in relative_paths:
            normalized_relative_path = normalize_repo_relative_path(relative_path)
            try:
                raw_symbols = list(lsp.request_document_symbols(normalized_relative_path).iter_symbols())
            except SolidLSPException as exc:
                message = str(exc)
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
                items.append(
                    SearchItemDTO(
                        item_type="symbol",
                        repo=repo_root,
                        relative_path=str(resolved_relative_path),
                        score=1.0,
                        source="lsp",
                        name=symbol_name,
                        kind=str(symbol_kind) if symbol_kind is not None else None,
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
                    return items[:limit]
        return items


class SymbolResolveService:
    """후보 파일을 LSP 심볼로 해석하는 서비스다."""

    def __init__(
        self,
        hub: LspHub,
        cache_repo: SymbolCacheRepository,
        backend: LspQueryBackend | None = None,
    ) -> None:
        """LSP Hub/캐시 저장소/백엔드를 주입한다."""
        self._hub = hub
        self._cache_repo = cache_repo
        self._backend = backend or SolidLspQueryBackend(hub)

    def resolve(self, candidates: list[CandidateFileDTO], query: str, limit: int) -> tuple[list[SearchItemDTO], list[SearchErrorDTO]]:
        """후보 파일 기반으로 LSP 심볼 결과를 생성한다."""
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
            )
            if cached_items is not None:
                for cached in cached_items:
                    items.append(cached)
                    if len(items) >= limit:
                        return items[:limit], errors
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
            return items[:limit], errors

        miss_path_set = {(candidate.repo_root, candidate.relative_path) for candidate in miss_candidates}
        miss_map = {(candidate.repo_root, candidate.relative_path): candidate for candidate in miss_candidates}

        per_path_items: dict[tuple[str, str], list[SearchItemDTO]] = {}
        for grouped_key, relative_paths in grouped_languages.items():
            repo_root, language = grouped_key
            try:
                resolved = self._query_symbols(
                    repo_root=repo_root,
                    language=language,
                    relative_paths=relative_paths,
                    query=query,
                    limit=limit,
                )
            except DaemonError as exc:
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
            self._cache_repo.upsert_items(
                repo_root=candidate.repo_root,
                relative_path=candidate.relative_path,
                query=query,
                file_hash=candidate.file_hash,
                items=materialized_items,
            )
            for item in materialized_items:
                items.append(item)
                if len(items) >= limit:
                    return items[:limit], errors

        return items[:limit], errors

    def _query_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
    ) -> list[SearchItemDTO]:
        """백엔드 호환 모드(document/workspace)를 모두 지원해 심볼을 조회한다."""
        if hasattr(self._backend, "query_document_symbols"):
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
