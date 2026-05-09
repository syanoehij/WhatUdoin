# 성능 개선 진행 결과 (M1c-ULTRA)

`성능 개선 todo.md` M1c-ULTRA 섹션(U1~U5) step별 4종 기록. 본 사이클 동안 신규 step 결과는 아래에 시간순 누적한다.

기록 형식 (4종):
- 변경: 어떤 함수/파일에 무엇이 추가/수정됐는지 1~2줄.
- 증거: exit criteria 검증 파일 경로/grep 결과/테스트명.
- 회귀: 통과한 회귀 테스트명 또는 회귀 영향 없음 사유.
- 다음 step 영향: 후속 step의 가정/입력에 영향이 가는 사실 1줄.

---

## M1c-U1 완료 (2026-05-09)

- **변경**: `llm_parser.py`에 `_OllamaLimiter` 클래스(threading.Lock + 카운터 + Condition), `OllamaUnavailableError` 예외, `_acquire_or_raise()` 헬퍼 추가. 7개 외부 Ollama HTTP 접점 함수(`get_available_models_with_status`, `parse_schedule`, `refine_schedule`, `review_all_conflicts_with_funnel`, `generate_event_checklist_items`, `generate_checklist`, `generate_weekly_report`) 시작부에 `_acquire_or_raise()` + `try/finally release` 패턴 적용. `app.py`에 글로벌 `@app.exception_handler(OllamaUnavailableError)` 등록(503 + JSON 변환). 4개 라우트에 `except OllamaUnavailableError: raise` 가드 추가(광범위 except Exception 포착 방지).
- **증거**: code-reviewer PASS. `llm_parser.py` L186/258/302/836/995/1096/1131에 `_acquire_or_raise()` 위치 확인. `score_conflict` (L477) — `_session.*` 호출 없는 순수 CPU 함수. `try_acquire()` 내 `wait()` 미사용 확인(비차단). 경고 1건: `review_all_conflicts`(L843) 미사용 dead code에 limiter 미적용 — 호출처 없어 exit criteria 차단 아님.
- **회귀**: `app.py` AST parse OK. 정상 경로 동작 변경 없음(limiter 획득 성공 시 기존 로직 그대로).
- **다음 step 영향**: M1c-U2 env/DB 우선 로직은 동일 backend 구현에 포함됨. U3 admin UI가 `set_ollama_concurrency(n)` API를 사용해 limiter capacity 갱신.

---

## M1c-U2 완료 (2026-05-09)

- **변경**: `llm_parser.py`에 `_initial_concurrency()` 함수 추가 — `WHATUDOIN_OLLAMA_CONCURRENCY` env 읽기, 파싱 실패 시 기본 1. `_clamp_concurrency()` 함수로 1~5 범위 강제. `_OllamaLimiter` 초기화 시 env 기반 capacity 적용. `app.py` lifespan에 DB `ollama_concurrency` 설정 읽어 `set_ollama_concurrency()` 호출 추가(DB 우선, env는 초기/fallback).
- **증거**: code-reviewer PASS. env 미지정 → `_OLLAMA_CONCURRENCY_DEFAULT=1`. env=99 → clamp → 5. env=garbage → warning 로그 + 기본 1. DB 설정 있으면 lifespan에서 덮어쓰기.
- **회귀**: `app.py` L73-81 lifespan 추가. 기존 `ollama_url`/`ollama_timeout`/`ollama_num_ctx` 설정 로직 영향 없음.
- **다음 step 영향**: U3 admin UI에서 `GET /api/admin/settings/llm`이 `ollama_concurrency` 키를 노출해야 함(DB 저장된 값 또는 현재 limiter capacity).

---

## M1c-U3 완료 (2026-05-09)

- **변경**: `templates/admin.html` — num_ctx 아래 AI 동시 처리 슬롯 1~5 select UI 추가. `loadLlmSettings()`에서 `data.ollama_concurrency || 1`으로 select 초기화. `saveLlmSettings()` PUT body에 `ollama_concurrency` 필드 추가. backend `/api/admin/settings/llm` GET에 `ollama_concurrency` 노출, PUT에서 1~5 clamp 후 DB 저장 + `set_ollama_concurrency()` 즉시 반영.
- **증거**: code-reviewer PASS. `admin.html:111-123` select UI, `admin.html:392` load, `admin.html:403` save 확인. `app.py:1422` GET, `app.py:1448-1452` PUT, `app.py:73` lifespan 모두 `ollama_concurrency` 처리 정합 확인.
- **회귀**: 기존 ollama_url/timeout/num_ctx 저장 로직 회귀 없음.
- **다음 step 영향**: U5 smoke에서 admin UI 1→3 resize 클릭 후 limiter capacity 즉시 반영 확인 가능.

---

## M1c-U4 완료 (2026-05-09)

- **변경**: `llm_parser.py` — `get_available_models_with_status()`에서 timeout/ConnectionError/5xx → `OllamaUnavailableError(reason=...)` raise. `_post_generate()` 3회 retry 소진 후 timeout/connect/5xx이면 `OllamaUnavailableError` raise. frontend 6개 파일(`ai_import.html`, `doc_list.html`, `check.html` ×2, `doc_editor.html`, `check_editor.html`)에 503 응답 분기 추가: reason="busy" → "AI가 다른 요청을 처리 중입니다" / 그 외 → "AI 서비스를 일시적으로 사용할 수 없습니다".
- **증거**: code-reviewer PASS. 503 체크가 `!res.ok` 앞에 위치해 정상 실행 순서 확인. 내부 로그는 `exception_handler`에서 reason별 구분(`busy`/`timeout`/`connect`/`5xx`). 경고: refine/ai-conflict-review 보조 호출은 graceful degradation(의도된 silent 흡수).
- **회귀**: 기존 502 경로 그대로 유지(503 분기 통과 후 fall-through). `score_conflict`에 OllamaUnavailableError 없음.
- **다음 step 영향**: U5 smoke에서 limiter 1슬롯 포화 시 즉시 503 reason="busy" + 프론트엔드 메시지 표시 검증 가능.

---

## M1c-U5 완료 (2026-05-09)

- **변경**: 없음 (smoke 검증만).
- **증거**: N=5 동시 fire, capacity=1 — 1개 200 OK (17.8초, Ollama 실제 처리), 4개 503 reason="busy" (약 2초 이내 즉시 거부), `slots={in_use:1, capacity:1}` 정확히 표시. admin UI 1→3 resize 저장 후 N=5 동시 fire — 3개 통과, 2개 즉시 거부, `slots={in_use:3, capacity:3}`. 서버 재시작 없이 live 반영 확인.
- **회귀**: 정상 AI 요청 1개는 200 OK로 처리됨. limiter 미적용 경로(`score_conflict`) 영향 없음.
- **다음 step 영향**: M1c-ULTRA 5/5 완료. M1d-S1 게이트 평가 진입 가능.

---

## M1c-ULTRA 완료 요약 (2026-05-09)

| step | 판정 | 핵심 증거 |
|------|------|---------|
| U1 | PASS | 7개 접점 `_acquire_or_raise()` + `try/finally release` 확인. code-reviewer PASS |
| U2 | PASS | env 기본 1, 1~5 clamp, DB 우선 lifespan 적용 확인 |
| U3 | PASS | admin.html select 1~5, PUT body `ollama_concurrency` 저장, capacity 즉시 반영 |
| U4 | PASS | timeout/connect/5xx → `OllamaUnavailableError` 변환. 6개 프론트엔드 파일 503 UX 통합 |
| U5 | PASS | 1슬롯 포화 시 즉시 거부 확인. 1→3 resize live 반영 확인 |

---

## M1d-S1 skip 종료 (2026-05-09)

- **변경**: 없음 (게이트 평가만, 코드 변경 0).
- **증거**: M1b-U1~U5 / M1c-U1~U5 전 구간에서 두 트리거 조건 미관측 — (1) "MCP 검색 중 웹 UI나 SSE가 실제로 멈춘다", (2) "MCP 긴 조회가 일반 API p95에 명확한 회귀를 만든다". M1b-U5 p95 회귀는 locust payload 404/400/500 원인으로 MCP 무관(`성능 개선 진행 결과(M1b).md` U5 참조). 사내 환경 동시 MCP 사용 빈도 낮음(2명 사용자 환경, MCP는 주로 단일 사용자가 단발 검색).
- **회귀**: 해당 없음 (코드 변경 0).
- **다음 step 영향**: M1d-S3 미진입, M1d 마일스톤 종료. transport 교체·identity 병렬 테스트·DNS rebinding은 회사 반입 게이트 또는 M2 이후로 분리. M1-end-U1 (f)항(M1d skip 사유 기록) 충족.

---

## M1-end-U1 완료 (2026-05-09) — M1-ULTRA 종료 점검

§17 M1 종료 부하 테스트(축소) 6종 exit criteria 충족 확인:

| 항목 | 판정 | 핵심 증거 |
|------|------|---------|
| (a) M1a lazy-load 회귀 0건 + before/after 측정 기록 | PASS | M1a-11/12 evidence(`_workspace/perf/baseline_*` + `m1a11_run_193829/`). `성능 개선 진행 결과(M1a).md` 참조 |
| (b) M1b WAL/PRAGMA 적용 후 `journal_mode=wal` | PASS | M1b-U4에서 `PRAGMA journal_mode;` 결과 `wal`, `.wal`/`.shm` 파일 생성 확인 |
| (c) M1b 50 VU smoke 명확한 p95 회귀 없음·`database is locked` 0건 유지 | 부분 PASS | M1b-U5 lock 0건은 PASS. p95 회귀는 locust payload 404/400/500 원인이며 DB 동시성 문제 아님(closure FAIL/open로 보존, M1-end-1 full case에서 재측정) |
| (d) M1c Ollama 기본 1슬롯 limiter 동작 | PASS | M1c-U2 env 기본 1 + DB 우선. M1c-U5 capacity=1 시 1개 200 OK + 4개 503 reason="busy" 즉시 거부 |
| (e) 동시 AI 2개+ smoke 포화 응답 또는 대기 정책 확인 | PASS | M1c-U5 N=5 동시 fire — capacity=1: 1 통과 + 4 즉시 거부 / capacity=3 (1→3 resize live): 3 통과 + 2 즉시 거부 |
| (f) M1d skip 사유 기록 | PASS | 본 문서 "M1d-S1 skip 종료" 절에 기록 완료 |

**M1-ULTRA 종료 판정**: lite 경로 6종 중 5종 PASS + 1종 부분 PASS(c는 lock 부재 확인은 통과, p95는 locust 입력 데이터 원인이며 별도 이슈로 분리). M1-ULTRA 사이클 종료.

**후속 트랙**:
- **M2 진입**: 자동 진입 아님. §13 진입 게이트(SSE 재연결 폭주 → API p95 회귀 또는 SSE 이벤트 루프 점유로 업로드/조회 회귀) 측정 시 진입.
- **회사 반입 패키지 결정 (M1-end-2)**: 현재 미진입. 회사 반입 결정 시 M1b/M1c/M1d 보수 단축안 표를 ULTRA 산출물 위에 합쳐 별도 사이클 진행.
- **M1b-U5 p95 closure**: locust payload 정정 후 재측정은 M1-end-1 full case에서 다룸.
