"""검색 중요도 정책과 벡터 재정렬 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path
import time

from sari.core.models import CollectedFileL1DTO, SearchItemDTO, now_iso8601_utc
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.vector_embedding_repository import VectorEmbeddingRepository
from sari.db.schema import init_schema
from sari.search.importance_scorer import ImportanceScorer, ImportanceScorePolicyDTO
from sari.search.vector_reranker import VectorConfigDTO, VectorIndexSink, VectorReranker


def test_importance_scorer_applies_fan_in_kind_path_and_recency(tmp_path: Path) -> None:
    """중요도 점수는 fan-in/kind/path/recency 정책을 반영해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    lsp_repo = LspToolDataRepository(db_path)

    now_iso = now_iso8601_utc()
    current_mtime_ns = time.time_ns()
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_root="/repo",
            relative_path="src/core/main.py",
            absolute_path="/repo/src/core/main.py",
            repo_label="repo",
            mtime_ns=current_mtime_ns,
            size_bytes=128,
            content_hash="h-main",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="TOOL_READY",
        )
    )
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_root="/repo",
            relative_path="src/other/a.py",
            absolute_path="/repo/src/other/a.py",
            repo_label="repo",
            mtime_ns=current_mtime_ns,
            size_bytes=64,
            content_hash="h-a",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="TOOL_READY",
        )
    )
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_root="/repo",
            relative_path="src/other/b.py",
            absolute_path="/repo/src/other/b.py",
            repo_label="repo",
            mtime_ns=current_mtime_ns,
            size_bytes=64,
            content_hash="h-b",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="TOOL_READY",
        )
    )

    lsp_repo.replace_relations(
        repo_root="/repo",
        relative_path="src/other/a.py",
        content_hash="h-a",
        relations=[{"from_symbol": "caller_a", "to_symbol": "Main", "line": 1}],
        created_at=now_iso,
    )
    lsp_repo.replace_relations(
        repo_root="/repo",
        relative_path="src/other/b.py",
        content_hash="h-b",
        relations=[{"from_symbol": "caller_b", "to_symbol": "Main", "line": 2}],
        created_at=now_iso,
    )

    scorer = ImportanceScorer(file_repo=file_repo, lsp_repo=lsp_repo)
    scored = scorer.apply(
        items=[
            SearchItemDTO(
                item_type="symbol",
                repo="/repo",
                relative_path="src/core/main.py",
                score=1.0,
                source="lsp",
                name="Main",
                kind="class",
                content_hash="h-main",
                rrf_score=1.0,
                final_score=1.0,
            )
        ],
        query="main",
    )

    assert len(scored) == 1
    assert scored[0].importance_score > 1.0
    assert scored[0].final_score == scored[0].rrf_score


def test_vector_reranker_boosts_semantically_similar_item(tmp_path: Path) -> None:
    """벡터 재정렬은 질의와 유사한 파일 점수를 가산해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = VectorEmbeddingRepository(db_path)
    config = VectorConfigDTO(enabled=True, candidate_k=10, rerank_k=5, blend_weight=0.5)
    sink = VectorIndexSink(repository=repo, config=config)
    reranker = VectorReranker(repository=repo, config=config)

    sink.upsert_file_embedding(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h-a",
        content_text="def login_user(): return token",
    )
    sink.upsert_file_embedding(
        repo_root="/repo",
        relative_path="src/b.py",
        content_hash="h-b",
        content_text="draw chart axis plot figure",
    )

    reranked = reranker.rerank(
        items=[
            SearchItemDTO(
                item_type="file",
                repo="/repo",
                relative_path="src/a.py",
                score=1.0,
                source="candidate",
                name=None,
                kind=None,
                content_hash="h-a",
                rrf_score=1.0,
                final_score=1.0,
            ),
            SearchItemDTO(
                item_type="file",
                repo="/repo",
                relative_path="src/b.py",
                score=1.1,
                source="candidate",
                name=None,
                kind=None,
                content_hash="h-b",
                rrf_score=1.1,
                final_score=1.1,
            ),
        ],
        query="login token auth",
        limit=5,
    )

    assert len(reranked) == 2
    assert reranked[0].relative_path == "src/a.py"
    assert reranked[0].vector_score is not None


def test_importance_scorer_applies_log_normalize_and_cap(tmp_path: Path) -> None:
    """중요도 점수는 정규화와 상한 정책을 적용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    lsp_repo = LspToolDataRepository(db_path)
    now_iso = now_iso8601_utc()
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_root="/repo",
            relative_path="src/core/mega.py",
            absolute_path="/repo/src/core/mega.py",
            repo_label="repo",
            mtime_ns=time.time_ns(),
            size_bytes=1024,
            content_hash="h-mega",
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="TOOL_READY",
        )
    )
    relation_rows = [{"from_symbol": f"caller_{index}", "to_symbol": "MegaClass", "line": index + 1} for index in range(300)]
    lsp_repo.replace_relations(
        repo_root="/repo",
        relative_path="src/core/rels.py",
        content_hash="h-rels",
        relations=relation_rows,
        created_at=now_iso,
    )
    scorer = ImportanceScorer(
        file_repo=file_repo,
        lsp_repo=lsp_repo,
        policy=ImportanceScorePolicyDTO(normalize_mode="log1p", max_importance_boost=50.0),
    )
    scored = scorer.apply(
        items=[
            SearchItemDTO(
                item_type="symbol",
                repo="/repo",
                relative_path="src/core/mega.py",
                score=1.0,
                source="lsp",
                name="MegaClass",
                kind="class",
                content_hash="h-mega",
                rrf_score=1.0,
                final_score=1.0,
            )
        ],
        query="mega",
    )
    assert len(scored) == 1
    assert scored[0].importance_score <= 50.0


def test_vector_reranker_skips_boost_when_similarity_below_threshold(tmp_path: Path) -> None:
    """유사도가 임계치 미만이면 벡터 가산을 적용하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = VectorEmbeddingRepository(db_path)
    config = VectorConfigDTO(
        enabled=True,
        candidate_k=10,
        rerank_k=5,
        blend_weight=0.9,
        min_similarity_threshold=0.95,
        max_vector_boost=0.1,
    )
    sink = VectorIndexSink(repository=repo, config=config)
    reranker = VectorReranker(repository=repo, config=config)
    sink.upsert_file_embedding(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h-a",
        content_text="def login_user(): return token",
    )
    reranked = reranker.rerank(
        items=[
            SearchItemDTO(
                item_type="file",
                repo="/repo",
                relative_path="src/a.py",
                score=1.0,
                source="candidate",
                name=None,
                kind=None,
                content_hash="h-a",
                rrf_score=1.0,
                final_score=1.0,
            )
        ],
        query="login auth",
        limit=5,
    )
    assert len(reranked) == 1
    assert reranked[0].score == 1.0
    assert reranked[0].final_score == 1.0
    assert reranked[0].vector_score is not None


def test_vector_reranker_does_not_overwrite_score_fields(tmp_path: Path) -> None:
    """벡터 단계는 신호만 기록하고 score/final_score를 덮어쓰지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = VectorEmbeddingRepository(db_path)
    config = VectorConfigDTO(enabled=True, candidate_k=5, rerank_k=5, blend_weight=0.8)
    sink = VectorIndexSink(repository=repo, config=config)
    reranker = VectorReranker(repository=repo, config=config)
    sink.upsert_file_embedding(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h-a",
        content_text="auth login token session",
    )
    items = [
        SearchItemDTO(
            item_type="file",
            repo="/repo",
            relative_path="src/a.py",
            score=2.5,
            source="candidate",
            name=None,
            kind=None,
            content_hash="h-a",
            rrf_score=2.5,
            final_score=2.5,
        )
    ]
    reranked = reranker.rerank(items=items, query="auth token", limit=5)
    assert len(reranked) == 1
    assert reranked[0].score == 2.5
    assert reranked[0].final_score == 2.5
    assert reranked[0].vector_score is not None


def test_vector_reranker_skips_for_too_short_query(tmp_path: Path) -> None:
    """토큰 수가 부족한 질의는 벡터 재정렬을 생략해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = VectorEmbeddingRepository(db_path)
    config = VectorConfigDTO(enabled=True, min_token_count_for_rerank=3)
    reranker = VectorReranker(repository=repo, config=config)
    items = [
        SearchItemDTO(
            item_type="file",
            repo="/repo",
            relative_path="src/a.py",
            score=1.2,
            source="candidate",
            name=None,
            kind=None,
            content_hash="h-a",
            rrf_score=1.2,
            final_score=1.2,
        )
    ]
    reranked = reranker.rerank(items=items, query="auth", limit=5)
    assert reranked == items


def test_importance_scorer_uses_memory_and_persistent_cache() -> None:
    """fan-in 점수는 메모리/영속 캐시를 통해 중복 조회를 줄여야 한다."""

    class _FakeFileRepo:
        """recency 계산용 파일 저장소 더블이다."""

        def get_file(self, repo_root: str, relative_path: str):  # type: ignore[no-untyped-def]
            del repo_root, relative_path
            return None

    class _FakeLspRepo:
        """fan-in 조회 호출 수를 추적하는 LSP 저장소 더블이다."""

        def __init__(self) -> None:
            self.calls = 0

        def count_distinct_callers(self, repo_root: str, symbol_name: str) -> int:
            del repo_root, symbol_name
            self.calls += 1
            return 7

    class _FakeCacheRepo:
        """영속 캐시 저장소 더블이다."""

        def __init__(self) -> None:
            self.data: dict[tuple[str, str, int], int] = {}

        def get_reference_count(self, repo_root: str, symbol_name: str, revision_epoch: int = 0) -> int | None:
            return self.data.get((repo_root, symbol_name, revision_epoch))

        def upsert_reference_count(
            self,
            repo_root: str,
            symbol_name: str,
            reference_count: int,
            updated_at: str,
            revision_epoch: int = 0,
        ) -> None:
            del updated_at
            self.data[(repo_root, symbol_name, revision_epoch)] = reference_count

    item = SearchItemDTO(
        item_type="symbol",
        repo="/repo",
        relative_path="src/core/a.py",
        score=1.0,
        source="lsp",
        name="Alpha",
        kind="class",
        content_hash="h-a",
        rrf_score=1.0,
        final_score=1.0,
    )
    lsp_repo = _FakeLspRepo()
    cache_repo = _FakeCacheRepo()
    scorer = ImportanceScorer(file_repo=_FakeFileRepo(), lsp_repo=lsp_repo, cache_repo=cache_repo, cache_ttl_sec=120)

    scorer.apply(items=[item], query="alpha")
    scorer.apply(items=[item], query="alpha")
    assert lsp_repo.calls == 1

    scorer2 = ImportanceScorer(file_repo=_FakeFileRepo(), lsp_repo=lsp_repo, cache_repo=cache_repo, cache_ttl_sec=120)
    scorer2.apply(items=[item], query="alpha")
    assert lsp_repo.calls == 1
