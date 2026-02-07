#!/usr/bin/env python3
"""
Sari guidance tool for LLMs.
Returns a short usage guide to encourage search-first behavior.
"""
from typing import Any, Dict
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_text


def execute_sari_guide(args: Dict[str, Any]) -> Dict[str, Any]:
    text = (
        "Sari Agentic Workflow Guide (Expert Edition)\n\n"
        "[핵심 원칙: 도구의 급 나누기]\n"
        "- **Simple (초급)**: `search`, `read_file` -> 단순 텍스트 탐색 및 확인용.\n"
        "- **Advanced (중급)**: `search_symbols`, `list_symbols`, `read_symbol` -> 구조적 탐색.\n"
        "- **Expert (고급)**: `get_callers`, `call_graph`, `get_implementations` -> **코드 이해의 치트키.**\n\n"
        "[토큰 효율의 정점: 분석 도구 활용]\n"
        "LLM이 가장 많이 실수하는 것은 '코드를 직접 읽어서 흐름을 파악하려는 행위'입니다. 아래 상황에서는 반드시 전용 분석 도구를 사용하세요:\n"
        "1) **\"이 함수 어디서 쓰여?\"**: `search` 금지. 반드시 `get_callers` 사용 (토큰 90% 절약).\n"
        "2) **\"시스템 전체 흐름이 뭐야?\"**: 여러 파일 `read_file` 금지. 반드시 `call_graph` 사용.\n"
        "3) **\"인터페이스의 실제 로직이 어디야?\"**: `get_implementations` 사용.\n"
        "4) **\"API 엔드포인트 찾기\"**: `search_api_endpoints` 사용.\n\n"
        "[고급 워크플로우 예시]\n"
        "- **영향도 분석**: `search_symbols` -> `get_callers` -> `call_graph` (코드를 거의 읽지 않고도 영향 파악 가능)\n"
        "- **신규 기능 파악**: `repo_candidates` -> `search_api_endpoints` -> `call_graph` -> `read_symbol`\n\n"
        "[사용 주의]\n"
        "- `read_file`은 분석의 **마지막 단계**에서만 사용하세요. 그 전단계는 모두 고급 분석 도구로 대체 가능합니다."
    )
    def build_pack() -> str:
        lines = [pack_header("sari_guide", {}, returned=1)]
        lines.append(pack_line("t", single_value=pack_encode_text(text)))
        return "\n".join(lines)

    return mcp_response(
        "sari_guide",
        build_pack,
        lambda: {"content": [{"type": "text", "text": text}]},
    )
