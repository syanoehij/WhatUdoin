# Frontend 변경 (그룹 D catchup — 비로그인 진입 재설계)

## templates/base.html

### nav 영역 (line 386~) 분기 재작성
- 로그인 사용자: 기존 전역 nav 유지 (`/doc`, `/check`, `/calendar`, `/kanban`, `/gantt`, `/project-manage`, `/avr`).
- 비로그인 + `portal_team` set: 그 팀의 `portal_menu` 외부공개 메뉴 4개(kanban/gantt/doc/check) 만 노출. 링크는 `/{팀이름 | urlencode}/{한글키 | urlencode}` 형태.
- 비로그인 + `portal_team` 없음 (예: `/`, `/register`): 4개 메뉴 모두 숨김 (미니멀 진입).

## templates/team_portal.html

### 별도 탭 영역(`.portal-tabs`) 제거
헤더 nav 가 메뉴 진입을 처리하므로 본문은 `active_menu` 1개 패널만 렌더.

### `active_menu` 분기 렌더
- `active_menu is none` → "현재 공개된 콘텐츠가 없습니다" 안내.
- `active_menu == 'kanban' | 'gantt' | 'doc' | 'check'` → 해당 패널만 `display: block` 으로 렌더.

### scripts 정리
탭 전환 JS(`#portal-tabs .portal-tab` 클릭 핸들러) 제거 — 더 이상 필요 없음.

### invariant 보존 (phase82)
- hero-sub `"공개 포털 — 공개 설정된 항목만 ..."` 문구 유지 (line 138/234 assert).
- 우상단 `<a href="/register" class="btn btn-sm btn-primary">계정 가입</a>` 비로그인 분기 유지.
- 삭제 예정 팀 안내 페이지 유지 (`{% if deleted %}` 분기).

## templates/admin.html

### `tab-teams` 패널 안에 미리보기 섹션 추가 (표 위)
- 마커: `id="admin-public-preview"`.
- 데이터: 컨텍스트 기존 `teams` (admin_page 가 `db.get_all_teams()` 로 채움) 에서 `deleted_at` 있는 팀을 `rejectattr` 로 제외.
- 카드: 각 활성 팀 이름이 `/{팀 | urlencode}` 로 링크(`target="_blank"`).
- SSR 렌더. JS gate 없음.

## templates/home.html
변경 없음. 사용자 요청 "미니멀 진입"의 핵심은 base.html 헤더 메뉴 제거였음 — 본문(view-guest 블록의 팀 카드 그리드)은 이미 적합. phase81 invariant (`view-guest`/`view-user`/`view-unassigned` 마커 + 공유 함수) 보존.
