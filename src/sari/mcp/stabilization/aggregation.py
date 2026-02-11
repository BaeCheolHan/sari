from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field


@dataclass
class _SessionBundle:
    items: list[dict[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)


_LOCK = threading.RLock()
_BUNDLES: dict[str, _SessionBundle] = {}


def _bundle_key(session_key: str) -> _SessionBundle:
    bundle = _BUNDLES.get(session_key)
    if bundle is None:
        bundle = _SessionBundle()
        _BUNDLES[session_key] = bundle
    return bundle


def _hash_item(mode: str, path: str, text: str) -> str:
    return hashlib.sha1(f"{mode}\n{path}\n{text}".encode("utf-8")).hexdigest()


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

        all_ids = ",".join(item["id"] for item in bundle.items)
        bundle_id = hashlib.sha1(all_ids.encode("utf-8")).hexdigest() if all_ids else ""
        return {
            "context_bundle_id": bundle_id,
            "bundle_items": len(bundle.items),
        }


def reset_bundles_for_tests() -> None:
    with _LOCK:
        _BUNDLES.clear()
