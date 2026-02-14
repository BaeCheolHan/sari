from __future__ import annotations

import hashlib
from typing import Mapping


class PreviewManager:
    def __init__(self, limit: int, max_total_chars: int = 10000):
        self.limit = limit
        self.max_total_chars = max_total_chars
        self.degraded = False

    def get_adjusted_max_chars(self, item_count: int, requested_max: int) -> int:
        if item_count <= 0:
            return requested_max
        budget_per_item = self.max_total_chars // item_count
        if budget_per_item < requested_max:
            self.degraded = True
            return max(100, budget_per_item)
        return requested_max


def safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def clip_text(value: object, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def candidate_id(match: Mapping[str, object], index: int) -> str:
    raw = f"{match.get('path','')}|{match.get('identity','')}|{match.get('type','')}|{index}"
    return "cand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def bundle_id(query: str, matches: list[dict[str, object]]) -> str:
    parts = [query] + [str(m.get("path", "")) for m in matches]
    return "bundle_" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def next_calls_for_matches(matches: list[dict[str, object]], bundle: str) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for match in matches[:3]:
        mode = "file"
        target = str(match.get("path") or "")
        if str(match.get("type") or "") == "symbol":
            mode = "symbol"
            target = str(match.get("identity") or "")
        call_args: dict[str, object] = {
            "mode": mode,
            "target": target,
            "candidate_id": str(match.get("candidate_id") or ""),
            "bundle_id": bundle,
        }
        if mode == "symbol":
            call_args["path"] = str(match.get("path") or "")
        calls.append({"tool": "read", "arguments": call_args})
    return calls
