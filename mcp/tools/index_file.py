from typing import Any, Dict

def execute_index_file(args: Dict[str, Any], indexer: Any) -> Dict[str, Any]:
    """Force immediate re-indexing of a specific file."""
    path = args.get("path", "").strip()
    if not path:
        return {"success": False, "error": "File path is required"}

    if not indexer:
        return {"success": False, "error": "Indexer not available"}

    try:
        # Trigger watcher event logic which handles upsert/delete
        indexer._process_watcher_event(path)
        # If queue is enabled, we might need to flush it (optional)
        if hasattr(indexer, "queue") and indexer.queue:
            pass
             
        return {
            "success": True,
            "path": path,
            "message": f"Successfully requested re-indexing for {path}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
