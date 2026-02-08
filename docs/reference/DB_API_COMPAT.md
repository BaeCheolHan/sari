# LocalSearchDB API 정합성 매핑표

구버전(`/Users/baecheolhan/Documents/study/sari`)의 `LocalSearchDB` 메서드를 기준으로,
현재 코드베이스에서의 위치/책임을 정리했습니다.

| Legacy Method | Legacy Location | New Home | Status | Notes |
| --- | --- | --- | --- | --- |
| `search_v2` | `sari/core/db/main.py` | `SearchRepository.search_v2` + `LocalSearchDB.search_v2`(facade) | OK | 엔진 있으면 엔진 우선, 없으면 DB 폴백 |
| `repo_candidates` | `sari/core/db/main.py` | `SearchRepository.repo_candidates` + `LocalSearchDB.repo_candidates` | OK | 엔진 있으면 엔진 우선 |
| `list_snippets_by_tag` | `sari/core/db/main.py` | `SnippetRepository.list_snippets_by_tag` + facade | OK | 최신 스키마에 맞게 정렬 |
| `list_snippet_versions` | `sari/core/db/main.py` | `SnippetRepository.list_snippet_versions` + facade | OK | `snippet_versions` 테이블 |
| `search_snippets` | `sari/core/db/main.py` | `SnippetRepository.search_snippets` + facade | OK | content/tag/path/note 검색 |
| `upsert_snippet_tx` | `sari/core/db/main.py` | `SnippetRepository.upsert_snippet_tx` + facade | OK | tool row 형식 자동 정규화 |
| `update_snippet_location_tx` | `sari/core/db/main.py` | `SnippetRepository.update_snippet_location_tx` + facade | OK | anchor 기반 갱신 대응 |
| `get_context_by_topic` | `sari/core/db/main.py` | `ContextRepository.get_context_by_topic` + facade | OK | contexts 스키마 사용 |
| `search_contexts` | `sari/core/db/main.py` | `ContextRepository.search_contexts` + facade | OK | topic/content/tags 검색 |
| `upsert_context_tx` | `sari/core/db/main.py` | `ContextRepository.upsert_context_tx` + facade | OK | contexts 스키마 사용 |
| `count_failed_tasks` | `sari/core/db/main.py` | `FailedTaskRepository.count_failed_tasks` + facade | OK | failed_tasks 스키마 사용 |
| `upsert_failed_tasks_tx` | `sari/core/db/main.py` | `FailedTaskRepository.upsert_failed_tasks_tx` | OK | (필요 시 facade 추가 가능) |
| `clear_failed_tasks_tx` | `sari/core/db/main.py` | `FailedTaskRepository.clear_failed_tasks_tx` | OK |  |
| `list_failed_tasks_ready` | `sari/core/db/main.py` | `FailedTaskRepository.list_failed_tasks_ready` | OK |  |
| `get_failed_tasks` | `sari/core/db/main.py` | `FailedTaskRepository.get_failed_tasks` | OK |  |
| `get_symbol_block` | `sari/core/db/main.py` | `SymbolRepository.get_symbol_range` + facade | OK | range→파일 읽기 조합 |
| `upsert_symbols_tx` | `sari/core/db/main.py` | `SymbolRepository.upsert_symbols_tx` + facade | OK | 스키마 정합 |
| `upsert_relations_tx` | `sari/core/db/main.py` | `SymbolRepository.upsert_relations_tx` | OK | root_id 포함 스키마 기준 |

## 원칙
- `LocalSearchDB`는 **facade**로만 사용하고, **SQL은 repository**로 캡슐화합니다.
- API는 유지하되 스키마 불일치가 없도록 **정규화 로직을 repository에 집중**합니다.
