import os
import pytest
from pathlib import Path
from sari.core.config.main import Config
from sari.core.workspace import WorkspaceManager

def test_seamless_root_expansion_with_git(tmp_path):
    """
    Scenario: User is deep inside a Git repo.
    Expectation: Sari should automatically add the Git root to workspace_roots.
    """
    git_root = tmp_path / "my_project"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    
    deep_dir = git_root / "src" / "deep" / "path"
    deep_dir.mkdir(parents=True)
    
    # Simulate being in the deep directory
    config = Config.load(workspace_root_override=str(deep_dir))
    
    # Verification
    # 1. Current deep dir should be there
    assert str(deep_dir) in [str(Path(r)) for r in config.workspace_roots]
    # 2. THE GIT ROOT SHOULD BE AUTOMATICALLY ADDED!
    assert str(git_root) in [str(Path(r)) for r in config.workspace_roots]

def test_workspace_roots_deduplication():
    """
    Verify that redundant paths are deduplicated.
    """
    root = os.getcwd()
    config = Config(workspace_roots=[root, root, root])
    assert len(config.workspace_roots) == 1
    assert config.workspace_roots[0] == root
