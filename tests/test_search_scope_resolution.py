from pathlib import Path

from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.search_dispatch import execute_core_search_raw


class CaptureDB:
    def __init__(self):
        self.seen_opts = None

    def search(self, opts):
        self.seen_opts = opts
        return [], {"total": 0}


def test_core_search_resolves_workspace_name_scope_to_root_filter(tmp_path):
    ws_a = tmp_path / "stock-manager-api"
    ws_b = tmp_path / "stock-manager-front"
    ws_a.mkdir()
    ws_b.mkdir()

    db = CaptureDB()
    execute_core_search_raw(
        {
            "query": "AuthService",
            "search_type": "code",
            "repo": "stock-manager-front",
            "limit": 5,
        },
        db,
        [str(ws_a), str(ws_b)],
    )

    expected_root = WorkspaceManager.root_id_for_workspace(str(Path(ws_b).resolve()))
    assert db.seen_opts is not None
    assert db.seen_opts.repo is None
    assert db.seen_opts.root_ids == [expected_root]
