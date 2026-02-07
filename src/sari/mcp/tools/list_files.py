from typing import Any, Dict, List


def execute_list_files(args: Dict[str, Any], db: "LocalSearchDB", logger=None, roots: List[str] = None) -> Dict[str, Any]:
    """Modernized List Files Tool: Uses centralized DB access method."""
    limit = int(args.get("limit", 50))
    
    # Truth: Query through LocalSearchDB method to ensure correct connection
    files = db.list_files(limit=limit)
    
    lines = [f"PACK1 tool=list_files ok=true returned={len(files)} total={len(files)}"]
    for f in files:
        lines.append(f"f:path={f['path']} size={f['size']} repo={f['repo']}")
        
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}