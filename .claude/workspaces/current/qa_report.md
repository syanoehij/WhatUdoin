# QA 보고서 — 비로그인 진입 재설계 (그룹 D catchup)

## 신규 슈트
- `tests/phase99_unauth_redesign.py` (15 케이스, 모두 PASS)
  - 정적 invariant 7건 (라우트 순서, 헬퍼 존재, _ctx portal_* 키, RESERVED 잠식 방지, base.html 분기, team_portal 재설계, admin 미리보기 마커).
  - TestClient 동작 8건 (/ 미니멀, /{팀} 기본 메뉴, 메뉴별 라우트 4종, 영문/캘린더 라우트 404, 메뉴 0개 안내, 삭제 예정 팀, 로그인 사용자 글로벌 nav, 예약어 잠식 방지).

## 본 변경으로 의미가 바뀐 invariant 갱신
- `phase82_team_portal.py`
  - `test_dynamic_route_registered_last`: 검사 대상 함수를 `team_public_portal` → `_render_team_menu` 로 이동 (위임 패턴).
  - `test_team_portal_template`: `portal.menu` 마커 → `active_menu` 마커.
  - `test_portal_data_filtering`: 단일 페이지 4채널 검사 → 메뉴별 라우트 4건 분할 검사.
- `phase83_team_portal_loggedin.py`
  - `test_route_passes_my_team_status_no_redirect`: 헬퍼 본문 + 라우트 본문 양쪽에 RedirectResponse 부재 검사.
  - `test_admin_no_join_button_no_redirect`: `id="portal-tabs"` 마커 → "공개 포털 — 공개 설정된 항목만" 문구 (별도 탭 영역 제거 반영).
- `phase92_public_portal_invariant.py`
  - `test_INV7_template_panels_inside_menu_gates`: `{% if m.X %}` 패널 직속 검사 → `{% elif active_menu == 'X' %}` 분기 검사 (단일 패널 모델 전환).

## 회귀 검증 결과 (phase82 / phase83 / phase92 / phase99)
- 40 케이스 모두 PASS.

## 본 변경 외 baseline 회귀 (기존 회귀 — 본 변경과 무관)
`git stash` 베이스라인에서도 동일하게 실패하는 케이스. 본 변경이 이들을 추가로 깨지 않았음을 확인.
- `phase81_unassigned_user.py` 5건 (create_team("팀A") 한글 이름 `invalid_name` 등).
- `phase86_links_multiteam.py` 7건 (한글 팀명 invalid_name).
- `phase87_team_notices_multiteam.py` 7건 (한글 팀명 invalid_name).
- `phase81_unassigned_user.py::test_source_invariants` 1건 (`create_doc` 의 "None if unassigned" 주석이 이전 사이클에서 사라진 baseline 회귀).

총 20건 — 모두 본 변경 이전부터 fail 중이며 본 변경 범위 밖. 후속 catchup 으로 추적.

## phase97/98 .spec.js (Playwright)
운영 서버 재시작 후 sanity 확인 권장 — 본 변경은 신규 한글 path 라우트 4개 추가이므로 서버 reload 필수. 사용자 명시 재시작 후 진행.

## 최종 판정
- TestClient 슈트: PASS.
- 본 변경의 의도된 재설계로 인한 phase82/83/92 invariant 갱신: 완료.
- baseline 회귀(본 변경 이전): 본 사이클 범위 밖, 후속 catchup.
- **서버 재시작 필요 — 사용자 수동 재시작 후 Playwright sanity 권고**.
