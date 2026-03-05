"""sari_guide MCP 도구 구현."""

from __future__ import annotations

from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_success


def _success(items: list[dict[str, object]]) -> dict[str, object]:
    """pack1 success 응답을 생성한다."""
    return pack1_success(
        {
            "items": items,
            "meta": Pack1MetaDTO(
                candidate_count=len(items),
                resolved_count=len(items),
                cache_hit=False,
                errors=[],
                stabilization=None,
            ).to_dict(),
        }
    )


class SariGuideTool:
    """sari_guide MCP 도구를 처리한다."""

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """가이드 텍스트를 pack1 형식으로 반환한다."""
        del arguments
        return _success(
            [
                {
                    "name": "sari_guide",
                    "summary": "최소 호출 흐름: search -> read(file) -> search_symbol",
                    "quick_start": [
                        {"tool": "search", "arguments": {"repo": "sari", "query": "AuthService", "limit": 5}},
                        {"tool": "read", "arguments": {"repo": "sari", "mode": "file", "target": "README.md", "limit": 40}},
                        {"tool": "search_symbol", "arguments": {"repo": "sari", "query": "Auth", "limit": 10}},
                    ],
                    "alias_map": {
                        "repo": ["repo_id", "repo_key"],
                        "read.target": ["path", "file_path", "relative_path"],
                        "search.query": ["q", "keyword"],
                        "search_symbol.path_prefix": ["path"],
                        "read.mode": {"file_preview": "file", "preview": "diff_preview"},
                    },
                }
            ]
        )
