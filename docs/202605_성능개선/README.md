# WhatUdoin 성능 개선 사이클 아카이브 (2026-05-09 ~ 2026-05-10)

토요일(2026-05-09) 오전 7시 ~ 일요일(2026-05-10) 오후 진행한 성능/안정성 개선 사이클의 모든 자료를 한 곳에 모은 폴더.

---

## 시작하려면 — 결과 요약부터 보기

[**`성능 개선 결과.md`**](성능%20개선%20결과.md) — **한눈 요약 + 외형/동작 변화 + 마일스톤별 성과 + 측정값 + 활성화 가이드**

이 문서 하나만 봐도 변경 내용 전체 파악 가능.

---

## 자료 분류

### 1. 의사결정 / 동결 문서

| 파일 | 용도 |
|------|------|
| [`성능 개선 계획.md`](성능%20개선%20계획.md) | 마스터 plan rev29 (1977 라인). §1 목적 ~ §18 후속 작업까지 모든 정책/거부/검토 반론 기록. 회사 반입 결정 또는 후속 후보 평가 시 참조. |
| [`성능 개선 todo.md`](성능%20개선%20todo.md) | rev29 동결 step 단위 실행 todo. M1a~M6 모든 step 체크박스 + 진행 상태 보드. 후속 사이클 진입 시 시작점. |
| [`성능 개선 단축안(M1b-M1d).md`](성능%20개선%20단축안(M1b-M1d).md) | M1a 완료 후 trim 결정 — full case → ULTRA case. 잘라낸 항목 + 복구 비용 표. 회사 반입 결정 시 활성화 후보. |

### 2. 마일스톤별 진행 결과 (4종 기록 — 변경/증거/회귀/다음 step 영향)

| 파일 | 마일스톤 | 핵심 산출 |
|------|---------|----------|
| [`성능 개선 진행 결과(M1a).md`](성능%20개선%20진행%20결과(M1a).md) | M1a — baseline + lazy-load | locust 1/5/10/25/50 VU 분리 측정, viewer 보조 화면 lazy-load |
| [`성능 개선 진행 결과(M1b).md`](성능%20개선%20진행%20결과(M1b).md) | M1b-ULTRA — WAL/PRAGMA | SQLite WAL + PRAGMA 5종, 50 VU lock 0건 |
| [`성능 개선 진행 결과(M1c).md`](성능%20개선%20진행%20결과(M1c).md) | M1c-ULTRA / M1d / M1-ULTRA 종료 | Ollama limiter 7접점 + admin UI + busy/unavailable UX, MCP skip |
| [`성능 개선 진행 결과(M2).md`](성능%20개선%20진행%20결과(M2).md) | M2 — SSE service + Front Router + 보안 11종 | M2-1~M2-20 + 사용자 의도 회복 fix 4건 + 분리 4단계 활성화 |
| [`성능 개선 진행 결과(M3).md`](성능%20개선%20진행%20결과(M3).md) | M3 — Scheduler service | maintenance_owners.py + 라이브 14/14 + p95 12.9ms |
| [`성능 개선 진행 결과(M4).md`](성능%20개선%20진행%20결과(M4).md) | M4 — Ollama service + uvicorn 결정 | ollama_service.py + IPC + hang 중 p95 31ms + 분리 3단계 활성화 |
| [`성능 개선 진행 결과(M5).md`](성능%20개선%20진행%20결과(M5).md) | M5 — Media service | media_service.py + DB write 0 boundary + 부하 p95 78ms + 분리 2단계 활성화 |
| [`성능 개선 진행 결과(M6).md`](성능%20개선%20진행%20결과(M6).md) | M6 — MCP write owner boundary | mcp_command_registry.py + `_call_web_api_command` boundary + AST 검사 0건 |

### 3. 회귀 테스트 26개 (`tests/` 서브폴더)

phase54 ~ phase79 회귀 잠금 — 누적 700+ 단언 모두 PASS.

각 phase는 grep + AST + 라이브 단언으로 특정 정책/boundary를 잠금. 향후 같은 영역 변경 시 즉시 회귀 검출.

> **주의**: 본 폴더의 `tests/`는 **참고용 사본**이다. 실제 회귀 suite는 프로젝트 루트의 `tests/phase54~79_*.py`이며, 이 사본을 수정해도 회귀 검증에 영향 없음. 사본은 사이클 시점의 단언 내용 보존용.

---

## 변경 규모

- **54개 commit** (시작 `9fd0c2f` → 마지막 `b1eb71c`)
- **97 files changed**, **+17,317 / −379 라인**
- **9 신규 service/인프라 파일** (sse_service / scheduler_service / ollama_service / media_service / supervisor / front_router / publisher / maintenance_owners / mcp_command_registry)
- **26 회귀 테스트** (phase54~79)
- **8 진행 결과 문서** (M1a/b/c/2/3/4/5/6)
- **마일스톤 9개 종료** (M1a / M1b-ULTRA / M1c-ULTRA / M1d / M1-ULTRA / M2 / M3 / M4 / M5 / M6)
- **분리 활성화 4단계 통합** (Scheduler / Media / Ollama / Front Router + Web API internal-only + SSE)

---

## 후속 작업 시작점

### 회사 반입 결정이 들어오면

1. [`성능 개선 todo.md`](성능%20개선%20todo.md) "보수 단축안 / 회사 반입 게이트 / 후속 후보" 섹션 활성화
2. M1b-1~17 / M1c-1~13 / M1d-1~9 step 진행
3. 50 VU 종료 부하 + 회사 반입 패키지 결정

### MCP write tool 운영 요구가 들어오면

1. [`성능 개선 진행 결과(M6).md`](성능%20개선%20진행%20결과(M6).md) M6-1+M6-2 boundary 재확인
2. `mcp_server.py`에 `@mcp.tool()` 추가 + `_call_web_api_command` 경유
3. supervisor `WHATUDOIN_WEB_API_INTERNAL_URL` 자동 주입 + IPC NotImplementedError 해제
4. M6-3 재평가하여 DB command service 도입 여부 결정

### 측정 회귀가 발생하면

1. M1c uvicorn `limit_concurrency` 재평가 (현재 미적용 채택)
2. multi-worker 전환 검토 (현재 §13 거부)
3. `scheduler_service` write race 측정 → DB command service 도입 후보

---

## 폴더 외부에 그대로 남은 것 (운영/회귀 활성)

- `app.py`, `main.py`, `database.py`, `auth.py`, `crypto.py`, `backup.py`, `llm_parser.py`, `mcp_server.py`, `text_utils.py` — 운영 코드
- `sse_service.py`, `scheduler_service.py`, `ollama_service.py`, `media_service.py` — 신규 service (분리 활성화 시 사용)
- `supervisor.py`, `front_router.py`, `publisher.py` — 신규 인프라
- `maintenance_owners.py`, `mcp_command_registry.py` — 정책 데이터
- `tests/phase54~79_*.py` — 회귀 suite (활성, 변경 검출 즉시 작동)

본 아카이브 폴더는 정책 결정/측정 결과/문서 보존용. 운영 코드와 회귀 suite는 그대로 루트에서 동작.
