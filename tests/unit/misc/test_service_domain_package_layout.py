"""도메인 서비스 패키지 레이아웃 정리 회귀 테스트."""

from __future__ import annotations


def test_lsp_matrix_and_read_modules_exist_under_dedicated_packages() -> None:
    """lsp_matrix/read 서비스 모듈이 하위 패키지 경로로 import 가능해야 한다."""
    from sari.services.lsp_matrix.diagnose_service import LspMatrixDiagnoseService
    from sari.services.read.facade_service import ReadFacadeService

    assert LspMatrixDiagnoseService is not None
    assert ReadFacadeService is not None
