# QA 보고서 — 팀 기능 그룹 A #4 (데이터 백필 1차)

## 검증 방식

`team_phase_4_data_backfill_v1`을 합성 DB로 검증. 실서버 미사용 (VSCode 디버깅 모드 호환).

- 스크립트: `.claude/workspaces/current/scripts/verify_data_backfill.py`
- 실행: `python .claude/workspaces/current/scripts/verify_data_backfill.py`
- 격리: 매 시나리오마다 `tempfile.mkdtemp()`로 새 DB + `WHATUDOIN_RUN_DIR`/`WHATUDOIN_BASE_DIR` 환경변수 격리 + `database` 모듈 reload
- phase 본문 단독 호출: 합성 데이터 주입 → `reset_phase4_marker()` → `db._run_phase_migrations()`

## 결과

```
결과: 40/40 passed, 0 failed
```

## 시나리오 매트릭스

### 시나리오 1 — 빈 DB 노옵 (4/4)
- [x] 1.1 빈 DB `init_db()` 성공
- [x] 1.2 phase 4 마커 존재
- [x] 1.3 빈 DB → 백필 warning 카테고리 누적 없음
- [x] 1.4 `pending_users` 0건

### 시나리오 2 — 정상 케이스 7개 백필 (10/10)
- [x] 2.1 events.team_id 백필 (작성자 alice → team 1, bob → team 2)
- [x] 2.2 checklists.team_id 백필
- [x] 2.3 meetings 분기 (C) `is_team_doc=1` + 정상 작성자
- [x] 2.4 meetings 분기 (D) `is_team_doc=0` + 정상 작성자
- [x] 2.5 projects.team_id 백필 — `owner_id` 기반 단계 2
- [x] 2.6 notifications.team_id 백필 — `events.team_id` 의존
- [x] 2.7 links.team_id 백필 — `scope='team'`
- [x] 2.8 links `scope='personal'` 영향 없음 (NULL 그대로)
- [x] 2.9 team_notices.team_id 백필
- [x] 2.10 정상 케이스만 → 백필 warning 누적 없음

### 시나리오 3 — 결정 불가 → NULL 유지 + warning 5종 (7/7)
- [x] 3.1 events 결정 불가 → NULL + `data_backfill_events`
- [x] 3.2 checklists 결정 불가 → `data_backfill_events` 카테고리에 합산 + 메시지 prefix `checklists`
- [x] 3.3 meetings 분기 (A) admin + `is_team_doc=1` → `data_backfill_meetings_team_doc_no_owner`
- [x] 3.4 projects 단계 4 → `data_backfill_projects`
- [x] 3.5 links 결정 불가 → `data_backfill_links`
- [x] 3.6 team_notices 결정 불가 → `data_backfill_team_notices`
- [x] 3.7 사용된 카테고리는 정확히 5종 (사양서 §exit criteria 일치)

### 시나리오 4 — meetings 4분기 (5/5)
- [x] 4.A admin + `is_team_doc=1` → NULL
- [x] 4.A admin + `is_team_doc=1` → warning `meetings_team_doc_no_owner` 생성
- [x] 4.B admin + `is_team_doc=0` → NULL + warning 생성 안 함 (정상)
- [x] 4.C member + `is_team_doc=1` → 백필
- [x] 4.D member + `is_team_doc=0` → 백필

### 시나리오 5 — notifications event_id 분기 (5/5)
- [x] 5.1 event_id 매칭 + `events.team_id` 있음 → 백필
- [x] 5.2 event_id 매칭 + `events.team_id` NULL → NULL 유지
- [x] 5.3 event_id NULL → NULL 유지
- [x] 5.4 event_id가 존재하지 않는 이벤트 가리킴 → NULL 유지
- [x] 5.5 notifications 백필이 warning 카테고리 누적 안 함 (transient noise 회피)

### 시나리오 6 — links scope 분기 (4/4)
- [x] 6.1 personal + team_id NULL → NULL 그대로
- [x] 6.2 personal + team_id=99(비정상 데이터) → 99 보존 (가드가 `scope='team'`만 잡음)
- [x] 6.3 team + 정상 → 백필
- [x] 6.4 team + 결정 불가 → NULL + `data_backfill_links` warning

### 시나리오 7 — pending_users 자동 삭제 (1/1)
- [x] 7.1 status 무관 (pending/rejected/approved) 모두 삭제 → 0건

### 시나리오 8 — idempotency (3/3)
- [x] 8.1 마커 강제 삭제 + 재실행 → events 백필 결과 동일 (NULL row는 다시 시도하지만 결과 같음)
- [x] 8.2 warning dedup — 같은 (category, message) 두 번째 실행에서 재누적 안 됨
- [x] 8.3 phase 4 마커 재생성

### 시나리오 9 — 다중 팀 멤버 우선순위 (1/1)
- [x] 9.1 user_teams ≥2건 → `joined_at` 가장 이른 팀(t2)으로 백필 (사양서 §44 우선순위 2)

## 통과 ✅

### 사양서 exit criteria 검증

- [x] 빈 DB → phase 본문 노옵 (UPDATE/DELETE 모두 0행 영향, warning 누적 없음)
- [x] 합성 구 DB → 정상 케이스 채워지고 실패 케이스는 NULL 유지 + warning 누적
- [x] 두 번째 init_db() → 마커 덕에 phase 미실행 (1.2에서 마커 존재 확인)
- [x] 마커 강제 삭제 후 재실행 → 가드 덕에 결과 동일 (8.1·8.2)
- [x] meetings 4분기 모두 검증 (특히 admin + `is_team_doc=1` warning 생성 4.A)
- [x] notifications.team_id → events.team_id NULL이면 그대로 NULL (5.2)
- [x] links scope='personal' 영향 없음 (2.8, 6.1, 6.2)
- [x] pending_users 마이그레이션 후 0건 (7.1)
- [x] warning 카테고리 정확히 5종 (3.7)
- [x] 같은 row 두 번 시도해도 dedup으로 중복 누적 안 됨 (8.2)

## 회귀 확인

- [x] 기존 phase 1·2·3·4(indexes) 모두 OK 로그 출력 (모든 시나리오의 마이그레이션 출력에서 확인)
- [x] `init_db()` 자체는 본 사이클에서 변경 없음 — 기존 백필·시드 동작 보존

## 발견된 경계 사례 (참고)

1. **시나리오 6.2** — 비정상 데이터 (`scope='personal'` + `team_id=99`)는 백필 가드(`scope='team'`)에 의해 영향 받지 않음. 이는 사양서 의도와 일치 (백필은 NULL row만 채움, 기존 값 보존).
2. **시나리오 5.4** — 존재하지 않는 event_id를 가리키는 notification은 EXISTS 가드로 NULL 유지. SQL subquery NULL 안전성 확인됨.
3. **다중 팀 + legacy users.team_id 충돌**: scenario 9에서 user.team_id=1이지만 user_teams 가장 이른 팀이 t2라면 헬퍼는 user_teams 우선(우선순위 1·2 > 3)이라 t2 반환. 이 동작은 사양서 §43-46과 일치.

## 회신 검증 사항

- 코드 리뷰 W2 (meetings.is_team_doc NULL 안전성): 본 시나리오에서 명시 검증은 안 했음. 현 구현 `is_team_doc == 1`은 NULL을 분기 (B) ≡ "warning 안 함"으로 처리. legacy DB에서 NULL row가 있다면 보수적 처리(warning 미발생)로 동작. 본 사이클 범위에서 issue 없음.

## 산출물

- `.claude/workspaces/current/scripts/verify_data_backfill.py` (검증 스크립트, 9 시나리오 / 40 케이스)
- `.claude/workspaces/current/qa_report.md` (이 문서)

## 최종 판정

**통과.** 40/40 케이스 전부 통과. 차단·실패 없음.
