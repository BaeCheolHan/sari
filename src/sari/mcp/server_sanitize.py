"""Sanitization helpers for MCP server schemas and logs."""

from __future__ import annotations

from copy import deepcopy


def sanitize_for_llm_tools(schema: dict) -> dict:
    """Normalize JSON schema to be broadly compatible with LLM tool callers."""
    s = deepcopy(schema)

    def walk(node):
        if not isinstance(node, dict):
            return node
        t = node.get("type")
        if isinstance(t, str):
            if t == "integer":
                node["type"] = "number"
                if "multipleOf" not in node:
                    node["multipleOf"] = 1
        elif isinstance(t, list):
            t2 = [x if x != "integer" else "number" for x in t if x != "null"]
            if not t2:
                t2 = ["object"]
            node["type"] = t2[0] if len(t2) == 1 else t2
            if "integer" in t or "number" in t2:
                node.setdefault("multipleOf", 1)

        for key in ("properties", "patternProperties", "definitions", "$defs"):
            if key in node and isinstance(node[key], dict):
                for k, v in list(node[key].items()):
                    node[key][k] = walk(v)
        if "items" in node:
            node["items"] = walk(node["items"])
        return node

    return walk(s)


def sanitize_value(value: object, sensitive_keys: tuple[str, ...], key: str = "") -> object:
    key_l = (key or "").lower()
    if any(s in key_l for s in sensitive_keys):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: sanitize_value(v, sensitive_keys, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(v, sensitive_keys, key) for v in value[:20]]
    if isinstance(value, str):
        if key_l in {"content", "text", "source", "snippet", "body"}:
            return f"[REDACTED_TEXT len={len(value)}]"
        if len(value) > 200:
            return value[:120] + "...[truncated]"
        return value
    return value
