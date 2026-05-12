# 요청

'팀 기능 구현 todo.md' 그룹 B #14 — `/팀이름` 로그인 사용자 (소속 무관). 상세 사양: '팀 기능 구현 계획.md' 섹션 7 (팀에 배정된 로그인 사용자, 시스템 관리자) + 섹션 7 말미 "`/팀이름` 공개 포털에서의 계정/팀 신청 버튼 상태" 표.

#13에서 `/{team_name}` 동적 라우트(`app.py` `team_public_portal`) + `templates/team_portal.html` 공개 포털이 이미 생겼고, 로그인 사용자도 200 공개 포털을 받되 redirect는 안 하는 상태. 로그인 상태별 UI 분기만 #14로 미뤄둠.

# 분류

백엔드 수정 + 프론트 수정 (라우트 컨텍스트 추가 + 템플릿 버튼 분기). 실행 모드: 팀 모드 (backend → frontend → reviewer → qa).

# 핵심 사양 — `/팀이름` 공개 포털 계정/팀 신청 버튼 상태 (계획서 섹션 7 표가 권위)

| 접근 상태 | 버튼 |
|-----------|------|
| 비로그인 | "계정 가입" → `/register` (가입 후 자동 로그인 + `/`로) — **이미 #13에서 구현됨, 그대로 유지** |
| 로그인, 해당 팀 미소속 (user_teams row 없음 / status='rejected' / 추방) | "팀 신청" → 해당 팀이 미리 선택된 상태로 바로 신청 (이름·비밀번호 입력 없음) |
| 로그인, 해당 팀 pending 대기 중 | "가입 대기 중" (disabled, 비활성) |
| 로그인, 해당 팀 approved 소속 | 버튼 없음 |
| 로그인, admin | **표에 없음 → 버튼 없음**으로 결정. admin은 슈퍼유저 — `user_teams` 소속이 가상이고 "팀 신청" 의미가 없다. 라우트에서 명시적으로 admin → 버튼 없음 처리하고 근거 주석을 남긴다. (계획서: admin은 일반 사용자와 동일하게 공개 포털을 보되 redirect 안 함.) |

공통: 로그인이든 admin이든 `/팀이름`은 항상 200 공개 포털 — redirect 절대 금지. 홈 버튼은 `/`로 이동 (이미 #13에서 `<a href="/">홈</a>` 구현됨, 유지).

`pending_other`(다른 팀 pending) 처리: #12 패턴 그대로. `/팀이름`에서 "해당 팀에 pending"이 아니면 그냥 "팀 신청" 버튼을 보인다. 사용자가 클릭하면 기존 `POST /api/me/team-applications`가 `pending_other` 에러를 반환한다. **#14에서 "다른 팀 신청 중" 같은 새 UI 상태를 추가하지 않는다.** home.html과 동일하게 동작.

# 에이전트별 작업

## backend-dev

대상 파일: `app.py` (`team_public_portal` 라우트만)

`team_public_portal` 라우트(`app.py` ~L4990)에서 deleted가 아닌 경우 템플릿 컨텍스트에 **해당 팀에 대한 현재 사용자 상태**를 추가한다:

- 키 이름: **`my_team_status`** (프론트와 합의된 이름 — 다른 이름 쓰지 말 것).
- 값: `"approved"` | `"pending"` | `"rejected"` | `None`.
- 계산 방법 (#12 패턴 재사용, 새 DB 헬퍼 만들지 않음):
  - `user = auth.get_current_user(request)` (`_ctx`가 이미 user를 넣지만 라우트에서 명시적으로 다시 가져와 분기 — 기존 라우트가 이미 `request`만 받으므로 user 변수 추가).
  - `user` 가 `None` → `my_team_status = None` (비로그인 — 템플릿에서 `not user`로 분기되므로 사실상 안 쓰임).
  - `auth.is_admin(user)` → `my_team_status = None` (admin은 버튼 없음 — 위 표 결정. 주석으로 근거).
  - 그 외: `team["id"] in auth.user_team_ids(user)` → `"approved"`; 아니면 `db.get_my_team_statuses(user["id"]).get(team["id"])` (→ `"pending"` | `"rejected"` | `None`).
- deleted 팀 분기는 `my_team_status` 전달 불필요 (안내 페이지만 — 버튼 없음). 그대로 둔다.
- `_ctx(request, team=team, deleted=False, portal=portal, my_team_status=my_team_status)` 형태로 넘긴다.
- 라우트 상단 주석의 "#14 범위" 언급(`"팀 신청 / 가입 대기 중" 등 로그인 사용자 UI 분기는 #14 범위.`)을 #14 구현 완료에 맞게 수정 (예: "로그인 사용자·admin 별 버튼 분기는 my_team_status 컨텍스트 + 템플릿에서 처리").

주의: `auth.user_team_ids`는 approved 소속 set을 반환한다(`auth.py` 확인). `auth.is_admin` 존재 확인.

## frontend-dev

대상 파일: `templates/team_portal.html` (`.portal-hero-actions` 영역 + 하단 `{% block scripts %}`)

1. `.portal-hero-actions` 안의 버튼 분기를 다음으로 교체 (계획서 섹션 7 표):
   - `<a href="/" class="btn btn-sm btn-outline">홈</a>` — 그대로 유지 (홈 버튼은 `/`로).
   - `{% if not user %}` → `<a href="/register" class="btn btn-sm btn-primary">계정 가입</a>` — 그대로 유지.
   - `{% elif my_team_status == 'approved' %}` → 버튼 없음 (아무것도 렌더 안 함).
   - `{% elif my_team_status == 'pending' %}` → `<button class="btn btn-sm" disabled>가입 대기 중</button>` (home.html과 동일한 마크업).
   - `{% else %}` (로그인 + 미소속/rejected, admin은 my_team_status=None이지만 admin도 여기 들어옴 — admin 버튼 없음 처리 필요!) →
     - **admin 처리**: `{% elif user.role == 'admin' %}` 를 `{% else %}` 앞에 추가해서 admin이면 버튼 없음. 그 다음 `{% else %}` 에서 `<button class="btn btn-sm btn-primary" onclick="applyToTeam({{ team.id }})">팀 신청</button>`.
     - 즉 분기 순서: `not user` → `my_team_status == 'approved'` → `my_team_status == 'pending'` → `user.role == 'admin'` → `else (팀 신청)`.
   - 기존 line 73 `{# #14 범위: 로그인 사용자용 "팀 신청 / 가입 대기 중" 버튼 분기는 #13 에서 구현하지 않는다. #}` 주석 제거(또는 #14 구현 설명 주석으로 교체).
   - 상단 docstring 주석(L4~8) 의 "#14 에서 로그인 UI 분기 추가" 도 완료에 맞게 정리.

2. `applyToTeam` JS 함수: home.html의 것을 `team_portal.html` `{% block scripts %}` 안에 최소 버전으로 복제 (약 15줄). `showToast`/`alert` fallback 포함. 성공 시 `setTimeout(() => location.reload(), 600)`. `team_portal.html`에는 home.html의 다른 JS가 없으므로 독립 복제가 적절(중복 허용 — surgical change). `{% if not deleted %}` 안에 둔다 (deleted 페이지에는 팀 신청 버튼 자체가 없음).
   - `showToast`가 base.html에서 전역으로 제공되는지 확인하고, 없으면 `alert` fallback만으로도 충분.

3. 데이터 노출/탭/패널 등 #13 구현은 일절 건드리지 않는다 (surgical).

# 검증 (qa)

TestClient(임시 DB) — 운영 서버는 IP 자동 로그인이라 특정 사용자 상태(미소속/pending/admin) 브라우저 재현 불가. `tests/phase83_team_portal_loggedin.py` 신규.

케이스:
1. 미소속 로그인 사용자, 신청 이력 없음 → `/ABC` 200, "팀 신청" 버튼(`applyToTeam(<team_id>)`) 노출, "가입 대기 중" 부재, "계정 가입" 부재.
2. 해당 팀 pending → `/ABC` 200, "가입 대기 중" + `disabled` 노출, "팀 신청" 부재.
3. 다른 팀 pending (해당 팀은 미소속) → `/ABC` 200, "팀 신청" 노출 (서버가 클릭 시 차단 — UI 관심사 아님).
4. 해당 팀 approved 멤버 → `/ABC` 200, "팀 신청"·"가입 대기 중"·"계정 가입" 모두 부재.
5. 해당 팀 rejected → `/ABC` 200, "팀 신청" 재노출 (재신청 가능).
6. admin → `/ABC` 200 (30x 아님), "팀 신청"·"가입 대기 중" 부재, 포털 본문 정상.
7. 모든 케이스: status 200, redirect(30x) 없음, 홈 버튼 `href="/"` 존재.
8. 비로그인 → `/ABC` 200, "계정 가입" 그대로 (#13 회귀).
9. 회귀: `tests/phase80_landing_page.py`(#11), `tests/phase81_unassigned_user.py`(#12), `tests/phase82_team_portal.py`(#13) 전부 PASS.
10. `import app` OK.

TestClient에서 특정 사용자로 로그인하는 방법: 기존 phase81/82 테스트가 어떻게 세션/쿠키를 세팅하는지 참고. 임시 DB에 user + user_teams row를 직접 insert 후 auth 세션 쿠키 발급 경로 사용.

# 주의사항

- 라우트는 절대 redirect하지 않는다 — 로그인/admin 모두 200 포털.
- `my_team_status` 키 이름을 backend/frontend가 동일하게 사용해야 함. 다르면 템플릿이 조용히 모두에게 "팀 신청"을 렌더.
- 분기 순서 주의: admin(role='admin')은 `my_team_status=None`이므로 `else`에 빠지지 않게 `user.role == 'admin'` 분기를 `else` 앞에 둔다.
- 데이터 노출 로직(#13)·예약어·404·deleted 안내·탭 전환 IIFE는 변경 금지.
- 스키마 무변경 — 마이그레이션 phase 추가 없음 (SELECT만 재사용).
