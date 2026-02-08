# 트러블슈팅 가이드

이 문서는 Sari MCP 사용 중 자주 발생하는 장애와 기본 복구 흐름을 정리합니다.

---

## 1. 기본 복구 플로우 (권장 순서)

1. `sari doctor` 실행으로 상태 확인
2. DB/엔진 오류가 있으면 **자동 복구/안내**를 먼저 수행
3. `scan_once` 또는 `rescan`으로 인덱싱 재시도
4. 도구 재호출

---

## 2. DB 관련 오류

### 2.1 `DB Access` 실패 / `database must be initialized`

**원인**
- 설정 파일에 `db_path`가 비어있음

**해결**
- `doctor` 실행 시 자동으로 기본 `db_path`가 설정됩니다.
- 수동 설정 예시:
```json
{
  "db_path": "/Users/<user>/.local/share/sari/index.db"
}
```

### 2.2 DB를 초기화하고 다시 인덱싱하고 싶은 경우

**해결**
1. Sari 데몬/서버 종료
2. DB 파일 삭제
3. `scan_once` 또는 `rescan`

---

## 3. 설치/업데이트 오류

### 3.1 `No virtual environment found`

**원인**
- `uv pip install -U sari`를 venv 없이 실행

**해결**
```bash
uv venv .venv
source .venv/bin/activate
uv pip install -U sari
```

---

## 4. MCP 연결 오류

### 4.1 `MCP error -32000: Connection closed`

**원인**
- MCP 설정의 `command`가 잘못된 경로를 가리킴
- `sari` 바이너리 대신 다른 래퍼가 실행됨
- stdio 실행 전에 데몬이 꺼져 있음

**해결**
- MCP 설정의 `command`는 **Python 실행 파일**이어야 합니다.
- 권장: `"/abs/path/to/.venv/bin/python"` + `"-m", "sari", "--transport", "stdio"`
- 데몬이 내려가 있다면 `sari daemon start -d`로 다시 시작

### 4.2 `Server not initialized`

**원인**
- 클라이언트가 초기화 직후 연결을 끊음
- 표준 출력(stdout) 오염으로 JSON-RPC 프레임이 깨짐

**해결**
- MCP 서버는 stdout에 로그를 쓰지 않도록 유지
- stderr 로그 확인 후 재시도

---

## 5. 검색/인덱싱 오류

### 5.1 검색 결과가 엉뚱한 경로로 섞이는 경우

**원인**
- venv/가상환경 경로가 인덱싱 대상에 포함됨

**해결**
- 기본 exclude에 `.venv`, `venv`, `env` 등이 포함되어 있습니다.
- 이미 인덱싱된 경우, `rescan`으로 재인덱싱 필요

---

## 6. 도구 실패 시 권장 fallback

- `grep_and_read` 실패
  → `search` 후 `read_file`로 대체

- `repo_candidates` 실패
  → `list_files`로 레포 목록/분포 확인

- `search_api_endpoints` 결과가 섞임
  → `repo` 또는 `root_ids`를 명시

- `call_graph` 결과가 빈약하거나 잘리는 경우
  → `repo`/`root_ids`를 지정하고 `max_nodes`/`max_edges` 예산을 상향

---

## 7. 요약

Sari는 DB/엔진 상태에 따라 도구 가용성이 크게 좌우됩니다.
따라서 **상태 진단 → 자동 복구 → 재시도** 흐름을 기본으로 유지하는 것이 가장 안정적인 운영 방식입니다.

---

## 8. 부분응답(Partial) 안내

DB 장애나 인덱싱 미완료 상태에서는 `partial=true`로 표시되며,  
`db_health`, `index_ready` 등의 메타를 함께 제공합니다.  
부분응답은 **정확도가 제한될 수 있으므로** 가능하면 복구 후 재시도를 권장합니다.
