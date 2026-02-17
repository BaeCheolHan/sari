"""예외 정책 후속 강화를 검증한다."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
import warnings

from pytest import MonkeyPatch, raises

from sari.core.config import AppConfig
from sari.core.exceptions import ErrorContext, ValidationError
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.http.app import HttpContext, create_app, pipeline_policy_set_endpoint
from sari.services.admin_service import AdminService


class _PipelineServiceValidationError:
    """update_policy에서 ValidationError를 발생시키는 테스트 더블이다."""

    def update_policy(self, **kwargs):  # type: ignore[no-untyped-def]
        """정책 검증 오류를 재현한다."""
        _ = kwargs
        raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="invalid policy"))


class _PipelineServiceRuntimeError:
    """update_policy에서 RuntimeError를 발생시키는 테스트 더블이다."""

    def update_policy(self, **kwargs):  # type: ignore[no-untyped-def]
        """예상 외 런타임 오류를 재현한다."""
        _ = kwargs
        raise RuntimeError("unexpected failure")


def _build_context(db_path: Path, pipeline_control_service: object) -> HttpContext:
    """테스트용 HttpContext를 생성한다."""
    runtime_repo = RuntimeRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    admin_service = AdminService(
        config=AppConfig(db_path=db_path, host="127.0.0.1", preferred_port=47777, max_port_scan=1, stop_grace_sec=1),
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=SymbolCacheRepository(db_path),
    )
    return HttpContext(
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        search_orchestrator=object(),
        admin_service=admin_service,
        file_collection_service=None,
        pipeline_control_service=pipeline_control_service,  # type: ignore[arg-type]
    )


def _make_request(context: HttpContext, query: dict[str, str]) -> object:
    """파이프라인 정책 endpoint 호출용 최소 request 더블을 생성한다."""
    app = SimpleNamespace(state=SimpleNamespace(context=context))
    return SimpleNamespace(app=app, query_params=query)


def test_pipeline_policy_set_returns_400_for_validation_error(tmp_path: Path) -> None:
    """ValidationError는 400 ERR_POLICY_INVALID 응답으로 변환되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    request = _make_request(
        context=_build_context(db_path=db_path, pipeline_control_service=_PipelineServiceValidationError()),
        query={"workers": "0"},
    )
    response = asyncio.run(pipeline_policy_set_endpoint(request))

    assert response.status_code == 400
    payload = response.body.decode("utf-8")
    assert "ERR_POLICY_INVALID" in payload


def test_pipeline_policy_set_does_not_swallow_unexpected_error(tmp_path: Path) -> None:
    """예상 외 런타임 오류는 400으로 삼키지 않고 500으로 노출되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    request = _make_request(
        context=_build_context(db_path=db_path, pipeline_control_service=_PipelineServiceRuntimeError()),
        query={"workers": "5"},
    )
    with raises(RuntimeError, match="unexpected failure"):
        asyncio.run(pipeline_policy_set_endpoint(request))


def test_engine_status_does_not_swallow_unexpected_import_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """engine_status는 예상 외 import 예외를 숨기지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = AdminService(
        config=AppConfig(db_path=db_path, host="127.0.0.1", preferred_port=47777, max_port_scan=1, stop_grace_sec=1),
        workspace_repo=WorkspaceRepository(db_path),
        runtime_repo=RuntimeRepository(db_path),
        symbol_cache_repo=SymbolCacheRepository(db_path),
    )

    original_import_module = importlib.import_module

    def _raising_import(name: str, package: str | None = None) -> object:
        """특정 모듈에서 예기치 않은 import 오류를 발생시킨다."""
        if name == "tantivy":
            raise RuntimeError("broken dependency")
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _raising_import)

    with raises(RuntimeError, match="broken dependency"):
        service.engine_status()


def test_http_global_handler_returns_400_for_validation_error(tmp_path: Path) -> None:
    """전역 ValidationError 핸들러는 DB 매핑 오류를 400 JSON으로 내려야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    app = create_app(_build_context(db_path=db_path, pipeline_control_service=_PipelineServiceValidationError()))
    assert ValidationError in app.exception_handlers
    handler = app.exception_handlers[ValidationError]
    response = asyncio.run(
        handler(
            SimpleNamespace(),
            ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="invalid row")),
        )
    )
    assert response.status_code == 400
    assert "ERR_DB_MAPPING_INVALID" in response.body.decode("utf-8")


def test_create_app_does_not_emit_middleware_deprecation_warning(tmp_path: Path) -> None:
    """앱 생성 시 middleware decorator deprecation 경고가 없어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always", DeprecationWarning)
        _ = create_app(_build_context(db_path=db_path, pipeline_control_service=_PipelineServiceValidationError()))
    deprecations = [entry for entry in records if issubclass(entry.category, DeprecationWarning)]
    assert len(deprecations) == 0
