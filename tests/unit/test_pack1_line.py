"""PACK1 v2 라인 포맷 변환을 검증한다."""

from sari.mcp.pack1_line import PackLineOptionsDTO, render_pack_v2


def test_render_pack_v2_success_default_hides_structured() -> None:
    """기본 옵션에서는 structuredContent가 숨겨져야 한다."""
    payload = {
        "isError": False,
        "structuredContent": {
            "items": [
                {
                    "type": "symbol",
                    "repo": "/repo",
                    "relative_path": "a.py",
                    "name": "hello",
                    "kind": "function",
                    "score": 0.9,
                    "source": "rrf",
                }
            ],
            "meta": {"stabilization": {"degraded": False, "fatal_error": False}},
        },
    }
    rendered = render_pack_v2(
        tool_name="search",
        arguments={"repo": "/repo"},
        payload=payload,
        options=PackLineOptionsDTO(include_structured=False),
    )
    assert rendered["isError"] is False
    assert "structuredContent" not in rendered
    text = str(rendered["content"][0]["text"])
    assert "@V 2" in text
    assert "@SUM " in text
    assert "@R " in text
    assert "@NEXT " in text


def test_render_pack_v2_error_contains_err_line() -> None:
    """오류 응답은 @ERR 라인을 포함해야 한다."""
    payload = {
        "isError": True,
        "structuredContent": {
            "error": {"code": "ERR_REPO_REQUIRED", "message": "repo is required"},
            "meta": {"errors": [{"code": "ERR_REPO_REQUIRED", "message": "repo is required"}]},
        },
    }
    rendered = render_pack_v2(
        tool_name="search",
        arguments={},
        payload=payload,
        options=PackLineOptionsDTO(include_structured=True),
    )
    assert rendered["isError"] is True
    assert "structuredContent" in rendered
    text = str(rendered["content"][0]["text"])
    assert "@V 2" in text
    assert "@ERR code=ERR_REPO_REQUIRED" in text


def test_render_pack_v2_admin_record_does_not_fail_contract() -> None:
    """관리 도구 record 아이템은 계약 위반 없이 라인 포맷으로 렌더링되어야 한다."""
    payload = {
        "isError": False,
        "structuredContent": {
            "items": [{"repo": "/repo-a", "name": None}],
            "meta": {"stabilization": {"degraded": False, "fatal_error": False}},
        },
    }
    rendered = render_pack_v2(
        tool_name="repo_candidates",
        arguments={"repo": "/repo-a"},
        payload=payload,
        options=PackLineOptionsDTO(include_structured=False),
    )
    assert rendered["isError"] is False
    text = str(rendered["content"][0]["text"])
    assert "@R kind=record " in text
    assert "src=tool" in text


def test_render_pack_v2_maps_lsp_kind_code_to_symbol_kind() -> None:
    """숫자 LSP kind 코드는 사람이 읽을 수 있는 sk 값으로 매핑되어야 한다."""
    payload = {
        "isError": False,
        "structuredContent": {
            "items": [
                {
                    "repo": "/repo",
                    "relative_path": "src/a.py",
                    "name": "foo",
                    "kind": "12",
                    "score": 0.91,
                    "source": "rrf",
                }
            ],
            "meta": {"stabilization": {"degraded": False, "fatal_error": False}},
        },
    }
    rendered = render_pack_v2(
        tool_name="search",
        arguments={"repo": "/repo"},
        payload=payload,
        options=PackLineOptionsDTO(include_structured=False),
    )
    assert rendered["isError"] is False
    text = str(rendered["content"][0]["text"])
    assert "@R kind=symbol " in text
    assert "sk=function" in text


def test_render_pack_v2_search_symbol_enforces_strict_contract() -> None:
    """심볼 계열 도구는 strict 계약으로 필수 필드 누락 시 실패해야 한다."""
    payload = {
        "isError": False,
        "structuredContent": {
            "items": [
                {
                    "repo": "/repo",
                    "relative_path": "src/a.py",
                    "name": "foo",
                    "kind": "function",
                    "score": "not-a-number",
                }
            ],
            "meta": {"stabilization": {"degraded": False, "fatal_error": False}},
        },
    }
    rendered = render_pack_v2(
        tool_name="search_symbol",
        arguments={"repo": "/repo"},
        payload=payload,
        options=PackLineOptionsDTO(include_structured=False),
    )
    assert rendered["isError"] is True
    text = str(rendered["content"][0]["text"])
    assert "@ERR code=ERR_PACK_CONTRACT_VIOLATION" in text
