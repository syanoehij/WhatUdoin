## 코드 리뷰 보고서 — 팀 기능 #12 (`/` 팀 미배정 로그인 사용자 + "내 자료")

### 리뷰 대상 파일
- `auth.py` — `is_unassigned()` 신규
- `database.py` — `get_my_team_statuses()`, `get_my_personal_meetings()` 신규
- `app.py` — `_ctx()`, `index()`, `create_doc`/`update_doc`/`rotate_doc_visibility`, `/api/notifications/{count,pending}`
- `templates/home.html` — `#view-unassigned` 블록 + CSS + JS
- `templates/base.html` — `IS_UNASSIGNED` 전역 + 알림 벨 게이팅
- `templates/doc_editor.html` — 미배정 시 doc-type 세그먼트 숨김 + team_share 옵션 제거

### 차단(Blocking) ❌
- 없음.

### 경고(Warning) ⚠️
- `app.py:_ctx()` — 모든 페이지 렌더 시 `auth.is_unassigned(user)` 호출 → 로그인 비-admin 사용자에 한해 +1 DB 쿼리(`user_team_ids`). 비로그인·admin 은 짧은 경로(`user is None` / `is_admin`)로 즉시 반환하므로 영향 제한적. 인트라넷 규모상 허용. (#15에서 work_team 쿠키 도입 시 캐시 가능.)
- `templates/doc_editor.html` — 미배정 사용자가 `?personal=1` 없이 `/doc/new` 직접 진입 시 숨겨진 doc-type 세그먼트가 'team' active 로 렌더 → 저장 페이로드 `is_team_doc=true`. 단 `POST /api/doc` 가 미배정이면 서버에서 `is_team_doc=0`/`team_share=0`/`team_id=NULL` 강제하므로 데이터상 무해. "내 자료 → + 새 문서" 정규 동선은 `/doc/new?personal=1` 사용 → 'personal' 렌더.
- `templates/home.html` — `applyToTeam`은 `fetch` 실패 시 `showToast` 미정의 환경 대비 `alert` fallback 있음. `res.ok` 체크 + `detail` 파싱 정상.

### 통과 ✅
- [x] 권한 체크: `create_doc`/`update_doc`/`rotate_doc_visibility` 모두 기존 `_require_editor` + `_can_write_doc` 유지. `index()`는 누구나 접근(의도). 알림 엔드포인트 미배정 빈 응답은 추가 가드(누수 방지).
- [x] `_ctx()` 사용: `index()`가 `_ctx(request, teams=teams, **extra)` 올바르게 호출.
- [x] DB 경로: 변경 없음 (DB 헬퍼는 SELECT만, `get_conn()` 사용).
- [x] SQL 파라미터화: `get_my_team_statuses`/`get_my_personal_meetings` 모두 `?` 바인딩. f-string SQL 없음.
- [x] 스키마 변경 없음 — `_migrate` phase 미추가 (요청 사양 부합).
- [x] XSS: home.html 의 `my_docs`/`teams` 출력은 Jinja2 자동 이스케이프. `applyToTeam`은 `fetch` 인자만 사용, `innerHTML` 미사용. doc 제목 등은 서버 렌더.
- [x] 미배정 판별 정합: `auth.is_unassigned`가 `is_admin` 먼저 체크 → admin 은 미배정 아님(advisor 지적 사항 준수). 프론트는 SSR `IS_UNASSIGNED` 만 신뢰(legacy `user.team_id` 재추론 안 함).
- [x] `team_id=NULL` 강제: `create_doc`이 `None if unassigned else resolve_work_team(...)` — `resolve_work_team`의 legacy fallback 우회 (계획서 섹션 3·7).
- [x] "내 자료" 범위: `get_my_personal_meetings`는 `is_team_doc=0 AND created_by=?` 만 — `team_share` 무관 노출(본인 화면), `team_id IS NULL` 미필터(추방 후 잔존 문서 포함). 일정·체크·팀 문서 제외. "+ 새 문서" 버튼은 `user.role in ('member','editor','admin')` — 가입 시 role=member 인 미배정 사용자에게도 노출(`_require_editor`=is_member 통과 정합, advisor 지적 반영). `view-user`의 "+ 일정 추가"는 기존 `('editor','admin')` 그대로 — 범위 밖 사전 불일치, 본 사이클에서 손대지 않음.
- [x] 알림 비노출: base.html 벨 블록 `{% if not is_unassigned %}` + 알림 IIFE `IS_UNASSIGNED` early-return + 백엔드 빈 응답 — 카드/뱃지/페이지(별도 페이지 라우트 없음) 모두 차단.
- [x] 범위 경계: `/팀이름` 동적 라우트(#13) 미추가 — 미배정 화면 팀 카드는 링크 아님. `work_team_id` 쿠키 UI(#15) 미추가 — admin 은 여전히 `view-user`.
- [x] `import app` OK. 익명 `GET /` 200 (템플릿 렌더 정상, undefined `my_docs`/`team_status_map` 비크래시).

### 최종 판정
**통과** — 차단 결함 없음. QA 진행 가능.
