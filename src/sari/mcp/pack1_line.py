"""PACK1 v2 라인 포맷 변환 유틸리티를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import quote


class PackContractViolationError(RuntimeError):
    """PACK 계약 위반을 표현한다."""


STRICT_CONTRACT_TOOLS: frozenset[str] = frozenset(
    {
        "search",
        "read",
        "read_file",
        "list_symbols",
        "read_symbol",
        "search_symbol",
        "get_callers",
        "get_implementations",
        "call_graph",
    }
)


RECORD_FALLBACK_TOOLS: frozenset[str] = frozenset(
    {
        "status",
        "doctor",
        "rescan",
        "repo_candidates",
        "scan_once",
        "list_files",
        "index_file",
    }
)


@dataclass(frozen=True)
class PackLineOptionsDTO:
    """라인 포맷 렌더링 옵션을 표현한다."""

    include_structured: bool = False


def render_pack_v2(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    payload: Mapping[str, object],
    options: PackLineOptionsDTO,
) -> dict[str, object]:
    """기존 pack1 payload를 PACK1 v2 라인 포맷으로 변환한다."""
    is_error = bool(payload.get("isError", False))
    structured = payload.get("structuredContent")
    if not isinstance(structured, dict):
        structured = {}

    lines: list[str] = ["@V 2"]
    if is_error:
        lines.extend(_build_error_lines(tool_name=tool_name, structured=structured, payload=payload))
    else:
        try:
            lines.extend(_build_success_lines(tool_name=tool_name, arguments=arguments, structured=structured))
        except PackContractViolationError as exc:
            lines = ["@V 2", f"@SUM tool={_raw(tool_name)} items=0 degraded=1 fatal=1", f"@ERR code=ERR_PACK_CONTRACT_VIOLATION msg={_enc(str(exc))}"]
            is_error = True

    result: dict[str, object] = {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "isError": is_error,
    }
    if options.include_structured:
        result["structuredContent"] = structured
    return result


def _build_error_lines(
    *,
    tool_name: str,
    structured: Mapping[str, object],
    payload: Mapping[str, object],
) -> list[str]:
    """오류 응답 라인을 생성한다."""
    error = structured.get("error")
    if not isinstance(error, dict):
        error = {}
    code = str(error.get("code", "ERR_UNKNOWN")).strip() or "ERR_UNKNOWN"
    message = str(error.get("message", "")).strip()
    if message == "":
        content = payload.get("content")
        if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
            raw_text = content[0].get("text")
            if isinstance(raw_text, str):
                message = raw_text.strip()
    if message == "":
        message = "tool failed"
    return [
        f"@SUM tool={_raw(tool_name)} items=0 degraded=1 fatal=1",
        f"@ERR code={_raw(code)} msg={_enc(message)}",
    ]


def _build_success_lines(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    structured: Mapping[str, object],
) -> list[str]:
    """성공 응답 라인을 생성한다."""
    strict_mode = tool_name in STRICT_CONTRACT_TOOLS
    record_fallback_mode = tool_name in RECORD_FALLBACK_TOOLS or tool_name.startswith("pipeline_")
    items_raw = structured.get("items")
    items: list[dict[str, object]] = []
    if isinstance(items_raw, list):
        for item in items_raw:
            if isinstance(item, dict):
                items.append(item)

    meta = structured.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    stabilization = meta.get("stabilization")
    if not isinstance(stabilization, dict):
        stabilization = {}

    symbol_count = len([item for item in items if _resolve_kind(item) == "symbol"])
    file_count = len([item for item in items if _resolve_kind(item) == "file"])
    degraded = 1 if bool(stabilization.get("degraded", False)) else 0
    fatal = 1 if bool(stabilization.get("fatal_error", False)) else 0
    first_rid = _resolve_rid(item=items[0], arguments=arguments) if len(items) > 0 else "-"

    lines: list[str] = [
        (
            "@SUM "
            f"tool={_raw(tool_name)} "
            f"items={len(items)} "
            f"sym={symbol_count} "
            f"file={file_count} "
            f"degraded={degraded} "
            f"fatal={fatal} "
            f"first_rid={_enc(first_rid)}"
        )
    ]

    for item in items:
        kind = _resolve_kind(item)
        if kind not in {"symbol", "file", "snippet", "context", "record", "edge"}:
            if strict_mode:
                raise PackContractViolationError(f"invalid kind: {kind}")
            kind = "record"
        if record_fallback_mode and kind == "file":
            kind = "record"
        rid = _resolve_rid(item=item, arguments=arguments)
        if strict_mode and (rid.strip() == "" or rid.strip() == ":"):
            raise PackContractViolationError("rid is required")
        path = _resolve_path(item)
        if strict_mode and path.strip() == "":
            raise PackContractViolationError("path is required")
        name = _resolve_name(item)
        symbol_kind = _resolve_symbol_kind(item)
        if strict_mode and kind == "symbol" and symbol_kind in {"-", "", "unknown"}:
            raise PackContractViolationError("sk is required for symbol")
        score = _resolve_score(item)
        if strict_mode:
            _validate_score(score)
        source = _resolve_source(item)
        if strict_mode and source.strip() in {"", "-"}:
            raise PackContractViolationError("src is required")
        lines.append(
            (
                "@R "
                f"kind={_raw(kind)} "
                f"rid={_enc(rid)} "
                f"path={_enc(path)} "
                f"name={_enc(name)} "
                f"sk={_enc(symbol_kind)} "
                f"score={_raw(score)} "
                f"src={_enc(source)}"
            )
        )

    next_calls = stabilization.get("next_calls")
    if isinstance(next_calls, list) and len(next_calls) > 0 and isinstance(next_calls[0], dict):
        next_call = next_calls[0]
        next_tool = str(next_call.get("tool", "read")).strip() or "read"
        next_args = next_call.get("arguments")
        if isinstance(next_args, dict):
            next_rid = str(next_args.get("rid", "")).strip()
            if next_rid == "":
                next_rid = str(next_args.get("resource_id", "")).strip()
            if next_rid == "" and len(items) > 0:
                next_rid = _resolve_rid(item=items[0], arguments=arguments)
            lines.append(f"@NEXT tool={_raw(next_tool)} rid={_enc(next_rid)}")
    elif len(items) > 0:
        lines.append(f"@NEXT tool=read rid={_enc(_resolve_rid(item=items[0], arguments=arguments))}")

    if tool_name in {"read", "read_file"} and len(items) > 0:
        body = _extract_body_text(items[0])
        if body != "":
            lines.append("@TEXT begin")
            lines.append(body)
            lines.append("@TEXT end")
    return lines


def _extract_body_text(item: Mapping[str, object]) -> str:
    """read 계열 본문 텍스트를 추출한다."""
    for key in ("content", "text", "preview_after", "snippet"):
        value = item.get(key)
        if isinstance(value, str) and value != "":
            return value
    return ""


def _resolve_kind(item: Mapping[str, object]) -> str:
    """결과 종류를 결정한다."""
    for key in ("kind", "result_kind", "type", "item_type"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            normalized = value.strip().lower()
            mapped_kind = _map_lsp_kind_code(normalized)
            if mapped_kind is not None:
                return "symbol"
            if normalized in {"class", "function", "method", "symbol", "interface"}:
                return "symbol"
            if normalized in {"file", "snippet", "context"}:
                return normalized
            if normalized in {"edge", "relation"}:
                return "edge"
            return "record"
    if isinstance(item.get("name"), str):
        return "symbol"
    if isinstance(item.get("from_symbol"), str) or isinstance(item.get("to_symbol"), str):
        return "edge"
    return "file"


def _resolve_path(item: Mapping[str, object]) -> str:
    """상대 경로를 추출한다."""
    for key in ("path", "relative_path", "source_path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    for key in ("relativePath",):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    return "-"


def _resolve_name(item: Mapping[str, object]) -> str:
    """심볼 이름을 추출한다."""
    for key in ("name", "symbol_name", "topic", "tag"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    from_symbol = item.get("from_symbol")
    to_symbol = item.get("to_symbol")
    if isinstance(from_symbol, str) and from_symbol.strip() != "":
        return from_symbol.strip()
    if isinstance(to_symbol, str) and to_symbol.strip() != "":
        return to_symbol.strip()
    return "-"


def _resolve_symbol_kind(item: Mapping[str, object]) -> str:
    """심볼 종류를 추출한다."""
    for key in ("sk", "symbol_kind", "kind"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            normalized = value.strip().lower()
            mapped = _map_lsp_kind_code(normalized)
            if mapped is not None:
                return mapped
            return normalized
    return "unknown"


def _resolve_source(item: Mapping[str, object]) -> str:
    """정렬 소스를 추출한다."""
    for key in ("src", "source"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    return "tool"


def _resolve_score(item: Mapping[str, object]) -> str:
    """점수를 문자열로 변환한다."""
    for key in ("score", "rank_score", "final_score"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return f"{float(value):.4f}"
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    if isinstance(item.get("line"), (int, float)):
        return f"{float(item.get('line', 0)):0.4f}"
    return "0.0000"


def _resolve_rid(item: Mapping[str, object], arguments: Mapping[str, object]) -> str:
    """resource id를 결정한다."""
    rid = item.get("rid")
    if isinstance(rid, str) and rid.strip() != "":
        return rid.strip()
    resource_id = item.get("resource_id")
    if isinstance(resource_id, str) and resource_id.strip() != "":
        return resource_id.strip()
    repo = item.get("repo")
    if not isinstance(repo, str) or repo.strip() == "":
        repo_arg = arguments.get("repo")
        repo = repo_arg if isinstance(repo_arg, str) else ""
    path = _resolve_path(item)
    symbol_key = item.get("symbol_key")
    if isinstance(symbol_key, str) and symbol_key.strip() != "":
        return f"{repo}:{path}:{symbol_key.strip()}"
    return f"{repo}:{path}"


def _enc(value: str) -> str:
    """PACK value를 percent-encoding한다."""
    # RFC3986 unreserved만 safe로 두고 나머지는 모두 인코딩한다.
    return quote(str(value), safe="-._~")


def _raw(value: object) -> str:
    """raw 스칼라를 문자열로 변환한다."""
    return str(value)


def _map_lsp_kind_code(raw_kind: str) -> str | None:
    """LSP kind 코드 문자열을 의미 문자열로 매핑한다."""
    mapping = {
        "5": "class",
        "6": "method",
        "7": "property",
        "8": "field",
        "9": "constructor",
        "10": "enum",
        "11": "interface",
        "12": "function",
        "13": "variable",
        "14": "constant",
        "22": "enum_member",
        "23": "struct",
        "24": "event",
        "25": "operator",
        "26": "type_parameter",
    }
    return mapping.get(raw_kind)


def _validate_score(raw_score: str) -> None:
    """점수 문자열이 부동소수점으로 파싱 가능한지 검증한다."""
    try:
        _ = float(raw_score)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise PackContractViolationError(f"invalid score: {raw_score}") from exc
