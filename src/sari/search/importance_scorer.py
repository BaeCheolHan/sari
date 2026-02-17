"""기존 sari 중요도 정책(결정론적 4축)을 구현한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import threading
import time

from sari.core.models import SearchItemDTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.symbol_importance_repository import SymbolImportanceRepository
from sari.core.models import now_iso8601_utc


@dataclass(frozen=True)
class ImportanceWeightsDTO:
    """중요도 점수 가중치 정책을 표현한다."""

    kind_class: float = 600.0
    kind_function: float = 500.0
    kind_interface: float = 450.0
    kind_method: float = 350.0
    fan_in_weight: float = 24.0
    filename_exact_bonus: float = 1.0
    core_path_bonus: float = 0.6
    noisy_path_penalty: float = -0.7
    code_ext_bonus: float = 0.3
    noisy_ext_penalty: float = -1.0
    recency_24h_multiplier: float = 1.5
    recency_7d_multiplier: float = 1.3
    recency_30d_multiplier: float = 1.1

    def to_dict(self) -> dict[str, float]:
        """직렬화 가능한 정책 딕셔너리를 반환한다."""
        return {
            "kind_class": self.kind_class,
            "kind_function": self.kind_function,
            "kind_interface": self.kind_interface,
            "kind_method": self.kind_method,
            "fan_in_weight": self.fan_in_weight,
            "filename_exact_bonus": self.filename_exact_bonus,
            "core_path_bonus": self.core_path_bonus,
            "noisy_path_penalty": self.noisy_path_penalty,
            "code_ext_bonus": self.code_ext_bonus,
            "noisy_ext_penalty": self.noisy_ext_penalty,
            "recency_24h_multiplier": self.recency_24h_multiplier,
            "recency_7d_multiplier": self.recency_7d_multiplier,
            "recency_30d_multiplier": self.recency_30d_multiplier,
        }


@dataclass(frozen=True)
class ImportanceScorePolicyDTO:
    """중요도 점수 후처리 정책을 표현한다."""

    normalize_mode: str = "log1p"
    max_importance_boost: float = 200.0

    def to_dict(self) -> dict[str, float | str]:
        """직렬화 가능한 정책 딕셔너리를 반환한다."""
        return {
            "normalize_mode": self.normalize_mode,
            "max_importance_boost": self.max_importance_boost,
        }


class ImportanceScorer:
    """호출관계/심볼종류/경로/최신성 4축 중요도 점수를 계산한다."""

    _DEFAULT_CORE_PATH_TOKENS = ("src", "app", "core")
    _DEFAULT_NOISY_PATH_TOKENS = ("test", "tests", "build", "dist")
    _DEFAULT_CODE_EXTENSIONS = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".kts", ".go", ".rs")
    _DEFAULT_NOISY_EXTENSIONS = (".lock", ".min.js")
    _KIND_BY_CODE: dict[str, str] = {
        "5": "class",
        "6": "method",
        "11": "interface",
        "12": "function",
    }

    def __init__(
        self,
        file_repo: FileCollectionRepository,
        lsp_repo: LspToolDataRepository,
        cache_repo: SymbolImportanceRepository | None = None,
        weights: ImportanceWeightsDTO | None = None,
        policy: ImportanceScorePolicyDTO | None = None,
        cache_ttl_sec: int = 30,
        core_path_tokens: tuple[str, ...] | None = None,
        noisy_path_tokens: tuple[str, ...] | None = None,
        code_extensions: tuple[str, ...] | None = None,
        noisy_extensions: tuple[str, ...] | None = None,
    ) -> None:
        """저장소와 중요도 가중치 정책을 주입한다."""
        self._file_repo = file_repo
        self._lsp_repo = lsp_repo
        self._cache_repo = cache_repo
        self._weights = weights if weights is not None else ImportanceWeightsDTO()
        self._policy = policy if policy is not None else ImportanceScorePolicyDTO()
        self._cache_ttl_sec = max(1, cache_ttl_sec)
        self._memory_cache: dict[tuple[str, str], tuple[int, float]] = {}
        self._cache_lock = threading.RLock()
        self._core_path_tokens = tuple(part.lower() for part in (core_path_tokens or self._DEFAULT_CORE_PATH_TOKENS))
        self._noisy_path_tokens = tuple(part.lower() for part in (noisy_path_tokens or self._DEFAULT_NOISY_PATH_TOKENS))
        self._code_extensions = {suffix.lower() for suffix in (code_extensions or self._DEFAULT_CODE_EXTENSIONS)}
        self._noisy_extensions = {suffix.lower() for suffix in (noisy_extensions or self._DEFAULT_NOISY_EXTENSIONS)}

    @property
    def weights(self) -> ImportanceWeightsDTO:
        """현재 중요도 정책 가중치를 반환한다."""
        return self._weights

    @property
    def policy(self) -> ImportanceScorePolicyDTO:
        """현재 중요도 후처리 정책을 반환한다."""
        return self._policy

    def apply(self, items: list[SearchItemDTO], query: str) -> list[SearchItemDTO]:
        """검색 아이템에 중요도 점수를 계산해 메타 필드로 반영한다."""
        normalized_query = query.strip().lower()
        raw_scores = [self._compute_importance(item=item, normalized_query=normalized_query) for item in items]
        normalized_scores = self._normalize_scores(raw_scores)
        scored: list[SearchItemDTO] = []
        for index, item in enumerate(items):
            base_score = item.rrf_score if item.rrf_score > 0.0 else item.score
            importance_score = normalized_scores[index]
            scored.append(
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=base_score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    content_hash=item.content_hash,
                    rrf_score=base_score,
                    importance_score=importance_score,
                    base_rrf_score=item.base_rrf_score if item.base_rrf_score > 0.0 else base_score,
                    importance_norm_score=item.importance_norm_score,
                    vector_norm_score=item.vector_norm_score,
                    hierarchy_score=item.hierarchy_score,
                    hierarchy_norm_score=item.hierarchy_norm_score,
                    symbol_key=item.symbol_key,
                    parent_symbol_key=item.parent_symbol_key,
                    depth=item.depth,
                    container_name=item.container_name,
                    ranking_components=item.ranking_components,
                    vector_score=item.vector_score,
                    blended_score=item.blended_score,
                    final_score=base_score,
                )
            )
        return scored

    def _compute_importance(self, item: SearchItemDTO, normalized_query: str) -> float:
        """4축 가중치를 결합한 중요도 점수를 계산한다."""
        kind_score = self._kind_score(item.kind)
        fan_in_score = self._fan_in_score(item)
        path_score = self._path_score(relative_path=item.relative_path, normalized_query=normalized_query)
        recency_multiplier = self._recency_multiplier(repo_root=item.repo, relative_path=item.relative_path)
        composite = kind_score + fan_in_score + path_score
        return composite * recency_multiplier

    def _normalize_scores(self, raw_scores: list[float]) -> list[float]:
        """정책에 따라 중요도 점수 스케일을 보정한다."""
        if len(raw_scores) == 0:
            return []
        mode = self._policy.normalize_mode.strip().lower()
        normalized: list[float] = []
        if mode == "none":
            normalized = list(raw_scores)
        elif mode == "minmax":
            minimum = min(raw_scores)
            maximum = max(raw_scores)
            if math.isclose(minimum, maximum):
                normalized = [0.0 for _ in raw_scores]
            else:
                scale = maximum - minimum
                normalized = [((value - minimum) / scale) for value in raw_scores]
        else:
            normalized = [math.copysign(math.log1p(abs(value)), value) for value in raw_scores]
        cap = abs(self._policy.max_importance_boost)
        if cap <= 0.0:
            return normalized
        return [max(-cap, min(cap, value)) for value in normalized]

    def _kind_score(self, kind: str | None) -> float:
        """심볼 종류에 따른 기본 점수를 계산한다."""
        if kind is None:
            return 0.0
        normalized = kind.strip().lower()
        mapped = self._KIND_BY_CODE.get(normalized, normalized)
        if mapped == "class":
            return self._weights.kind_class
        if mapped == "function":
            return self._weights.kind_function
        if mapped == "interface":
            return self._weights.kind_interface
        if mapped == "method":
            return self._weights.kind_method
        return 0.0

    def _fan_in_score(self, item: SearchItemDTO) -> float:
        """호출 관계(Fan-in) 점수를 계산한다."""
        if item.name is None or item.name.strip() == "":
            return 0.0
        reference_count = self._resolve_reference_count(repo_root=item.repo, symbol_name=item.name)
        return float(reference_count) * self._weights.fan_in_weight

    def _resolve_reference_count(self, repo_root: str, symbol_name: str) -> int:
        """fan-in 참조 수를 메모리/DB 캐시 우선으로 조회한다."""
        cache_key = (repo_root, symbol_name)
        now_ts = time.monotonic()
        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)
        if cached is not None:
            cached_count, expire_at = cached
            if now_ts < expire_at:
                return cached_count

        if self._cache_repo is not None:
            persisted = self._cache_repo.get_reference_count(repo_root=repo_root, symbol_name=symbol_name)
            if persisted is not None:
                with self._cache_lock:
                    self._memory_cache[cache_key] = (persisted, now_ts + float(self._cache_ttl_sec))
                return persisted

        computed = self._lsp_repo.count_distinct_callers(repo_root=repo_root, symbol_name=symbol_name)
        with self._cache_lock:
            self._memory_cache[cache_key] = (computed, now_ts + float(self._cache_ttl_sec))
        if self._cache_repo is not None:
            self._cache_repo.upsert_reference_count(
                repo_root=repo_root,
                symbol_name=symbol_name,
                reference_count=computed,
                updated_at=now_iso8601_utc(),
            )
        return computed

    def _path_score(self, relative_path: str, normalized_query: str) -> float:
        """파일 경로/파일명/확장자 기반 점수를 계산한다."""
        path = Path(relative_path)
        score = 0.0
        file_stem = path.stem.lower()
        if normalized_query != "" and file_stem == normalized_query:
            score += self._weights.filename_exact_bonus

        lowered_parts = [part.lower() for part in path.parts]
        if any(part in self._core_path_tokens for part in lowered_parts):
            score += self._weights.core_path_bonus
        if any(part in self._noisy_path_tokens for part in lowered_parts):
            score += self._weights.noisy_path_penalty

        full_lower = relative_path.lower()
        if full_lower.endswith(".min.js"):
            score += self._weights.noisy_ext_penalty
            return score
        suffix = path.suffix.lower()
        if suffix in self._code_extensions:
            score += self._weights.code_ext_bonus
        if suffix in self._noisy_extensions:
            score += self._weights.noisy_ext_penalty
        return score

    def _recency_multiplier(self, repo_root: str, relative_path: str) -> float:
        """파일 최신성 기반 배수를 계산한다."""
        file_row = self._file_repo.get_file(repo_root=repo_root, relative_path=relative_path)
        if file_row is None:
            return 1.0
        updated_at = datetime.fromtimestamp(float(file_row.mtime_ns) / 1_000_000_000.0, tz=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
        if age.total_seconds() <= 24.0 * 3600.0:
            return self._weights.recency_24h_multiplier
        if age.total_seconds() <= 7.0 * 24.0 * 3600.0:
            return self._weights.recency_7d_multiplier
        if age.total_seconds() <= 30.0 * 24.0 * 3600.0:
            return self._weights.recency_30d_multiplier
        return 1.0
