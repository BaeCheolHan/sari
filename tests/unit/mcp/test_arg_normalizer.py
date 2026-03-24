"""도구 인자 정규화 규칙을 검증한다."""

from sari.mcp.tools.arg_normalizer import ARG_META_KEY, normalize_tool_arguments
from sari.mcp.tools.arg_normalizer import ArgNormalizationError


def test_normalize_read_maps_mode_and_path_alias() -> None:
    """read는 file_preview/path를 file/target으로 정규화해야 한다."""
    result = normalize_tool_arguments(
        "read",
        {
            "repo": "/repo",
            "mode": "file_preview",
            "path": "README.md",
        },
    )
    assert result.arguments["mode"] == "file"
    assert result.arguments["target"] == "README.md"
    assert result.normalized_from["mode"] == "file_preview"
    assert result.normalized_from["target"] == "path"
    assert isinstance(result.arguments[ARG_META_KEY], dict)


def test_normalize_prefers_canonical_when_alias_conflicts() -> None:
    """canonical 키가 있으면 alias 값보다 우선되어야 한다."""
    result = normalize_tool_arguments(
        "search",
        {
            "repo": "/repo",
            "query": "canonical",
            "q": "alias",
        },
    )
    assert result.arguments["query"] == "canonical"


def test_normalize_raises_ambiguous_error_for_conflicting_aliases() -> None:
    """canonical 부재 + alias 값 충돌은 명시적 모호성 오류를 반환해야 한다."""
    try:
        normalize_tool_arguments("search", {"repo": "/repo", "q": "a", "keyword": "b"})
    except ArgNormalizationError as exc:
        assert exc.code == "ERR_ARGUMENT_AMBIGUOUS"
        assert exc.hint.expected == ["query"]
    else:
        raise AssertionError("ArgNormalizationError must be raised")


def test_normalize_maps_repo_id_to_repo() -> None:
    """repo_id 입력은 내부 canonical인 repo로 정규화되어야 한다."""
    result = normalize_tool_arguments(
        "search",
        {
            "repo_id": "sari",
            "query": "AuthService",
        },
    )
    assert result.arguments["repo"] == "sari"
    assert result.normalized_from["repo"] == "repo_id"


def test_normalize_maps_symbol_key_alias_for_symbol_tools() -> None:
    """symbol 계열 도구는 symbol_key 입력을 symbol canonical로 정규화해야 한다."""
    result = normalize_tool_arguments(
        "get_callers",
        {
            "repo": "/repo",
            "symbol_key": "py:/repo/src/main.py#status_endpoint",
        },
    )
    assert result.arguments["symbol"] == "py:/repo/src/main.py#status_endpoint"
    assert result.normalized_from["symbol"] == "symbol_key"
