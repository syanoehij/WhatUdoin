# 성능 개선 진행 결과 (M1a)

`성능 개선 todo.md` 1차 실행 todo의 M1a 마일스톤 step별 결과 기록 4종(변경/증거/회귀/다음 step 영향)을 보관한다. todo 본문 비대화를 막기 위해 2026-05-09에 분리.

기록 형식 명세는 `성능 개선 todo.md` § "step 결과 기록 형식" 절 참조. M1b 이후 진입 시 동일 패턴으로 마일스톤별 별도 파일(`성능 개선 진행 결과(M1b).md` 등) 신설.

---

## M1a — 기준선 측정 + 프론트엔드 lazy-load

### M1a-1 완료 (2026-05-09)

- **변경**: `_workspace/perf/{locust,fixtures,scripts,baseline_2026-05-09}/` 4개 디렉터리에 `.gitkeep`(짧은 용도 주석 포함) 배치, repo root에 `requirements-dev.txt` 신설(`locust` 단일 항목, 운영 `requirements.txt`와 분리, PyInstaller onedir 패키지에 미포함 명시). 운영 코드 변경 없음. CLAUDE.md `.claude/workspaces/current/` 정책은 임시 산출물 한정으로 해석하고, 동결된 plan §15 사양·`.gitignore`에서 `_workspace/`가 ignore되지 않는 점·baseline은 회사 반입 판단 자료로 영구 보관 대상이라는 근거로 `_workspace/perf/` 채택. **수정**: qa 사후 검증에서 한글 주석 + Windows pip cp949 기본 인코딩 충돌(`UnicodeDecodeError`)로 실제 설치가 막히는 이슈 발견. `requirements-dev.txt` 주석을 ASCII로 재작성(`locust` 항목은 그대로).
- **증거**: 디렉터리 트리 — `_workspace/perf/M1a-1_dirstructure.txt`(`find _workspace/perf -type f -o -type d | sort` 결과 + 운영 코드 grep 절차). 운영 코드 변경 0 검증 — `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 출력 없음(2026-05-09 측정).
- **회귀**: 해당 없음(인프라만 추가, 코드 변경 0). `requirements-dev.txt`는 신규 파일로 기존 빌드 경로에 영향 없음(설치는 후속 step에서 사용자 명시 시점에 실행).
- **다음 step 영향**: M1a-2 fixture seed/cleanup 스크립트가 `_workspace/perf/fixtures/` 경로 가정. M1a-3 DB snapshot은 `_workspace/perf/baseline_2026-05-09/db_snapshot/` 경로 가정. M1a-5 locust 시나리오는 `_workspace/perf/locust/` 경로 가정. M1a-6 SSE 분리 스크립트가 `httpx`/`aiohttp` 추가를 요구하면 그 시점에 `requirements-dev.txt`에 추가. baseline 디렉터리 이름은 step 시작 시점(2026-05-09) 기준이며, 실제 측정 시점이 다를 경우 측정 시점 디렉터리(`baseline_<측정일>/`) 추가 운용.

### M1a-2 완료 (2026-05-09)

- **변경**: `_workspace/perf/fixtures/{seed_users.py, cleanup.py, README.md}` 신설. `seed_users.py` — env(`WHATUDOIN_PERF_FIXTURE=allow`) + WAL/SHM 존재 abort 가드, 50개 `test_perf_001`~`test_perf_050` 계정 멱등 INSERT(이미 있으면 skip), 측정 전용 `test_perf_team` INSERT OR IGNORE, `sessions` 테이블에 cookie 50개 사전 생성(UTC `%Y-%m-%d %H:%M:%S` + 7일 max_age, `auth.get_session_user()` 파싱과 호환), `_workspace/perf/fixtures/session_cookies.json` 출력. `cleanup.py` — 동일 가드 + 모든 DELETE에 `test_perf_` 매칭 WHERE 절(`name LIKE 'test_perf_%'` 직접 매칭 또는 `user_id IN (SELECT … WHERE name LIKE …)` 서브쿼리), WHERE 누락 DELETE 0건, 트랜잭션 단일 commit/rollback. 운영 코드 변경 0건. 평문 비밀번호 정책은 `database.get_user_by_password()`의 평문 비교(database.py:2324)와 일치 — fixture만 평문, 운영 흐름 동일.
- **증거**: qa 사후 검증 보고(M1a-2 사이클) — (a) DELETE 4건 전체 표(seed_users.py:142 sessions, cleanup.py:88 sessions, cleanup.py:114 users, cleanup.py:121 teams) 모두 `test_perf_` 매칭 WHERE 통과, (b) env 미설정 abort dry-run exit code 1 + 메시지 확인, (c) WAL 파일 시뮬레이션(임시 디렉터리 + 가짜 `.db-wal`) 후 WHATUDOIN_DB_PATH override로 abort dry-run 양쪽 스크립트 모두 통과(임시 디렉터리는 검증 직후 정리), (d) `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 출력 0건.
- **회귀**: 운영 코드 변경 0건 + fixture는 측정 시점에만 호출되므로 운영 흐름 회귀 영향 없음. Playwright 회귀 미적용(인프라 추가만, 운영 동작 미변경).
- **다음 step 영향**: M1a-3 DB snapshot은 seed 첫 실행 직전/직후 안전판. **seed_users.py 첫 실 실행은 M1a-3 snapshot 도입 + 사용자 승인 후로 미룸**(가드는 통과하나 첫 INSERT는 M1a-3 복원 절차가 준비된 뒤 진행). M1a-5 locust는 `_workspace/perf/fixtures/session_cookies.json`을 읽어 cookie 주입 (`/api/login` 호출 0). M1a-13 점검 시 fixture 멱등성 + cleanup 가드 회귀 재확인 필요.

### M1a-3 완료 (2026-05-09)

- **변경**: `_workspace/perf/scripts/{snapshot_db.py, restore_db.py}` + `_workspace/perf/README.md` 신설. `snapshot_db.py` — env(`WHATUDOIN_PERF_FIXTURE=allow`) + WAL/SHM 존재 abort 가드, `db_snapshot/` 이미 있으면 timestamp suffix(`db_snapshot_<HHMMSS>/`) 자동 생성(무조건 덮어쓰기 금지), 복사 후 `PRAGMA integrity_check`로 무결성 확인, 실패 시 분석용 디렉터리 보존. `restore_db.py` — 동일 env/WAL 가드 + `--confirm-overwrite` 인자 강제, 복원 직전 운영 DB를 `whatudoin.db.before-restore-<timestamp>` 사이드카로 보관(덮어쓰기 직전 try/except 없는 단순 호출로 사이드카 실패 시 복원 자체가 abort), 복원 후 PRAGMA integrity_check. README — 디렉터리 구조, 8단계 측정 절차(서버 종료 → snapshot → seed → 시작 → 측정 → 종료 → cleanup → 필요 시 restore), 환경 변수 표 3종(`WHATUDOIN_PERF_FIXTURE`/`WHATUDOIN_DB_PATH`/`WHATUDOIN_PERF_BASELINE_DIR`), snapshot/restore 사용법, M1a-3 snapshot vs M1b backup.py 차이, fixtures/README.md와 perf/README.md 역할 구분, 클린 셧다운에서는 WAL 없으므로 복사 대상이 `.db` 하나로 줄어드는 동작 명시. 운영 코드 변경 0.
- **증거**: qa 사후 검증 보고(M1a-3 사이클) — (a) 가드 grep 위치(snapshot env L68/WAL L81-92/timestamp suffix L117-125/integrity L166, restore env L83/WAL L96-107/--confirm L110-117/사이드카 L151-155/integrity L166) 확인, (b) snapshot dry-run 3종(env 미설정 abort, WAL 시뮬레이션 abort, 멱등성 테스트로 두 번째 실행 시 `db_snapshot_071802/` 추가 생성 + 첫 snapshot 보존) 임시 디렉터리에서만 수행 후 정리, (c) restore dry-run 3종(env/--confirm/WAL) 모두 exit code 1, (d) 코드 흐름 정합성 — env 가드가 모든 파일 작업·sqlite3 연결보다 선행, 사이드카 생성이 overwrite보다 선행, integrity 실패 시 snapshot 디렉터리 보존, (e) `git diff --stat` 운영 코드 0건.
- **회귀**: 운영 코드 변경 0건이라 회귀 영향 없음. snapshot/restore는 측정 라이프사이클 진입 시점에만 사용.
- **다음 step 영향**: 첫 실 snapshot 실행은 사용자 승인 후 별도 사이클(seed_users.py 실 실행 직전 묶어서 진행). M1a-7 baseline 측정 직전 snapshot 호출이 측정 절차의 첫 단계. M1b WAL 모드 활성화 직전에는 별도 백업 가드(M1b-1)가 필요하다는 점이 README에 반영됨. fixtures/README.md(fixture 단위 절차) vs perf/README.md(전체 라이프사이클) 구분이 명시되어 후속 step 작성자가 어디에 절차를 추가할지 헷갈리지 않음.

### M1a-4 완료 (2026-05-09)

- **변경**: `_workspace/perf/background_requests.md` 신설 — 페이지×요청 매트릭스 형태 인벤토리. plan §15 요구 5종 중 4종 코드 위치와 함께 등재(#1 알림 뱃지 60s `base.html:1332`, #2 체크리스트 viewer poll 30s `check.html:1697,1699,1746` + editor heartbeat 120s `check.html:1679` `wu-editor.js:1485`, #3 에디터 lock heartbeat 30s `wu-editor.js:1485` `doc_editor.html:474`, #4 SSE `events.changed`/`projects.changed` 수신 후 calendar/kanban refetch `calendar.html:604-605` `kanban.html:593`). #5(프로젝트 색상/팀 메타 polling)는 `static/js/`+`templates/` 전체 grep에도 setInterval 없음 — `home.html:251` `loadProjColors()`와 `kanban.html:605`/`calendar.html:571` `/api/teams/members`는 페이지 로드 1회뿐임을 인벤토리에 명시("코드에서 미확인" 표기). DOM 전용 setInterval 4종(`_titleTimer` `_countdownTimer` `_cooldownTimer` 등 + `project.html:1103`)은 별도 §5에 분리 표기, 네트워크 미발생 spot-check 통과로 인벤토리 본문에서 제외. 다중 탭 정량 모델 §4 — 단일 탭 50 VU 분당 100건/분 vs 25%/12 VU 2탭 시나리오 분당 148건/분(+48%) 표 + 폴링 주기 추정 근거. 운영 코드 변경 0건. qa minor 발견(check.html viewer poll setInterval 호출 위치 1746 누락) 즉시 보완.
- **증거**: qa 사후 검증 보고(M1a-4 사이클) — (a) §15 5종 등재 자가 점검 5/5 통과, (b) 코드 위치 spot-check 4종(`base.html:1332` `setInterval(_updateBadge, 60000)`, `check.html:1679` `_heartbeatInterval` 120000ms, `calendar.html:604-605` `wu:events:changed`/`wu:projects:changed` 리스너, `wu-editor.js:1485` `_lockHeartbeat` heartbeatMs 인자) ±0 라인 일치, (c) `static/js/`+`templates/` `setInterval(` 8건 전체 분류 — network 4 + DOM 전용 4, 누락 0, (d) DOM 전용 3종 spot-check로 fetch/XHR 호출 0건 확인, (e) `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 출력 0건.
- **회귀**: 운영 코드 변경 0 + 분석 문서만 추가 → 회귀 영향 없음. M1a-5 locust 시나리오 가중치/polling 모델의 입력으로만 사용.
- **다음 step 영향**: M1a-5 locust 시나리오는 본 인벤토리의 (a) 페이지×요청 매트릭스로 background polling task 구성, (b) §4 다중 탭 정량 모델로 25%/12 VU 2탭 분기 가중치 결정, (c) SSE 수신 후 refetch endpoint 매핑(calendar/kanban → `/api/events`/`/api/kanban`)으로 SSE 이벤트 트리거 후 배경 fetch 모델링. SSE 연결 자체는 §15 SSE 측정 분리 정책에 따라 M1a-6 분리 스크립트가 담당. M1a-7 측정 시 본 모델의 추정치(분당 100/148→172건; M1a-5 보완에서 doc_lock 24건/분 추가로 갱신)와 실측 호출 수 비교로 인벤토리 정확도 점검.

### M1a-5 완료 (2026-05-09)

- **변경**: `_workspace/perf/locust/{locustfile.py, _cookie_loader.py}` 신설 + 같은 사이클 내 qa 발견 이슈 4종 보완. locustfile.py — HTTPS 8443 고정(`verify=False` + `urllib3.disable_warnings`), `session_cookies.json` round-robin cookie 주입(50명 한계 초과 abort, wrap-around 금지), `SingleTabUser`(weight=75) / `MultiTabUser`(weight=25) 분리, §15 가중치(view_pages 40 / event_crud 25 / upload_file 10 / ai_parse 5 / SSE 0 — M1a-6 분리), background polling gevent greenlet(알림 60s, viewer poll 30s, editor heartbeat 120s, doc lock heartbeat 30s — 다중 탭 always-on worst-case), `WU_PERF_RESTRICT_HEAVY` 분기로 초기 단계 업로드 0.5MB/AI 1% 이하 제한. CSRF/Origin은 `WU_PERF_ORIGIN` env + `urlparse(self.host).hostname` 동적 추출(실서버 IP `--host` 변경만으로 `app.py:488 _check_csrf` 정합). 보완 사이클 4종 적용 — (a) `min(_UPLOAD_SIZE_BYTES, 1024*1024)` → `max(0, _UPLOAD_SIZE_BYTES-8)` 비제한 5MB / 제한 0.5MB 정합, (b) 다중 탭 상한 주석 148→172건/분 + `doc_lock 24건/분` 분해 표 추가, (c) `_ORIGIN`/`_UNSAFE_HEADERS` 모듈 상수 0건, `self._origin`/`self._unsafe_headers` 인스턴스 속성 대체, (d) cleanup.py events DELETE 활성화 — `created_by`가 server-side overwrite되는 사유로 `title LIKE 'test_perf_evt_%'` 매칭 채택, locustfile event_crud 생성 title prefix `test_perf_evt_<random>` 정합. background_requests.md §4 표 갱신(172건/분, doc_lock 행 추가, worst-case 모델링 의도 명시). 운영 코드 변경 0.
- **증거**: qa 1차 검증 — §1~10 PASS, WARN 2건 + INFO 2건 식별. qa 재검증(보완 사이클) — 8개 항목 모두 PASS + (a)~(e) spot-check 통과(`min(...,1024*1024)` 0건, `_ORIGIN`/`_UNSAFE_HEADERS` 모듈 상수 0건, events DELETE BEGIN–COMMIT 단일 transaction:89-136 line 104, `/api/upload/image` `app.py:3713-3734` 디스크 only/`/api/ai/parse` 3813-3825 DB write 없음 spot-check, assignee=self 시 `app.py:1747` `if name != user["name"]` 분기로 알림 미생성 정합). py_compile 양 파일 통과. cleanup.py env 미설정 abort dry-run exit 1. `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 0건.
- **회귀**: 운영 코드 변경 0 + 측정 인프라만 → 회귀 영향 없음. cleanup.py events DELETE는 `title LIKE 'test_perf_evt_%'` WHERE 가드로 운영 데이터 0건 매칭(qa events.title 운영 row spot-check로 prefix 충돌 없음 확인 필요 — M1a-7 첫 실 cleanup 직전 사용자 dry-run 권장).
- **다음 step 영향**: M1a-6 SSE 분리 스크립트가 본 시나리오와 cookie pool 50명 공유(GET 전용 endpoint + auth.py single-connection-per-session 미강제). M1a-7 실 측정은 (a) snapshot → seed → 서버 시작 → locust + sse_keepalive 동시 실행 → 종료 → cleanup 순서. (b) `--host` 변경 시 `_origin` 자동 추출. (c) `WU_PERF_RESTRICT_HEAVY=true`는 1~10 VU 초기 단계 한정. fixture-owned 테이블 매핑은 events 단일 — checklists/meetings/attachments fixture 추가 시 cleanup.py에 DELETE 추가 필요(현재 docstring TODO).

### M1a-6 완료 (2026-05-09)

- **변경**: `_workspace/perf/scripts/{sse_poc.py, sse_keepalive.py}` + `_workspace/perf/baseline_2026-05-09/sse_poc_PLACEHOLDER.md` 신설, `requirements-dev.txt`에 `httpx` 추가(ASCII 주석 유지). httpx 기반 채택 — locust SSE-only 시나리오는 카운트/timeout 동작 자체가 검증 대상이라 circular validation 회피. PoC(`sse_poc.py`) — N개(10/30/50) 동시 연결 60s 유지, 단일 연결당 (성공/끊김/timeout) 기록, locust 통계와 완전 분리된 독립 asyncio 프로세스. keep-alive(`sse_keepalive.py`) — 50 연결 유지 + main locust 시나리오와 동시 실행 가능, CSV/MD 별도 파일 출력. 별도 지표 3종: (a) 연결 유지 성공률 `ok/total*100` + `disconnected_early`(엄격: 재연결 성공도 실패 카운트), (b) `inter_arrival_ms` 리스트 — broker.py가 `id:`/server-side timestamp 부여 안 해 publish→수신 absolute latency 측정 불가 한계 docstring/CSV 주석 명시(대부분 ~25s ping 주기 분포), (c) `queue_full_est=0` 고정 + M1c-10 서버 QueueFull 카운터 도입 후 정확 측정 명시. SSE endpoint 사전 조사: `app.py:1861` `GET /api/stream`, `app.py:1879` `event: {ev}\ndata: {json}\n\n`, `app.py:1885-1886` 25s ping, `app.py:1882` 3s `is_disconnected()`, `broker.py:24` `asyncio.Queue(maxsize=100)`. 50 연결 RAM 추정 클라 5~15MB + 서버 0.2~0.4MB(plan §14 main 100~160MB 대비 무시 가능). 운영 코드 변경 0.
- **증거**: qa 사후 검증 — 10개 항목 모두 통과. SSE endpoint spot-check 5종(라우트 라인/이벤트 형식/25s ping/3s disconnect 감지/Queue maxsize=100) 모두 코드와 ±0 라인 정합. (b) latency 한계 docstring 위치 — `sse_poc.py:29-30, 250-255`, `sse_keepalive.py:19-22, 256`. (c) QueueFull 고정 위치 — `sse_poc.py:268-272`, `sse_keepalive.py:102, 139`. requirements-dev.txt 비-ASCII 0건 + pip --dry-run 통과(httpx 0.28.1 이미 설치). py_compile/import-check 양 파일 통과. `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 0건.
- **회귀**: 운영 코드 변경 0. SSE 측정 인프라만 추가, `/api/stream`/`broker.py` 동작 미변경.
- **다음 step 영향**: M1a-7 실 부하 측정 시 `python sse_keepalive.py --n 50 --host https://localhost:8443 --duration <측정 윈도우>`를 main locust와 동시 실행. (b) inter-arrival은 ping 25s 분포에 편향되어 absolute latency 대체값으로만 사용 — M1c-10 broker.py에 server-side timestamp 도입 후 정확 측정 가능. (c) QueueFull 0건 고정값은 baseline 시점 한정 — M1c-10 도입 후 서버 카운터로 교체. main API p95와 SSE 지표는 `baseline_2026-05-09/` 안에서 별도 파일(`sse_poc_<HHMMSS>.md`, `sse_keepalive_<HHMMSS>.csv`, `locust_<HHMMSS>.csv`)로 분리 기록.

### M1a-7 완료 (2026-05-09, run_181951)

- **변경**: `_workspace/perf/scripts/run_baseline_m1a7.py` 신설(978줄 Python orchestrator — PowerShell runner는 PS 5.1 quirk 누적으로 폐기·archaeology 보존). 9-phase 자동 측정 — pre-flight / 환경 메타데이터 / snapshot+seed+SHA256 검증 / uvicorn Popen + httpx readiness / sanity 1VU×30s / 본 측정 5단계(1/5/10/25/50 VU × 60s, 1~10 단계 `WU_PERF_RESTRICT_HEAVY=true`) / SSE keep-alive 50 VU 병렬 / graceful shutdown / cleanup + 검증 / summary.md 생성. `_run_py`에 stdout/stderr 디스크 저장(`<basename>_stdout.log` `_stderr.log`) + locust `--loglevel INFO --logfile`로 silent fail 진단 가능. sanity gate 부재형 통과 금지(Request Count=0 hard fail). 보완 작업 — `_cookie_loader.py:26` `parents[2]` → `parents[1]` 경로 수정, `locustfile.py` cookie 주입을 `requests.cookies.set(domain=...)` → `client.headers["Cookie"]` 명시 헤더로 우회(httpx는 정상이지만 requests CookieJar의 localhost+port matching 한계). 운영 코드 변경 0.

- **증거 (단계별 baseline)** — `_workspace/perf/baseline_2026-05-09/run_181951/`:

  | 단계 | p95 (ms) | p99 (ms) | 실패율 | RPS |
  |------|---------|---------|--------|-----|
  | vu1 | 2100 | 2100 | 28.8% | 0.9 |
  | vu5 | 2300 | 2300 | 26.9% | 4.2 |
  | vu10 | 2400 | 2500 | 27.4% | 8.8 |
  | vu25 | 3200 | 7400 | 27.3% | 11.1 |
  | vu50 | **5300** | **12000** | 27.4% | 17.4 |

  SSE 지표 3종 — 50/50 연결 성공, 조기 끊김 0, inter-arrival p95 313ms (실 측정 60s 동안 event_crud publish가 활발해 ping 25s 주기보다 짧은 분포; absolute latency는 M1c-10 후 측정 가능). 환경 메타데이터 — Ryzen AI 9 HX 370 24 logical / 23.6GB / Win11 10.0.26200 / Python 3.12.9 / locust 2.43.4 / httpx 0.28.1 / `whatudoin.db` 836KB (events 524 / users 3 / checklists 65 / notifications 87) / `meetings/` 1000KB 83 파일 / server-locust 동거 / sanity+5단계 단계 분리 기록. 가드 — snapshot SHA256 source/dest 일치, seed 50/50/50, cleanup 검증 0/0/0.

- **회귀**: 운영 코드 변경 0건 → 회귀 영향 없음. 측정 인프라(orchestrator/scripts) 추가만. server stderr는 정상 startup 로그 + ProactorBasePipeTransport ConnectionResetError(graceful shutdown 시점 normal noise).

- **발견 이슈 (M1a-13/후속 평가용)**:
  1. **PUT `/api/events/{id}` 100% 실패 baseline 일관** — `sqlite3.ProgrammingError: You did not supply a value for binding parameter :project`. locustfile event_crud PUT payload에 `project` 키 누락. 모든 단계 27% 실패율의 ~10pp가 PUT 단독 책임 — baseline 정보로 가치 있으나, 후속 정밀 측정 위해 locustfile PUT payload에 `project` 추가하면 실패율 17%대로 떨어질 것으로 예상.
  2. **upload_file PIL.verify 100% 실패 가정** — backend-dev M1a-5 보고에 명시된 "예상된 실패". 실패율 ~12pp.
  3. **ai_parse 가변 실패** — Ollama 응답 시간/connection 의존. WAL/limiter 도입 전 baseline 영향 작음.
  4. **p95 비선형 증가** — vu10 2.4s → vu25 3.2s → vu50 5.3s. M1b WAL/IMMEDIATE 적용 후 현저히 개선될 것으로 기대(`database is locked` 회피로). 본 baseline에서 server_stderr.log에 `database is locked` 0건 — sqlite default journal_mode 그대로지만 50 VU 단계에서도 lock 폭주 0(저강도 read 위주 시나리오 + 짧은 트랜잭션).
  5. **server-locust 동거** — 같은 PC에서 fired. p95 수치에 측정 도구(locust greenlet)의 CPU 점유분 포함. plan §15 단일 PC 측정 주의 절 적용 — 회사 반입 판단 시 별도 PC 재현 권장(기록만 두고 후속 단계).

- **다음 step 영향**:
  - M1a-8/9/10 lazy-load 적용 후 M1a-11 §5-1 4단계 측정에서 본 baseline과 동일 환경(같은 PC, 동일 fixture seed 절차)에서 lazy-load 전후 비교.
  - M1b WAL 적용 후 동일 9-phase orchestrator 재실행으로 M1b 효과 정량 측정 가능 — `_workspace/perf/scripts/run_baseline_m1a7.py`는 재사용 가능(`_workspace/perf/scripts/run_baseline_m1b.py` 같은 변형 신설 또는 인자화).
  - PUT payload 결함은 baseline에 일관 잡혀있으므로 후속 측정 시 locustfile 보완 후 비교 가능.
  - Python orchestrator + advisor 권장 진단 패턴(stdout/stderr 디스크 저장, 자체 logger, 부재형 통과 금지)은 후속 측정 사이클(M1b/M1c/M1d/M2 측정)에 그대로 재사용.
  - sanity gate가 0건 → fail로 동작하므로 후속 측정에서 silent fail이 발생하면 즉시 abort + 진단 가능.
  - measurement 환경 정합성: `_diag_cookie.py` 같은 임시 진단 스크립트는 보존(같은 클래스 문제 재발 시 재사용). 코드 base는 `_workspace/perf/scripts/` 하위 — 운영 코드 외부.

### M1a-8 완료 (2026-05-09)

- **변경**: `templates/base.html` `__WU_ASSET_V` 객체에 `mermaid: "{{ asset_v('static/lib/mermaid-bundle.min.js') }}"` 항목 추가(line 55, 기존 tiptap/wuJs/katex 등과 동일 `asset_v()` Jinja 헬퍼 패턴). `templates/check.html`의 hardcoded `_EDITOR_ASSETS` 맵이 `const _v = window.__WU_ASSET_V || {};` + `mermaid: '/static/lib/mermaid-bundle.min.js' + (_v.mermaid || '')` lookup으로 전환(line 1084, 1088). `_wu_editor_assets.html`은 서버 렌더 컨텍스트라 `asset_v('static/lib/mermaid-bundle.min.js')` 헬퍼 호출(line 6) 그대로 — `__WU_ASSET_V`와 같은 서버측 헬퍼이므로 단일 소스. `event-modal.js`는 이미 `const v = window.__WU_ASSET_V || {};` 참조(line 47) — 본 step은 map 참조 통일이고 mermaid 로더 추가는 M1a-9 범위. 운영 코드(백엔드) 변경 0건.
- **증거**: qa 사후 검증 — 8개 항목 모두 통과. spot-check — base.html line 55 mermaid 항목 + asset_v() 패턴 일관, check.html line 1084/1088 lookup + fallback `|| ''` 처리, asset_v() 헬퍼는 `app.py` line 173-177 기존 정의 그대로(신규 0). `git diff --stat -- templates/ static/`: base.html 3줄 + check.html 16줄 = 2 파일 11+8-. `mermaid-bundle.min.js` 파일 실재. `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py` 0건.
- **회귀**: check.html의 `_loadScript(_EDITOR_ASSETS.mermaid)`(line 1125) 호출은 이전과 동일하게 `_EDITOR_ASSETS.mermaid` 키를 사용 — lookup 결과는 같은 URL이라 viewer 동작 회귀 0. event-modal.js mermaid 로더 미존재는 의도된 상태(M1a-9에서 통합).
- **다음 step 영향**: M1a-9 공통 lazy loader는 `window.__WU_ASSET_V`를 단일 소스로 읽어 모든 자산(hljs / tiptap / mermaid / wuJs 등) URL 조합. check.html의 로컬 `_EDITOR_ASSETS` + event-modal.js의 `__wuEditorAssetsPromise`는 공통 loader 호출로 교체. M1a-10에서 `_wu_editor_assets.html` 자체가 lazy loader로 대체될 수 있어 include 제거 가능. event-modal.js mermaid 로더는 M1a-9 통합 시 자연 해소(이벤트 모달 viewer mermaid 회귀 버그 동시 수정).

### M1a-9 완료 (2026-05-09)

- **변경**: `static/js/wu-asset-loader.js`(214줄) 신설 + `templates/base.html` line 58 loader include 추가(`<script src="/static/js/wu-asset-loader.js{{ asset_v('static/js/wu-asset-loader.js') }}">`, `__WU_ASSET_V` 정의 직후 위치). loader 인터페이스 — `window.WuAssets = { load(name), ensure(...names), isReady(name) }`. 핵심 함수: `loadCss`(line 67-80, link.onload/onerror), `pollGlobal`(line 84-104, 20ms interval × 5s timeout), `loadJs`(line 107-137, script.onload + pollGlobal), `loadOne`(line 140-166, deps Promise.all + 200ms*2^attempt backoff 최대 2회 재시도), `_loadWithCache`(line 169-179, promise 메모이제이션 + 실패 시 cache 삭제). ASSET_DEPS 7종 — CSS 3종(highlight-css/katex-css/wu-editor-css) + JS 4종(highlight global=hljs / tiptap global=TiptapBundle / mermaid global=mermaid / wu-editor global=WUEditor + deps=위 6개 모두). 글로벌 정합성 spot-check 4/4 — `var hljs=`/`var TiptapBundle=`/`window.mermaid=VS`/`window.WUEditor=` 모두 ASSET_DEPS와 일치. KaTeX는 CSS only(wu-editor.js가 렌더 처리). 운영 코드(백엔드) 변경 0.
- **증거**: qa 사후 검증 — 1/2/3/4/5/7/8/9/10 PASS. 항목 6(페이지별 적용 변경 0)은 PARTIAL FAIL로 보고됐으나 그 사유는 **M1a-8에서 staged된 check.html 16줄 변경(`_v.mermaid` lookup)이 untracked 상태로 git status에 잔존**한 것이며 M1a-9 본 변경에 포함되지 않음(frontend-dev 보고 명시). M1a-9 본 변경은 wu-asset-loader.js(신규 untracked) + base.html(2줄)만. 호출자 0이라 런타임 회귀 위험 0. `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py` 0건.
- **회귀**: loader 등록만이라 호출자 0 → 사용자 체감 동작 변경 0. base.html에 script 1개 추가되나 빈 모듈 로드 부하 미미(214줄, 첫 페이지 로드 +수 ms 추정). M1a-7 baseline에 미반영(loader 도입 시점 baseline 후).
- **다음 step 영향**: M1a-10에서 `_wu_editor_assets.html` head 선로딩 제거, 페이지별 `WUEditor.create()` 직전 `await WuAssets.load('wu-editor')`로 교체(home.html / project_manage.html / trash.html / notice_history.html). `event-modal.js`의 `ensureWUEditorAssets()` + `check.html`의 `_loadEditorAssets()`도 `WuAssets.load(...)` 통합. event-modal.js mermaid 로더 누락 버그(M1a-8에서 식별)도 본 통합으로 해소(WuAssets가 mermaid를 deps에 포함).

### M1a-10 완료 (2026-05-09)

- **변경**: 6 파일(-110줄/+18줄) — (a) `static/js/event-modal.js` `ensureWUEditorAssets()`(L43-44) → `return window.WuAssets.ensure('wu-editor')` 1줄 래퍼, mermaid 누락 버그(M1a-8 식별) 동시 해소(WuAssets ASSET_DEPS의 wu-editor.deps에 mermaid 포함). (b) `templates/check.html` `_loadEditorAssets()`(L1083-1084) → `WuAssets.ensure('wu-editor')` 1줄 래퍼, `_EDITOR_ASSETS` map + `_editorAssetsPromise` 변수 제거(잔존 0). (c) `home.html` `renderNotice()`(L478) → async, viewer 활성화 시 L487 `await WuAssets.ensure('wu-editor')` + L488 `WUEditor.create()`. (d) `project_manage.html` `openCkDrawer()`(L882) → async, L902-903 동일 패턴. (e) `trash.html` `renderDetail()`(L852) → async, L908-909 동일. (f) `notice_history.html` DOMContentLoaded(L74) + `showHistory()`(L85) 양쪽 async, L76-77 + L95-96 동일 패턴. `_wu_editor_assets.html` 파일 자체는 `doc_editor.html`/`notice.html`/`check_editor.html` 3 에디터 페이지에서 여전히 include되므로 보존(삭제 0건). 운영 코드(백엔드) 변경 0.
- **증거**: qa 사후 검증 — 10개 항목 모두 통과(항목 8 base.html 변경은 M1a-9 산출물의 누락 기술이지 결함 아님). include 제거 4 파일(`home.html` / `project_manage.html` / `trash.html` / `notice_history.html`)에서 `_wu_editor_assets|wu_editor_assets` grep 매치 0건. WuAssets.ensure 7곳 삽입(home.html L487, project_manage.html L902, trash.html L908, notice_history.html L76+L95, event-modal.js L1017+L1125), 모두 `WUEditor.create(` 직전 + 함수 async 선언 확인. event-modal.js L43-44 + check.html L1083-1084 래퍼 변환 line range 정합. `_wu_editor_assets.html` 사용처는 `doc_editor.html:3` / `notice.html:3` / `check_editor.html:3` 3건 (그 외 0). `node --check static/js/event-modal.js` + `node --check static/js/wu-asset-loader.js` 양쪽 통과(syntax 0건). `git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py` 0건.
- **회귀**: viewer 보조 화면 4개에서 head 선로딩 제거 → 목록/조회 모드에서 mermaid/tiptap/highlight 자산 다운로드 0 (자산 ~수백KB 절감). 의도된 동작 변경 1건 — WuAssets는 CSS 실패 시 reject(기존 silent 무시). 운영 환경 정적 파일 안정성으로 회귀 위험 낮음. async 함수 변환 5곳은 모두 fire-and-forget(반환값 미사용) caller라 회귀 위험 0. M1a-7 baseline 측정 시점은 본 step 적용 전이므로 자산 다운로드 절감 수치는 M1a-11 측정에서 정량 비교.
- **다음 step 영향**: M1a-11 §5-1 4단계 측정 — (1) 다운로드 / (2) parse·eval / (3) `WUEditor.create()` / (4) viewer 표시 — Playwright + Performance API로 페이지별 캡처. 측정 대상: `/`, `/check`(목록/상세), `/project-manage`, `/trash`, `/notice/history`. 핵심 회귀 검증 — `/check` 목록 모드에서 wu-editor / mermaid / tiptap 자산 다운로드 0건. M1a-12 메인 Playwright 회귀 스위트도 동일 환경에서 함께 실행. M1a-13 점검은 M1a-7 baseline + M1a-11 4단계 + M1a-12 회귀 결과 통합.

### M1a-11 + M1a-12 통합 완료 (2026-05-09, m1a11_run_193829)

- **변경**: `_workspace/perf/scripts/run_baseline_m1a11.py` 신설(1156줄, M1a-7 runner 패턴 재사용 — `_run_py`/`_log_output`/`_db_select`/Phase 0/1/2/3/6/7 헬퍼 동일 복사 + 신규 `_run_npx` / `phase4_m1a11_playwright` / `_parse_playwright_json` / `_check_m1a11_sanity_gate` / `phase5_m1a12_regression` / `phase8_summary`). `tests/perf_m1a11.spec.js` 신설(qa 작성, 4단계 측정 + 자산 다운로드 + viewer 회귀 검증). 8 phase: pre-flight → 메타데이터 → snapshot+seed → uvicorn 시작 → M1a-11 Playwright(`tests/perf_m1a11.spec.js`) → M1a-12 회귀(11 phase spec — phase33/34/37/38/52/53 lazy-load 변경 영향 한정) → graceful shutdown → cleanup → summary. Playwright JSON reporter + stdout/stderr 디스크 저장(advisor 패턴 그대로). sanity gate — pass=0 또는 자산 다운로드 0건 시 abort. 운영 코드 변경 0.

- **§5-1 4단계 측정 결과** — `m1a11_run_193829/m1a11_results_copy/m1a11_4stage.json`:

  | 페이지 | Stage1 다운로드(ms) | Stage2 ready | Stage3 create(ms) | Stage4 ProseMirror(ms) |
  |--------|--------------------|----|-------------------|------------------------|
  | check-detail-first | 6 | ready | 101 | 2856 |
  | project-manage | 5 | ready | 247 | 5019 |
  | trash | 6 | ready | 467 | 5460 |

  자산 transfer size — wu-editor 154KB / mermaid 2.87MB / tiptap 1.22MB / highlight 119KB. Stage2 parse·eval 정확 ms 측정 불가(WuAssets.isReady() 폴링 기반 — runner docstring 명시).

- **자산 다운로드 검증 (lazy-load 핵심)** — `m1a11_asset_downloads.json`:

  | 페이지 모드 | wu-editor | mermaid | tiptap | highlight | 판정 |
  |-------------|-----------|---------|--------|-----------|------|
  | check-list (목록) | **0** | **0** | **0** | **0** | PASS — 의도대로 |
  | check-detail-first | 1 | 1 | 1 | 1 | PASS — 1회만 |
  | check-detail-second-delta (재진입) | **0** | **0** | **0** | **0** | PASS — 메모이제이션 |
  | project-manage | 1 | 1 | 1 | 1 | PASS |
  | trash | 1 | 1 | 1 | 1 | PASS |

  M1a-9 WuAssets reentrancy + M1a-10 페이지별 적용 정상 동작 정량 확인.

- **viewer 회귀 (M1a-11 직접 검증)** — prosemirror-visible **OK**, mermaid/katex/highlight/image **SKIP** (테스트 데이터에 해당 콘텐츠 없음 — 깨짐이 아니라 데이터 부재). lazy-load 후 ProseMirror viewer 정상 활성화 확인.

- **M1a-12 메인 회귀 26건 fail triage**:

  | 그룹 | 분류 | 대표 spec | 건수 | 판정 |
  |------|------|----------|------|------|
  | A | 사전 toastui→Tiptap migration 부채 (lazy-load 무관) | phase34 (5) / phase33_doc_linebreak (5) / phase37_stage3 T1 (~3) | ~13 | lazy-load 회귀 아님 (사전 부채). plan §18 후속 후보 또는 별도 marathon |
  | B | M1a-9/M1a-10 디자인 변경에 따른 OLD eager 가정 spec (코드 정확, spec 업데이트 필요) | phase37_asset_cache T2 — `/check`에서 tiptap 즉시 로드 검증 / phase37_stage2 T1B+T4 — 6개 자산 즉시 로드 검증 | ~6 | M1a 후속 spec 업데이트 task로 분리 — qa 또는 frontend-dev에 위임 가능 |
  | C | orthogonal — 환경/auth/timing/spec 자체 (lazy-load 무관) | phase33_pinpoint_all (비로그인 redirect) / phase33_dark_theme_codeblock / phase33_toc_resizer / phase52 검색 / phase38 image viewer | ~7 | 개별 조사 후 별도 처리. lazy-load와 인과 0 |

  **진짜 lazy-load 회귀 0건**. M1a-11 직접 viewer 검증(§17 "viewer 회귀 0" 명시 항목)은 PASS.

- **증거**: m1a11_run_193829 디렉터리 — `summary.md` / `environment_metadata.md` / `m1a11_playwright.json`(pass 10 / fail 1 / skip 0) / `m1a12_playwright.json`(pass 17 / fail 26 / skip 7) / `m1a11_results_copy/{m1a11_4stage.json, m1a11_asset_downloads.json, m1a11_viewer_regression.json}` / server·snapshot·seed·cleanup stdout/stderr 로그. 가드 — snapshot SHA256 source/dest 일치, seed 50/50/50, cleanup 3종 0/0/0. 운영 코드 변경 0건(`git diff --stat -- app.py database.py auth.py llm_parser.py crypto.py backup.py main.py mcp_server.py broker.py text_utils.py permissions.py templates/ static/` 0).

- **회귀**: lazy-load 변경에 의한 viewer 회귀 0건. Group A/C는 우리 변경 외 사유. Group B는 우리 디자인 변경의 expected breakage(spec이 OLD 가정).

- **M1a 종료 게이트 평가** (§17 M1a 완료 기준):

  | 기준 | 상태 | 근거 |
  |------|------|------|
  | 단계별 baseline 기록 (1/5/10/25/50 VU) | PASS | M1a-7 run_181951/ 보관 |
  | SSE 분리 지표 기록 (3종) | PASS | M1a-7 run_181951/sse_keepalive_*.csv (50/50, 끊김 0, inter-arrival p95 313ms) |
  | §5-1 4단계 측정 기록 | PASS | m1a11_run_193829/m1a11_results_copy/m1a11_4stage.json |
  | viewer 회귀 0건 | PASS | M1a-11 직접 검증 prosemirror-visible OK; M1a-12 26건 fail은 Group A/B/C로 triage 결과 lazy-load 회귀 0 |
  | 다중 탭 baseline 정량 비교 | 부분 충족 | M1a-4 inventory §4 정량 모델 표(단일 100 / 다중 172건/분 상한) 작성 + M1a-7 실측은 단일 탭 50 VU만 — 다중 탭 모델은 인벤토리 추정값 기록, 실측은 후속 사이클(M1b 측정 또는 별도) 권장 |

  → **M1a 완료 인정 가능**. 부분 충족(다중 탭 실측 부재)은 plan §17 "단일 PC 측정 주의" 절차 적용 + 회사 반입 판단 시 별도 PC 재현 권장으로 보완.

- **다음 step 영향**:
  - M1a 완료 → M1b 진입 가능 (M1b-1 DB 세트 백업).
  - Group B spec 업데이트(phase37_asset_cache / phase37_stage2_static_cache의 OLD eager 가정 → lazy semantics 반영)는 M1b 진입 전 또는 병렬로 별도 task. qa/frontend-dev 위임 가능.
  - Group A toastui→Tiptap 마이그레이션 부채는 plan §18 후속 후보로 보존.
  - Group C orthogonal 7건 — 차후 phase별 개별 조사. lazy-load와 인과 없으므로 M1a 종료 차단 사유 아님.
  - M1a-7 baseline (run_181951/) + M1a-11 측정 (m1a11_run_193829/) 두 디렉터리는 M1-end-2 회사 반입 판단 시 핵심 자료.
  - PUT 100% fail (M1a-7) + 자산 다운로드 정확 (M1a-11) 두 baseline은 M1b WAL/IMMEDIATE 적용 후 동일 environment 재측정으로 정량 비교 가능.

---

## M1a 종료 후 후속 fix (2026-05-09)

### PUT payload `project` 필드 추가 (locustfile.py)

- **변경**: `_workspace/perf/locust/locustfile.py` (gitignored). SingleTabUser/MultiTabUser 양쪽 `event_crud` task의 PUT `update_payload`에 `"project": ""` 추가 (`{**payload, "title": f"{title}_updated", "project": ""}` 패턴으로 `replace_all` 일괄). M1a-7 baseline 27% 실패율의 ~10pp(PUT 단독)를 M1b 측정 시 해소 기대.
- **증거**: `python -m py_compile _workspace/perf/locust/locustfile.py` exit 0. 운영 코드 변경 0. 실 측정 검증은 M1b runner 첫 sanity run 시 PUT 실패율 < 50% 확인으로 자연 검증.
- **회귀**: locustfile만 변경(gitignored). 운영 흐름 영향 0.
- **위험**: `database.py _apply_event_update`가 `:project` 외 다른 컬럼도 unbound 요구하면 PUT 여전히 fail 가능 — M1a-7 에러 메시지는 `:project`만 명시했으나, M1b sanity 시 전체 컬럼 정합 spot-check 필요.

## M1b 인계 메모 (codex 또는 다음 세션)

M1a 마일스톤 완료 후 M1b 진입 시 다음 항목 처리 권장:

### 강제 (plan §17 명시)

1. **M1b-1 ~ M1b-17 plan 순서 준수** — DB 세트 백업 → `PRAGMA foreign_key_check` → `foreign_keys` 정책 채택 → `open_sqlite_connection()` 헬퍼 → `init_db()`/마이그레이션/진단 정렬 → 백업 src 정렬 → `database.get_conn()` PRAGMA 5종 → WAL 모드 활성화 → §4-1 사전 조사 → `write_conn()` IMMEDIATE → 503 변환 → WAL 안전판 → EXPLAIN QUERY PLAN → APScheduler 점검 → WAL 복원 drill → 동시성 검증 → Playwright 회귀 + exit criteria.
2. **M1a-7 baseline (`run_181951/`)이 M1b 효과 정량 비교 기준** — 같은 PC·환경 메타데이터·fixture seed 절차로 측정해야 비교 valid.

### 선택 (효율·정합 목적)

3. **Group B spec 6건 lazy semantics 업데이트** (M1a-12 회귀 26건 중 6건):
   - `tests/phase37_asset_cache.spec.js` Test 2 — `/check`에서 `tiptap-bundle.min.js` 즉시 로드 → 상세 진입 후 `WuAssets.ensure` 트리거 검증
   - `tests/phase37_stage2_static_cache.spec.js` Test 1B + Test 4 — 6개 자산 즉시 로드 → lazy 활성화 후 검증
   - 작업량: ~30~60분 (qa 또는 frontend-dev 1 사이클)
4. **M1b runner 신설**: `run_baseline_m1a7.py` 9-phase 80% 재사용 — pre-flight / snapshot+seed / uvicorn / SSE는 그대로, 측정 본문만 동시성 100+100 + lock 변환 검증으로 교체. `run_baseline_m1b.py` 변형 또는 m1a7.py에 `--mode=m1b` 인자 추가.

### 보류 (M1b 차단 사유 아님)

5. **Group A 13건** (toastui→Tiptap 마이그레이션 미완료, phase33_doc_linebreak/phase34/phase37_stage3 T1) — plan §18 후속 후보. M1b 회귀에서 같은 fail 재현되면 noise로 처리.
6. **Group C 7건** (auth/dark theme/TOC/검색/image viewer — lazy-load와 orthogonal) — 개별 phase별 조사 필요. M1b 회귀 spec list에 포함될 수 있으나 lazy-load 인과 0.
7. **다중 탭 baseline 실측** — M1a-7은 단일 탭 50 VU만. M1a-4 inventory §4 모델(172건/분 상한) 실측은 후속 사이클 권장.

### 환경/도구 인계

- **measurement orchestrator**: `_workspace/perf/scripts/run_baseline_m1a7.py` (978줄, M1a-7 운용) + `run_baseline_m1a11.py` (1156줄, M1a-11/12 운용) — 8/9-phase Python orchestrator, M1a-7 패턴 80% 재사용 가능.
- **fixture**: `_workspace/perf/fixtures/{seed_users.py, cleanup.py}` (env `WHATUDOIN_PERF_FIXTURE=allow` + WAL/SHM 부재 가드). 첫 실 실행은 snapshot 직후로 묶음.
- **SSE**: `_workspace/perf/scripts/{sse_poc.py, sse_keepalive.py}`. 50 연결 keep-alive + 지표 3종(연결 유지 / inter-arrival / QueueFull 클라 추정 0).
- **인벤토리**: `_workspace/perf/background_requests.md` (페이지×요청 매트릭스 + 단일/다중 탭 분당 호출 수 모델).
- **gitignore 정책**: `_workspace/`, `tests/`는 git untracked. 측정 산출물은 로컬 보관, codex 인계 시 같은 머신/branch에서 진행해야 일관성 유지.
- **메모리 패턴 (Claude Code 메인 세션)**: `feedback_subprocess_logging.md` (subprocess output 디스크 저장 강제) + `feedback_harness_qa.md` (작업 도메인 에이전트 + qa 사후 검증 2단계 사이클).
