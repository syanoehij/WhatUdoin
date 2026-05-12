# code_review_report — 팀 기능 그룹 B #15-2 (links 다중 팀 전환)

리뷰 대상: `database.py` (get_links/update_link/create_link), `app.py` (/api/links 4개 라우트), `tests/phase86_links_multiteam.py`. base.html 무변경.

## 차단 결함
없음.

## 경고 / 관찰
1. **admin GET 시 orphan team 링크 노출** — `get_links(work_team_ids=None)` 의 `OR (scope='team')` 절은 `team_id IS NULL` 인 잔존 team 링크(그룹 A #4 백필 누락분, 정상이면 0건)도 admin에게 노출한다. admin 슈퍼유저 패턴(`/api/events`·`/api/checklists` 등 admin→전 팀)과 일관하므로 의도된 동작. 운영 후 `settings.team_migration_warnings` 에 links 경고가 있으면 운영자가 정리. → **수용**.
2. **`require_work_team_access` 와 `resolve_work_team` 의 explicit 우선** — POST에서 `data.get("team_id")` 가 명시되면 `resolve_work_team` 이 무조건 신뢰(`explicit_id` 우선)하지만, 직후 `require_work_team_access` 가 비admin 비소속이면 403. admin은 슈퍼유저로 통과 → 임의 팀에 team 링크 작성 가능 (의도됨 — admin은 모든 팀 자료 큐레이션 가능). `manage/projects` 라우트와 동일 패턴. → **수용**.
3. **`api_delete_link` default role** `"editor"` → `"member"` 정리 — 동작 무영향(role이 dict에 항상 존재). 관례 일관성. → **수용**.
4. **프로젝트 관례 준수**: `_work_scope`/`resolve_work_team`/`require_work_team_access` 사용 = #10 라우트와 동일 패턴. `_require_editor` 미변경(#16 책임). `create_link` 시그니처 보존 — 호출부가 team_id 확정. 마이그레이션 phase 추가 없음. ✔

## 테스트 커버리지
- 정적 invariant 3건 (시그니처/admin 분기/라우트 헬퍼 사용 + import).
- TestClient 시나리오 A~I: 작업 팀 전환 / 다른 팀 멤버 격리 / personal 본인 한정 / POST team_id 확정 / admin CRUD + 전 팀 GET / 같은 팀 멤버 B 403 / admin 타인 편집·삭제 / admin work_team 없이 POST 400 / 회귀(personal CRUD, 비로그인 [], validation).
- 직접 DB: get_links work_team_ids 컨벤션 (None/set()/{tid}/{ta,tb}/타 사용자).
- 회귀: phase80~85 60 PASS.

**판정: 통과.**
