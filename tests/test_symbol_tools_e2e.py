import re
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.call_graph import execute_call_graph
from sari.mcp.tools.get_callers import execute_get_callers
from sari.mcp.tools.get_implementations import execute_get_implementations
from sari.mcp.tools.read_symbol import execute_read_symbol
from sari.mcp.tools.search_symbols import execute_search_symbols


def _pack_header(text: str) -> str:
    return text.split("\n", 1)[0]


def _pack_returned(text: str) -> int:
    m = re.search(r"\breturned=(\d+)\b", _pack_header(text))
    return int(m.group(1)) if m else 0


def _first_sid(text: str) -> str:
    decoded = urllib.parse.unquote(text)
    m = re.search(r"\bsid=([a-f0-9]{40})\b", decoded)
    return m.group(1) if m else ""


def test_structural_tools_e2e_with_repo_name_scope(tmp_path):
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
    stock = execute_search_symbols(
        {"query": "StockService", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    stock_text = stock["content"][0]["text"]
    assert _pack_returned(stock_text) > 0

    helper = execute_search_symbols(
        {"query": "helper", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    helper_text = helper["content"][0]["text"]
    helper_sid = _first_sid(helper_text)
    assert helper_sid
    helper_path = urllib.parse.unquote(helper_text).split(" path=", 1)[1].split(" ", 1)[0]

    do_work = execute_search_symbols(
        {"query": "doWork", "repo": "StockManager-v-1.0", "limit": 10},
        db,
        logger,
        roots,
    )
    do_work_text = do_work["content"][0]["text"]
    do_work_sid = _first_sid(do_work_text)
    do_work_path = urllib.parse.unquote(do_work_text).split(" path=", 1)[1].split(" ", 1)[0]
    root_id = do_work_path.split("/", 1)[0]
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
    # Standard Format: index 1 is name
    names = [s[1] for s in symbols]
    assert "boot" in names
    assert "if" not in names
    assert "catch" not in names
    assert "d" not in names