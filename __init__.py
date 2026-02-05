"""
Shim package to ensure `import sari.*` resolves to the implementation
located under `sari/sari/` when running from a repo/workspace checkout.
"""
from __future__ import annotations

from pathlib import Path
import sys

_pkg_root = Path(__file__).resolve().parent
_impl_root = _pkg_root / "sari"

if _impl_root.is_dir():
    # Redirect package resolution to the real implementation package.
    __path__ = [str(_impl_root)]  # type: ignore[name-defined]
    # Ensure direct imports like `import sari.main` work consistently.
    if str(_impl_root) not in sys.path:
        sys.path.insert(0, str(_impl_root))

try:
    from sari.version import __version__  # type: ignore[import-not-found]
except Exception:
    __version__ = "dev"
