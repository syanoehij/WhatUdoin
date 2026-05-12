# 요청

'팀 기능 구현 todo.md' 그룹 B의 첫 항목 — **#11. `/` 비로그인 접속 화면** 구현.
상세 사양: '팀 기능 구현 계획.md' 섹션 7 (비로그인 사용자).

## 사양 (todo #11)
- [ ] `/` 비로그인: 생성된 팀 목록 + 로그인 버튼 + 계정 가입 버튼 표시
- [ ] 팀 클릭 시 `/팀이름`으로 이동
- 검증: 팀 목록에 삭제 예정 팀(`teams.deleted_at IS NOT NULL`) 제외 확인

## 계획서 섹션 7 발췌
> 비로그인 사용자
> - `/` 접속 시: 생성된 팀 목록 + 로그인 버튼 + 계정 가입 버튼 표시
> - 팀을 클릭하면 `/팀이름`으로 이동하며 해당 팀의 공개 설정된 데이터를 읽기 전용으로 표시.

# 분류

기능 추가 (UI 정비 + 백엔드 라우트 동작 변경) / 팀 모드: backend-dev → frontend-dev → code-reviewer → qa

# 에이전트별 작업

## backend-dev

1. `app.py` `index()` 라우트 (line ~690) 변경:
   - 현재: 비로그인 시 `RedirectResponse("/kanban")`. → **제거.**
   - 변경 후: 비로그인 사용자도 `home.html`을 렌더한다. 단 `teams`는 **삭제 예정 팀 제외** 목록을 넘긴다.
   - 로그인 사용자 동작은 그대로 유지(기존 `view-user` 대시보드).
2. 삭제 예정 팀 제외:
   - `database.py`에 `get_visible_teams()` 새 헬퍼 추가 — `SELECT * FROM teams WHERE deleted_at IS NULL ORDER BY name`.
   - **기존 `get_all_teams()`는 건드리지 않는다** (다른 라우트들이 공유). `index()`에서만 `get_visible_teams()` 사용.
   - 비로그인이든 로그인이든 `index()`가 넘기는 `teams`는 visible(=비삭제) 목록으로 통일 (현재 home.html은 로그인 시 teams를 게스트 필터에만 쓰므로 영향 미미하나, 일관성 위해 둘 다 visible).
3. `/kanban`, `/calendar`, `/gantt` 등 다른 라우트의 `get_all_teams()` 호출 — **이번 단위 범위 밖. 건드리지 않는다.**
4. 검증: `app.py` 변경 후 import 에러 없는지 빠르게 확인.

### 주의
- `/kanban` 라우트의 비로그인 진입은 막지 않는다(기존 게스트 칸반 뷰 그대로). 이번 단위는 `/` 동작만 바꾼다.
- `/팀이름` 동적 라우트는 #13 책임. 이번 단위에서는 만들지 않는다 → 비로그인 사용자가 팀 링크 클릭 시 현재로선 404 가능. **이는 의도된 상태이며 QA가 결함으로 잡지 않도록 명시한다.**

## frontend-dev

`templates/home.html` 게스트 뷰 정비:

1. 현재 `#view-guest` 블록은 **비로그인 사용자용 칸반 보드**(`guest-team-filter` select + `guest-board` + `loadGuest()`)다. → **제거하고 "팀 목록 랜딩"으로 교체.**
   - 새 게스트 뷰: 환영 헤더 + 생성된 팀 카드/리스트. 각 팀은 `/팀이름`으로 이동하는 링크.
   - 로그인 버튼: base.html 헤더에 이미 `openLoginModal()` 버튼이 비로그인 시 노출됨. 게스트 랜딩 본문에도 명시적 "로그인" / "계정 가입(`/register`)" 버튼/링크를 둔다 (계획서 섹션 7: "로그인 버튼 + 계정 가입 버튼 표시").
2. 팀 링크 href: 팀 이름에 한글·특수문자가 들어가므로 **URL 인코딩** 필요. Jinja `{{ team.name | urlencode }}` 또는 적절한 인코딩 사용. `<a href="/{{ team.name }}">` 같은 raw 삽입 금지.
3. **고아 코드 정리** (CLAUDE.md "Surgical Changes"):
   - `loadGuest()` 함수 제거 (더 이상 호출 안 됨).
   - `#view-guest` 전용 CSS(`.home-toolbar`, `guest-board` 관련 등) 중 게스트 칸반에만 쓰던 규칙 제거.
   - **공유 코드는 절대 건드리지 않는다**: `cardHTML`, `buildBoard`, `loadUser`, `renderNotice`, 로그인 대시보드(`#view-user`)에서 쓰는 모든 것.
   - `DOMContentLoaded` 핸들러의 `else { ... loadGuest() }` 분기 → 새 게스트 뷰 표시 로직으로 교체 (정적 SSR 렌더이므로 단순히 `view-guest` 표시만으로 충분할 수 있음).
   - `window.onEventSaved`, `wu:events:changed` 핸들러에서 `loadGuest` 참조 제거/정리.
4. 빈 팀 목록 처리: 생성된 팀이 하나도 없으면 "아직 생성된 팀이 없습니다" 안내.
5. 디자인은 기존 프로젝트 톤(`var(--surface)`, `var(--accent)`, `.btn` 클래스 등) 유지. 과한 신규 스타일 금지.

### 주의
- 팀 클릭 → `/팀이름`은 #13 라우트가 없어 현재 404일 수 있음. 링크 자체는 사양대로 구현한다.
- 게스트 뷰에서 `CURRENT_USER`는 falsy. `view-user` 분기 로직 깨지 않게.

# 주의사항 (공통)

- 의존: #11은 그룹 A(#8 포함) 완료 후 unblocked. 그룹 A 완료됨.
- 범위 밖(절대 손대지 말 것): #12("내 자료" 영역·팀 신청 버튼 분기), #13(`/팀이름` 라우트), #15(work_team_id 쿠키). 이번 단위는 `/` 비로그인 화면만.
- `get_all_teams()` 시그니처/동작 변경 금지 (광범위 공유).
- 임시 산출물(스크린샷 등)은 `.claude/workspaces/current/screenshots/`에 저장.
