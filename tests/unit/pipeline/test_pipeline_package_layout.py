"""파이프라인 서비스 패키지 레이아웃 정리 회귀 테스트."""

from __future__ import annotations


def test_pipeline_modules_exist_under_dedicated_package() -> None:
    """pipeline 모듈들이 하위 패키지 경로로 import 가능해야 한다."""
    from sari.services.pipeline.ab_report import compare_case_metrics
    from sari.services.pipeline.control_service import PipelineControlService
    from sari.services.pipeline.lsp_matrix_ports import PipelineLspMatrixPort
    from sari.services.pipeline.lsp_matrix_service import PipelineLspMatrixService
    from sari.services.pipeline.perf_service import PipelinePerfService
    from sari.services.pipeline.quality_service import PipelineQualityService

    assert callable(compare_case_metrics)
    assert PipelineControlService is not None
    assert PipelineLspMatrixPort is not None
    assert PipelineLspMatrixService is not None
    assert PipelinePerfService is not None
    assert PipelineQualityService is not None
