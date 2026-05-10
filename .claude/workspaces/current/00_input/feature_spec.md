# 팀 기능 그룹 A — #1 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#1. DB 마이그레이션 인프라 구축**만 한 사이클로 끊어 진행한다. 마스터 plan은 `팀 기능 구현 계획.md` §13 (실행 절차 + Phase 1~5 + 운영자 체크리스트) 기준.

#2 이후 항목은 본 사이클 범위 밖. #1이 끝나면 별도 사이클로 인계한다.

## 분류

백엔드 핵심 인프라 추가. 프론트 변경 없음.
**팀 모드: backend → reviewer → qa.** frontend는 호출하지 않는다.

## 핵심 인계 사실 (메인이 이미 파악)

### 코드 구조
- `database.py:27` `init_db()` — 모든 `CREATE TABLE IF NOT EXISTS ...` 후 `_migrate(...)` 호출로 컬럼 보강.
- `database.py:616` `_migrate(conn, table, columns)` — 기존 컬럼 비교 후 `ALTER TABLE ADD COLUMN` 패턴. 빈 테이블이면 노옵.
- `database.py:626` `get_conn()` — `@contextmanager`. WAL 보장 + pragma 적용 + `conn.commit()` (with 블록 끝).
- `database.py:2872`/`2878` `get_setting(key, default=None)` / `set_setting(key, value)` — `settings` 테이블 (key, value) 활용.
- `database.py:165, 367` `CREATE TABLE IF NOT EXISTS settings` — 두 군데에서 정의되어 있음 (중복 — 본 작업 범위는 아니지만 reviewer가 인지).
- `backup.py:18` `run_backup(db_path, run_dir) -> Path` — `backupDB/whatudoin-YYYYMMDD-HHMMSS.db`로 SQLite `src.backup(dst)`. 현재 APScheduler 일일 백업에서 호출.
- `backup.py:9` `BACKUP_RETENTION_DAYS = 90`.

### 마스터 plan 핵심 (§13)
- Phase 1 = 컬럼·테이블 추가 (idempotent), Phase 2 = 데이터 백필, Phase 3 = 시드 데이터 정리, Phase 4 = 제약·인덱스, Phase 5 = 호환 컬럼 drop (별도 릴리스).
- 본 #1은 Phase 자체가 아니라 **Phase들을 안전하게 돌리는 인프라**. 실제 Phase 1~4 SQL은 #2 이후에 채운다.
- `settings.team_migration_warnings`(JSON 배열)에 백필 경고를 누적해 운영자가 `/admin`에서 검토 (admin UI는 후속 작업, 본 사이클은 누적 헬퍼만).

## #1 step 분해

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #1-S1 | 자동 백업 + Phase 마커 + 트랜잭션 래퍼 | 미적용 마이그레이션이 1개라도 있을 때만 백업 1회. Phase 마커 헬퍼(`is_phase_done`/`mark_phase_done`)는 `settings` 테이블 사용. Phase 단위 트랜잭션 실패 시 롤백 + 서버 시작 거부 + stdout 로그. |
| #1-S2 | 경고 누적 로그 + name normalize 헬퍼 | `settings.team_migration_warnings`에 JSON 배열로 append (race-safe, 같은 카테고리 중복 방지). `normalize_name(s)` = NFC + lower (Unicode `unicodedata.normalize("NFC", s).casefold()` 또는 `.lower()` — backend가 결정). |
| #1-S3 | Phase 4 UNIQUE preflight 검사 골격 | preflight 결과 누적 + 충돌 시 서버 시작 거부 + 경고 로그 기록 패턴만. 실제 검사 SQL은 #2 이후 phase 추가 시 같이 채워질 수 있도록 **확장 포인트(검사 함수 목록)** 정의. |

> step 분할 기준: S1은 인프라 골격(백업·마커·트랜잭션), S2는 데이터 헬퍼(경고·정규화), S3은 사전 점검 골격. S2와 S3은 S1에 의존하지만 서로 독립적이므로 순서대로 진행하되 S2와 S3을 묶어서 1 backend 호출로 처리해도 무방하다(플래너 판단).

## exit criteria (사이클 전체)

- [ ] 빈 DB에서 첫 시작 → 인프라가 정의된 모든 phase 마커가 적절히 기록되거나(또는 phase가 아직 없으면 인프라만 정상 동작) 서버 시작 성공.
- [ ] 재시작 시 모든 마커가 그대로 남아 phase 본문이 다시 안 돌고 서버 정상 시작.
- [ ] 인위적 phase 실패 주입 시 트랜잭션 롤백 + 서버 시작 거부 + stdout에 어느 phase에서 실패했는지 명확.
- [ ] 백업 파일이 `whatudoin.db.bak.{ISO8601}` 형식(또는 합의된 위치)으로 미적용 마이그레이션이 있을 때만 생성.
- [ ] preflight 골격이 호출되고, 충돌 검사 함수가 0개여도 정상 통과.
- [ ] Phase 마커 강제 삭제 후 재실행 시뮬레이션 — 인프라가 다시 도는 것 자체는 안전 (실제 phase가 아직 없으므로 데이터 변경 없음).

## 진행 방식

- step 1개당 1 backend 호출(또는 S2+S3 묶음). step 종료마다 `backend_changes.md`에 4종(변경 파일·요약·검증 명령·다음 step 인계) 추가.
- 모든 step backend 완료 후 1회 reviewer 호출 → 1회 qa 호출.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **#1은 phase 본문 SQL을 추가하지 않는다.** 본문 SQL은 #2 이후 작업의 책임이다. #1은 phase를 안전하게 돌리는 기반만 제공.
- 자동 백업 위치는 기존 `backupDB/`를 재사용할지, 새 `whatudoin.db.bak.{ISO8601}` 명세를 따로 만들지 backend가 결정한 뒤 reviewer가 정합성 검토.
- `team_migration_warnings`는 동시 쓰기가 거의 없지만 `get_conn()` 트랜잭션 안에서 read → append → write 패턴으로 race를 막는다.
- VSCode 디버깅 모드라 서버 자동 재시작이 안 됨 — qa가 재시작 필요 시 사용자에게 요청.
- `_migrate` 패턴과의 관계: 컬럼 추가 자체는 `_migrate`로 충분하지만, 본 #1의 phase 마커·백업·트랜잭션은 `_migrate`보다 **상위 layer**에서 phase 단위로 감싸는 구조다. backend가 `init_db()` 안에 phase 래퍼 진입점을 새로 만들고, 실제 phase 본문은 후속 사이클에서 그 안에 등록한다.
- 본 사이클에서는 `users.name_norm`·비밀번호 hash 변환 등 phase 본문은 **만들지 않는다.** 그건 #2/#3/#7의 책임.

## 산출물 위치

- `backend_changes.md`: backend 변경 일지 (step별 섹션 분리)
- `code_review_report.md`: reviewer 결과
- `qa_report.md`: qa 결과
- 임시 산출물(스크린샷·디버그): `.claude/workspaces/current/screenshots/` 등
