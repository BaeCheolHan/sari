from pathlib import Path
from unittest.mock import patch


def test_command_context_resolves_paths_from_cwd(tmp_path):
    from sari.entry_command_context import CommandContext

    with patch("sari.entry_command_context.WorkspaceManager.resolve_config_path", return_value="/tmp/cfg.json") as mock_cfg:
        with patch("sari.entry_command_context.WorkspaceManager.resolve_workspace_root", return_value="/tmp/ws") as mock_ws:
            ctx = CommandContext(cwd=tmp_path)
            assert ctx.resolve_config_path() == "/tmp/cfg.json"
            assert ctx.resolve_workspace_root() == "/tmp/ws"
            mock_cfg.assert_called_once_with(str(tmp_path))
            mock_ws.assert_called_once()


def test_command_context_normalize_existing_dir(tmp_path):
    from sari.entry_command_context import CommandContext

    existing = tmp_path / "existing"
    existing.mkdir()
    missing = tmp_path / "missing"

    with patch("sari.entry_command_context.WorkspaceManager.normalize_path", side_effect=lambda p: str(Path(p).expanduser())):
        ctx = CommandContext(cwd=tmp_path)
        assert ctx.normalize_existing_dir(str(existing)) == str(existing)
        assert ctx.normalize_existing_dir(str(missing)) is None
