import os
import pytest
from pathlib import Path
import unittest.mock as mock
from sari.core.workspace import WorkspaceManager

def test_workspace_canonicalization_logic():
    """
    Scenario: Simulation of being in a subdirectory of a Git repo.
    Expectation: resolve_workspace_root should promote the detected Git root.
    """
    mock_git_root = "/tmp/mega_project"
    mock_subdir = "/tmp/mega_project/src/api"
    
    with (
        mock.patch("os.getcwd", return_value=mock_subdir),
        mock.patch("sari.core.workspace.WorkspaceManager.find_git_root", return_value=mock_git_root),
        mock.patch("sari.core.workspace.WorkspaceManager.resolve_workspace_roots", return_value=[mock_subdir, mock_git_root]),
        mock.patch("pathlib.Path.exists", return_value=True) # For the .git check in resolve_workspace_root
    ):
        # We don't provide URI, so it uses CWD (mocked as subdir)
        canonical_root = WorkspaceManager.resolve_workspace_root()
        
        # Should return the Git root
        assert canonical_root == mock_git_root

def test_workspace_roots_expansion_logic():
    """
    Verify that resolve_workspace_roots includes both the original and expanded Git root.
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
        assert mock_subdir in roots
        assert mock_git_root in roots
