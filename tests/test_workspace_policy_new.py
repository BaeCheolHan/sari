import unittest.mock as mock
from sari.core.workspace import WorkspaceManager

def test_workspace_canonicalization_logic():
    """
    Scenario: Simulation of being in a subdirectory of a Git repo.
    Expectation: resolve_workspace_root should keep the explicit subdirectory.
    """
    mock_git_root = "/tmp/mega_project"
    mock_subdir = "/tmp/mega_project/src/api"
    
    with (
        mock.patch("os.getcwd", return_value=mock_subdir),
        mock.patch("sari.core.workspace.WorkspaceManager.find_git_root", return_value=mock_git_root),
        mock.patch("sari.core.workspace.WorkspaceManager.resolve_workspace_roots", return_value=[mock_subdir]),
        mock.patch("pathlib.Path.exists", return_value=True) # For the .git check in resolve_workspace_root
    ):
        # We don't provide URI, so it uses CWD (mocked as subdir)
        canonical_root = WorkspaceManager.resolve_workspace_root()
        
        # Must keep caller input boundary.
        assert canonical_root == mock_subdir

def test_workspace_roots_expansion_logic():
    """
    Verify that resolve_workspace_roots keeps only the original workspace root.
    """
    mock_git_root = "/tmp/mega_project"
    mock_subdir = "/tmp/mega_project/src/api"
    
    # We must mock WorkspaceManager.settings to prevent it from picking up real sari_project
    from sari.core.settings import Settings
    mock_settings = Settings()
    mock_settings.WORKSPACE_ROOT = None
    
    with (
        mock.patch("os.getcwd", return_value=mock_subdir),
        mock.patch("sari.core.workspace.WorkspaceManager.settings", mock_settings),
        mock.patch("sari.core.workspace.WorkspaceManager.find_git_root", return_value=mock_git_root)
    ):
        roots = WorkspaceManager.resolve_workspace_roots()
        assert roots == [mock_subdir]
