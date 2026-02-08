# DDD 2단계: 모델 캡슐화 (Stage 2)

이 문서는 Sari의 도메인 모델에 행동을 부여하고, 도구 레벨의 중복 로직을 줄이는 2단계 변경 사항을 정리합니다.

---

## 1. 적용 내용

- `SearchHit`에 `to_result_dict()` 추가
- `IndexStatus`에 `to_meta()` 추가
- MCP `search` 도구는 모델의 메서드를 사용

---

## 2. 의도

- **포맷/검증 로직을 모델에 캡슐화**하여 도구 레벨 중복 제거
- 결과 응답 구조가 일관되도록 보장
- 향후 서비스 계층(3단계)에서 재사용성 강화

---

## 3. 다음 단계

- 서비스 계층 도입 (SearchService / IndexService / CallGraphService)
- Interface 레이어는 서비스만 호출하도록 변경
