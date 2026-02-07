# ⚡ SARI v0.4.0: High-Performance Intelligent MCP Engine

Sari is a hyper-fast, intelligent local indexing and search engine designed for LLM agents. Built for speed, efficiency, and deep code understanding.

## 🚀 Key Improvements in v0.4.0

- **10x Performance Boost**: Metadata preloading and batch updates enable indexing 4,000+ files in seconds.
- **Deep AST Understanding**: Powered by `tree-sitter`. Intelligent symbol and API endpoint extraction for Java (Spring), React, Go (Gin), Rust (Actix), and more.
- **Real-time Sync**: Instant incremental indexing via `watchdog`. Changes in your code reflect in search results within 100ms.
- **Insight Dashboard**: Built-in Grafana-style dashboard with real-time CPU/RAM/DB metrics and workspace management.
- **Expert Tools**: Advanced code analysis tools including `call_graph`, `get_callers`, and `search_api_endpoints`.

## 🛠 Installation

```bash
# Clone and install in editable mode
git clone https://github.com/BaeCheolHan/sari.git
cd sari
pip install -e .
```

## 📊 Dashboard

Access the real-time metrics dashboard at:
`http://localhost:47777`

## ⚙️ Configuration

Sari uses a layered configuration system:
1. **Environment Variables**: Prefixed with `SARI_` (e.g., `SARI_DAEMON_PORT`).
2. **Workspace Config**: `.sari/mcp-config.json`.
3. **Global Config**: `~/.config/sari/config.json`.

## 🤖 LLM Workflow (Token Optimizer)

Sari is optimized to save up to 90% of tokens:
1. **Status Check**: `sari status`
2. **Identify Target**: `search` or `search_symbols`
3. **Analyze Structure**: `list_symbols` or `call_graph`
4. **Read Precise Code**: `read_symbol` or `read_file` (with pagination)

## ⚖️ Governance & Policy

- **Single Source of Truth**: All daemon and workspace states are managed in a central registry.
- **Zero-Downtime Upgrades**: Use `sari daemon drain` for seamless transition between versions.
- **Secure by Default**: Enforced loopback-only communication and strict file access policies.

---
*Built with ❤️ for the Gemini CLI & Codex communities.*