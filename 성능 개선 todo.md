# 성능 개선 todo

본 문서는 `성능 개선 계획.md`(rev29 동결, 1차 실행 계획)를 step 단위로 분해한 실행 todo다. 메인 Opus 1M 세션이 마스터 plan을 동결 상태로 들고 있고, 각 step을 하위 에이전트에 위임해 한 번에 한 step씩 처리한다.

## 상태

- **1차 실행 todo로 동결** (2026-05-09): 마스터 plan rev29 + 다회 외부 검토 사이클을 거쳐 M1a~M1d 실행 + M2 이후 조건부 운영 구조 변경 step이 모두 정합 상태로 확정됐다. 신규 step 추가 사이클은 종결한다.
- **추후 변경 원칙**: 본 todo는 마스터 plan §0 "문서 라이프사이클 정책"과 동일하게 동결 상태로 둔다. 새 의견이 들어와도 M1a~M1d 실행을 막는 실재 코드/운영 리스크가 아니면 본문을 더 확장하지 않고, 구현 중 발견 사실 → commit/PR 메시지, 운영 정책/후속 마일스톤 후보 → 마스터 plan §18로 분리한다.
- **다음 행동**: M1a-1부터 step 단위 실행. 진입 즉시 가능.

## 운영 원칙

- **운영 환경 전제**: 본 todo는 **Claude Code Opus 1M 메인 세션 + 하위 에이전트 위임 패턴**을 기준으로 작성됐다. Codex CLI 등 다른 하네스에서 본 todo를 사용하는 경우, 해당 하네스의 위임 정책에 맞춰 적용한다 — 예를 들어 Codex CLI는 사용자가 명시적으로 subagent/병렬 위임을 요청한 경우에만 subagent를 spawn하므로, 아래 "1 step = 1 하위 에이전트 spawn" 원칙은 그런 명시 요청이 있을 때만 적용된다. 단일 세션 직접 실행 환경에서는 step 단위로 끊어 진행하되 위임은 생략한다.
- **1 step = 1 하위 에이전트 spawn (Claude Code Opus mode 기준)**: step의 작업 내용 + §참조 + exit criteria만 위임. 하위 에이전트가 코드 분석·변경·자체 검증을 자기 컨텍스트에서 종결하고, 메인에는 결과 요약만 돌려준다.
- **메인 컨텍스트 보존**: 하위 에이전트가 만든 diff 전체를 메인이 들고 있지 않는다. step 결과는 (a) 변경된 함수/파일 목록, (b) 회귀 테스트 통과 여부, (c) 다음 step에 영향 가는 사실 — 셋만 본 todo 또는 마스터 plan에 1~3줄로 기록.
- **권장 모델 표시**: 각 step 옆 `[Opus]`/`[Sonnet]`은 하위 에이전트 권장 모델. 메인은 항상 Opus 1M.
  - `[Opus]` — 동시성/권한/보안 회귀 위험이 큰 step. 정밀 추론 필요.
  - `[Sonnet]` — 단순 적용/테스트 작성/문서 갱신.
- **순서 — 마일스톤 안 step은 위→아래 순서 권장**. 의존성이 명시된 step은 dep 통과 전에 진입하지 않는다.
- **마일스톤 종료 게이트**: M1 종료 부하 테스트(§17 M1 종료) 통과 전까지 M2 진입 금지. M2 이후 마일스톤은 §17 진입 게이트 평가 step 통과 시에만 진입.
- **§참조는 마스터 plan의 절 이름 기준**(라인 번호 아님 — 라인은 drift). 구현 직전에 `rg -n "절 이름"`으로 위치 재확인.

---

## M1a — 기준선 측정 + 프론트엔드 lazy-load

마일스톤 의도: 후속 단계의 비교용 baseline을 만들고, 가장 회귀 위험이 낮은 lazy-load 작업을 먼저 끝낸다. 운영 코드 변경 최소.

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [x] M1a-1 | `_workspace/perf/` 인프라 골격 | Sonnet | §15 M1a 측정 인프라 | 디렉터리 구조(`locust/`/`fixtures/`/`scripts/`/`baseline_<날짜>/`) 생성, `requirements-dev.txt`에 locust, 운영 코드 변경 0 |
| [x] M1a-2 | fixture seed/cleanup 스크립트 | Sonnet | §15 fixture 정책 + cleanup 정책 | `seed_users.py`(test_perf_001~050 + 세션 쿠키 사전 생성) + `cleanup.py`(test_perf_ 접두어 외 row 삭제 가드) + 서버 종료 상태에서만 실행 절차 |
| [x] M1a-3 | DB snapshot 백업 가드 | Sonnet | §15 측정 전 DB 백업 가드 | `whatudoin.db`/`.db-wal`/`.db-shm` 세트를 `baseline_<날짜>/db_snapshot/`에 보관, 복원 절차 README |
| [x] M1a-4 | background request inventory | Sonnet | §15 background request inventory + 다중 탭 모델 | 페이지×요청 매트릭스 문서(`background_requests.md`) — 알림 1분 polling/lock heartbeat/SSE refetch 전체 식별 |
| [x] M1a-5 | locust 시나리오 작성 (단일 탭 + 다중 탭) | Sonnet | §15 VU 사용자 모델 + background inventory | HTTPS 8443 고정, 세션 쿠키 주입, 단일 탭/다중 탭 분리 task, 가중치 적용 |
| [x] M1a-6 | SSE 측정 PoC + 분리 스크립트 | Sonnet | §15 SSE 측정 분리 | PoC로 SSE 연결 N개 유지 시 locust 카운트/timeout 동작 확인, 별도 keep-alive 스크립트(`httpx`/`aiohttp` 또는 locust SSE-only) 작성, main locust와 동시 실행 가능, 별도 지표 3종(연결 유지 성공률 / publish→수신 지연 / `QueueFull` 발생 수)을 main API p95와 분리 기록 |
| [x] M1a-7 | VU 1→5→10→25→50 단계별 baseline 측정 | Sonnet | §15 VU 단계 상승 절차 | 단계별 p95/p99/`database is locked`/RSS/CPU + 환경 메타데이터(서버-locust 동거 여부, locust CPU%) 단계 분리 기록, SSE 지표는 M1a-6 분리 스크립트 결과를 같은 단계 메타데이터로 묶어 기록 |
| [x] M1a-8 | `__WU_ASSET_V` mermaid 항목 추가 | Sonnet | §5-1 적용 내용 (asset map 단일화) | `base.html`의 `__WU_ASSET_V`에 `mermaid` 추가, `_wu_editor_assets.html`/`check.html`/`event-modal.js`가 동일 map 참조 |
| [x] M1a-9 | 공통 lazy loader 도입 | Sonnet | §5-1 적용 내용 + readiness 보장 기준 | `static/js/`의 단일 모듈 또는 `base.html` 공통 함수, readiness 5종(CSS/JS 글로벌/의존성/실패 후 재시도/reentrancy) 통과 |
| [x] M1a-10 | 홈/프로젝트 관리/휴지통/히스토리 lazy-load 적용 | Sonnet | §5-1 적용 내용 (페이지별) | viewer 보조 화면에서 `_wu_editor_assets.html` head 선로딩 제거, 상세 진입 시 한 번만 로드, `check.html` 기존 lazy-load 동작 유지 |
| [x] M1a-11 | §5-1 4단계 측정 + 회귀 검증 | Sonnet | §5-1 체감 지연 4단계 측정 + §15 프론트엔드 체감 로딩 검증 | 다운로드/parse·eval/`WUEditor.create()`/viewer 표시 4단계 before/after 기록, Mermaid·KaTeX·이미지 viewer 회귀 0 |
| [x] M1a-12 | M1a 회귀 — Playwright 메인 스위트 | Sonnet | §15 마일스톤 간 회귀 자동화 | `npx playwright test tests/*.spec.js` + lazy-load 관련 phase 통과, 회귀 0건 |
| [x] M1a-13 | M1a exit criteria 점검 | Opus | §17 M1a 완료 기준 | 단계별 baseline 기록 / SSE 분리 지표 기록 / 4단계 측정 기록 / viewer 회귀 0 / 다중 탭 baseline 정량 비교 — 모두 충족 |

---

## M1b — WAL/PRAGMA + BEGIN IMMEDIATE + 백업/인덱스

마일스톤 의도: SQLite 동시성 기반을 정리한다. WAL/PRAGMA, 헬퍼 분리, IMMEDIATE 트랜잭션, lock 변환, 백업·복원 절차를 묶어 검증.

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M1b-1 | M1b 진입 직전 DB 세트 백업 | Sonnet | §14 단계별 롤백 원칙 + §6 WAL 복원 절차 | 서버 종료 상태에서 `.db`/`.db-wal`/`.db-shm` 세트 보관, 복원 가드 확인 |
| [ ] M1b-2 | `PRAGMA foreign_key_check;` 사전 점검 | Sonnet | §4 PRAGMA foreign_keys 정책 결정 | 위반 row 카운트/리스트 기록, 0건 또는 정리 후 0건 확보 |
| [ ] M1b-3 | `foreign_keys` 정책 채택 결정 | Opus | §4 PRAGMA foreign_keys 정책 결정 | OFF 유지(권장) 또는 ON 전환 채택, 모든 헬퍼에 일관 적용 명시 |
| [ ] M1b-4 | 저수준 헬퍼 `open_sqlite_connection()` 신설 | Opus | §4 연결 헬퍼 분리 설계 | path 인자 + PRAGMA 5종 + foreign_keys 일괄 적용, row_factory 옵션, 트랜잭션 시작 안 함 |
| [ ] M1b-5 | `init_db()`/마이그레이션/진단 연결 정렬 | Opus | §4 + §17 M1b 완료 기준 | **저수준 헬퍼(`open_sqlite_connection()`) 정의부를 제외한** 직접 `sqlite3.connect(DB_PATH)` 호출 0건(grep). 헬퍼 자체는 SQLite를 직접 열어야 하므로 grep 결과 해석 시 헬퍼 정의부는 정상으로 간주. 모든 경로(`init_db()`/마이그레이션/진단/백업)가 저수준 헬퍼 경유, `run_backup()`→`init_db()` 순서 보존 |
| [ ] M1b-6 | 백업 src 연결 헬퍼 경유 | Sonnet | §6 백업 종류 구분 + 방향 | `backup.run_backup()`이 저수준 헬퍼 사용, 트랜잭션 매니저 미사용, 두 백업 시점(시작 직전/스케줄러) 의미 분리 명시 |
| [ ] M1b-7 | `database.get_conn()` PRAGMA 5종 적용 | Opus | §4 적용 내용 — 연결 단위 + §4 정책 | `timeout=5`/`busy_timeout=5000`/`synchronous=NORMAL`/`cache_size=-8000`/`temp_store=MEMORY` 매 연결 적용. **`synchronous`는 환경 변수(예: `WHATUDOIN_SYNCHRONOUS_MODE=FULL`)로 override 가능하게 한다** — §4 정책의 "보수적 내구성 환경에서 FULL override" 조항 충족. override 미지정 시 기본 NORMAL |
| [ ] M1b-8 | WAL 모드 활성화 + 검증 | Sonnet | §4 적용 내용 — DB 파일 단위 + §15 WAL 검증 | `PRAGMA journal_mode=WAL` 1회 적용, `wal`/`-shm` 파일 생성 확인, `PRAGMA journal_mode;` 결과 `wal` |
| [ ] M1b-9 | §4-1 사전 조사 표 작성 | Opus | §4-1 사전 조사 단계 | write 함수 6컬럼 표(read-then-write/lock 시간/외부 I/O/nested helper/commit 후처리/적용 대상) — IMMEDIATE 적용 대상/비대상 결정 |
| [ ] M1b-10 | `write_conn()` + BEGIN IMMEDIATE 헬퍼 | Opus | §4-1 적용 내용 + §4 트랜잭션 헬퍼 | 적용 대상 hot path만 IMMEDIATE 시작, read-only 경로는 DEFERRED 유지, 트랜잭션 안 파일/외부/LLM 호출 0건 |
| [ ] M1b-11 | DB lock 503 변환 정책 적용 | Opus | §4 DB lock 예외 → 503 변환 정책 + 변환 범위 lock/busy 한정 | FastAPI exception handler 등록, lock/busy 메시지·SQLite error code 한정 변환, 다른 OperationalError는 500 유지, 진단 로그 채널 |
| [ ] M1b-12 | WAL 파일 크기 안전판 + PASSIVE checkpoint | Sonnet | §6 WAL 파일 크기 안전판 + PASSIVE 한계 | 백그라운드 `.wal` 크기 모니터링, 256MB 임계값에서 `wal_checkpoint(PASSIVE)` 트리거, FULL/TRUNCATE 운영 중 미사용 |
| [ ] M1b-13 | hot path EXPLAIN QUERY PLAN 점검 | Sonnet | §5 적용 방식 + §15 인덱스/쿼리 검증 | 점검 대상 8개(`/api/events`/`/api/kanban`/`/api/doc`/`/api/checklists`/`/api/notifications/count`/`/api/notifications/pending`/MCP 검색/휴지통·백업·정리) query plan 기록 + 기존 인덱스가 의도대로 선택되는지 확인. **신규 인덱스는 풀스캔이 hot path에서 확인되고 부하 테스트 근거가 있을 때만 후보 제안/추가**(§5 "있으면 좋아 보이는 인덱스를 무작정 추가하지 않는다"). 중복 인덱스 추가 0건 |
| [ ] M1b-14 | APScheduler write 작업 점검 | Sonnet | §11 점검 대상 + 정책 | 6개 작업의 트랜잭션 길이 측정, 짧은 트랜잭션 원칙 위반 0, 필요 시 IMMEDIATE 헬퍼 적용 |
| [ ] M1b-15 | WAL 복원 drill 1회 수행 | Opus | §6 WAL 복원 절차 + §17 M1b 완료 기준 | 모든 service 종료 → 세트 백업 → 복원 → `PRAGMA integrity_check;`/`journal_mode;` 검증, 절차 문서 `_workspace/perf/README.md` 갱신 |
| [ ] M1b-16 | M1b 동시성 검증 | Opus | §15 동시성 검증 | 저장 100 + 조회 100 동시 — `database is locked` 0건, lock 변환 경로 검증 |
| [ ] M1b-17 | M1b Playwright 회귀 + exit criteria 점검 | Opus | §17 M1b 완료 기준 | 회귀 0 / `init_db()`·마이그레이션·진단·백업 경로의 직접 `sqlite3.connect(DB_PATH)` 호출 0건(저수준 헬퍼 정의부 제외 grep 결과로 확인) / IMMEDIATE 본문 외부 호출 0 / 503 변환 좁은 적용 / 백업 무결성 / restore drill 기록 / scheduler 회귀 0 |

---

## M1c — 업로드 / Ollama 세마포어 / SSE 가드

마일스톤 의도: 리소스 OOM/잠식 가드. M1b 동시성 기반 위에서 측정.

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M1c-1 | route별 body cap 표 확정 | Opus | §7 옵션 A 안전판 — route별 body cap 표 | 5개 route군별 cap 결정(이미지 ~10.5MB / 첨부 ~20.5MB / 큰 본문 JSON 4MB / CRUD 1MB / 인증·관리 256KB), 큰 본문 JSON 회귀 테스트 통과 후 8MB 단계 상향 가능 |
| [ ] M1c-2 | Content-Length 사전 거부 | Sonnet | §7 옵션 A 안전판 — Content-Length 사전 거부 | 헤더 cap 초과 시 본문 read 없이 413, 기존 size error 응답 코드 통일 |
| [ ] M1c-3 | chunked read hard cap | Sonnet | §7 옵션 A 안전판 — chunked read hard cap | 업로드 핸들러에서 chunked 누적 추적, 한도 초과 시 413 abort, 임시 버퍼 폐기 |
| [ ] M1c-4 | ASGI body size middleware (request body 한정) | Opus | §7 옵션 A 안전판 — ASGI body size 제한 + request body 한정·응답 body 미접촉 원칙 | 순수 ASGI middleware, `BaseHTTPMiddleware` 미사용, `receive` wrapper만(send 없음). **본문 없는 method 4종 명시 제외** — `GET`/`HEAD`/`OPTIONS`/`DELETE` 모두 wrapper 통과. `text/event-stream` route 명시 제외. cap 적용 대상은 POST/PUT/PATCH 등 request body가 있는 method + route별 body cap 표에 매핑된 경로에 한정 |
| [ ] M1c-5 | 업로드 threadpool + 세마포어 | Sonnet | §7 적용 내용 (옵션 A) | `PIL.verify`/`Path.write_bytes` threadpool, `asyncio.Semaphore(8)` 적용, 세마포어 위치 명확화 |
| [ ] M1c-6 | Ollama resizable limiter 추상 결정 | Opus | §8 resizable limiter 설계 | 카운터+condition 자체 limiter 또는 `anyio.CapacityLimiter` 중 한쪽 채택, capacity 변경 시 사용 슬롯 보존 |
| [ ] M1c-7 | Ollama limiter 모든 외부 HTTP 접점 적용 | Opus | §8 limiter 적용 대상 — 모든 외부 Ollama HTTP 접점 | 7개 접점(파싱/refinement/체크리스트 생성/주간 보고/conflict review/health/model) 감싸기, `score_conflict` 제외, `try_acquire` 즉시 false 시 busy |
| [ ] M1c-8 | admin UI 1~5 슬롯 설정 + busy UX | Sonnet | §8 admin UI 설정 항목 + 통합 거부 응답 | 1~5 선택 UI, 변경 즉시 limiter capacity 반영, 사용자 화면 "AI 사용 중 (N/N)" 안내 |
| [ ] M1c-9 | Ollama 외부 장애 통합 UX | Sonnet | §8 Ollama 서버 장애 통합 처리 | 단계 ① 시점부터 `ConnectionError`/timeout/5xx도 슬롯 포화와 동일한 "AI 사용 불가" UX로 통합. 내부 로그에서는 사유 구분, 사용자에게는 같은 재시도 안내. admin UI에 Ollama 서버 health 표시. 회귀 — 외부 Ollama 일시 정지 시 7개 limiter 적용 접점 모두 동일 UX 응답 |
| [ ] M1c-10 | SSE broker QueueFull 카운터 + 큐 크기 결정 | Sonnet | §10 적용 내용 | `QueueFull` 발생 카운터, 100 → 500 조정 결정 근거 기록 |
| [ ] M1c-11 | 로그 회전 + 모니터링 | Sonnet | §14 로그 회전 + 메모리/디스크 모니터링 | `RotatingFileHandler`/`TimedRotatingFileHandler` 일별·14일, RSS/`.wal`/업로드 디렉터리 임계값 경고 |
| [ ] M1c-12 | M1c 회귀 — SSE/응답 스트림 보호 | Opus | §17 M1c 완료 기준 (8차 #6 반영) | `/api/stream` 유지, `/uploads/meetings/*` 조회, export/download 응답 끝까지 완료, GET/HEAD cap 미적용 |
| [ ] M1c-13 | M1c 부하 + exit criteria 점검 | Opus | §17 M1c 완료 기준 | 업로드 중 일반 API 회귀 0(보조 p95 300ms), 메모리 풀로드 100~160MB, busy p95 100ms, admin 즉시 반영, queue 카운터 동작, Ollama 외부 장애 통합 UX 회귀 0 |

---

## M1d — MCP 호환성 + threadpool

마일스톤 의도: MCP transport 통합 + 권한/identity 격리. 호환성 게이트 통과 전까지 SSE 제거 강행 금지.

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M1d-1 | MCP DB 조회 threadpool 분리 | Opus | §9 적용 내용 — 조회/쓰기 경계 | DB 조회만 threadpool, 사용자 조회·권한 검사는 main async, worker thread `_mcp_user` 재읽기 0건 |
| [ ] M1d-2 | MCP DNS rebinding 보호 정책 결정 | Opus | §9 MCP DNS rebinding 보호 정책 | `enable_dns_rebinding_protection=True` + 허용 Host, 또는 앱/라우터 Host allowlist 중 한쪽 채택 |
| [ ] M1d-3 | MCP 클라이언트 handshake 사전 조사 | Sonnet | §9 호환성 게이트 + §16 M1d 2번 | Codex CLI/Claude Desktop/Claude Code/Cline 각자 `/mcp` Streamable HTTP handshake 결과 기록 |
| [ ] M1d-4 | 권한/가시성 회귀 테스트 시나리오 작성 | Opus | §9 권한/가시성 회귀 테스트 + §15 MCP 권한 회귀 | 비멤버/히든 프로젝트/메타데이터 노출 차단 시나리오, transport 변경 전후 같은 시나리오 통과 |
| [ ] M1d-5 | 동시 요청 identity 격리 테스트 | Opus | §9 동시 요청 identity 격리 테스트 | 두 사용자 토큰 병렬 호출, interleave message, 교차 노출 0건, `_mcp_user` 누락 0건 |
| [ ] M1d-6 | handshake 통과 시 transport 교체 (한 commit/패키지 묶음, M1d-7+M1d-8 산출물 포함) | Opus | §9 한 commit/패키지 묶음 원칙 | **단일 commit/패키지로 다음 7종이 모두 포함되어야 회사 반입 결정 가능** — (a) `mcp_server.py` `/mcp` mount Streamable HTTP 교체, (b) `mcp_server.py` 상단 설명 갱신, (c) 설정 UI(`/admin` 또는 `/settings/mcp`) 안내 문구 갱신, (d) 사내 문서/README MCP 클라이언트 가이드(M1d-7 산출물), (e) `/mcp-codex` alias/deprecation 응답 + 접속 시 안내 메시지(M1d-7 산출물), (f) 실패 시 사용자 안내 메시지, (g) DNS rebinding 시뮬레이션 회귀 테스트(M1d-8 산출물). 분리 commit으로 일부만 들어가는 상태로는 마일스톤 종료 불가 |
| [ ] M1d-7 | 클라이언트 설정 이전 안내 게시 (M1d-6에 통합 완료) | Sonnet | §9 운영 절차 (2번) | README/사내 문서/`/mcp-codex` 접속 시 안내 메시지/MCP 토큰 발급 절차 — **M1d-6과 같은 패키지로 묶어 commit** |
| [ ] M1d-8 | DNS rebinding 시뮬레이션 회귀 테스트 (M1d-6에 통합 완료) | Opus | §9 회귀 테스트 (rebinding) | 허용 Host 정상 응답 / 외부 도메인 시뮬레이션 403 또는 400 차단 — **M1d-6과 같은 패키지로 묶어 commit** |
| [ ] M1d-9 | M1d exit criteria 점검 | Opus | §17 M1d 완료 기준 | **공통 항목** — threadpool contextvar 0건 / handshake 결과 기록 / 권한 회귀 통과 / identity 격리 통과 / DNS rebinding 차단 / 롤백 경로 식별. **분기 처리** — (a) **handshake 통과 분기**: M1d-6/7/8 단일 패키지로 transport 교체 + alias/deprecation + 회귀 테스트 모두 commit 완료, `/mcp-codex` 정리 일정 결정. (b) **handshake 미통과 클라이언트 존재 분기**: SSE 제거 보류, M1d 종료 기준은 "threadpool 적용 + 호환성 조사 기록 + 권한 회귀 테스트 통과 + 호환 정책 문서 작성"까지로 한정. transport 교체 commit은 회사 반입 패키지에 포함하지 않고 미통과 클라이언트의 사용 종료/버전 업그레이드 일정 확정 후 별도 마일스톤으로 분리. 어느 분기든 마일스톤 종료 자체는 가능 |

---

## M1 종료 게이트

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M1-end-1 | M1 종료 50 VU 부하 테스트 | Opus | §17 M1 종료 부하 테스트 | `database is locked` 0건 / 업로드·AI 동시성 가드 동작 / 에디터 lazy-load 회귀 0 / before/after 개선 폭 기록 |
| [ ] M1-end-2 | 회사 반입 패키지 결정 | Opus | §0 문서 라이프사이클 정책 | M1a/M1b/M1c/M1d 각 패키지가 §15 판단 우선순위(1~4) 통과 시 반입, 1건이라도 미통과면 보류 |

> **여기서 멈춤 권장**: M2 이후는 운영 구조 변경(성능 최적화 아님). M1 측정 결과로 진입 게이트 미통과면 §18 후속 후보로 유지.

---

## M2 — SSE service 분리 (선택적, 진입 게이트 통과 시)

마일스톤 의도: Supervisor + Front Router 골격 + SSE service 분리 첫 패키지.

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M2-0 | M2 진입 게이트 평가 | Opus | §13 진입 게이트 — SSE service (M2) | M1 측정에서 main app 재시작 SSE 재연결 폭주 → 일반 API p95 측정 가능 회귀 또는 SSE 이벤트 루프 점유로 업로드/조회 회귀 — 둘 중 하나 확인 시 진입 |
| [ ] M2-1 | 외부 포트 8000/8443 listener 소유자 결정 | Opus | §16 M2 0번 + §13 Tray/Supervisor + Front Router | 기존 외부 포트(8000/8443)를 **Tray/Supervisor/Front Router가 소유 vs main app이 계속 소유** 중 어느 쪽인지 명시 결정. 결정 결과가 M2 이후 모든 step의 라우팅 구조를 좌우하므로 M2-2 사용자-facing SSE 경로 결정과 정합되어야 함. rev14 장기 모델 권장안은 Tray/Supervisor/Front Router 소유 + 내부 service 분배 |
| [ ] M2-2 | 사용자-facing SSE 접근 경로 (a)/(b)/(c) 결정 | Opus | §13 사용자-facing SSE 접근 경로 | (a)/(b)/(c) 중 하나 명시 채택, 결정에 부합하는 측정 시나리오 정의. M2-1 결정과 정합되어야 함 — 라우터 소유 + (a) 라우팅, 라우터 소유 + (c) main app proxy, 또는 main app 소유 + (b) 별도 포트 |
| [ ] M2-3 | 외부 canonical URL 정책 결정 | Opus | §13 외부 canonical URL 정책 | `WHATUDOIN_PUBLIC_BASE_URL` 또는 supervisor 계산 origin 단일 소유, `request.base_url.hostname` 직접 사용 0건 grep, **적용 범위 표 작성** — `/settings/mcp` 안내 URL · AVR/`/remote` redirect · MCP 클라이언트 가이드 · 인증서 안내 · 외부 알림 링크 각각의 현재 코드 위치(app.py:673, app.py:4132 등)와 교체 후 형태 명시 |
| [ ] M2-4 | HTTP 8000 fallback 인증/쓰기 범위 결정 + baseline 표 | Opus | §13 HTTP 8000 fallback 인증/쓰기 범위 | (권장) HTTP unsafe write 차단 + AVR 화이트리스트 / (대안) IP whitelist + AVR 예외만 허용 — 한쪽 채택. **현재 동작 baseline 표 필수** — HTTP 8000 + IP whitelist 자동 로그인 상태에서 `/api/events` POST · `/api/doc/*` PUT · `/api/checklists` POST · `/api/upload/*` POST가 어떻게 응답하는지 표로 기록한 뒤 채택 정책에 부합하지 않는 응답 0건 검증 |
| [ ] M2-5 | HSTS 적용 여부 결정 | Opus | §13 HSTS 적용 여부 결정 | HTTP fallback 유지 중 미적용 / 종료 후 짧은 max-age 적용 — 한쪽 채택 |
| [ ] M2-6 | AVR Front Router 호환 정책 결정 | Opus | §13 AVR Front Router 호환 정책 | (a) AVR scope-out / (b) AVR 사용 — 회귀 테스트 시나리오 5종 정의 — 한쪽 채택 |
| [ ] M2-7 | (b) 옵션 비채택 결정(채택 시 CSP 비용 5종 처리) | Opus | §13 (b) 옵션 CSP/EventSource credential 비용 | (a)/(c) 채택 시 (b) 비채택 명시 / (b) 채택 시 connect-src·withCredentials·CORS·cookie·SAN 5종 명세 완료 |
| [ ] M2-8 | PyInstaller frozen self re-spawn 검증 | Sonnet | §15 PyInstaller sidecar 빌드 검증 | onedir 빌드, `sys.executable --service=<name>` spawn, freeze_support, graceful shutdown 모두 동작 |
| [ ] M2-9 | Supervisor 골격 도입 | Opus | §13 Tray/Supervisor + Front Router + supervisor lifecycle | 7단계 startup 순서, 내부 토큰 파일(`_RUN_DIR/internal_token` ACL 평문 + spawn 환경변수), startup 실패 vs runtime crash 카운터 분리 |
| [ ] M2-10 | Front Router 최소 구현 | Opus | §13 외부 라우팅 표 | `/api/stream` → SSE service, `/`/`/api/*` → Web API, `/uploads/meetings/*` → Web API의 `_ProtectedMeetingStaticFiles`, `/internal/*` 외부 차단 |
| [ ] M2-11 | strip-then-set forwarded 헤더 정책 | Opus | §13 Front Router strip-then-set forwarded 헤더 정책 | 외부 inbound forwarded 6종 폐기 후 라우터 재작성, 위조 헤더 3종 동시 주입 회귀 통과 |
| [ ] M2-12 | SlowAPI limiter trusted-proxy 통일 | Opus | §13 SlowAPI rate limiter key 통일 | `Limiter(key_func=auth.get_client_ip)` 또는 동등, 50 VU 분리 bucket / 위조 무시 / 분당 11회 429 회귀 통과 |
| [ ] M2-13 | TRUSTED_PROXY + 외부 직접 접근 차단 한 세트 | Opus | §13 `TRUSTED_PROXY` + 외부 직접 접근 차단 | (a) loopback bind/방화벽 / (b) 외부 직접 호출 실패 / (c) 신뢰 proxy 외 X-Forwarded-For 무시 — 셋 모두 적용 |
| [ ] M2-14 | Front Router CSRF Host 보존 조건 | Opus | §13 Front Router CSRF Host 보존 조건 | Host 보존 또는 X-Forwarded-Host 채택, unsafe POST/PUT/DELETE 라우터 경유 통과, Origin 위조 403 |
| [ ] M2-15 | HTTPS probe middleware 라우터 호환 | Opus | §13 Front Router HTTPS probe middleware 호환 조건 | 라우터 뒤 비활성화 또는 `scope["scheme"]` 재구성, 라우터 경유 페이지 probe HTML 0, 외부 직접 8000 인증서 안내 회귀 0 |
| [ ] M2-16 | SSE broker SSE service 프로세스로 이전 | Opus | §13 채택 — SSE service 분리 + main → SSE 통신 | broker 이전, Web API는 publish IPC 클라이언트만, 내부 endpoint loopback bind |
| [ ] M2-17 | 내부 토큰 인증 + healthcheck + 로그 분리 + crash-loop | Opus | §13 supervisor lifecycle + watchdog + §14 watchdog | `/internal/publish` 토큰 인증, `/healthz`, 5분 3회 crash-loop 차단, degraded 표시 |
| [ ] M2-18 | publish 실패 유실 정책 적용 | Opus | §13 publish 실패 유실 정책 | DB rollback 금지, `sse_publish_failure` 카운터, 클라이언트 SSE 재연결 시 재조회 |
| [ ] M2-19 | Front Router SSE proxy 6종 조건 적용 | Opus | §13 Front Router SSE proxy 조건 | buffering/compression 비활성, idle timeout, disconnect 전파, cookie/header 통과, `/internal/*` 차단 |
| [ ] M2-20 | M2 종료 부하 테스트 + 정책별 증거 인덱스 작성 | Opus | §17 M2 완료 기준 | 50 SSE + Web API 재시작 끊김 0(또는 (c) 결정 시 재연결 시간 단축), 일반 API 회귀 0. **정책별 증거 인덱스 필수** — 다음 항목 각자에 (증거 파일 경로 / 로그 발췌 / Playwright 또는 회귀 테스트명) 3종을 표로 기록. **라우팅/보안 11종**: ① 외부 포트 소유자(M2-1), ② 사용자-facing SSE 경로 결정과 측정(M2-2), ③ canonical URL 적용 범위 — `request.base_url.hostname` 직접 사용 0건 grep 결과 + `/settings/mcp` 응답에 외부 canonical URL + 내부 loopback/`8000` 0건(M2-3), ④ HTTP 8000 fallback baseline 표 + 채택 정책에 부합하지 않는 응답 0건(M2-4), ⑤ HSTS 적용/미적용 결정과 max-age(M2-5), ⑥ AVR scope-out 또는 회귀 테스트 5종 통과(M2-6), ⑦ strip-then-set 위조 헤더 3종 동시 주입 회귀(M2-11), ⑧ SlowAPI limiter 50 VU 분리 bucket + 위조 무시 + 분당 11회 429 회귀(M2-12), ⑨ TRUSTED_PROXY + 외부 직접 차단 3종(M2-13), ⑩ CSRF Host 보존 + Origin 위조 차단(M2-14), ⑪ HTTPS probe middleware 라우터 경유 페이지 probe HTML 0 + 외부 직접 8000 회귀 0(M2-15). **Supervisor/lifecycle 4종**: ⑫ Supervisor 상태 표시 — Web API/SSE service의 pid·health·restart_count·last_error·log path 모두 표시/기록(M2-9·M2-17), ⑬ service crash 1회 자동 재시작 + 5분 3회 crash-loop 차단/degraded 전환 검증(M2-17), ⑭ `sse_publish_failure` 카운터 동작 + SSE service 강제 종료/재시작 시 카운터만 증가하고 정상 흐름 0건(M2-18), ⑮ DB rollback 금지 — publish 실패 시 사용자 write가 DB commit 정상 + SSE 재연결 시 화면 재조회로 최종 상태 일치(M2-18). **Build·Infra·SSE proxy 3종**: ⑯ PyInstaller frozen 환경 self re-spawn 빌드 검증 통과 — onedir 빌드 산출물에서 `sys.executable --service=<name>` spawn, multiprocessing freeze_support, 단일 EXE 종료 신호로 모든 service graceful 종료(M2-8 결과), ⑰ main app(Web API) → SSE service publish 채널 내부 토큰 인증 — 토큰 없는 `/internal/publish` 호출 401, 잘못된 토큰 401, 정상 토큰 200 회귀 테스트(M2-17), ⑱ Front Router SSE proxy 6종 조건 — buffering 비활성/compression 미적용/idle timeout이 SSE heartbeat보다 길게 설정/client disconnect 시 upstream close 전파/session cookie·Authorization·X-Forwarded-For 통과/외부 `/internal/*` 차단을 (a) 실제 브라우저(Chrome/Edge) network trace EventStream 탭, (b) 50 SSE 연결 스크립트 평균 publish→수신 지연, (c) `curl https://host/internal/publish` 외부 호출 404/403 응답 — 3종 증거로 모두 확인(M2-19). **추가 보안 검증**: MCP handshake 실패/access log에 `Authorization` raw 값과 Bearer token 원문 0건 grep(§17 M2 완료 기준), `/uploads/meetings/*` 비멤버 권한 검사 회귀(M2-10) |

---

## M3 — Scheduler service 분리

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M3-0 | M3 진입 게이트 평가 | Opus | §13 진입 게이트 — Scheduler service (M3) | scheduler write로 일반 API 응답성 회귀 측정 또는 multi-worker 전환 검토 시점일 때만 진입 |
| [ ] M3-1 | startup maintenance 단일 owner 표 확정 | Opus | §11 startup maintenance 작업의 단일 소유자 | 6개 작업의 owner 명시(Web API lifespan / Scheduler / 수동 admin), 같은 job 두 service 동시 보유 0 |
| [ ] M3-2 | APScheduler 별도 프로세스 이전 | Opus | §16 M3 1번 + §13 Scheduler service | 별도 spawn, main app lifespan에서 `scheduler.start()` 제거, IMMEDIATE 헬퍼 사용 |
| [ ] M3-3 | healthcheck/로그/graceful shutdown 검증 | Sonnet | §13 watchdog + §14 graceful shutdown | `/healthz`, 단독 로그 파일 회전, shutdown 순서 |
| [ ] M3-4 | M3 종료 부하 + exit criteria 점검 | Opus | §17 M3 완료 기준 | 예약 작업 중복 실행 0건. **single-owner 정책 위반 3종 증거** — Web API service + Scheduler service 동시 기동 상태에서 (a) 중복 history row 0건, (b) 중복 알림 0건, (c) 백업 파일 동시 쓰기 0건이 각각 별도 측정/로그로 확인됨. 야간 정리 작업 또는 수동 실행 중 `database is locked` 0건, 일반 API p95 500ms 이하. job lock 메커니즘이 적용된 경우 owner 정책의 보조 수단으로만 동작(lock만으로 owner 미지정 작업 통과 0건) |

---

## M4 — Ollama service 분리 + uvicorn 동시성 결정

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M4-0 | M4 진입 게이트 평가 | Opus | §13 진입 게이트 — Ollama service (M4) | M1c 단계 ① 후에도 hang/메모리 누수가 main app 응답성에 측정 가능 영향일 때만 진입 |
| [ ] M4-1 | Ollama service 별도 프로세스 + JSON IPC | Opus | §8 적용 내용 — Ollama service 코어 분리 + §13 main → Ollama | `/internal/llm` 단일 채널, 내부 토큰, busy/unavailable 거부 응답 |
| [ ] M4-2 | M1c-9 통합 UX의 service 분리 후 유지 + 강제 종료/hang 검증 | Sonnet | §8 Ollama 서버 장애 통합 처리 + §17 M4 완료 기준 | M1c-9에서 main app 안에 구현한 통합 UX(`ConnectionError`/timeout/5xx/포화 → "AI 사용 불가")가 Ollama service 분리 후에도 동일하게 동작하는지 회귀(신규 구현 아님). **새로 추가되는 검증** — Ollama service 강제 종료/hang/메모리 누수 시뮬레이션 시 main app 응답성 저하 0, IPC timeout 시 사용자에게 같은 "AI 사용 불가" UX, 트레이가 service 단독 재시작 시 다음 요청부터 정상 응답. admin UI Ollama health 표시는 M1c-9 결과 재사용 |
| [ ] M4-3 | uvicorn `limit_concurrency` 최종 결정 | Opus | §12 sidecar 도입 여부에 따른 분기 | 측정 결과로 (가) 분기 후보 결정 또는 미적용, SSE service 미적용 명시 |
| [ ] M4-4 | M4 종료 부하 + exit criteria 점검 | Opus | §17 M4 완료 기준 | hang/강제 종료 시 main app 영향 0, AI 처리 중 일반 API p95 500ms 이하, 트레이 단독 재시작 |

---

## M5 후보 — Upload/Media service 분리

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M5-0 | M5 진입 게이트 평가 | Opus | §13 진입 게이트 — Upload/Media service (M5 후보) | M1c 후에도 업로드 중 Web API p95/RSS/SSE 수신 지연 회귀 시에만 진입 |
| [ ] M5-1 | Web API staging 파일 + 내부 IPC 채널 | Opus | §13 main → Upload/Media service 통신 | staging root 정규화, 사용자 경로 미신뢰, 큰 파일 base64 미사용 |
| [ ] M5-2 | Media service (저장/검증/썸네일/메타데이터) | Sonnet | §7 M1c 이후 후보 + §13 서비스 책임 표 | DB write 0, 내부 토큰만 호출, 권한 검사는 Web API에 남김 |
| [ ] M5-3 | M5 종료 부하 + exit criteria 점검 | Opus | §17 M5 후보 완료 기준 | 20MB/10MB 혼합 업로드 중 일반 API p95/RSS/SSE 수신 지연이 M1c 대비 회귀 0, Media 실패/강제 종료 시 Web API/SSE/MCP/LLM 정상 동작. **소유권 증거 3종** — (a) Media service의 SQLite write 0건(grep + 코드 리뷰), (b) Web API가 외부 업로드 endpoint·인증/권한 검사·대상 리소스 검증·DB metadata write·SSE publish/history/audit 후처리를 모두 보유(소유 함수 목록 + 회귀 테스트), (c) Web API가 staging root 밖 경로를 정규화 후 거부하는 경로 정규화 단위 테스트 통과(악성 파일명/`..` 우회/심볼릭 링크 시도 모두 거부). Media service는 외부 노출 없이 내부 토큰으로만 호출됨이 외부 직접 호출 회귀 테스트로 확인됨 |

---

## M6 후보 — MCP write/edit + DB command service

| step | 제목 | 모델 | §참조 | exit criteria |
|------|------|------|-------|---------------|
| [ ] M6-0 | M6 진입 게이트 평가 | Opus | §13 MCP write/edit 추가 정책 + §16 M6 후보 | MCP write/edit 운영 요구 발생 시에만 진입 |
| [ ] M6-1 | write/edit tool 위험도 분류 | Opus | §16 M6 1번 | destructive 분류, 우선 후보(일정 생성·체크리스트 추가) 좁은 범위로 시작 |
| [ ] M6-2 | Web API command endpoint + MCP proxy | Opus | §9 MCP write/edit 추가 정책 (단기 원칙) | MCP service 직접 SQLite write 0, 모든 write/edit Web API write path 경유, 권한·lock·history·SSE publish 통합 |
| [ ] M6-3 | DB command service 도입 결정 | Opus | §16 M6 3~4번 + §13 DB command service | 측정으로 단일화 필요 확인 시 도입, 미확인 시 §18 후속 유지 |
| [ ] M6-4 | M6 종료 회귀 + exit criteria | Opus | §17 M6 후보 완료 기준 | hidden-project 차단 / lock / history / SSE publish 통합 / 베이스라인 회귀 0. **MCP write owner 원칙 재확인 필수** — MCP service 코드에 직접 SQLite write 0건(grep + 코드 리뷰), 모든 write/edit tool이 Web API command endpoint(또는 도입 시 DB command service) 경유로 동작함이 호출 grep으로 확인됨. M6-2에서 잡은 원칙이 후속 tool 추가로 회귀하지 않았는지 종료 단계에서 한 번 더 검증 |

---

## 진행 상태 보드

> 카운트 기준: 진입 게이트(`-0`) step을 포함한 row 총수.

| 마일스톤 | 진입 게이트 | 진행 중 | 완료 | 비고 |
|---------|-----------|--------|------|------|
| M1a | (즉시 가능) | **완료** (Group B spec 업데이트 후속) | **13/13** | baseline run_181951/ + m1a11_run_193829/ 보관; M1a-12 26건 fail은 Group A 사전 부채 + Group B 디자인 변경 expected + Group C orthogonal로 triage 완료 |
| M1b | M1a 완료 | — | 0/17 | — |
| M1c | M1b 완료 | — | 0/13 | Ollama 외부 장애 통합 UX 포함 |
| M1d | M1c 완료 | — | 0/9 | M1d-6/7/8 단일 패키지 묶음 필수 |
| M1 종료 게이트 | M1a~M1d 완료 | — | 0/2 | 회사 반입 결정 |
| M2 | §13 진입 게이트 통과 | — | 0/21 | 외부 포트 소유자 결정 step + baseline 표 포함 |
| M3 | §13 진입 게이트 통과 | — | 0/5 | M3-0 게이트 평가 포함 |
| M4 | §13 진입 게이트 통과 | — | 0/5 | M4-0 게이트 평가 포함 |
| M5 후보 | M1c 회귀 측정 | — | 0/4 | M5-0 게이트 평가 포함, 조건부 |
| M6 후보 | 운영 요구 발생 | — | 0/5 | M6-0 게이트 평가 포함, 조건부 |

## step 결과 기록 형식

step 완료 시 본 todo 또는 별도 진행 노트에 다음 4종을 기록한다(증거 인덱스 필수). M2-20 정책별 증거 인덱스 운영 방식을 step 단위에도 동일하게 적용해 회사 반입 판단 자료가 누락되지 않게 한다.

```
M1a-1 완료 (2026-MM-DD)
- 변경: _workspace/perf/{locust,fixtures,scripts,baseline_2026-MM-DD}/ 생성, requirements-dev.txt 추가. 운영 코드 변경 없음.
- 증거: 디렉터리 ls 결과 경로(_workspace/perf/M1a-1_dirstructure.txt) / pip install 로그(_workspace/perf/M1a-1_install.log) / 관련 commit hash.
- 회귀: 해당 없음(인프라만 추가, 코드 변경 0) 또는 통과한 Playwright/회귀 테스트명(예: tests/phase33_doc_*.spec.js).
- 다음 step 영향: M1a-2 fixture seed 스크립트가 _workspace/perf/fixtures/ 경로 가정.
```

기록 항목 4종:

- **변경**: 1~2줄 요약. 어떤 함수/파일에 무엇이 추가/수정됐는지. diff 전체 보존 금지.
- **증거**: step exit criteria 검증에 사용한 파일 경로 / 로그 발췌 / Playwright 또는 회귀 테스트명. exit criteria가 회귀 0건 같은 부재형 조건이면 그 부재를 확인한 grep 명령/결과를 적는다.
- **회귀**: 통과한 회귀 테스트명 또는 회귀 영향 없음 사유. step 모델이 `[Opus]`인 경우(동시성/권한/보안) 통과 회귀 테스트가 최소 1건 이상 명시되어야 한다.
- **다음 step 영향**: 후속 step의 가정/입력에 영향이 가는 사실 1줄. 영향 없으면 생략 가능.

다음 step 시작 전: 직전 step의 위 4종 기록을 메인 컨텍스트에 보존, 하위 에이전트가 만든 diff 전체는 보존하지 않는다. 증거 파일 경로는 메인이 추후 회사 반입 판단(M1-end-2) 단계에서 일괄 점검 가능해야 한다.

---


## 진행 로그

step별 결과 기록 4종(변경/증거/회귀/다음 step 영향)은 본문 비대화를 피하기 위해 마일스톤별 별도 파일로 분리한다(2026-05-09 도입).

- M1a: [`성능 개선 진행 결과(M1a).md`](성능%20개선%20진행%20결과(M1a).md)
- M1b 이후 진입 시 동일 패턴으로 `성능 개선 진행 결과(M1b).md` 등 신설.

본 todo는 **체크박스 + 진행 상태 보드 + 진행 가능 여부 한 줄**만 유지한다. 상세 4종 기록·증거·발견 이슈는 위 파일 참조.

