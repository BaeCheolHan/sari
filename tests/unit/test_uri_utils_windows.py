"""Windows URI 파싱 호환성을 검증한다."""

from sari.lsp.uri_utils import file_uri_to_repo_relative


def test_file_uri_to_repo_relative_windows_drive_case_insensitive() -> None:
    """Windows 드라이브 문자의 대소문자가 달라도 상대경로 변환이 되어야 한다."""
    repo_root = r"c:\work\repo"
    uri = "file:///C:/work/repo/src/main.py"

    relative = file_uri_to_repo_relative(uri=uri, repo_root=repo_root)

    assert relative == "src/main.py"


def test_file_uri_to_repo_relative_windows_unc_path() -> None:
    """UNC file URI도 repo 상대경로로 변환되어야 한다."""
    repo_root = r"\\server\share\repo"
    uri = "file://server/share/repo/pkg/mod.py"

    relative = file_uri_to_repo_relative(uri=uri, repo_root=repo_root)

    assert relative == "pkg/mod.py"
