import html
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import Response
from starlette.responses import HTMLResponse, JSONResponse
from sari.core.exceptions import ValidationError
from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext
from sari.http.middleware import BackgroundProxyMiddleware, RuntimeSessionMiddleware, _parse_proxy_target
from sari.http.meta_endpoints import status_endpoint
from sari.http.pipeline_endpoints import (
    pipeline_lsp_matrix_report_api_endpoint,
    pipeline_lsp_matrix_run_api_endpoint,
    pipeline_perf_report_api_endpoint,
    pipeline_perf_run_api_endpoint,
    pipeline_policy_set_endpoint,
    pipeline_quality_report_api_endpoint,
    pipeline_quality_run_api_endpoint,
)
from sari.http.read_endpoints import read_diff_preview_endpoint, read_endpoint, read_file_endpoint
from sari.http.search_endpoints import search_endpoint
from sari.http.routes import build_http_routes

async def validation_error_endpoint_handler(request, exc: ValidationError) -> JSONResponse:
    del request
    error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
    return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
def create_app(context: HttpContext) -> Starlette:
    app = Starlette(
        debug=False,
        exception_handlers={ValidationError: validation_error_endpoint_handler},
        middleware=[
            Middleware(BackgroundProxyMiddleware),
            Middleware(RuntimeSessionMiddleware, runtime_repo=context.runtime_repo),
        ],
        routes=build_http_routes(),
    )
    app.state.context = context
    return app
