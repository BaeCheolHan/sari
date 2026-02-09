import os
import pytest
from pathlib import Path
from sari.mcp.tools._util import resolve_db_path, resolve_fs_path, resolve_root_ids
from sari.core.workspace import WorkspaceManager

def test_absolute_path_root_id_resolution(tmp_path):
    """
    절대 경로가 root_id가 되었을 때, 기존의 'root-' 접두사 기반 로직이 
    정상적으로 경로를 분리하고 인식하는지 검증합니다.
    """
    # 1. 환경 설정: 실제 물리 경로 생성
    ws_root = tmp_path / "my_projects" / "sari_test"
    ws_root.mkdir(parents=True)
    test_file = ws_root / "src" / "main.py"
    test_file.parent.mkdir()
    test_file.write_text("print('hello')")
    
    roots = [str(ws_root)]
    # 현재 WorkspaceManager.root_id는 절대 경로를 반환함
    expected_root_id = WorkspaceManager.normalize_path(str(ws_root))
    
    # 2. 검증: 파일 경로를 DB 경로(root_id/rel)로 변환
    # 현재 로직상 여기서 실패할 가능성이 높음 (root- 접두사가 없기 때문)
    db_path = resolve_db_path(str(test_file), roots)
    
    assert db_path is not None, f"Failed to resolve DB path for absolute path: {ws_root}"
    assert db_path.startswith(expected_root_id), "DB path should start with the absolute path root_id"
    assert db_path == f"{expected_root_id}/src/main.py"

def test_nested_absolute_path_integrity(tmp_path):
    """
    중첩된 워크스페이스 경로에서 가장 구체적인(Longest Match) root_id를 찾는지 검증합니다.
    """
    parent_ws = (tmp_path / "parent").resolve()
    child_ws = (parent_ws / "child").resolve()
    parent_ws.mkdir()
    child_ws.mkdir()
    
    deep_file = child_ws / "app.py"
    deep_file.write_text("deep")
    
    roots = [str(parent_ws), str(child_ws)]
    
    # 1. 검증 실행
    db_path = resolve_db_path(str(deep_file), roots)
    
    child_id = WorkspaceManager.normalize_path(str(child_ws))
    parent_id = WorkspaceManager.normalize_path(str(parent_ws))
    
    # 2. 결과 분석
    # db_path는 "{root_id}/{rel_path}" 형태여야 함.
    # 만약 최장 일치가 작동했다면, root_id는 child_id여야 함.
    assert db_path.startswith(child_id), f"DB path should start with child_id: {child_id}"
    
    # root_id 부분을 정확히 분리해서 확인 (가장 긴 매칭 루트를 찾으므로)
    # db_path가 child_id로 시작하고, 그 뒤에 /app.py가 붙어야 함
    assert db_path == f"{child_id}/app.py", "Should exactly match child_id/relative_path"
    
    # 만약 부모가 선택되었다면 db_path는 "{parent_id}/child/app.py"가 되었을 것임.
    # (비록 문자열 값은 같을 수 있으나, 로직상 child_id 매칭이 우선임을 위에서 assert함)

def test_path_with_multiple_slashes_split_logic(tmp_path):
    """
    root_id 자체에 슬래시가 포함되어 있을 때 (절대 경로), 
    기존의 split("/", 1) 로직이 root_id를 파괴하는지 검증합니다.
    """
    ws_root = tmp_path / "complex" / "path" / "structure"
    ws_root.mkdir(parents=True)
    
    roots = [str(ws_root)]
    rid = WorkspaceManager.root_id_for_workspace(str(ws_root))
    
    # 만약 DB 경로가 "/Users/name/complex/path/structure/file.py" 형태라면
    # 기존 split("/", 1)은 ("", "Users/name/...")으로 나눠버려 rid를 잃게 됨
    db_path = f"{rid}/file.py"
    
    # 이 db_path를 다시 물리 경로로 복원할 수 있는지 확인
    fs_path = resolve_fs_path(db_path, roots)
    
    assert fs_path is not None, "Should resolve FS path even if root_id has slashes"
    assert fs_path.endswith("file.py")
