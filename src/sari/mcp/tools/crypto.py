import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Mapping

from sari.core.workspace import WorkspaceManager


_KEY_FILE_NAME = "ctx_keys.json"
_LEGACY_CONFIG_DIR = Path.home() / ".config" / "sari"
_TOKEN_PREFIX = "ctx_"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _secret_file_path() -> Path:
    return WorkspaceManager.get_global_data_dir() / _KEY_FILE_NAME


def _legacy_secret_path() -> Path:
    return _LEGACY_CONFIG_DIR / _KEY_FILE_NAME


def _ensure_file_mode_600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _create_default_keyset() -> dict[str, object]:
    key_bytes = secrets.token_bytes(32)
    key_text = _b64url_encode(key_bytes)
    return {
        "active_kid": "k1",
        "keys": {
            "k1": key_text,
        },
    }


def _load_or_init_keyset() -> dict[str, object]:
    target = _secret_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        legacy = _legacy_secret_path()
        if legacy.exists():
            try:
                target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        if not target.exists():
            target.write_text(json.dumps(_create_default_keyset(), ensure_ascii=False, indent=2), encoding="utf-8")
        _ensure_file_mode_600(target)

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        data = _create_default_keyset()
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _ensure_file_mode_600(target)

    if not isinstance(data, dict):
        data = _create_default_keyset()
    keys = data.get("keys")
    if not isinstance(keys, dict) or not keys:
        data = _create_default_keyset()
    if not data.get("active_kid") or data["active_kid"] not in data.get("keys", {}):
        first_kid = next(iter(data["keys"]))
        data["active_kid"] = first_kid
    return data


def _canonical_payload(payload: Mapping[str, object]) -> bytes:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def issue_context_ref(payload: Mapping[str, object], ttl_seconds: int | None = None) -> str:
    keyset = _load_or_init_keyset()
    kid = str(keyset["active_kid"])
    key_text = str(keyset["keys"][kid])
    key_bytes = _b64url_decode(key_text)

    now = int(time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else _DEFAULT_TTL_SECONDS)
    signed_payload = dict(payload)
    signed_payload.setdefault("v", 1)
    signed_payload["kid"] = kid
    signed_payload["iat"] = now
    signed_payload["exp"] = now + ttl

    payload_bytes = _canonical_payload(signed_payload)
    payload_b64 = _b64url_encode(payload_bytes)
    sig = hmac.new(key_bytes, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{_TOKEN_PREFIX}{payload_b64}.{sig_b64}"


def verify_context_ref(token: str) -> dict[str, object]:
    text = str(token or "").strip()
    if not text.startswith(_TOKEN_PREFIX):
        raise ValueError("invalid context_ref format")
    body = text[len(_TOKEN_PREFIX) :]
    if "." not in body:
        raise ValueError("invalid context_ref format")
    payload_b64, sig_b64 = body.split(".", 1)
    if not payload_b64 or not sig_b64:
        raise ValueError("invalid context_ref format")

    keyset = _load_or_init_keyset()
    payload_raw = _b64url_decode(payload_b64)
    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid context_ref payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid context_ref payload")

    kid = str(payload.get("kid") or "")
    keys = keyset.get("keys")
    if not isinstance(keys, dict) or kid not in keys:
        raise ValueError("unknown context_ref kid")
    key_bytes = _b64url_decode(str(keys[kid]))
    expected = hmac.new(key_bytes, payload_b64.encode("ascii"), hashlib.sha256).digest()
    given = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected, given):
        raise ValueError("invalid context_ref signature")

    now = int(time.time())
    exp = int(payload.get("exp") or 0)
    if exp and exp < now:
        raise ValueError("context_ref expired")
    return payload

