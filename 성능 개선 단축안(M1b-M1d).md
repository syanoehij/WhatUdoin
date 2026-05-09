# 성능 개선 단축안(M1b-M1d)

작성일: 2026-05-09

이 문서는 `성능 개선 todo.md`를 바로 수정하지 않고, M1a 종료 후 참고할 M1b/M1c/M1d 단축 실행안을 별도로 보관하기 위한 문서다.

검토 반영 상태: 2차 리뷰를 반영해 기준을 "상용 서비스/회사 반입"이 아니라 "사내 소수 사용자 도구"로 낮췄다. 기존 보수 단축안은 회사 반입 게이트로 내리고, 실제 로컬 성능 개선 기본 경로는 `M1-ULTRA`로 둔다.

## 전제

- WhatUdoin은 상용 SaaS가 아니라 사내 소수 사용자(n≈1~5명) 인트라넷 도구다.
- 현재 운영은 IP whitelist/사내 LAN 전제를 가진다. 외부 불특정 사용자 공격 모델을 M1 로컬 개선의 기본 기준으로 삼지 않는다.
- M1a-7 baseline에서 50 VU에서도 `database is locked` 0건이 기록됐다. 따라서 광범위 `BEGIN IMMEDIATE`, DB lock 503 변환, scheduler 감사는 지금 필수로 보지 않는다.
- M1a가 아직 마무리 중이면 `성능 개선 todo.md`는 수정하지 않는다. 이 문서는 M1a 완료 후 todo 반영용 초안이다.
- M2 이후는 여전히 조건부 운영 구조 변경이다. M1-ULTRA를 끝냈다고 M2 진입 근거가 생기는 것은 아니다.

## 트랙 선택

| 트랙 | 예상 시간 | 용도 |
|---|---:|---|
| M1-ULTRA | 4~5시간 | 사내 소수 사용자 기준의 실제 권장 경로 |
| 보수 단축안 | 12~16시간 | 회사 반입 전 증거를 더 남기고 싶을 때 |
| 기존 todo 전체 | 20~40시간 | 상용/감사 대응 수준의 완전 방어형 검증 |

현재 권장: **M1-ULTRA**.  
보수 단축안은 지금 실행하지 말고, 회사 반입 결정이나 실제 장애 징후가 생겼을 때 끌어온다.

## 운영 원칙

1. 성능 원인 분리를 우선한다. 여러 병목을 한 번에 바꾸지 않는다.
2. "미리 다 막기"보다 "낮은 확률 리스크는 발생 시 빠르게 보완"으로 간다.
3. 코드 변경은 M1b WAL/PRAGMA, M1c Ollama limiter에 집중한다.
4. MCP transport/identity/DNS rebinding은 성능 개선이 아니라 호환성·보안 운영 항목이다. M1에서는 기본 제외한다.
5. 각 단계는 raw 증거를 과하게 쌓기보다, 재현 가능한 smoke와 명확한 rollback 가능성을 남긴다.

## M1-ULTRA

### M1b-ULTRA — SQLite WAL/PRAGMA만 적용

예상: 약 1.5시간

목표: 가장 값싼 SQLite 개선만 적용한다. connection helper 전면 추출이나 모든 raw connect 제거는 하지 않는다.

| step | 내용 | 완료 기준 |
|---|---|---|
| M1b-U1 | DB 세트 백업 | 현재 DB를 되돌릴 수 있는 위치 기록 |
| M1b-U2 | `database.get_conn()`에 PRAGMA 5종 적용 + `journal_mode=WAL` 1회 활성화 | `timeout=5`, `busy_timeout=5000`, `synchronous=NORMAL` 기본, `cache_size=-8000`, `temp_store=MEMORY`, `journal_mode=wal` 확인 |
| M1b-U3 | `snapshot_db.py`가 `.db-wal`/`.db-shm`도 복사하는지 코드 확인 | WAL 파일이 있으면 세트로 복사된다는 코드 경로 확인. 별도 restore drill은 하지 않음 |
| M1b-U4 | 서버 재시작 + PRAGMA 확인 | 재시작 후 `PRAGMA journal_mode;` 결과 `wal` |
| M1b-U5 | M1a 도구 재사용 50 VU smoke | p95 명확한 회귀 없음, `database is locked` 0건 또는 발생 시 함수 기록 |

### M1b에서 제외

- `open_sqlite_connection()` 전면 헬퍼 추출
- `init_db()`/마이그레이션/진단/백업 raw connect 0건 grep 게이트
- `PRAGMA foreign_key_check`
- WAL restore drill
- APScheduler write spot-check
- 전체 write 함수 조사표
- 광범위 `BEGIN IMMEDIATE`
- DB lock 503 변환
- WAL 크기 모니터/checkpoint 자동화
- 전체 hot path `EXPLAIN QUERY PLAN`

근거: M1a-7 baseline에서 lock 0건이 이미 측정됐다. startup 단발 경로와 야간 scheduler까지 지금 성능 개선 범위에 끌어들이면 시간이 급증한다.

### M1c-ULTRA — Ollama limiter만 우선

예상: 약 2.5시간

목표: 외부 Ollama hang/장기 요청이 main app을 잠식하지 않게 한다. 업로드/body cap은 실제 문제 발생 전까지 보류한다.

| step | 내용 | 완료 기준 |
|---|---|---|
| M1c-U1 | Ollama 7접점에 limiter 적용 | 파싱/refinement/체크리스트 생성/주간 보고/conflict review/health/model 조회가 limiter를 통과 |
| M1c-U2 | 기본 동시성은 env `WHATUDOIN_OLLAMA_CONCURRENCY=1` | env 미지정 시 1, 허용 범위 1~5 |
| M1c-U3 | admin UI 1~5 설정 + DB persist | 사용자 요구사항 충족. 운영 중 실제 사용은 거의 1로 전제 |
| M1c-U4 | 포화/장애 UX 통합 | 슬롯 포화, timeout, ConnectionError, 5xx를 사용자에게 같은 "AI 사용 중/사용 불가, 잠시 후 재시도" 흐름으로 안내 |
| M1c-U5 | 동시 AI 2개 fire smoke | 기본 1슬롯에서 1개 처리, 1개 busy 또는 정책상 대기 확인 |

### M1c에서 제외

- 업로드 `Content-Length` cap 선제 적용
- chunked hard cap
- ASGI body size middleware
- 업로드 threadpool/세마포어
- SSE QueueFull 카운터
- 로그 회전/모니터링
- Ollama 1~5 전체 부하 매트릭스
- 1→3 resize 전용 검증 항목

근거: 사용자는 설정 1~5를 원하지만 실제 운영은 거의 1슬롯이다. resize는 admin UI 구현 직후 한 번 클릭해보는 자연스러운 동작 확인으로 충분하고, 별도 exit gate로 만들지 않는다. 업로드 cap은 사내 알려진 사용자 환경에서는 발생 시 30분 내 보완 가능한 리스크다.

### M1d — 기본 skip

예상: 0시간

MCP는 M1에서 제외한다. 현재 MCP는 이미 작동 중인 클라이언트에 대해 동작하고 있고, transport 교체는 성능 개선이 아니라 호환성/운영 변경이다.

예외적으로 다음이 확인되면 M1d-S3만 수행한다.

- MCP 검색 중 웹 UI나 SSE가 실제로 멈춘다.
- MCP 긴 조회가 일반 API p95에 명확한 회귀를 만든다.

이 경우에도 작업은 "MCP DB 조회 threadpool 적용 여부 판단"까지만 한다. transport 교체, identity 병렬 테스트, DNS rebinding 정책은 M2 또는 회사 반입 게이트로 넘긴다.

## M1-ULTRA 종료 기준

- M1a lazy-load 회귀 0건과 before/after 측정 기록
- M1b WAL/PRAGMA 적용 후 `journal_mode=wal`
- M1b 50 VU smoke에서 명확한 p95 회귀 없음
- M1c Ollama 기본 1슬롯 limiter 동작
- 동시 AI 2개 smoke에서 포화 응답 또는 대기 정책 확인
- M1d는 skip 사유 기록. MCP 병목 징후가 있으면 threadpool 판단만 수행

## 잘라낸 항목과 복구 비용

| 잘라낸 항목 | 발생 가능 시나리오 | 대응 |
|---|---|---|
| WAL restore drill | snapshot에 `.wal` 누락 후 복원 데이터가 stale | snapshot 스크립트 세트 복사 확인/수정 후 재시도 |
| 업로드 cap | 사용자가 거대 파일 업로드해 RSS spike | 서버 재시작 후 cap 추가 |
| MCP identity test | 두 사용자가 MCP 동시 사용 중 교차 노출 | 발견 시 핫픽스. M1에서는 동시 MCP 사용 빈도 낮음 |
| DNS rebinding 정책 | 악의적 페이지 + LAN 노출 + IP whitelist 조합 | 발생 가능성 낮음. 필요 시 Host/origin allowlist 추가 |
| APScheduler 감사 | 야간 작업이 API blocking | 실제 체감/로그 발생 시 scheduler 경로 점검 |

이 항목들은 상용 서비스라면 선제 검증 대상이지만, 현재 조건에서는 반응형 처리 비용이 낮다.

## 보수 단축안

회사 반입 전 증거가 필요하거나 운영 리스크를 더 줄이고 싶으면 아래 항목을 M1-ULTRA 위에 추가한다.

### M1b 보강

- `open_sqlite_connection()` 도입 및 raw `sqlite3.connect(DB_PATH)` 경로 정리
- `PRAGMA foreign_key_check`
- WAL restore drill 1회
- APScheduler write spot-check
- 짧은 동시 read/write 검증

### M1c 보강

- 업로드 `Content-Length` 사전 거부 + route별 cap
- 1→3 resize 수동 검증
- `/api/stream` 유지와 업로드 정상/초과 413 focused 회귀

### M1d 보강

- 실제 사용 클라이언트 handshake 조사
- 권한/identity 시나리오 작성 + 두 사용자 병렬 호출 1회 실행
- DNS rebinding 운영 노출 모드 정책 결정
- MCP threadpool 적용 여부 측정 기반 결정

보수 단축안을 적용하면 예상 시간은 약 12~16시간이다.

## M1a 종료 후 todo 반영 방법

1. `성능 개선 todo.md`의 M1b~M1d 전체를 바로 삭제하지 않는다.
2. 각 마일스톤 위에 `M1-ULTRA 실행안`을 먼저 추가한다.
3. 기존 상세 step은 `보수 단축안/회사 반입 게이트/후속 후보`로 이동한다.
4. M1c Ollama 항목에는 “설정 범위 1~5, 기본 운영 1, 전체 1~5 부하 매트릭스 생략”을 명시한다.
5. M1d는 M1에서 기본 skip으로 두고, MCP 병목이 실제 측정될 때만 threadpool 판단 step을 수행한다.
