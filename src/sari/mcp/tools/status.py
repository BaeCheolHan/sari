from typing import Dict, Any, Optional
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config
from sari.mcp.tools._util import pack_header


def _row_get(row: Any, key: str, index: int, default: Any = 0) -> Any:
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return default

def execute_status(args: Dict[str, Any], indexer: Optional[Indexer], db: Optional[LocalSearchDB], cfg: Optional[Config], workspace_root: str, server_version: str, logger=None) -> Dict[str, Any]:
    """
    Sari 서버의 상태를 조회하는 현대화된 상태 도구입니다.
    인덱서 및 DB의 실시간 상태와 풍부한 메타데이터를 제공합니다.
    """
    
    # DB 통계 정보 수집
    total_symbols = 0
    total_files = 0
    db_error = ""
    if db:
        try:
            files_row = db.db.execute_sql("SELECT COUNT(1) AS count_files FROM files").fetchone()
            symbols_row = db.db.execute_sql("SELECT COUNT(1) AS count_symbols FROM symbols").fetchone()
            total_files = int(_row_get(files_row, "count_files", 0, 0) or 0)
            total_symbols = int(_row_get(symbols_row, "count_symbols", 0, 0) or 0)
        except Exception:
            db_error = "DB access failed"
    else:
        db_error = "DB not connected"

    status_data = {
        "index_ready": indexer.status.index_ready if indexer else False,
        "indexed_files": indexer.status.indexed_files if indexer else 0,
        "scanned_files": indexer.status.scanned_files if indexer else 0,
        "symbols_extracted": indexer.status.symbols_extracted if indexer else 0,
        "errors": indexer.status.errors if indexer else 0,
        "total_files_db": total_files,
        "total_symbols_db": total_symbols,
        "db_error": db_error,
        "server_version": server_version,
        "workspace_root": workspace_root,
        "db_engine": "PeeWee+Turbo",
        "fts_enabled": True,
        "cfg_include_ext": ",".join(cfg.include_ext) if cfg and cfg.include_ext else "",
    }
    
    # 풍부한 정보를 담은 PACK1 응답 생성
    lines = [pack_header("status", {}, returned=len(status_data))]
    for k, v in status_data.items():
        val = str(v).lower() if isinstance(v, bool) else v
        lines.append(f"m:{k}={val}")
        
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}
