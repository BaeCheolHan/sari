"""HTTP/MCP 공용 read 실행 계층을 제공한다."""

from __future__ import annotations

from sari.mcp.stabilization.ports import StabilizationPort
from sari.mcp.tools.read_ports import ReadKnowledgePort, ReadLayerSymbolPort, ReadSymbolPort, ReadWorkspacePort
from sari.mcp.tools.read_tool import ReadTool
from sari.services.collection.ports import CollectionScanPort


class ReadFacadeService:
    """MCP read 로직을 HTTP에서도 재사용하기 위한 파사드다."""

    def __init__(
        self,
        workspace_repo: ReadWorkspacePort,
        file_collection_service: CollectionScanPort,
        lsp_repo: ReadSymbolPort,
        knowledge_repo: ReadKnowledgePort,
        tool_layer_repo: ReadLayerSymbolPort | None = None,
        stabilization_service: StabilizationPort | None = None,
    ) -> None:
        """ReadTool 의존성을 구성한다."""
        self._read_tool = ReadTool(
            workspace_repo=workspace_repo,
            file_collection_service=file_collection_service,
            lsp_repo=lsp_repo,
            knowledge_repo=knowledge_repo,
            tool_layer_repo=tool_layer_repo,
            stabilization_service=stabilization_service,
        )

    def read(self, arguments: dict[str, object]) -> dict[str, object]:
        """입력 인자로 read 결과(pack1)를 반환한다."""
        return self._read_tool.call(arguments=arguments)
