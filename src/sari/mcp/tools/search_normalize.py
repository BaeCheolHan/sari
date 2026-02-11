from __future__ import annotations

import json
import re
from typing import Any


ToolResult = dict[str, object]


def extract_first_line_number(snippet: str) -> int:
    if not snippet:
        return 0
    match = re.search(r"L(\d+):", snippet)
    if match:
        return int(match.group(1))
    return 0


def normalize_results(
    res_type: str, raw: ToolResult
) -> tuple[list[dict[str, Any]], int, list[str]]:
    matches: list[dict[str, Any]] = []
    total = 0
    warnings: list[str] = []

    try:
        if "content" in raw and isinstance(raw["content"], list):
            content_item = raw["content"][0] if raw["content"] else None
            if isinstance(content_item, dict):
                text = content_item.get("text", "{}")
                if isinstance(text, str) and text.strip().startswith("{"):
                    raw = json.loads(text)

        if res_type == "symbol":
            results = raw.get("results", []) if isinstance(raw, dict) else []
            total = raw.get("count", len(results)) if isinstance(raw, dict) else len(results)
            for r in results:
                if not isinstance(r, dict):
                    warnings.append("Dropped non-object symbol result during normalization.")
                    continue
                matches.append(
                    {
                        "type": "symbol",
                        "path": r.get("path"),
                        "identity": r.get("name"),
                        "location": {"line": r.get("line"), "qualname": r.get("qualname")},
                        "extra": {"kind": r.get("kind")},
                    }
                )
        elif res_type == "api":
            results = raw.get("results", []) if isinstance(raw, dict) else []
            total = len(results)
            for r in results:
                if not isinstance(r, dict):
                    warnings.append("Dropped non-object api result during normalization.")
                    continue
                matches.append(
                    {
                        "type": "api",
                        "path": r.get("file", ""),
                        "identity": r.get("path", ""),
                        "location": {"line": r.get("line", 0)},
                        "extra": {"method": r.get("method"), "handler": r.get("handler")},
                    }
                )
        elif res_type == "repo":
            results = raw.get("candidates", []) if isinstance(raw, dict) else []
            total = len(results)
            for r in results:
                if not isinstance(r, dict):
                    warnings.append("Dropped non-object repo candidate during normalization.")
                    continue
                matches.append(
                    {
                        "type": "repo",
                        "path": r.get("repo", ""),
                        "identity": r.get("repo", ""),
                        "location": {},
                        "extra": {"score": r.get("score")},
                    }
                )
        else:
            results = raw.get("results", []) if isinstance(raw, dict) else []
            meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
            total = meta.get("total", len(results)) if isinstance(meta, dict) else len(results)
            for r in results:
                if not isinstance(r, dict):
                    warnings.append("Dropped non-object code result during normalization.")
                    continue
                snippet = r.get("snippet", "")
                first_line = extract_first_line_number(str(snippet))
                matches.append(
                    {
                        "type": "code",
                        "path": r.get("path"),
                        "identity": str(r.get("path", "")).split("/")[-1],
                        "location": {"line": first_line},
                        "snippet": snippet,
                        "extra": {"repo": r.get("repo"), "score": r.get("score")},
                    }
                )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
        warnings.append(f"Normalization fallback due to parse error: {e}")

    return matches, total, warnings


def is_empty_result(result: ToolResult) -> bool:
    try:
        if "content" in result and isinstance(result["content"], list):
            content_item = result["content"][0]
            if isinstance(content_item, dict):
                text = content_item.get("text", "")
                if isinstance(text, str) and text.strip().startswith("{"):
                    data = json.loads(text)
                    if "results" in data and len(data["results"]) == 0:
                        return True
                    if "hits" in data and len(data["hits"]) == 0:
                        return True
                    if "candidates" in data and len(data["candidates"]) == 0:
                        return True
                elif isinstance(text, str) and "returned=0" in text:
                    return True

        if "results" in result and len(result["results"]) == 0:
            return True
        if "hits" in result and len(result["hits"]) == 0:
            return True
        if "candidates" in result and len(result["candidates"]) == 0:
            return True
    except Exception:
        return False
    return False
