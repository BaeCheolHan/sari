from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field


@dataclass
class _SessionBundle:
    items: list[dict[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    last_seen_seq: int = 0


_LOCK = threading.RLock()
_BUNDLES: dict[str, _SessionBundle] = {}
_SEQUENCE = 0
try:
    _MAX_BUNDLES = max(64, int(os.environ.get("SARI_BUNDLES_MAX", "4096") or "4096"))
except Exception:
    _MAX_BUNDLES = 4096
try:
    _MAX_BUNDLE_ITEMS = max(8, int(os.environ.get("SARI_BUNDLE_ITEMS_MAX", "128") or "128"))
except Exception:
    _MAX_BUNDLE_ITEMS = 128


def _next_sequence() -> int:
    global _SEQUENCE
    _SEQUENCE += 1
    return _SEQUENCE


def _bundle_key(session_key: str) -> _SessionBundle:
    _evict_bundles_if_needed()
    bundle = _BUNDLES.get(session_key)
    if bundle is None:
        bundle = _SessionBundle()
        _BUNDLES[session_key] = bundle
    return bundle


def _evict_bundles_if_needed() -> None:
    max_items = int(_MAX_BUNDLES or 0)
    if max_items <= 0:
        return
    if len(_BUNDLES) < max_items:
        return
    overflow = len(_BUNDLES) - max_items + 1
    victims = sorted(
        _BUNDLES.items(),
        key=lambda kv: int(getattr(kv[1], "last_seen_seq", 0) or 0),
    )[:overflow]
    for key, _bundle in victims:
        _BUNDLES.pop(key, None)


def _hash_item(mode: str, path: str, text: str) -> str:
    return hashlib.sha256(f"{mode}\n{path}\n{text}".encode("utf-8")).hexdigest()


def add_read_to_bundle(
    session_key: str,
    *,
    mode: str,
    path: str,
    text: str,
) -> dict[str, object]:
    item_hash = _hash_item(mode, path, text)
    with _LOCK:
        bundle = _bundle_key(session_key)
        bundle.last_seen_seq = _next_sequence()
        if item_hash not in bundle.seen:
            bundle.seen.add(item_hash)
            bundle.items.append(
                {
                    "id": item_hash,
                    "mode": mode,
                    "path": path,
                    "chars": str(len(text)),
                }
            )
            while len(bundle.items) > int(_MAX_BUNDLE_ITEMS or 0):
                dropped = bundle.items.pop(0)
                dropped_id = str(dropped.get("id", "") or "")
                if dropped_id:
                    bundle.seen.discard(dropped_id)

        all_ids = ",".join(item["id"] for item in bundle.items)
        bundle_id = hashlib.sha256(all_ids.encode("utf-8")).hexdigest() if all_ids else ""
        return {
            "context_bundle_id": bundle_id,
            "bundle_items": len(bundle.items),
        }


def reset_bundles_for_tests() -> None:
    with _LOCK:
        _BUNDLES.clear()
