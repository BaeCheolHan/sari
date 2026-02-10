import importlib
import inspect
from pathlib import Path


def test_all_tool_modules_are_importable_and_expose_execute_entrypoints():
    tool_dir = Path("src/sari/mcp/tools")
    assert tool_dir.exists()

    tool_modules = sorted(
        p.stem
        for p in tool_dir.glob("*.py")
        if p.stem != "__init__" and not p.stem.startswith("_")
    )
    assert tool_modules

    discovered_execute_functions = 0
    for module_name in tool_modules:
        module = importlib.import_module(f"sari.mcp.tools.{module_name}")
        execute_functions = [
            func
            for name, func in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("execute_")
        ]
        for func in execute_functions:
            params = list(inspect.signature(func).parameters.values())
            assert params
            assert params[0].name == "args"
        discovered_execute_functions += len(execute_functions)

    # Guard against silent path/loader regressions.
    assert discovered_execute_functions >= 20
