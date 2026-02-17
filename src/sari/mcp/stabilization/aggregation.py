"""세션 단위 read 번들 집계를 제공한다."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from dataclasses import field


@dataclass
class _SessionBundle:
    """세션 번들 상태를 표현한다."""

    items: list[dict[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    last_seen_seq: int = 0


_LOCK = threading.RLock()
_BUNDLES: dict[str, _SessionBundle] = {}
_SEQUENCE = 0
_MAX_BUNDLES = max(64, int(os.environ.get("SARI_BUNDLES_MAX", "4096") or "4096"))
_MAX_BUNDLE_ITEMS = max(8, int(os.environ.get("SARI_BUNDLE_ITEMS_MAX", "128") or "128"))


def _next_sequence() -> int:
    """증분 시퀀스를 반환한다."""
    global _SEQUENCE
    _SEQUENCE += 1
    return _SEQUENCE


def _evict_bundles_if_needed() -> None:
    """번들 캐시 상한을 넘으면 오래된 항목부터 제거한다."""
    if len(_BUNDLES) < _MAX_BUNDLES:
        return
    overflow = len(_BUNDLES) - _MAX_BUNDLES + 1
    victims = sorted(_BUNDLES.items(), key=lambda item: int(item[1].last_seen_seq or 0))[:overflow]
    for key, _bundle in victims:
        _BUNDLES.pop(key, None)


def _bundle(session_key: str) -> _SessionBundle:
    """세션 키에 대응하는 번들을 반환한다."""
    _evict_bundles_if_needed()
    bundle = _BUNDLES.get(session_key)
    if bundle is None:
        bundle = _SessionBundle()
        _BUNDLES[session_key] = bundle
    return bundle


def _item_hash(mode: str, path: str, text: str) -> str:
    """번들 항목 중복 제거용 해시를 생성한다."""
    raw = f"{mode}\n{path}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_read_to_bundle(session_key: str, mode: str, path: str, text: str) -> dict[str, object]:
    """read 결과를 세션 번들에 추가하고 번들 요약을 반환한다."""
    hashed = _item_hash(mode=mode, path=path, text=text)
    with _LOCK:
        bundle = _bundle(session_key)
        bundle.last_seen_seq = _next_sequence()
        if hashed not in bundle.seen:
            bundle.seen.add(hashed)
            bundle.items.append({"id": hashed, "mode": mode, "path": path, "chars": str(len(text))})
            while len(bundle.items) > _MAX_BUNDLE_ITEMS:
                dropped = bundle.items.pop(0)
                dropped_id = str(dropped.get("id") or "")
                if dropped_id != "":
                    bundle.seen.discard(dropped_id)
        all_ids = ",".join(item["id"] for item in bundle.items)
        bundle_id = hashlib.sha256(all_ids.encode("utf-8")).hexdigest() if all_ids != "" else ""
        return {"context_bundle_id": bundle_id, "bundle_items": len(bundle.items)}


def reset_bundles_for_tests() -> None:
    """테스트를 위해 번들 상태를 초기화한다."""
    with _LOCK:
        _BUNDLES.clear()

