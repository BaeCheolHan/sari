import os
from pathlib import Path
from sari.core.config.main import Config

def test_workspace_roots_do_not_expand_to_git_root(tmp_path):
    """
    Scenario: User is deep inside a Git repo.
    Expectation: Sari must keep only the explicit workspace path.
    """
    git_root = tmp_path / "my_project"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    
    deep_dir = git_root / "src" / "deep" / "path"
    deep_dir.mkdir(parents=True)
    
    # Simulate being in the deep directory
    config = Config.load(workspace_root_override=str(deep_dir))
    
    roots = [str(Path(r)) for r in config.workspace_roots]
    assert roots == [str(deep_dir)]

def test_workspace_roots_deduplication():
    """
    Verify that redundant paths are deduplicated.
    """
    root = os.getcwd()
    config = Config(workspace_roots=[root, root, root])
    assert len(config.workspace_roots) == 1
    assert config.workspace_roots[0] == root
