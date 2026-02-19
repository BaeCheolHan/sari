"""관리용 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.exceptions import DaemonError
from sari.http.context import HttpContext


async def errors_endpoint(request) -> JSONResponse:
    """데몬 점검 결과와 오류 정보를 반환한다."""
    context: HttpContext = request.app.state.context
    return JSONResponse({"errors": [], "doctor": context.admin_service.doctor()})


async def rescan_endpoint(request) -> JSONResponse:
    """강제 인덱싱 재실행을 요청한다."""
    context: HttpContext = request.app.state.context
    return JSONResponse(context.admin_service.index())


async def repo_candidates_endpoint(request) -> JSONResponse:
    """레포 후보 목록을 반환한다."""
    context: HttpContext = request.app.state.context
    return JSONResponse({"items": context.admin_service.repo_candidates()})


async def doctor_endpoint(request) -> JSONResponse:
    """헬스체크 결과를 반환한다."""
    context: HttpContext = request.app.state.context
    checks = context.admin_service.doctor()
    return JSONResponse({"checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks]})


async def daemon_list_endpoint(request) -> JSONResponse:
    """실행 중인 데몬 목록을 반환한다."""
    context: HttpContext = request.app.state.context
    return JSONResponse({"items": context.admin_service.daemon_list()})


async def daemon_reconcile_endpoint(request) -> JSONResponse:
    """런타임 불일치 정리를 실행하고 결과를 반환한다."""
    context: HttpContext = request.app.state.context
    try:
        result = context.admin_service.runtime_reconcile()
    except DaemonError as exc:
        return JSONResponse({"error": {"code": exc.context.code, "message": exc.context.message}}, status_code=503)
    return JSONResponse({"result": result})
