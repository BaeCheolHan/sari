"""
Backward-compatibility shim for the old module name.

Use `sari.mcp.cli.compat_cli` for new code.
"""

from . import compat_cli as _compat_cli

for _name in dir(_compat_cli):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_compat_cli, _name)

__all__ = [name for name in globals() if not name.startswith("__")]
