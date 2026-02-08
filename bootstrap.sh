#!/bin/bash
# Sari Universal Bootstrap Script
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "--- Sari Universal Infrastructure Bootstrap ---"

PYTHON_CMD="python3"

# 1. Dependency Sync (All-in-One)
echo "Syncing all high-precision parsers and core libraries..."
if ! $PYTHON_CMD -m pip install -r requirements.txt --quiet; then
    echo "Attempting with --user..."
    $PYTHON_CMD -m pip install -r requirements.txt --user --quiet
fi

# 2. Critical Verification
echo "Verifying Multi-Language AST Support..."
$PYTHON_CMD -c "
import tree_sitter_java, tree_sitter_kotlin, tree_sitter_xml, tree_sitter_dockerfile, tree_sitter_rust
print('✅ ALL PARSERS LOADED: Java, Kotlin, XML, Docker, Rust, and more.')
"

# 3. Environment Setup
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

echo "Bootstrap complete. Sari is now a Fully Purified Universal Engine."
