"""HTTP 프록시 타깃 해석 유틸을 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sari.core.daemon_resolver import resolve_daemon_endpoint


@dataclass(frozen=True)
class HttpEndpointResolutionDTO:
    """HTTP 엔드포인트 해석 결과 DTO다."""

    host: str
    port: int
    reason: str


def resolve_http_endpoint(db_path: Path, workspace_root: str | None = None) -> HttpEndpointResolutionDTO:
    """HTTP 프록시 타깃을 단일 우선순위 규칙으로 해석한다."""
    daemon_endpoint = resolve_daemon_endpoint(db_path=db_path, workspace_root=workspace_root)
    return HttpEndpointResolutionDTO(
        host=daemon_endpoint.host,
        port=daemon_endpoint.port,
        reason=f"daemon:{daemon_endpoint.reason}",
    )
