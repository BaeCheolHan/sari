import re
from pathlib import Path
from unittest.mock import MagicMock

from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.search import execute_search


def _pack_header(text: str) -> str:
    return text.split("\n", 1)[0]


def _pack_returned(text: str) -> int:
    m = re.search(r"\breturned=(\d+)\b", _pack_header(text))
    return int(m.group(1)) if m else 0


def test_structural_tools_e2e_with_repo_name_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    ws_java = tmp_path / "StockManager-v-1.0"
    ws_vue = tmp_path / "stock-manager-front"
    (ws_java / "src" / "main" / "java").mkdir(parents=True)
    (ws_vue / "src").mkdir(parents=True)

    (ws_java / "src" / "main" / "java" / "StockService.java").write_text(
        "public class StockService {\n"
        "  public void doWork() {\n"
        "    helper();\n"
        "  }\n"
        "  private void helper() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (ws_java / "src" / "main" / "java" / "Repo.java").write_text(
        "public interface Repo extends JpaRepository {}\n",
        encoding="utf-8",
    )
    (ws_vue / "src" / "AssetBoard.vue").write_text(
        "<template><div/></template>\n"
        "<script>\n"
        "export default { methods: { loadData() { return true; } } }\n"
        "function boot() { return loadData(); }\n"
        "</script>\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "sari.db"
    db = LocalSearchDB(str(db_path))
    roots = [str(ws_java), str(ws_vue)]
    for r in roots:
        rid = WorkspaceManager.root_id_for_workspace(r)
        db.upsert_root(rid, r, str(Path(r).resolve()), label=Path(r).name)

    defaults = Config.get_defaults(str(ws_java))
    defaults["workspace_roots"] = roots
    defaults["workspace_root"] = str(ws_java)
    defaults["db_path"] = str(db_path)
    cfg = Config(**defaults)
    indexer = Indexer(cfg, db, logger=None)
    indexer.scan_once()

    logger = MagicMock()
    stock = execute_search(
        {"query": "StockService", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    stock_text = stock["content"][0]["text"]
    assert _pack_returned(stock_text) > 0

    helper = execute_search(
        {"query": "helper", "search_type": "symbol", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    helper_text = helper["content"][0]["text"]
    assert _pack_header(helper_text).startswith("PACK1 tool=search ok=true")

    do_work = execute_search(
        {"query": "doWork", "search_type": "symbol", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    do_work_text = do_work["content"][0]["text"]
    assert _pack_header(do_work_text).startswith("PACK1 tool=search ok=true")
    db.close_all()


def test_vue_regex_parser_filters_keyword_and_single_char_noise():
    from sari.core.parsers.factory import ParserFactory

    p = ParserFactory.get_parser(".vue")
    symbols, _ = p.extract(
        "Comp.vue",
        "<script>\n"
        "const d = () => 1;\n"
        "if (ok) { run(); }\n"
        "catch(err) { return; }\n"
        "function boot() { return true; }\n"
        "</script>\n",
    )
    # Standard Format
    names = [s.name for s in symbols]
    assert "boot" in names
    assert "if" not in names
    assert "catch" not in names
    assert "d" not in names
