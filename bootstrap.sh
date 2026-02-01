#!/bin/bash
# Deckard MCP Bootstrap Script
# Starts the server in Proxy Mode (stdio <-> Daemon)

# Resolve script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$DIR"

# Add repo root to PYTHONPATH
export PYTHONPATH="$ROOT_DIR:$PYTHONPATH"

# Run CLI in proxy mode
exec python3 -m mcp.cli proxy
