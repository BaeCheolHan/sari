"""collection.service의 LSP 추출 계약 re-export를 검증한다."""

from __future__ import annotations


def test_collection_service_reexports_lsp_extraction_contracts() -> None:
    """하위 호환 import 경로가 계약 모듈을 그대로 노출하는지 확인한다."""
    from sari.services.collection.service import LspExtractionBackend, LspExtractionResultDTO
    from sari.services.lsp_extraction_contracts import (
        LspExtractionBackend as ContractBackend,
        LspExtractionResultDTO as ContractResultDTO,
    )

    assert LspExtractionBackend is ContractBackend
    assert LspExtractionResultDTO is ContractResultDTO
