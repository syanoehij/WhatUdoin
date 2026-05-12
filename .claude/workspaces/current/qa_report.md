# qa_report — 팀 기능 그룹 B #14

## 방식
TestClient + 임시 DB (`monkeypatch.setattr(db, "DB_PATH", ...)`). 운영 서버는 IP 자동 로그인이라 특정 사용자 상태(미소속/pending/rejected/approved/admin) 브라우저 재현 불가 — phase81/82 와 동일하게 TestClient 로 검증. 임시 DB 는 실행 후 정리.

## 신규: `tests/phase83_team_portal_loggedin.py` — 9/9 PASS

| # | 케이스 | 단언 |
|---|--------|------|
| 1 | 정적: `team_public_portal` | `my_team_status` 컨텍스트 전달 + `is_admin`/`user_team_ids`/`get_my_team_statuses` 사용 + `RedirectResponse` 미사용 + `import app` OK |
| 2 | 정적: `team_portal.html` | `my_team_status == 'approved'/'pending'` 분기 + `user.role == 'admin'` 분기 + `applyToTeam(`/`async function applyToTeam`/`/api/me/team-applications` + `가입 대기 중` `disabled` + `#14` 주석 + `구현하지 않는다` 미루기 문구 제거됨 |
| 3 | 미소속 로그인, 신청 이력 없음 → `/ABC` | 200, `onclick="applyToTeam(` 노출, `가입 대기 중`·`btn-primary">계정 가입` 부재 |
| 4 | 해당 팀 pending → `/ABC` | 200, `<button class="btn btn-sm" disabled>가입 대기 중</button>` 노출, `applyToTeam(` 부재 |
| 5 | 다른 팀 pending(해당 팀 미소속) → `/ABC` | 200, `applyToTeam(` 노출 (서버가 클릭 시 `pending_other` 차단 — UI 관심사 아님), pending 버튼 부재 |
| 6 | 해당 팀 approved 멤버 → `/ABC` | 200, `applyToTeam(`·`가입 대기 중`·`계정 가입` 모두 부재 |
| 7 | 해당 팀 rejected → `/ABC` | 200, `applyToTeam(` 재노출 (재신청), pending 버튼 부재 |
| 8 | admin → `/ABC` | status 200 (30x 아님), `applyToTeam(`·`가입 대기 중`·`계정 가입` 부재, `id="portal-tabs"` 존재 (포털 본문 정상) |
| 9 | 비로그인 → `/ABC` | 200, `btn-primary">계정 가입` 노출 (#13 회귀), `applyToTeam(`·pending 버튼 부재 |

공통(`_assert_portal_ok`): 모든 케이스 status 200, `공개 포털 — 공개 설정된 항목만` 본문, `href="/" class="btn btn-sm btn-outline">홈` (홈 버튼이 `/` 로) 존재.

## 회귀: 모두 PASS
- `tests/phase80_landing_page.py` (#11) — 5/5
- `tests/phase81_unassigned_user.py` (#12) — 8/8
- `tests/phase82_team_portal.py` (#13) — 8/8
- 합계 phase83 포함 30/30 PASS (`pytest ... -v`, 7.9s)

## 사전 결함 (이번 변경 무관)
`tests/test_project_rename.py` 2 FAIL — 옛 픽스처 DB 에 `projects.team_id` 없음 (master HEAD 동일, #13 에서도 동일 기록).

## 서버 재시작
**필요** — `app.py` + `templates/team_portal.html` 변경 (코드/템플릿 reload). 스키마 무변경 → 마이그레이션 불필요. 본 단위 검증은 TestClient 로 완료(서버 재시작 없이 통과).
