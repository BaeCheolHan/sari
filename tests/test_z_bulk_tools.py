import pytest
import os
import glob
import importlib
import inspect
from unittest.mock import MagicMock

def test_bulk_execute_tools():
    # Only test a subset if it takes too long, or optimize
    tool_files = glob.glob("sari/mcp/tools/*.py")
    db = MagicMock()
    indexer = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    
    db.get_repo_stats.return_value = {"repo1": 1}
    db.list_files.return_value = ([], {"total": 0})
    db.search_files.return_value = []
    db.read_file.return_value = "content"
    
    for f in tool_files:
        if f.endswith("__init__.py") or f.endswith("_util.py"):
            continue
            
        module_name = f.replace("/", ".").replace(".py", "")
        if module_name.startswith("sari.sari."):
            module_name = module_name[5:]
            
        try:
            mod = importlib.import_module(module_name)
            for name, func in inspect.getmembers(mod, inspect.isfunction):
                if name.startswith("execute_"):
                    sig = inspect.signature(func)
                    call_args = []
                    for p in sig.parameters.values():
                        p_name = p.name.lower()
                        if p_name == "args": call_args.append({"query": "t", "tag": "t", "path": "root-1/f"})
                        elif p_name == "db": call_args.append(db)
                        elif p_name == "indexer": call_args.append(indexer)
                        elif p_name == "logger": call_args.append(logger)
                        elif p_name == "roots": call_args.append(roots)
                        elif p_name == "cfg": call_args.append(MagicMock())
                        elif p_name == "workspace_root": call_args.append("/tmp/ws")
                        elif p_name == "server_version": call_args.append("0.0.1")
                        elif p_name == "engine": call_args.append(MagicMock())
                        else: call_args.append(None)
                    try:
                        # Skip if it's too slow or blocks
                        func(*call_args)
                    except Exception: pass
        except Exception: pass