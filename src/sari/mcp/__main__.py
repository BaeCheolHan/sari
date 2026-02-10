#!/usr/bin/env python3
"""
Single MCP entrypoint shim.

All runtime modes are routed through `sari.main` so startup behavior is defined
in one place.
"""
import sys


def main() -> int:
    from sari.main import main as sari_main

    # Preserve argv semantics while centralizing runtime dispatch.
    return int(sari_main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
