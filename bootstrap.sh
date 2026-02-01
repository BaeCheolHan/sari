#!/bin/bash
# Deckard MCP Bootstrap Script
# Starts the server in Proxy Mode (stdio <-> Daemon)

# Resolve script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$DIR"

# Add repo root to PYTHONPATH
export PYTHONPATH="$ROOT_DIR:$PYTHONPATH"

# Inject Version from Git
if [ -d "$ROOT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    VERSION=$(git -C "$ROOT_DIR" describe --tags --abbrev=0 2>/dev/null)
    # If standard tag format (v1.2.3), strip 'v' if preferred, or keep it. 
    # server.py expects string. Let's keep it as is (v1.1.0) or strip? 
    # Most python libs use 1.1.0. 
    if [ -n "$VERSION" ]; then
        # Strip leading 'v'
        export DECKARD_VERSION="${VERSION#v}"
    fi
fi

# Run CLI (default to proxy mode if no args)
if [ $# -eq 0 ]; then
    exec python3 -m mcp.cli proxy
else
    exec python3 -m mcp.cli "$@"
fi
