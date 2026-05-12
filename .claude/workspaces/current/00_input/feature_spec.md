# 요청

'팀 기능 구현 todo.md' 그룹 B #12 — `/` 팀 미배정 로그인 사용자 화면 + "내 자료" 영역.
상세 사양: '팀 기능 구현 계획.md' 섹션 7 ("팀에 배정되지 않은 로그인 사용자"), 섹션 8 (개인 문서 가시성).
선행: 그룹 A #1~#10 + 그룹 B #11 완료(커밋 ae7a74e). #11에서 `home.html` 비로그인 뷰가 팀 목록 랜딩으로 교체, `app.py index()`가 비로그인·로그인 모두 `home.html` 렌더.

# 분류

기능 추가 / 팀 모드 (backend-dev → frontend-dev → code-reviewer → qa)

# 배경: 팀 미배정 사용자의 정의

"팀 미배정 로그인 사용자" = 로그인했으나 `user_teams` approved row가 0개 (또는 모두 삭제 예정 팀)인 비-admin 사용자.
- `auth.user_team_ids(user)`는 이미 `JOIN teams ... AND deleted_at IS NULL`로 삭제 예정 팀을 자동 제외하므로 "삭제 예정 팀만 남은 사용자 = 미배정"이 자동 충족된다.
- **admin은 미배정 분기에 들어가면 안 된다.** admin도 `user_teams` row가 없어 `user_team_ids(admin) == set()`이지만, 슈퍼유저이므로 일반 업무 대시보드(`view-user`)를 본다. 판별자는 반드시 `not is_admin`을 AND한다.
- 신규 헬퍼 `auth.is_unassigned(user)` 한 곳에서만 판별. 프론트는 SSR이 내려준 플래그(`IS_UNASSIGNED`)만 신뢰하고, `user.team_id`(legacy 필드)로 클라이언트에서 재추론하지 말 것 — 막 추방된 사용자는 legacy team_id가 남아 있어 오판을 부른다.

# backend-dev 담당 작업

변경 대상: `auth.py`, `app.py` (스키마 무변경 — 마이그레이션 phase 추가 없음)

## 1. `auth.is_unassigned(user)` 신규 헬퍼 (`auth.py`)
```python
def is_unassigned(user) -> bool:
    """로그인했으나 approved 소속 팀이 0개인 비-admin 사용자 (삭제 예정 팀만 남은 경우 포함)."""
    if user is None:
        return False
    if is_admin(user):
        return False
    return len(user_team_ids(user)) == 0
```
`user_team_ids` 바로 아래에 배치.

## 2. `app.py index()` — SSR 컨텍스트 분기
- `is_unassigned(user)`면 추가 컨텍스트를 `_ctx`로 넘긴다:
  - `is_unassigned=True`
  - `team_status_map`: `{team_id: 'pending'|'rejected'}` — 해당 사용자가 신청한 비-삭제 팀들의 `user_teams.status` (단, `approved`는 미배정 정의상 존재 안 함). 신규 DB 헬퍼 `db.get_my_team_statuses(user_id)` 추가 — `SELECT ut.team_id, ut.status FROM user_teams ut JOIN teams t ON t.id = ut.team_id WHERE ut.user_id = ? AND t.deleted_at IS NULL AND ut.status IN ('pending','rejected')`. 결과를 dict로.
  - `my_docs`: 본인 작성 개인 문서 — `db.get_my_personal_meetings(user_id)` 신규 헬퍼. `SELECT m.*, t.name as team_name FROM meetings m LEFT JOIN teams t ON m.team_id = t.id WHERE m.deleted_at IS NULL AND m.created_by = ? AND m.is_team_doc = 0 ORDER BY m.updated_at DESC`. **`team_share` 값으로 거르지 않는다** — 본인 화면이므로 `team_share=1`이라도 본인에게는 모두 보인다 (계획서 섹션 7·8: "자기 자료 통합 노출 목적"). `team_id IS NULL` 조건도 넣지 않는다 — 막 추방돼 `team_id != NULL`인 개인 문서도 본인 "내 자료"에 나와야 한다.
  - **주의**: 일정·체크·팀 문서(`is_team_doc=1`)는 "내 자료"에 표시하지 않는다 (전부 팀 컨텍스트 필요 — 계획서 섹션 7).
- `is_unassigned`가 아니면 기존대로(`teams=db.get_visible_teams()`만, 또는 비로그인이면 그대로). 비로그인/일반 로그인 동작 불변.
- 미배정 사용자에게도 `teams=db.get_visible_teams()`는 계속 넘긴다 (팀 목록 + 신청 버튼 렌더용).

## 3. `POST /api/doc` / `PUT /api/doc/{id}` / `PATCH /api/doc/{id}/visibility` — 미배정 서버측 강제 (`app.py`)
- 미배정 사용자가 문서 생성·수정 시 **서버에서** `is_team_doc=0`, `team_share=0`으로 강제 (UI disable만으로는 우회 가능).
  - `create_doc`: `if auth.is_unassigned(user): is_team_doc = 0; team_share = 0`
  - `update_doc`: 동일. (단 기존 문서가 팀 문서였다가 미배정 상태에서 수정되는 엣지 — `is_team_doc=0` 강제로 개인 문서화. 미배정 사용자가 편집할 수 있는 문서는 `_can_write_doc`상 본인 작성 개인 문서뿐이라 실질 영향 없음.)
  - `rotate_doc_visibility`: 미배정 사용자면 `team_share` 토글 단계를 건너뛰고 `is_public` 0↔1만 순환(또는 단순히 호출 자체를 막아도 됨 — 미배정이 가진 건 개인 문서뿐이고 개인 문서 visibility는 `(0,0)→(0,1)→(1,0)→...`인데 `team_share=1` 단계가 의미 없으므로). **최소 구현**: 미배정이면 `new_share`를 항상 0으로 고정하고 `is_public`만 토글.
- `create_doc`은 이미 `auth.resolve_work_team(request, user)`로 `team_id`를 채우는데, 미배정 사용자는 `user_team_ids`가 빈 set이고 admin도 아니므로 `resolve_work_team`이 legacy `user.team_id`를 반환할 수 있다. **미배정 사용자의 신규 개인 문서는 반드시 `team_id = NULL`** (계획서 섹션 3·7). → `create_doc`에서 `team_id = None if auth.is_unassigned(user) else auth.resolve_work_team(request, user)`로 명시.

## 4. `GET /api/notifications/pending` / `GET /api/notifications/count` — 미배정 빈 응답 (`app.py`) [경미한 하드닝]
- `if auth.is_unassigned(user): return []` / `return {"count": 0}`. (UI에서 벨을 숨기지만 SSE/직접 호출 방어.)

## 검증 시 주의 (backend가 self-check)
- `app` import OK.
- admin은 `is_unassigned`가 False.

# frontend-dev 담당 작업

변경 대상: `templates/home.html`, `templates/base.html`

## 1. `templates/home.html` — `view-unassigned` 블록 신규
- 기존 `#view-guest`(비로그인), `#view-user`(일반 로그인) 사이에 `#view-unassigned` 블록 추가. `class="hidden"`.
- 구성:
  1. **헤더 안내**: "아직 소속된 팀이 없습니다. 팀에 신청하면 관리자 승인 후 업무를 시작할 수 있어요." 같은 안내문.
  2. **팀 목록 + 신청 버튼** (`team_status_map` 활용):
     - 각 팀 카드(또는 행)에:
       - `team_status_map[team.id] == 'pending'` → "가입 대기 중" 비활성 버튼
       - 그 외(미신청 또는 `rejected`) → "팀 신청" 버튼 (클릭 시 `POST /api/me/team-applications` body `{team_id}`)
     - **`pending_other` 처리 결정**: 어느 한 팀에 pending이 있으면 그 팀만 "가입 대기 중", 나머지 팀은 "팀 신청" 버튼을 그대로 노출한다(클라이언트에서 비활성화하지 않음). 다른 팀 신청 버튼을 누르면 서버가 409("다른 팀 신청이 처리 대기 중입니다.")를 반환하므로 그 메시지를 toast/alert로 보여주고 끝낸다. 신청 성공(`{ok:true}`) 시 페이지를 reload하여 상태 갱신.
     - 빈 목록이면 "아직 생성된 팀이 없습니다."
     - 팀 이름 옆 `→` 화살표 등 `/팀이름` 링크는 #13 책임 — **여기서는 만들지 않는다**. 카드 자체를 클릭 가능 링크로 만들지 말 것(미배정 화면은 신청 버튼만).
  3. **"내 자료" 영역** (`my_docs` 활용):
     - 섹션 타이틀 "📄 내 자료" + "+ 새 문서" 버튼(`user.role in ('editor','admin')`이면 노출).
     - `my_docs` 각 항목: 제목 + 수정일 + (있으면) `team_name`(추방돼 team_id 남은 경우 표시) — 클릭 시 `/doc?open={id}` 또는 기존 문서 페이지 진입 패턴 따라 이동. **기존 `home.html`/`doc.html`이 단건 문서를 여는 방식을 확인해 그 패턴 재사용** (예: `location.href='/doc#'+id` 등 — 추측하지 말고 doc 페이지 라우트/쿼리 확인).
     - "+ 새 문서" 클릭 → 기존 doc 작성 모달/페이지 진입. **`team_share` 토글이 거기 있으면 미배정 사용자에겐 비활성화** (계획서 섹션 7: "team_share는 적용 의미가 없으므로 UI에서 비활성화"). doc 작성 UI가 별도 페이지면 그 페이지에서 `IS_UNASSIGNED` 플래그로 토글 disabled 처리. **doc 작성 UI 위치를 먼저 확인** (templates/doc.html 등) — home.html에 모달이 없으면 단순히 `/doc`로 이동시키고 doc.html 쪽에서 처리.
     - `my_docs`가 비면 "작성한 개인 문서가 없습니다."
  4. **알림 비노출**: 이 블록에는 알림 카드/뱃지/영역을 일절 넣지 않는다. (벨은 base.html에서 처리 — 아래.)
- **DOMContentLoaded 분기**: 기존
  ```js
  if (CURRENT_USER) { view-user 표시 + loadProjColors().then(loadUser) }
  else { view-guest 표시 }
  ```
  를 →
  ```js
  if (CURRENT_USER && IS_UNASSIGNED) { view-unassigned 표시 + (필요시 가벼운 초기화) }
  else if (CURRENT_USER) { view-user 표시 + loadProjColors().then(loadUser) }
  else { view-guest 표시 }
  ```
  `IS_UNASSIGNED`는 base.html이 내려주거나 home.html에서 `{{ is_unassigned | default(false) | tojson }}`로 정의. **`loadUser()`/`loadProjColors()`는 미배정 사용자에게 호출하지 말 것** (`/api/kanban`, `/api/my-meetings` 등이 빈 결과 — 호출해도 깨지진 않지만 불필요).
- `wu:events:changed` 핸들러 등 실시간 동기화: 미배정이면 early-return (이미 `if (!CURRENT_USER) return;` 패턴 — 미배정도 reload 불필요하므로 `if (!CURRENT_USER || IS_UNASSIGNED) return;`).
- `window.__pageSearch`: 미배정 화면에 칸반 카드가 없으므로 검색은 그대로 둬도 무해(매치 0). 굳이 손대지 않음.

## 2. `templates/base.html` — 알림 벨 게이팅
- `current_user_payload`에 `is_unassigned` 추가: `{'role': user.role, 'team_id': user.team_id, 'name': user.name, 'is_unassigned': user_is_unassigned}` — 단 base.html에서 `auth.is_unassigned`를 직접 호출할 수 없으므로, **`_ctx`가 항상 `is_unassigned` 키를 컨텍스트에 넣도록** `app.py _ctx`를 수정(`"is_unassigned": auth.is_unassigned(user)`). 그러면 base.html에서 `{{ is_unassigned | tojson }}`로 `var IS_UNASSIGNED` 정의 가능.
- 알림 벨 `#notif-bell-wrap` 블록을 `{% if user and not is_unassigned %}`로 감싼다 (현재 `{% if user %}` 안에 있으므로 그 안에서 한 번 더 분기). 미배정이면 벨 자체가 DOM에 없음 → 뱃지/드롭다운/페이지 모두 비노출.
- 알림 벨 관련 JS init(폴링 등)은 `#notif-bell-wrap`이 없으면 optional chaining(`?.`)으로 이미 안전하지만, 폴링 setInterval이 돌면 불필요한 `/api/notifications/*` 호출 발생 → 가능하면 `if (CURRENT_USER && !IS_UNASSIGNED)` 가드 추가. 깨지지만 않으면 최소 변경 우선.
- **다른 nav 링크는 건드리지 않는다** (#12 범위 밖). 미배정 사용자가 `/doc`, `/check`, `/kanban` 등에 접근하면 그쪽은 이미 `_work_scope`가 빈 set → 본인 NULL-team 자료 + public만 보임(그룹 A #10에서 처리됨). 그 라우트들의 접근 차단은 #12 범위 아님.

# 주의사항 / 범위 경계

- **#13 (`/팀이름` 동적 라우트)는 범위 밖** — 팀 카드 클릭 시 404는 #11 이후 의도된 단계적 상태. 미배정 화면에서는 팀 카드를 링크로 만들지 않고 신청 버튼만 둔다.
- **#15 (`work_team_id` 쿠키 UI)는 범위 밖** — admin은 여전히 일반 `view-user`를 본다. 쿠키 발급/검증/팀 변경 UI 추가 금지.
- **스키마 변경 없음** — 마이그레이션 phase 추가하지 않는다. 새 DB 헬퍼는 SELECT만.
- 기존 공유 코드(`cardHTML`/`buildBoard`/`loadUser`/`renderNotice`/`#view-user`/`__pageSearch`)는 일반 로그인 사용자용으로 그대로 둔다 — 미배정 분기만 추가.
- QA는 #11처럼 TestClient(임시 DB)로 검증 (운영 서버는 IP 자동 로그인이라 브라우저로 특정 사용자 화면 재현 불가).
