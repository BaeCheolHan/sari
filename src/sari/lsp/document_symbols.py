"""documentSymbol 요청 호환 유틸을 제공한다."""

from __future__ import annotations

import inspect
from typing import Any


def request_document_symbols_with_optional_sync(
    lsp: object,
    relative_path: str,
    *,
    sync_with_ls: bool,
) -> tuple[Any, bool]:
    """지원 시 sync_with_ls 힌트를 전달하고, 미지원 구현체는 legacy 호출로 폴백한다."""
    requester = getattr(lsp, "request_document_symbols")
    supports_sync_kwarg = False
    try:
        signature = inspect.signature(requester)
        for name, parameter in signature.parameters.items():
            if name == "sync_with_ls":
                supports_sync_kwarg = True
                break
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                supports_sync_kwarg = True
                break
    except (TypeError, ValueError):
        # 일부 래퍼/프록시 구현체는 시그니처 introspection을 지원하지 않을 수 있다.
        supports_sync_kwarg = True
    if not supports_sync_kwarg:
        return requester(relative_path), False
    try:
        return requester(relative_path, sync_with_ls=sync_with_ls), True
    except TypeError as exc:
        lowered = str(exc).lower()
        if "sync_with_ls" not in lowered and "keyword" not in lowered:
            raise
        return requester(relative_path), False
