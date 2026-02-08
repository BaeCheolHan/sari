# Sari 트러블슈팅

이 문서는 **안정 운용(stdio 고정, HTTP 분리)** 기준으로 작성되었습니다.

---

## 1) MCP 연결이 끊기는 경우

### 증상
- `Disconnected` / `Connection closed`
- 초기화 이후 바로 종료

### 확인 순서
1. **클라이언트 재시작**
2. **Sari 프로세스 확인**
   ```bash
   pgrep -af "sari|sari\.mcp|sari\.main|sari-mcp"
   ```
3. **로그 확인**
   ```bash
   tail -n 80 ~/.local/share/sari/logs/mcp_trace.log
   ```

### 정상 로그 특징
- `proxy_to_daemon: false`
- `transport_read_jsonl` → `transport_write_jsonl`로 응답 일치
- `Broken pipe` 없음

---

## 2) 요청이 타임아웃되는 경우

### 원인 후보
- 워크스페이스 루트 설정 오류
- 인덱싱이 매우 오래 걸리는 대규모 저장소

### 확인
```bash
python -m sari --transport stdio
```

워크스페이스 설정 확인:
```bash
cat ~/.config/sari/config.json
```

---

## 3) 설정 파일 문제

### 증상
- 시작 직후 종료
- `startup preflight failed` 메시지

### 조치
- 설정 파일이 **정상 JSON**인지 확인
- `db_path`는 파일 경로, `config`는 JSON 경로로 분리

---

## 4) 인덱스/DB 문제

### 증상
- 검색 결과 이상
- 이전 데이터가 계속 남음

### 조치
- DB 삭제 후 재인덱싱
```bash
rm -f ~/.local/share/sari/index.db
```

---

## 5) 버전/설치 확인

```bash
python -c "import sari; print(sari.__version__)"
```

설치 경로 확인:
```bash
python -c "import sari,inspect; print(inspect.getfile(sari))"
```

---

## 6) 클라이언트 설정 확인 (stdio 고정)

Gemini/Codex 설정에서 다음을 확인하세요.
- `python -m sari --transport stdio`
- `--format json` 사용 금지
- stdio와 HTTP 동시 운용 금지

---

## 7) 프로세스 정리

```bash
pkill -f "sari|sari\.mcp|sari\.main|sari-mcp"
rm -f ~/.local/share/sari/server.json ~/.local/share/sari/server.json.lock
```

