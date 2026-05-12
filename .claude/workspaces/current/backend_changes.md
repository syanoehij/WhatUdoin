# 백엔드 변경 — 팀 기능 #12

## auth.py
- `is_unassigned(user)` 신규 — 로그인했으나 approved 소속 팀 0개인 비-admin. `user_team_ids` 가 이미 `deleted_at IS NULL` 필터링 → 삭제 예정 팀만 남은 사용자도 미배정 취급. admin 은 False. `user_can_access_team` 바로 위에 배치.

## database.py
- `get_my_team_statuses(user_id)` 신규 — `{team_id: 'pending'|'rejected'}`. 비-삭제 팀의 user_teams.status 중 pending/rejected 만. `decide_team_application` 바로 위에 배치.
- `get_my_personal_meetings(user_id)` 신규 — 본인 작성 개인 문서(`is_team_doc=0`) 전체. `team_share`/`team_id IS NULL` 로 거르지 않음(본인 화면 통합 노출). `m.*` + `t.name as team_name` + `event_count`. `_viewer_team_ids` 바로 위에 배치.

## app.py
- `_ctx()`: `"is_unassigned": auth.is_unassigned(user)` 추가 — base.html 알림 벨 게이팅 등에 사용.
- `index()`: `auth.is_unassigned(user)` 면 `team_status_map`/`my_docs` 컨텍스트 추가. 비로그인·일반 로그인·admin 동작 불변(`teams=db.get_visible_teams()` 그대로).
- `POST /api/doc` (`create_doc`): 미배정이면 `is_team_doc=0`/`team_share=0` 강제, `team_id=None` 강제(`resolve_work_team` 의 legacy fallback 우회).
- `PUT /api/doc/{id}` (`update_doc`): 미배정이면 `is_team_doc=0`/`team_share=0` 강제.
- `PATCH /api/doc/{id}/visibility` (`rotate_doc_visibility`): 미배정이면 `is_public` 0↔1 만 토글(team_share 단계 스킵).
- `GET /api/notifications/count` · `GET /api/notifications/pending`: 미배정이면 빈 응답(SSE/직접 호출 방어).

스키마 변경 없음 — 마이그레이션 phase 추가 없음. `import app` OK.
