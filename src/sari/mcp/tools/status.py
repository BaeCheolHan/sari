from typing import Dict, Any, Optional
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config
from sari.mcp.tools._util import pack_header

def execute_status(args: Dict[str, Any], indexer: Optional[Indexer], db: Optional[LocalSearchDB], cfg: Optional[Config], workspace_root: str, server_version: str, logger=None) -> Dict[str, Any]:
    """Modernized Status Tool: Uses direct Indexer/DB state with rich metadata."""
    
    # Gather extra stats from DB if available
    total_symbols = 0
    total_files = 0
    if db:
        try:
            total_files = db.db.execute_sql("SELECT COUNT(1) FROM files").fetchone()[0]
            total_symbols = db.db.execute_sql("SELECT COUNT(1) FROM symbols").fetchone()[0]
        except Exception:
            pass

    status_data = {
        "index_ready": indexer.status.index_ready if indexer else False,
        "indexed_files": indexer.status.indexed_files if indexer else 0,
        "scanned_files": indexer.status.scanned_files if indexer else 0,
        "symbols_extracted": indexer.status.symbols_extracted if indexer else 0,
        "errors": indexer.status.errors if indexer else 0,
        "total_files_db": total_files,
        "total_symbols_db": total_symbols,
        "server_version": server_version,
        "workspace_root": workspace_root,
        "db_engine": "PeeWee+Turbo",
        "fts_enabled": True,
        "cfg_include_ext": ",".join(cfg.include_ext) if cfg and cfg.include_ext else "",
    }
    
    # Build a rich PACK1 response
    lines = [pack_header("status", {}, returned=len(status_data))]
    for k, v in status_data.items():
        val = str(v).lower() if isinstance(v, bool) else v
        lines.append(f"m:{k}={val}")
        
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}