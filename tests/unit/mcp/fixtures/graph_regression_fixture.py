"""MCP graph regression 테스트용 고정 fixture."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphRegressionFixture:
    """심볼/관계 fixture 묶음."""

    repo_root: str
    relative_path: str
    content_hash: str
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]


def build_graph_regression_fixture(repo_root: str) -> GraphRegressionFixture:
    """symbol lookup 편차 재현용 fixture를 생성한다."""
    return GraphRegressionFixture(
        repo_root=repo_root,
        relative_path="src/sari/db/repositories/lsp_tool_data_repository.py",
        content_hash="h-graph-regression",
        symbols=[
            {"name": "LspToolDataRepository", "kind": "Class", "line": 10, "end_line": 400},
            {"name": "replace_file_data_many", "kind": "Function", "line": 120, "end_line": 260},
        ],
        relations=[
            {
                "from_symbol": "run_installed_freshdb_smoke",
                "to_symbol": "LspToolDataRepository.replace_file_data_many",
                "line": 77,
                "caller_relative_path": "tools/ci/run_installed_freshdb_smoke.sh",
            }
        ],
    )

