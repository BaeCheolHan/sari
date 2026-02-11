import pytest
from types import SimpleNamespace
from starlette.datastructures import QueryParams

from sari.core.async_http_server import AsyncHttpServer


@pytest.mark.asyncio
async def test_async_http_server_search_tolerates_dict_hits():
    db = SimpleNamespace(
        search_v2=lambda _opts: ([{"path": "a.py", "score": 1.0}], {"total": 1}),
        engine=None,
    )
    indexer = SimpleNamespace(cfg=SimpleNamespace(snippet_max_lines=3))
    server = AsyncHttpServer(db, indexer, root_ids=["rid"])
    req = SimpleNamespace(query_params=QueryParams("q=test&limit=5"))

    resp = await server.search(req)
    assert resp.status_code == 200
