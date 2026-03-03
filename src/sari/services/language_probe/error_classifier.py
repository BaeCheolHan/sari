"""Language probe 오류 분류/정규화 유틸리티."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import TypedDict


class _MissingDependencyRule(TypedDict):
    dependency: str
    tokens: list[str]


class _ErrorPolicy(TypedDict):
    timeout_codes: list[str]
    timeout_tokens: list[str]
    missing_server_tokens: list[str]
    missing_dependency_rules: list[_MissingDependencyRule]
    server_binary_tokens: list[str]


_DEFAULT_POLICY: _ErrorPolicy = {
    "timeout_codes": [
        "ERR_LSP_TIMEOUT",
        "ERR_LSP_REQUEST_TIMEOUT",
        "ERR_LSP_DOCUMENT_SYMBOL_TIMEOUT",
    ],
    "timeout_tokens": [
        "timeout",
        "timed out",
    ],
    "missing_server_tokens": [
        "command not found",
        "no such file",
        "file not found",
        "not installed",
        "missing executable",
        "failed to spawn",
        "failed to start",
        "cannot find",
        "filenotfounderror",
    ],
    "missing_dependency_rules": [
        {"dependency": "pyright", "tokens": ["pyright"]},
        {"dependency": "node", "tokens": ["node"]},
        {"dependency": "npm", "tokens": ["npm"]},
        {"dependency": "dotnet", "tokens": ["dotnet"]},
        {"dependency": "java", "tokens": ["java"]},
    ],
    "server_binary_tokens": [
        "no such file",
        "command not found",
        "missing required commands",
    ],
}


@lru_cache(maxsize=1)
def _load_error_policy() -> _ErrorPolicy:
    """오류 분류 정책을 파일에서 로드한다."""
    override = os.getenv("SARI_LSP_PROBE_ERROR_POLICY_PATH", "").strip()
    if override != "":
        candidate = Path(override).expanduser()
        if candidate.exists():
            loaded = _parse_policy_file(candidate)
            if loaded is not None:
                return loaded
    default_path = Path(__file__).with_name("error_policy.json")
    loaded = _parse_policy_file(default_path)
    if loaded is not None:
        return loaded
    return _DEFAULT_POLICY


def _parse_policy_file(path: Path) -> _ErrorPolicy | None:
    """정책 파일을 파싱하고 유효한 정책만 반환한다."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    timeout_codes = _to_string_list(raw.get("timeout_codes"))
    timeout_tokens = _to_string_list(raw.get("timeout_tokens"))
    missing_server_tokens = _to_string_list(raw.get("missing_server_tokens"))
    server_binary_tokens = _to_string_list(raw.get("server_binary_tokens"))
    missing_dependency_rules = _to_dependency_rules(raw.get("missing_dependency_rules"))

    if (
        len(timeout_codes) == 0
        or len(timeout_tokens) == 0
        or len(missing_server_tokens) == 0
        or len(server_binary_tokens) == 0
    ):
        return None
    return {
        "timeout_codes": timeout_codes,
        "timeout_tokens": timeout_tokens,
        "missing_server_tokens": missing_server_tokens,
        "missing_dependency_rules": missing_dependency_rules,
        "server_binary_tokens": server_binary_tokens,
    }


def _to_string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for value in raw:
        if isinstance(value, str) and value.strip() != "":
            items.append(value.strip().lower())
    return items


def _to_dependency_rules(raw: object) -> list[_MissingDependencyRule]:
    if not isinstance(raw, list):
        return []
    rules: list[_MissingDependencyRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        dependency_raw = item.get("dependency")
        tokens_raw = item.get("tokens")
        if not isinstance(dependency_raw, str) or dependency_raw.strip() == "":
            continue
        tokens = _to_string_list(tokens_raw)
        if len(tokens) == 0:
            continue
        rules.append({"dependency": dependency_raw.strip().lower(), "tokens": tokens})
    return rules


def extract_error_code(message: str, default_code: str) -> str:
    """예외 메시지 선두의 ERR_* 코드를 추출한다."""
    trimmed = message.strip()
    if trimmed.startswith("ERR_"):
        code = trimmed.split(":", 1)[0].strip()
        if code != "":
            return code
    return default_code


def is_timeout_error(code: str, message: str) -> bool:
    """오류 코드/메시지가 타임아웃 성격인지 판별한다."""
    policy = _load_error_policy()
    timeout_codes = {item.upper() for item in policy["timeout_codes"]}
    normalized_message = message.strip().lower()
    return (code in timeout_codes) or any(token in normalized_message for token in policy["timeout_tokens"])


def is_recovered_by_restart(message: str) -> bool:
    """메시지에서 재시작 복구 여부 플래그를 탐지한다."""
    normalized_message = message.strip().lower()
    return ("recovered_by_restart" in normalized_message) and ("true" in normalized_message)


def classify_lsp_error_code(code: str, message: str) -> str:
    """LSP 오류를 정책 코드로 정규화한다."""
    policy = _load_error_policy()
    normalized_message = message.strip().lower()
    if any(token in normalized_message for token in policy["missing_server_tokens"]):
        return "ERR_LSP_SERVER_MISSING"
    if is_timeout_error(code=code, message=message):
        return "ERR_LSP_TIMEOUT"
    return code


def extract_missing_dependency(message: str) -> str | None:
    """예외 메시지에서 누락 의존성 토큰을 추출한다."""
    policy = _load_error_policy()
    normalized_message = message.strip()
    if normalized_message == "":
        return None
    lowered = normalized_message.lower()
    for rule in policy["missing_dependency_rules"]:
        if any(token in lowered for token in rule["tokens"]):
            return rule["dependency"]
    if any(token in lowered for token in policy["server_binary_tokens"]):
        return "server_binary"
    return None
