# code_review_report — 팀 기능 그룹 B #14

## 범위
`app.py` (`team_public_portal` 1곳), `templates/team_portal.html` (3곳), `tests/phase83_team_portal_loggedin.py` (신규).

## 결과: 차단 0 / 경고 1

### 통과 확인
- **계획서 섹션 7 표 충실 구현**: 비로그인="계정 가입" / approved=버튼 없음 / pending="가입 대기 중"(disabled) / 미소속·rejected="팀 신청" / admin=버튼 없음. admin 행은 표에 없어 본 구현이 명시 결정(주석으로 근거 — "슈퍼유저, 팀 신청 의미 없음").
- **redirect 없음**: 라우트에 `RedirectResponse` 미사용. 로그인/admin 모두 200 공개 포털 (계획서 핵심: "URL 은 권한 경계가 아니다", "홈 버튼으로 / 이동").
- **surgical**: 백엔드는 1 라우트만, 새 DB 헬퍼·마이그레이션 없음 (#12/#13 자산 재사용). 프론트는 버튼 분기 + JS 복제 + 주석 정리만 — 데이터 노출/탭 로직 무변경.
- **#12 패턴 일관**: `get_my_team_statuses` 재사용, `pending_other` 는 새 UI 안 만들고 서버 에러에 위임 (home.html 과 동일). `applyToTeam` 마크업/동작도 home.html 미러.
- **분기 순서 정확**: admin(`user.role=='admin'`, `my_team_status=None`)이 `else` 의 "팀 신청"으로 새지 않게 `else` 앞에 admin 분기 배치 — 템플릿·테스트 모두 검증.
- **주석 정리**: #13 이 남긴 "#14 에서 구현" 미루기 문구 2곳 제거. 테스트가 `'구현하지 않는다' not in html` 로 회귀 가드.
- **테스트**: phase83 9개 + 회귀 phase80(5)·phase81(8)·phase82(8) = 30/30 PASS. TestClient + 임시 DB (운영 IP 자동 로그인이라 특정 사용자 상태 브라우저 재현 불가 — 적절한 선택).

### 경고 (현 범위에서 허용)
1. **`applyToTeam` 중복** — home.html 과 team_portal.html 에 동일 함수 ~18줄. 공유 static JS 로 추출 가능하나 버튼 1개·범위 작음 → surgical 원칙상 복제 허용. 향후 공통 JS 정리 시 후보.

## 사전 결함 (이번 변경 무관)
`tests/test_project_rename.py` 2 FAIL — 옛 픽스처 DB 에 `projects.team_id` 없음. master HEAD 동일, #13 에서도 동일하게 기록됨.
