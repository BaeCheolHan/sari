import sys
import os

# Ensure package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    from sari.main import main
except ImportError:
    # If installed as site-package, try relative
    from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
