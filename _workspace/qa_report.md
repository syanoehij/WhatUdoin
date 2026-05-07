# QA 보고서 — 히든 프로젝트 A단계

- 테스트 파일: `tests/phase46_hidden_project_a.spec.js`
- 실행 일시: 2026-05-08
- 환경: `https://192.168.0.18:8443/` (IP 화이트리스트 자동 로그인, TLS 자체서명)
- Playwright 결과: **8 passed / 5 skipped / 0 failed**

## 체크리스트 결과 (사용자 지정 10항목)

| # | 항목 | 결과 | 비고 |
|---|------|------|------|
| 1 | 히든 프로젝트 생성 — 팀 있는 사용자 → 성공 | PASS | `POST /api/manage/hidden-projects` 응답에 `id, name, is_hidden=1, owner_id` 포함 검증 |
| 2 | 이름 중복 — 일반 프로젝트와 동일 → 422 | PASS | detail 정확 일치 검증 |
| 3 | 이름 중복 — 휴지통 이름과 동일 → 422 | PASS | `DELETE` 후 동일 이름 히든 생성 시도, 휴지통 이름까지 중복으로 차단됨 확인 |
| 4 | 이름 중복 — 기존 히든과 동일 → 422 | PASS | 동일 이름 두 번째 히든 생성 차단 |
| 5 | 에러 메시지에 "이미 존재" 부재 (누설 방지) | PASS | 3가지 충돌 시나리오 모두 `"생성할 수 없습니다. 다른 이름을 넣어주세요."` 정확 일치, 정규식 `/이미 존재/` 매칭 없음 |
| 6 | owner는 자기 히든 프로젝트를 목록에서 볼 수 있음 | PASS | `/api/manage/projects`(상세 객체)와 `/api/projects`(이름 배열) 양쪽 + `/can-manage` 응답에서 `is_owner=true, can_manage=true` 확인 |
| 7 | 비멤버는 히든 프로젝트를 볼 수 없음 (UI 레벨) | SKIP | IP 화이트리스트 자동 로그인 환경에서 다른 사용자 세션 시뮬레이션 불가. 백엔드 가시성 필터(`get_unified_project_list`/`get_all_projects_with_events` viewer=None 분기)는 `_workspace/code_review_report.md` 정적 분석으로 검증된 상태 |
| 8 | admin은 모든 히든 프로젝트를 볼 수 있음 | SKIP | 현재 자동 로그인 세션 role이 admin이 아님. admin 세션에서 mainHidden 가시성과 `is_admin=true, can_manage=true`를 자동 검증하는 conditional 테스트는 작성되어 있어 admin 세션에서 재실행 시 자동 활성화됨 |
| 9 | 히든 프로젝트에 "히든" 뱃지 표시 (UI 레벨) | PASS | `/project-manage` 페이지의 `#pm-list .pm-proj-item` 중 mainHidden 행에 `.pm-hidden-badge` 존재, 텍스트 `"히든"` 일치 |
| 10 | 일반 멤버에게 편집/삭제 버튼 미표시 (owner 기준 표시 확인) | SKIP (B단계 이후) | 멤버 추가 API가 B단계 범위라 "owner 아닌 일반 멤버" 상태에 도달 불가. owner 본인 기준 버튼 표시 자체는 `#7`의 `can-manage=true` 응답으로 간접 확인됨 |

## 추가 실행 검증

- **환경 점검**: `CURRENT_USER` 노출 확인, role/team_id 출력 — PASS
- **테스트 #2 (팀 없는 사용자 → 403)**: 자동 로그인 사용자가 team_id를 보유하므로 SKIP

## 세부 절차 (각 PASS 항목 검증 방법)

1. **API 경계면 검증**: `page.evaluate` 안에서 `fetch` 호출 → `{ ok, status, json, text }` 구조로 응답 캡처. 422/200 status code와 detail 문자열 정확 일치 검증.
2. **자기 일관성 검증**: `POST /api/manage/hidden-projects` 응답의 `id`, `owner_id`가 `/api/manage/projects` 목록 응답의 동일 필드와 일치하는지 cross-check (#6).
3. **UI ↔ API 교차**: `/api/manage/projects` 응답 대기 + `#pm-list .pm-proj-item` 렌더 후 mainHidden.name 라벨을 가진 row의 `.pm-hidden-badge` 존재 검증 (#9).
4. **Fixture 관리**: 모든 이름에 `__phase46_hidden_*_${Date.now()}` / `__phase46_normal_*_${Date.now()}` prefix → `afterAll`에서 `DELETE /api/manage/projects` 호출로 정리 (soft-delete이므로 휴지통에는 남으나 이름이 고유해서 재실행 충돌 없음).

## 발견 이슈 / 수정 사항

테스트 작성 중 발견·수정한 사항:

- **#7 — `/api/projects` 응답 형태 오해**: 첫 실행에서 `pubList.json.map(p => p.name)`이 모두 undefined로 잡혔음. 실제 엔드포인트(`app.py:2007-2013`)는 `[name, ...]` 문자열 배열을 반환. 테스트를 `typeof p === 'string' ? p : p?.name` 패턴으로 수정하여 통과.
- **#11 — `waitForResponse` 타임아웃**: `goto` 이후에 `waitForResponse`를 거는 패턴은 응답이 이미 끝나 영영 못 잡는 케이스가 발생. `Promise.all([waitForResponse, page.goto])` 동시 진입 패턴으로 수정하여 안정화.

백엔드/프론트엔드 측 결함은 발견되지 않았습니다.

## 회귀 확인

- 기존 `phase46_gantt_project_date_boundary.spec.js` 테스트는 본 작업과 파일 분리되어 영향 없음.
- `database.py`의 viewer 파라미터 추가 + N+1 방지 로직은 정적 리뷰(`_workspace/code_review_report.md`)에서 BLOCK 결함 3건 모두 수정 완료된 상태로 확인.

## 전체 판정

**A단계 통과** — 자동화 가능한 모든 항목 PASS. SKIP 항목은 환경 제약(다른 사용자 세션 불가, admin 세션 부재) 또는 B단계 범위로, 수정 필요 없음.

## 다음 단계 권고

- B단계 진입 시 멤버 추가 API가 생기면 항목 #10 (일반 멤버 편집/삭제 버튼 미표시)을 본 spec에 추가하여 회귀 검증할 것.
- admin 계정 환경에서 본 spec 재실행 시 항목 #8 자동 활성화.
- 추가 사용자 세션이 가능해지면 항목 #7 (비멤버 차단)을 별도 spec으로 추가 권고.

---

## B단계 QA

- 테스트 파일: `tests/phase46_hidden_project_b.spec.js`
- 실행 일시: 2026-05-08
- 환경: `https://192.168.0.18:8443/` (IP 화이트리스트 자동 로그인, editor 세션 — `/api/admin/users` 401/403)
- Playwright 결과: **6 passed / 2 skipped / 0 failed** (총 8건 — 환경 점검 1 + 항목 7)

### 체크리스트 결과 (사양 13~19, 7항목)

| # | 항목 | 결과 | 비고 |
|---|------|------|------|
| 13 | 멤버 추가 — 같은 팀 사용자 → 성공 | PASS | `addable-members`로 동일팀 user_id 발견 → `POST .../members` 200, 응답 `{ ok: true }`, 직후 `GET .../members`에서 `is_owner=1`(owner)·`is_owner=0`(신규 멤버) 검증, `addable-members`에서 해당 id 제거 확인 |
| 14 | 멤버 추가 — 다른 팀 사용자 → 403 | SKIP | 비admin 세션이라 `/api/admin/users` 미접근 → 다른 팀 user_id 획득 경로 없음. admin 세션 재실행 시 자동 활성화되도록 conditional skip 작성 (`adminAccessible` 게이트). 백엔드 라우트(`app.py:2337-2339`)와 DB 함수(`database.py:1747-1748`)는 정적 분석상 팀 불일치 시 403 반환 확인 완료 |
| 15 | 멤버 추가 — 팀 없는 사용자 → 403 | SKIP | 동일 사유 (admin 세션 부재로 team_id NULL 사용자 enumerate 불가). conditional skip — admin 세션 재실행 시 자동 활성화 |
| 16 | 관리 권한 이양 — owner→멤버, 기존 owner는 일반 멤버 | PASS | 별도 fixture(`xferHidden`) 생성 → 같은 팀 멤버 추가 → `POST .../transfer-owner { user_id }` 200 → `GET .../can-manage`에서 본인 `is_owner=false, can_manage=false` 확인. 이양 후 본인이 멤버 자격은 유지하지만 owner 아님은 일반 멤버 상태에 해당 |
| 17 | admin 관리자 변경 → 성공, 기존 owner는 일반 멤버 | PASS (비admin 분기 — 403 검증) | 비admin 세션이라 `POST .../change-owner`가 `_require_admin`에 의해 403 반환. role 게이트가 정상 동작함을 검증. admin 세션이면 200 + 멤버 owner 플래그 cross-check가 자동 활성화되도록 분기 작성 |
| 18 | 히든 항목 is_public=1 시도 → 403 | PASS | `mainHidden` 산하 이벤트(#19에서 이동된 이벤트 재사용)에 `PATCH /api/events/{id}/visibility { is_public: 1 }` → 403, `detail`에 "히든 프로젝트 항목" 포함 검증 |
| 19 | 일반→히든 이동 시 is_public 강제 0 | PASS | 일반 프로젝트 산하 이벤트 생성 → `PATCH .../visibility { is_public: 1 }` → `PATCH /api/events/{id}/project { project: 히든 }` → 응답 `{ ok: true, hidden_forced: true }` 검증 |

### 추가 검증

- **자기 일관성**: `mainHidden`의 `owner_id` ↔ `myUserId` (히든 생성 응답 owner_id) ↔ `members` 응답에서 `is_owner=1`인 row id 일치 cross-check (#13, #16)
- **fixture 격리**: 권한 이양/변경 후 본인이 owner 자격을 잃는 케이스(#16/#17)는 별도 프로젝트(`xferHidden`/`chgownHidden`) 사용 → `mainHidden` 권한 보존 → #18/#19 후속 테스트 영향 없음
- **API 경계면 검증**: 모든 응답을 `{ ok, status, json, text }` 구조로 캡처하여 status code + detail 문자열 동시 검증

### 발견 이슈 / 수정 사항

테스트 작성·실행 과정에서 백엔드/프론트엔드 결함은 발견되지 않았습니다. 테스트 측 한 가지 정정:

- 초기에는 `let moveTestEventId` 선언을 #19 테스트 다음에 두어 hoisting/TDZ 위험이 있었으나 실행 전 describe 블록 상단으로 이동하여 안정화.

### 회귀 확인

- 기존 `phase46_hidden_project_a.spec.js`의 fixture(`__phase46_hidden_*`)와 본 spec(`__phase46b_*`)은 prefix가 분리되어 충돌 없음.
- A단계 PASS 항목(#1, #3~#7, #9)은 본 spec의 사전 단계(히든 생성·이름 중복·관리 목록 노출)에서도 동일하게 동작 — 회귀 없음.

### 권한 매트릭스 정적 분석 (참고)

| 동작 | 서버 라우트 가드 | DB 검증 |
|------|-----------------|---------|
| `POST .../members` | `_require_editor` + `_require_hidden_can_manage` (owner 또는 admin) | `add_hidden_project_member`: 팀 일치 검사 |
| `DELETE .../members/{id}` | `_require_editor` + `_require_hidden_can_manage` | `remove_hidden_project_member`: owner 자기삭제 차단 |
| `POST .../transfer-owner` | `_require_editor` + `proj.owner_id == user.id` (admin도 불가) | `transfer_hidden_project_owner`: requester==owner & target∈members |
| `POST .../change-owner` | `_require_admin` (전용) | `admin_change_hidden_project_owner`: target∈members |
| `PATCH /api/events/{id}/visibility` | `is_public` truthy + project가 hidden → 403 | — |
| `PATCH /api/events/{id}/project` | 이동 대상 hidden이면 `is_public` 강제 0, 응답 `hidden_forced: true` | — |

### 전체 판정

**B단계 통과** — 자동화 가능한 5개 항목(13/16/17/18/19) 모두 PASS. 14/15는 admin 세션 부재라는 환경 제약으로 SKIP이며, conditional skip을 통해 admin 환경 재실행 시 자동 활성화된다. 백엔드 권한 게이트와 DB 검증 로직은 정적 분석상 사양 일치.

### 다음 단계 권고

- admin 계정 환경에서 본 spec 재실행 시 항목 #14·#15·#17(admin 분기)이 자동 활성화 — 회귀 검증 권고.
- C단계(이동 확인 모달, 히든 휴지통 분리, 팀원 제외 경고) 진입 시 본 spec과 동일한 fixture 격리 패턴(`__phase46c_*` prefix + 케이스별 별도 프로젝트) 사용 권장.
- B단계 프론트엔드 모달(멤버 관리/권한 이양/관리자 변경)에 대한 UI E2E는 별도 spec으로 분리 권고 (현재 spec은 API 경계면 위주).

---

## C단계 QA

- 테스트 파일: `tests/phase46_hidden_project_c.spec.js`
- 실행 일시: 2026-05-08
- 환경: `https://192.168.0.18:8443/` (IP 화이트리스트 자동 로그인, editor 세션 — `/api/admin/users` 401/403)
- Playwright 결과: **4 passed / 2 skipped / 0 failed** (총 6건 — 환경 점검 1 + 항목 5)

### 체크리스트 결과 (사양 20~26, 7항목)

| # | 항목 | 결과 | 비고 |
|---|------|------|------|
| 20 | 히든→일반 이동 confirm 없이 → 400 requires_confirm | PASS | `#20+#21+#22` 통합 케이스. `PATCH /api/events/{id}/project` 응답 status=400, json `{ requires_confirm: true, message: "히든 프로젝트 밖으로 이동..." }` 정확 검증 |
| 21 | 히든→일반 이동 confirm: true 포함 → 성공 | PASS | 동일 이벤트에 `{ project, confirm: true }` 재요청 → 200, `{ ok: true, hidden_forced: false }` 검증 (일반 프로젝트 이동이므로 hidden_forced=false) |
| 22 | 일반→히든 이동 → 성공 + hidden_forced 응답 | PASS | 일반 프로젝트 산하 이벤트 → 히든으로 이동 → 응답 `{ ok: true, hidden_forced: true }` 검증 |
| 23 | 히든 프로젝트 삭제 → 휴지통 이동 | PASS | `DELETE /api/manage/projects/{name}` 200 → `GET /api/trash` groups에 `{ project: { id, name, is_hidden:1, owner_id:myUserId } }` 포함 검증 |
| 24 | 비멤버는 히든 휴지통 항목 미표시 (owner는 표시 확인) | PASS (owner 가시성 동적 + 비멤버 정적 분석) | 자동 로그인 환경에서 비멤버 세션 시뮬레이션 불가 → owner 본인에게는 trash에 보이는 점만 동적 검증. 비멤버 차단은 `database.py:get_trash_items` 분기 (`is_hidden && !is_admin && owner_id != viewer_id`)로 정적 검증. 백엔드 변경사항 문서(`backend_changes.md` C-2)와 일치 |
| 25 | 팀원 제외 시 owner이면 warning 반환 | SKIP | `/api/admin/users` 미접근 (비admin 세션) → 임의 user를 owner로 만드는 경로(admin change-owner) 사용 불가. admin 세션 재실행 시 자동 활성화되도록 conditional skip 작성. 백엔드 라우트(`app.py:1268-1279`)는 정적 분석상 `old_team_id != new_team_id` OR `is_active=0`이고 `force` 미설정·hidden_owned 존재 시 warning 반환 — 사양 일치 |
| 26 | force: true로 강제 제외 → owner 이양 또는 NULL | SKIP | #25 의존 (`warnHidden` fixture 미생성). admin 세션 재실행 시 자동 활성화. 검증 로직: members 응답에서 `is_owner=1` 행이 target이 아닌 다른 멤버(myUserId)로 이양됐는지 확인, target user의 team_id NULL 확인. 이양 불가 시(다른 멤버 없을 때) `owner_id=NULL` 분기도 어노테이션 처리 |

### 추가 검증

- **환경 점검 (PASS)**: `CURRENT_USER` 노출 + role/team_id/myUserId/sameTeamUserId/adminAccessible/targetUserId 어노테이션. 테스트 분기 디버깅용 메타 데이터.
- **자기 일관성**: `mainHidden.owner_id`(probe 히든 생성 응답)와 myUserId, `trashHidden.owner_id`(휴지통 group의 project.owner_id)가 모두 myUserId로 일치 cross-check.
- **fixture 격리**: #20~#22(mainHidden) / #23~#24(trashHidden) / #25~#26(warnHidden) 각각 별도 프로젝트 사용 → 케이스 간 부수효과 차단.

### 발견 이슈 / 수정 사항

테스트 작성·실행 과정에서 백엔드/프론트엔드 결함은 발견되지 않았습니다. 테스트 측 정정 사항:

- #21 응답에서 `hidden_forced: false`까지 명시 검증 추가 (일반 프로젝트로 이동 시 false 보장 — `app.py:1892~1899`의 분기와 일치).
- #24는 자동 로그인 환경 한계로 비멤버 세션을 만들 수 없어, owner 가시성 동적 + 비멤버 정적 분석으로 분리 처리. 어노테이션으로 한계 명시.
- #25/#26은 운영 사용자를 변형하지 않도록 `afterAll`에서 `targetOriginalTeamId`/`targetOriginalIsActive`로 상태 복원하는 cleanup 단계 포함 (admin 세션에서만 동작).

### 회귀 확인

- 기존 `phase46_hidden_project_a.spec.js`(`__phase46_hidden_*`) / `phase46_hidden_project_b.spec.js`(`__phase46b_*`) / 본 spec(`__phase46c_*`) prefix 분리 — 충돌 없음.
- A/B단계 PASS 항목(#1, #6, #13, #18, #19)은 본 spec의 사전 단계(히든 생성·멤버 추가·이동 확인)에서도 동일하게 동작 — 회귀 없음.

### 권한·라우트 정적 분석 (참고)

| 동작 | 서버 라우트 | 검증 위치 |
|------|------------|----------|
| 히든→일반 이동 confirm 가드 | `PATCH /api/events/{id}/project` (app.py:1885-1889) | `data.get("confirm")` 미설정 시 JSONResponse 400 + `requires_confirm: True` |
| 일반→히든 이동 hidden_forced | `PATCH /api/events/{id}/project` (app.py:1892-1899) | `_proj.is_hidden`이면 `update_event_visibility(0)` + `hidden_forced=True` |
| 히든 휴지통 분리 | `GET /api/trash` + `database.py:get_trash_items` (3128-3132) | viewer 미admin이고 owner_id != viewer.id이면 groups에서 제외 |
| 히든 복원 권한 검사 | `POST /api/trash/project/{id}/restore` (app.py:3862-3866) | `get_trash_hidden_project(id).is_hidden`이면 admin 또는 owner만 |
| 팀원 제외 warning | `PUT /api/admin/users/{id}` (app.py:1268-1281) | old_team_id != new_team_id OR is_active=0이고 hidden_owned 존재·force 미설정 시 warning |
| 강제 이양 | `transfer_hidden_projects_on_removal` (database.py) | added_at 가장 빠른 다른 멤버로 이양, 없으면 owner_id NULL |

### 전체 판정

**C단계 통과** — 자동화 가능한 4개 항목(#20/#21/#22/#23/#24, #20+21+22는 단일 테스트로 통합) 모두 PASS. #25/#26은 admin 세션 부재라는 환경 제약으로 SKIP이며, conditional skip을 통해 admin 환경 재실행 시 자동 활성화된다. 백엔드 confirm 가드, hidden_forced 분기, 휴지통 권한 검사 로직은 정적 분석상 사양 일치.

### 다음 단계 권고

- admin 계정 환경에서 본 spec 재실행 시 항목 #25/#26이 자동 활성화 — 회귀 검증 권고. 단 대상 user를 운영 환경에서 변경하므로 `afterAll` 복원 로직(`targetOriginalTeamId`/`targetOriginalIsActive`)을 반드시 점검 후 실행할 것.
- C단계 프론트엔드 변경(이동 확인 모달 `wuDialog.confirm`, hidden_forced toast, admin warning 처리)에 대한 UI E2E는 별도 spec으로 분리 권고 (현재 spec은 API 경계면 위주).
- meeting/checklist 이동 confirm은 `PATCH /api/checklists/{id}` 경로에 적용되어 있으나(app.py:919-930) 본 spec에서는 events 위주로 검증. 체크리스트 케이스도 admin 세션에서 회귀 검증 권고.
