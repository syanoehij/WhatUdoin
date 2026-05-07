# 히든 프로젝트 관리 기능 — 구현 사양

## 구현 결정 사항 (advisor 검토 반영)

| 항목 | 결정 |
|------|------|
| `is_private` vs `is_hidden` | **독립 플래그**. `is_private`=비로그인 외부 차단(기존 유지), `is_hidden`=로그인 사용자 중 멤버/owner/admin에게만 노출(신규) |
| 이름 중복 검사 | `deleted_at` 필터 **제거** — 휴지통 포함 전체 중복 검사 |
| 히든 생성 에러 메시지 | 이유 불문하고 항상 "생성할 수 없습니다. 다른 이름을 넣어주세요." (충돌 대상 누설 방지) |
| 일반 프로젝트 생성 에러 메시지 | 기존 유지 ("이미 존재하는 이름입니다" 가능) |
| 이동 API 확인값 | request body에 `confirm: true` 필드 |
| 일반→히든 이동 시 공개 전환 알림 | 이동 직후 toast 메시지 "외부 공개가 비공개로 전환되었습니다" |
| owner 강제 제외 후 처리 | `projects.owner_id = NULL` → admin만 관리 가능 상태 유지 |
| 멤버 목록 필터 | admin도 owner 팀 기준으로 동일 목록 표시 (admin 우회 불가) |
| 복원 시 멤버 목록 | project_members 레코드 유지 (soft-delete 없음) |

## DB 스키마 변경 사항

```sql
-- projects 테이블 신규 컬럼 (_migrate 패턴)
ALTER TABLE projects ADD COLUMN is_hidden INTEGER DEFAULT 0;
ALTER TABLE projects ADD COLUMN owner_id INTEGER;  -- 일반 프로젝트는 NULL

-- 신규 테이블: 히든 프로젝트 멤버 (일반 멤버)
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, user_id)
);
```

- **owner_id**: 단일 컬럼으로 유일성 보장. `hidden_project_members`에는 일반 멤버만 저장.
- owner도 멤버 테이블에 포함 (조회 편의). owner가 "관리 권한 이양" 시 owner_id 교체 + 기존 owner는 members에만 남음.

---

## A단계: 스키마 + 히든 생성 + 가시성 필터 + 이름 중복 검사

### A-1. DB (backend-dev)

1. `_migrate(conn, "projects", [("is_hidden", "INTEGER DEFAULT 0"), ("owner_id", "INTEGER")])` 추가
2. `project_members` 테이블 `CREATE TABLE IF NOT EXISTS` 추가
3. 이름 중복 검사 쿼리 수정:
   - 현재: `WHERE LOWER(name) = LOWER(?) AND deleted_at IS NULL`
   - 변경: `WHERE LOWER(name) = LOWER(?)` (deleted_at 필터 제거 — 휴지통 포함)
   - 적용 위치: `create_project`, 히든 프로젝트 생성 함수 모두

### A-2. 신규 API (backend-dev)

#### `POST /api/manage/hidden-projects`
- 요청: `{ name, color, memo }`
- 검증:
  - 로그인 필수, 팀 없는 사용자 → 403 "팀 소속 사용자만 히든 프로젝트를 생성할 수 있습니다."
  - 이름 중복(일반+히든+휴지통 전체) → **422 "생성할 수 없습니다. 다른 이름을 넣어주세요."** (이유 불문)
- 처리:
  - `projects` 테이블에 `is_hidden=1, owner_id=현재 user.id` 로 INSERT
  - `project_members` 테이블에 `(project_id, user_id)` INSERT (owner도 멤버에 포함)
- 응답: 생성된 프로젝트 정보

#### `GET /api/projects` 및 관련 목록 API 수정
- `is_hidden=1` 프로젝트는 아래 조건 충족 시에만 포함:
  - `user.role == 'admin'`
  - 또는 `user.id in project_members.user_id WHERE project_id=해당 프로젝트`
  - 또는 `projects.owner_id == user.id`
- 수정 대상 DB 함수: `get_projects()`, `get_all_projects_with_events()`, `get_all_projects_meta()`
- viewer 파라미터 추가 또는 기존 파라미터 활용

#### `GET /api/manage/hidden-projects/{name}/can-manage`
- 현재 사용자가 해당 히든 프로젝트를 관리할 수 있는지 확인
- 응답: `{ can_manage: bool, is_owner: bool, is_admin: bool }`

### A-3. 프로젝트 목록 가시성 필터 — 기존 API 수정 (backend-dev)

- `GET /api/projects`, `GET /api/project-list`, `GET /api/manage/projects`, `GET /api/project-timeline` 등
- is_hidden=1 프로젝트: `_is_visible_hidden(user, project_row)` 헬퍼로 필터링
- 헬퍼 정의:
  ```python
  def _is_visible_hidden(user, proj_id):
      if not user: return False
      if user["role"] == "admin": return True
      # project_members 또는 owner 확인
      row = conn.execute(
          "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?",
          (proj_id, user["id"])
      ).fetchone()
      return row is not None
  ```

### A-4. 프론트엔드 (frontend-dev)

#### 히든 프로젝트 생성 버튼
- `project-manage` 페이지의 "프로젝트 생성" 버튼 **아래에** "히든 프로젝트 생성" 버튼 추가
- 버튼 클릭 시 모달 열기 (기존 생성 모달과 유사하되 헤더 "히든 프로젝트 생성"으로 구분)
- API 호출: `POST /api/manage/hidden-projects`
- 에러 422 시 "생성할 수 없습니다. 다른 이름을 넣어주세요." 표시

#### 프로젝트 목록에서 히든 프로젝트 표시
- 히든 프로젝트에 자물쇠 아이콘(🔒) 또는 "히든" 뱃지 표시
- 히든 프로젝트의 편집/삭제 버튼: `can_manage=true`인 경우에만 표시 (admin 또는 owner)
- 일반 멤버에게는 편집/삭제 버튼 숨김

---

## B단계: 멤버 관리 + 권한 이양 + 공개 락 (A단계 완료 후)

### B-1. 멤버 관리 API

#### `GET /api/manage/hidden-projects/{name}/members`
- 현재 멤버 목록 반환 (owner 포함)
- 권한: owner 또는 admin

#### `GET /api/manage/hidden-projects/{name}/addable-members`
- 추가 가능한 사용자 목록
- 필터: owner와 **같은 팀 소속** 사용자 중 현재 멤버 아닌 사람
- admin도 동일 필터 적용 (admin 우회 불가)

#### `POST /api/manage/hidden-projects/{name}/members`
- 요청: `{ user_id: int }`
- 권한: owner 또는 admin
- 검증: 대상 user의 team_id == owner의 team_id (필수)
- 팀 없는 사용자 추가 시도 → 403

#### `DELETE /api/manage/hidden-projects/{name}/members/{user_id}`
- 권한: owner 또는 admin
- owner 자신 삭제 불가 (먼저 권한 이양 필요)

#### `POST /api/manage/hidden-projects/{name}/transfer-owner`
- 요청: `{ user_id: int }` (현재 멤버 중 1명)
- 권한: owner만
- 처리: `projects.owner_id = user_id`, 기존 owner는 project_members에 유지

#### `POST /api/manage/hidden-projects/{name}/change-owner` (admin 전용)
- 요청: `{ user_id: int }`
- 권한: admin만
- 처리: `projects.owner_id = user_id`, 기존 owner는 project_members에 유지
- (transfer-owner와 별도 엔드포인트 — 강제 변경임을 명시)

### B-2. 산하 항목 외부 공개 락

- `PATCH /api/manage/projects/{name}/privacy` 에서 is_hidden=1 프로젝트 산하 항목 is_public 변경 차단
- `events`, `checklists`, `meetings`의 is_public 변경 API에서:
  - 해당 항목의 project가 is_hidden=1이면 → 403 "히든 프로젝트 항목은 외부 공개 불가"
- 히든 프로젝트로 항목 이동 시 is_public 강제 0으로 전환

### B-3. 프론트엔드

- 히든 프로젝트 상세/편집 화면에 "멤버 관리" 버튼 추가 (owner/admin에게만)
- 멤버 관리 모달: 현재 멤버 목록 + 추가 가능 사용자 드롭다운 + 삭제 버튼
- owner에게 "관리 권한 이양" 버튼 표시
- admin에게 "관리자 변경" 버튼 표시 (owner에게는 미표시)
- 일반 멤버에게 위 버튼 모두 숨김

---

## C단계: 이동 확인 모달 + 히든 휴지통 + 팀원 제외 경고 (B단계 완료 후)

### C-1. 이동 확인 모달

- `PATCH /api/events/{id}/project`, `PATCH /api/checklists/{id}/project`, `PATCH /api/meetings/{id}/project`:
  - 히든→일반 이동: request body에 `confirm: true` 없으면 → 400 `{ requires_confirm: true, message: "히든 프로젝트 밖으로 이동합니다. 계속하시겠습니까?" }`
  - 일반→히든 이동: 허용, 단 is_public 강제 0 + toast "외부 공개가 비공개로 전환되었습니다"
  - 일반↔일반: 기존 동작 유지
- 프론트: API 응답에 `requires_confirm: true`가 있으면 확인 모달 표시 → 사용자 확인 후 `confirm: true`로 재요청

### C-2. 히든 프로젝트 휴지통 분리

- 히든 프로젝트 삭제: 기존과 동일하게 soft-delete (deleted_at 설정)
- 휴지통 목록 API에서 히든 프로젝트:
  - `user.role == 'admin'` 또는 `projects.owner_id == user.id`인 경우만 조회
- 복원, 영구 삭제 API에서 히든 프로젝트: 동일 권한 검증 추가

### C-3. 팀원 제외 시 경고

- `DELETE /api/manage/teams/{team_id}/members/{user_id}` (또는 팀원 제외 API):
  - 제외 대상 user가 히든 프로젝트 owner인지 확인
  - 있다면 → 200 + `{ warning: true, hidden_projects: [프로젝트 이름 목록], message: "..." }` 반환 (제외 실행 안 함)
  - 2단계: request body에 `force: true` 포함 시 강제 제외 실행
  - 강제 제외 처리:
    - 해당 프로젝트 멤버 중 다른 팀원이 있으면 → 자동 이양 시도 (나이 순 또는 added_at 기준)
    - 이양 불가 시 → `owner_id = NULL` (admin만 관리 가능 상태)

---

## QA 체크리스트 (단계별)

### A단계 QA

1. 히든 프로젝트 생성 — 정상 (팀 있는 사용자)
2. 히든 프로젝트 생성 — 팀 없는 사용자 → 403
3. 이름 중복 — 일반 프로젝트와 동일 이름 → 422 "생성할 수 없습니다"
4. 이름 중복 — 휴지통 이름과 동일 → 422 "생성할 수 없습니다"
5. 이름 중복 — 히든 프로젝트와 동일 → 422 "생성할 수 없습니다"
6. 에러 메시지가 "이미 존재" 문구를 포함하지 않음 (누설 방지)
7. owner는 자신의 히든 프로젝트를 목록에서 볼 수 있음
8. 일반 멤버(히든 멤버)는 히든 프로젝트를 볼 수 있음
9. 비멤버(같은 팀이어도)는 히든 프로젝트를 볼 수 없음
10. admin은 모든 히든 프로젝트를 볼 수 있음
11. 히든 프로젝트에 자물쇠 아이콘/뱃지 표시
12. 일반 멤버에게는 편집/삭제 버튼 미표시

### B단계 QA
13. 멤버 추가 — 같은 팀 사용자 → 성공
14. 멤버 추가 — 다른 팀 사용자 → 403
15. 멤버 추가 — 팀 없는 사용자 → 403
16. 관리 권한 이양 — owner가 멤버에게 → 성공, 기존 owner는 일반 멤버
17. admin 관리자 변경 → 성공, 기존 owner는 일반 멤버
18. 히든 항목 is_public 변경 시도 → 403
19. 일반→히든 이동 시 is_public 강제 0

### C단계 QA
20. 히든→일반 이동 confirm 없이 → 400 requires_confirm
21. 히든→일반 이동 confirm: true 포함 → 성공
22. 일반→히든 이동 → 성공 + toast 메시지
23. 히든 프로젝트 삭제 → 휴지통 이동
24. 비멤버는 히든 휴지통 항목 미표시
25. 팀원 제외 시 owner이면 경고 반환
26. force: true로 강제 제외 → owner 이양 또는 NULL 처리
