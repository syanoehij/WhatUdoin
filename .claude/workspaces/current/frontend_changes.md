# #11 프론트엔드 변경

## templates/home.html
### `#view-guest` 블록 교체
- 기존: 비로그인 사용자용 칸반 보드 (`guest-team-filter` select, `guest-board`, `loadGuest()`).
- 신규: "팀 목록 랜딩"
  - `.landing-hero`: WhatUDoin 헤더 + 안내문 + "로그인"(`openLoginModal()`) / "계정 가입"(`/register`) 버튼.
  - `.landing-team-grid`: 각 팀을 `<a href="/{{ team.name | urlencode }}">` 카드로 렌더. 팀 이름 + 화살표.
  - 팀 없음 시: "아직 생성된 팀이 없습니다" 안내.

### CSS
- `.home-toolbar`, `.home-toolbar select`, `.kanban-total-label` 등 게스트 칸반 전용 스타일 제거.
- `.landing-hero`, `.landing-actions`, `.landing-team-grid`, `.landing-team-card`, `.landing-team-name`, `.landing-team-arrow` 신규 추가. 기존 토큰(`var(--surface)`, `var(--accent)` 등) 사용.

### JS — 고아 정리
- `loadGuest()` 함수 제거.
- `DOMContentLoaded`: `CURRENT_USER` 없으면 `view-guest` 표시만 (데이터 fetch 없음, SSR 정적 렌더). `loadProjColors()`는 로그인 시에만 호출.
- `window.onEventSaved`: `loadGuest()` 참조 제거 → `if (CURRENT_USER) loadUser()`.
- `wu:events:changed` 실시간 동기화 핸들러: 비로그인 시 early return.
- 공유 코드(`cardHTML`, `buildBoard`, `loadUser`, `renderNotice`, `#view-user` 로직, `__pageSearch`) **미변경**.

## 알려진 한계
- 팀 카드 → `/팀이름` 링크는 #13 라우트 부재로 현재 404. 링크 자체는 사양대로 구현 (#13에서 라우트 추가 시 동작).
