# #15-3 프론트엔드 — 확인만 (변경 불필요)

검토 대상: `templates/notice.html`, `templates/notice_history.html`, `templates/base.html`.

## 결론: 코드 변경 없음

### `templates/notice.html`
- 편집기/뷰 분기와 `INITIAL_MD` 는 SSR `notice`(=`db.get_notice_latest_for_team(work_team_id)`)에서 옴 → 작업 팀 기준으로 자연 반영. 다른 팀 공지는 SSR 단계에서 제외되어 렌더되지 않음.
- 작업 팀 전환: `base.html` 의 `selectWorkTeam()` → `location.reload()` (work_team_id 쿠키 갱신 후) → 페이지 새 렌더 → SSR `notice` 가 새 팀 공지로. JS 추가 작업 불필요.
- `GET /notice` 라우트에 `_ensure_work_team_cookie` 가 붙어 첫 진입 시 쿠키 없으면 발급됨 — 이후 `/api/notice` POST/notify·헤더 드롭다운이 같은 work_team_id 컨텍스트 사용.
- `IS_EDITOR` / `{% if user and user.role in ('editor','admin') %}` 게이팅 = #16(시스템 관리자 슈퍼유저 권한 정리) 책임 — 본 사이클 미변경.
- 헤더에 현재 팀 이름 pill 추가는 #15-3 요구사항 아님(`work_team_name` 은 이미 `_ctx` 에 있어 향후 polish 가능) — #15-2 처럼 "프론트 변경 없음" 유지.

### `templates/notice_history.html`
- `HISTORIES` 는 SSR `histories`(=`db.get_notice_history(work_team_id, include_null=is_admin)`)에서 옴 → 작업 팀 이력만(admin은 + NULL orphan). `h.created_by`(이름 문자열)·`h.created_at` 만 렌더 — `team_id` 미표시라 NULL orphan row 표시 깨짐 없음. 변경 불필요.

### `templates/base.html`
- 헤더의 `/notice` 링크는 페이지 라우트만 가리킴 — 페이지 자체가 작업 팀 기준이므로 변경 불필요.
- `selectWorkTeam`→`location.reload()` 흐름은 #15에서 이미 구축 — notice 페이지에도 그대로 적용됨.

## 검증
- `templates.get_template('notice.html'/'notice_history.html'/'base.html')` 파싱 OK (백엔드 검증 시 동시 확인).
