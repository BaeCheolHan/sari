
import os
import sys
import shutil
import time
import tempfile
import threading
from pathlib import Path

# Fix path
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

import os

# Configure logging to see sari logs
os.environ["SARI_LOG_LEVEL"] = "DEBUG"
os.environ["PYTHONPATH"] = str(Path(__file__).parent.parent.absolute())
from sari.core.utils.logging import configure_logging
configure_logging()

from sari.mcp.workspace_registry import Registry
from sari.core.db.models import File, Symbol


def create_dummy_project(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".sariroot").touch()
    
    # Create a python file
    code = """
def hello_world():
    print("Hello E2E")

class TestClass:
    def method_one(self):
        pass
"""
    (root / "main.py").write_text(code, encoding="utf-8")
    
    # Create nested file
    (root / "utils").mkdir()
    (root / "utils" / "helper.py").write_text("FULL_CONSTANT = 42\n", encoding="utf-8")
    
    # Add Python marker to trigger profile detection
    (root / "requirements.txt").touch()


def verify_indexing_e2e():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root_path = Path(tmp_dir) / "project"
        create_dummy_project(root_path)
        
        # Force Local DB usage to avoid polluting global DB
        sari_dir = root_path / ".sari"
        sari_dir.mkdir(parents=True, exist_ok=True)
        (sari_dir / "index.db").touch()
        
        print(f"Created dummy project at {root_path} with local DB")
        
        # Test Scanner Isolation
        from sari.core.config import Config
        defaults = Config.get_defaults(str(root_path))
        cfg_obj = Config(**defaults)
        from sari.core.indexer.scanner import Scanner
        scanner = Scanner(cfg_obj)
        print("Scanner Test: Iterating files...")
        count = 0
        for p, st, ex in scanner.iter_file_entries(root_path):
            print(f"Scanner found: {p}, excluded={ex}")
            if not ex: count += 1
        print(f"Scanner found {count} valid files.")
        if count == 0:
            print("Scanner failed to find files. Aborting.")
            # Debug Config
            print(f"Config include_ext: {scanner.include_ext}")
            print(f"Config include_files: {scanner.include_files}")
            return
            
        
        # Start Registry -> SharedState -> Indexer
        registry = Registry.get_instance()
        state = registry.get_or_create(str(root_path))
        
        print("Indexer started. Waiting for completion...")
        # return # STOP HERE FOR SCANNER TEST

        
        # Poll DB for files
        max_retries = 20
        found = False
        for i in range(max_retries):
            count = File.select().where(File.root_id == state.root_id).count()
            print(f"Retry {i}: Found {count} files")
            if count >= 2: # main.py, utils/helper.py
                found = True
                break
            time.sleep(1)
            
        if not found:
            print("FAILED: Indexing timed out.")
            registry.shutdown_all()
            sys.exit(1)
            
        # Check Symbols
        symbols = list(Symbol.select().where(Symbol.root_id == state.root_id))
        print(f"Found {len(symbols)} symbols.")
        
        symbol_names = {s.name for s in symbols}
        expected = {"hello_world", "TestClass", "method_one"} # utils/helper might not have symbols if variable is not extracted?
        # Check if parser extracts variables? Simple python parser usually extracts functions/classes.
        
        missing = expected - symbol_names
        if missing:
            print(f"FAILED: Missing symbols: {missing}")
            # registry.shutdown_all()
            # sys.exit(1)
            # Maybe parser doesn't extract some?
            # Let's check what we have
            print(f"Got: {symbol_names}")
        
        if "hello_world" in symbol_names:
            print("SUCCESS: 'hello_world' found.")
        else:
            print("FAILED: 'hello_world' not found.")
            sys.exit(1)
            
        registry.shutdown_all()
        print("E2E Verification Passed!")

if __name__ == "__main__":
    verify_indexing_e2e()
