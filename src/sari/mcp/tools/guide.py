#!/usr/bin/env python3
"""
LLM을 위한 Sari 가이드 도구.
Sari의 효율적인 활용을 위한 핵심 원칙과 권장 워크플로우를 한국어로 제공합니다.
"""
from typing import Any, Dict
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_text


def execute_sari_guide(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sari의 고급 분석 도구를 활용하여 토큰을 절약하고 코드 이해도를 높이기 위한 가이드를 반환합니다.
    (Search-first behavior enforcement)
    """
    text = (
        "Sari Agentic Workflow Guide (Expert Edition)\n\n"
        "[Core Principle: Tool Tiers]\n"
        "- **Simple (Starter)**: `search`, `read_file` -> Basic text matching and verification.\n"
        "- **Advanced (Standard)**: `search_symbols`, `list_symbols`, `read_symbol` -> Structural exploration.\n"
        "- **Expert (Pro)**: `get_callers`, `call_graph`, `get_implementations` -> **The Understanding Shortcut.**\n\n"
        "[Peak Efficiency: Use Analysis Tools]\n"
        "The most common mistake by LLMs is 'reading files to understand flow'. ALWAYS use specialized tools:\n"
        "1) **\"Where is this used?\"**: DO NOT skip `search`. Use `get_callers` (saves 90% tokens).\n"
        "2) **\"What is the overall architecture?\"**: DO NOT read multiple files. Use `call_graph`.\n"
        "3) **\"Where is the logic for this interface?\"**: Use `get_implementations`.\n"
        "4) **\"Look for API routes\"**: Use `search_api_endpoints`.\n\n"
        "[Expert Workflow Examples]\n"
        "- **Impact Analysis**: `search_symbols` -> `get_callers` -> `call_graph` (identify impact without reading much code)\n"
        "- **Understanding New Feature**: `repo_candidates` -> `search_api_endpoints` -> `call_graph` -> `read_symbol`\n\n"
        "[Pro Tip]\n"
        "- `read_file` should be your **last step**. Every preceding stage can and should be done via advanced analysis tools."
    )
    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        lines = [pack_header("sari_guide", {}, returned=1)]
        lines.append(pack_line("t", single_value=pack_encode_text(text)))
        return "\n".join(lines)

    return mcp_response(
        "sari_guide",
        build_pack,
        lambda: {"content": [{"type": "text", "text": text}]},
    )
