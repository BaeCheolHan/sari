"""검색 오케스트레이터 RRF 랭킹 정책을 검증한다."""

from __future__ import annotations

from sari.core.models import CandidateFileDTO, SearchItemDTO, WorkspaceDTO
from sari.search.candidate_search import CandidateSearchResultDTO
from sari.search.orchestrator import SearchOrchestrator


class _WorkspaceRepo:
    """고정 워크스페이스 목록을 반환하는 테스트 더블이다."""

    def list_all(self) -> list[WorkspaceDTO]:
        """워크스페이스 1개를 반환한다."""
        return [WorkspaceDTO(path="/repo-a", name="repo-a", indexed_at=None, is_active=True)]


class _CandidateService:
    """고정 후보 결과를 반환하는 테스트 더블이다."""

    def filter_workspaces_by_repo(self, workspaces: list[WorkspaceDTO], repo_root: str) -> list[WorkspaceDTO]:
        """repo 필터 정책을 모사한다."""
        return [workspace for workspace in workspaces if workspace.path == repo_root]

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> CandidateSearchResultDTO:
        """고정 후보 파일 목록을 반환한다."""
        del workspaces, query, limit
        return CandidateSearchResultDTO(
            candidates=[
                CandidateFileDTO(repo_root="/repo-a", relative_path="alpha.py", score=10.0, file_hash="h1"),
                CandidateFileDTO(repo_root="/repo-a", relative_path="beta.py", score=5.0, file_hash="h2"),
            ],
            source="tantivy",
            errors=[],
        )


class _SymbolService:
    """고정 심볼 결과를 반환하는 테스트 더블이다."""

    def resolve(self, candidates: list[CandidateFileDTO], query: str, limit: int) -> tuple[list[SearchItemDTO], list[object]]:
        """후보 파일에 대응되는 심볼 결과를 반환한다."""
        del candidates, query, limit
        return (
            [
                SearchItemDTO(
                    item_type="symbol",
                    repo="/repo-a",
                    relative_path="beta.py",
                    score=1.0,
                    source="lsp",
                    name="beta_symbol",
                    kind="12",
                )
            ],
            [],
        )


def test_search_orchestrator_uses_rrf_and_keeps_file_candidates() -> None:
    """심볼이 있어도 파일 후보가 모두 사라지지 않아야 한다."""
    orchestrator = SearchOrchestrator(
        workspace_repo=_WorkspaceRepo(),
        candidate_service=_CandidateService(),
        symbol_service=_SymbolService(),
    )

    result = orchestrator.search(query="beta", limit=5, repo_root="/repo-a")

    item_types = {item.item_type for item in result.items}
    assert "symbol" in item_types
    assert "file" in item_types


class _HighImportanceScorer:
    """importance_score를 과도하게 높게 주는 테스트 더블이다."""

    def __init__(self) -> None:
        self.weights = type("_W", (), {"to_dict": lambda self: {}})()
        self.policy = type("_P", (), {"normalize_mode": "none", "max_importance_boost": 10_000.0})()

    def apply(self, items: list[SearchItemDTO], query: str) -> list[SearchItemDTO]:
        del query
        out: list[SearchItemDTO] = []
        for index, item in enumerate(items):
            out.append(
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=item.score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    content_hash=item.content_hash,
                    rrf_score=item.rrf_score,
                    importance_score=5000.0 if index == len(items) - 1 else 0.0,
                )
            )
        return out


class _HierarchyScorer:
    """계층 점수를 주입하는 테스트 더블이다."""

    def apply(self, items: list[SearchItemDTO], query: str) -> list[SearchItemDTO]:
        """beta_symbol 항목에만 높은 계층 점수를 부여한다."""
        del query
        output: list[SearchItemDTO] = []
        for item in items:
            hierarchy_score = 10.0 if item.name == "beta_symbol" else 0.0
            output.append(
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=item.score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    content_hash=item.content_hash,
                    rrf_score=item.rrf_score,
                    importance_score=item.importance_score,
                    hierarchy_score=hierarchy_score,
                )
            )
        return output


def test_search_orchestrator_blends_scores_to_prevent_importance_takeover() -> None:
    """importance 극단값이 있어도 RRF 상위 relevance가 완전히 무너지지 않아야 한다."""
    orchestrator = SearchOrchestrator(
        workspace_repo=_WorkspaceRepo(),
        candidate_service=_CandidateService(),
        symbol_service=_SymbolService(),
        importance_scorer=_HighImportanceScorer(),
    )

    result = orchestrator.search(query="beta", limit=5, repo_root="/repo-a")

    assert len(result.items) >= 2
    assert result.items[0].blended_score >= result.items[1].blended_score
    assert result.items[0].base_rrf_score >= result.items[1].base_rrf_score


def test_search_orchestrator_applies_hierarchy_score_in_blend() -> None:
    """계층 점수가 높은 심볼이 결합 점수에 반영되어야 한다."""
    orchestrator = SearchOrchestrator(
        workspace_repo=_WorkspaceRepo(),
        candidate_service=_CandidateService(),
        symbol_service=_SymbolService(),
        hierarchy_scorer=_HierarchyScorer(),
    )

    result = orchestrator.search(query="beta", limit=5, repo_root="/repo-a")

    assert len(result.items) >= 1
    assert result.items[0].name == "beta_symbol"
    assert result.items[0].hierarchy_score > 0.0
