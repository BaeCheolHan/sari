from typing import List


def _dispatch_legacy_cli(argv: List[str]) -> int:
    from sari.mcp.cli import main as mcp_cli_main

    return mcp_cli_main(argv)
