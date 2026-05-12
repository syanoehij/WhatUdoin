# 프론트엔드 변경 — 팀 기능 #12

## templates/home.html
- CSS: `.unassigned-team-card` / `.my-docs-list` / `.my-doc-item` / `.my-doc-title` / `.my-doc-meta` / `.my-doc-team-tag` 추가 (게스트 랜딩 CSS 아래).
- 신규 `#view-unassigned` 블록 (`#view-guest`와 `#view-user` 사이, `class="hidden"`):
  - `.landing-hero` 안내문("아직 소속된 팀이 없습니다 …")
  - 팀 목록: 각 팀 `.unassigned-team-card` — `team_status_map.get(team.id) == 'pending'`이면 "가입 대기 중"(disabled), 아니면 "팀 신청"(`applyToTeam(team.id)`). 빈 목록 시 "아직 생성된 팀이 없습니다." 팀 이름은 링크로 만들지 않음(#13 책임).
  - "📄 내 자료" 섹션 + "+ 새 문서"(`/doc/new?personal=1`, `user.role in ('editor','admin')`만). `my_docs` 각 항목 `.my-doc-item` — 제목 + (있으면)`team_name` 태그 + `updated_at[:10]`, 클릭 시 `/doc/{id}`. 빈 목록 시 "작성한 개인 문서가 없습니다."
- JS: `const IS_UNASSIGNED = {{ ... }}` (SSR 플래그만 신뢰). `applyToTeam(teamId)` — `POST /api/me/team-applications`, 성공 시 toast + `location.reload()`, 실패 시 서버 detail toast(409 "다른 팀 신청 처리 대기 중"도 그대로 노출). DOMContentLoaded 3분기: `CURRENT_USER && IS_UNASSIGNED` → `view-unassigned` / `CURRENT_USER` → `view-user`+`loadUser` / else → `view-guest`. `wu:events:changed` 핸들러 early-return 조건에 `|| IS_UNASSIGNED` 추가(미배정은 reload 불필요).

## templates/base.html
- `var IS_UNASSIGNED = {{ 'true' if is_unassigned else 'false' }}` 추가 (CURRENT_USER 옆).
- 알림 벨 `#notif-bell-wrap` 블록을 `{% if not is_unassigned %}`로 감쌈 — 미배정이면 벨/뱃지/드롭다운 DOM 미생성.
- 앱 내 알림 IIFE 진입 가드 `if (!CURRENT_USER) return;` → `if (!CURRENT_USER || IS_UNASSIGNED) return;` (불필요한 `/api/notifications/*` 폴링 차단).
- 다른 nav 링크는 미변경(#12 범위 밖 — 그 라우트 접근 시 `_work_scope`가 그룹 A #10에서 이미 빈 set 처리).

## templates/doc_editor.html
- `const IS_UNASSIGNED = {{ 'true' if is_unassigned else 'false' }}` 추가.
- 세그먼트 초기화: 미배정이면 `doc-type-segment`(나만/팀원 수정) 숨김 + `VIS_OPTS`에서 "팀에게만 공개"(=team_share) 옵션 제거 → 미배정 사용자는 외부 비공개/외부 공개만 선택. 백엔드도 `is_team_doc=0`/`team_share=0` 강제하므로 UI 우회 무해.

## 비고
- 일반 로그인 사용자·비로그인 사용자·admin 화면 동작 불변(공유 코드 `cardHTML`/`buildBoard`/`loadUser`/`renderNotice`/`#view-user`/`__pageSearch` 미변경).
- 익명 `GET /` 200 + `view-guest`/`view-unassigned` 둘 다 마크업에 존재(하나는 hidden) 확인.
