# Workspace Activation Policy

## 목적
`workspace.is_active`를 단일 제어 축으로 사용해 수집/도구 접근 정책을 일관화한다.

## 상태별 동작

| 상태 | 수집 스케줄러 | watcher 등록 | MCP/HTTP repo 해석 | 인덱스 데이터 |
|---|---|---|---|---|
| `is_active=true` | 포함 | 포함 | 허용 | 유지 |
| `is_active=false` | 제외 | 제외 | `ERR_WORKSPACE_INACTIVE` | 유지(Soft-OFF) |

## Soft-OFF 정의
- `roots deactivate <path>`는 수집/도구 접근만 끈다.
- 비활성화 시점에 기존 Tantivy/DB 인덱스는 즉시 purge하지 않는다.
- `roots activate <path>`로 재활성화하면 같은 루트가 다시 수집 대상으로 복귀한다.

## 표준 오류
- code: `ERR_WORKSPACE_INACTIVE`
- message: `workspace is inactive`

## 운영 확인 절차
1. `sari roots list`로 `is_active` 값을 확인한다.
2. 비활성 루트 대상 도구 호출 시 `ERR_WORKSPACE_INACTIVE`를 확인한다.
3. 재활성화 후 scheduler/watcher가 루트를 다시 포함하는지 로그/상태로 확인한다.
