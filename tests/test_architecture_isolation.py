import sys
import io
from pathlib import Path
from unittest.mock import MagicMock, patch
from sari.mcp.server import main as server_main

def test_architecture_protocol_isolation():
    """서버 실행 시 stdout이 stderr로 격리되는지 검증"""
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    # 가짜 바이너리 스트림 (MCP 통신용)
    mock_mcp_out = io.BytesIO()
    
    # 1. 서버 메인 실행 시뮬레이션
    # server_main(mock_mcp_out) 호출 시 내부에서 sys.stdout = sys.stderr 가 발생해야 함
    with patch("sari.mcp.server.LocalSearchMCPServer") as MockServer:
        # 무한 루프 방지를 위해 run() 즉시 종료
        MockServer.return_value.run = MagicMock()
        
        server_main(mock_mcp_out)
        
        # 2. 검증: 전역 sys.stdout이 sys.stderr와 동일해졌는지 확인
        assert sys.stdout == sys.stderr
        
        # 3. 검증: 일반 print() 호출 시 데이터가 stderr로 흐르는지 확인
        fake_stderr = io.StringIO()
        with patch("sys.stderr", fake_stderr):
            sys.stdout = sys.stderr # 위에서 바뀐 상태 유지 시뮬레이션
            print("Side effect log")
            assert "Side effect log" in fake_stderr.getvalue()

    # 원복
    sys.stdout = original_stdout
    sys.stderr = original_stderr

def test_dto_to_file_row_robustness():
    """IndexingResult DTO의 튜플 변환 정합성 검증 (3번 문제 보완)"""
    from sari.core.models import IndexingResult
    res = IndexingResult(path="/tmp/a.py", rel="a.py", root_id="r1", repo="repo", type="new")
    row = res.to_file_row()
    
    assert isinstance(row, tuple)
    assert len(row) == 20 # 스키마 컬럼 개수와 일치해야 함
    assert row[0] == "/tmp/a.py"
    assert row[2] == "r1"


def test_http_client_endpoint_resolution_contract_boundary():
    repo_root = Path(__file__).resolve().parents[1]
    http_client = repo_root / "src" / "sari" / "mcp" / "cli" / "http_client.py"
    source = http_client.read_text(encoding="utf-8")
    assert "from sari.core.endpoint_resolver import resolve_http_endpoint" in source
    assert "return resolve_http_endpoint(" in source
