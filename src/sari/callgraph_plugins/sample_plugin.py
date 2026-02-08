def augment_neighbors(direction, neighbors, context):
    # Example: inject synthetic edges (no-op by default)
    # You could append a dict like:
    # {"from_path": "...", "from_symbol": "...", "from_symbol_id": "...", "to_path": "...", "to_symbol": "...", "to_symbol_id": "...", "rel_type": "calls", "line": 0}
    return neighbors


def filter_neighbors(direction, neighbors, context):
    # Example: filter out third-party paths
    out = []
    for n in neighbors:
        p = n.get("from_path") or n.get("to_path") or ""
        if "site-packages" in p or "node_modules" in p:
            continue
        out.append(n)
    return out
__callgraph_plugin_api__ = 1
__version__ = "0.1.0"
