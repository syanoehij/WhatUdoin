# frontend_changes — 팀 기능 그룹 B #14

## 변경 파일
- `templates/team_portal.html` (3곳: docstring 주석, `.portal-hero-actions` 버튼 분기, `{% block scripts %}`)

## 변경 내용

### 1. `.portal-hero-actions` 버튼 분기 (계획서 섹션 7 표)
`<a href="/" ...>홈</a>` 는 그대로 유지. 그 뒤를:
```
{% if not user %}              → <a href="/register" ... btn-primary>계정 가입</a>   (#13, 유지)
{% elif my_team_status == 'approved' %}  → 버튼 없음
{% elif my_team_status == 'pending' %}   → <button class="btn btn-sm" disabled>가입 대기 중</button>
{% elif user.role == 'admin' %}          → 버튼 없음 (슈퍼유저)
{% else %}                               → <button ... onclick="applyToTeam({{ team.id }})">팀 신청</button>
```
admin 분기를 `else` **앞**에 둔 이유: admin 은 `my_team_status=None` 이라 그냥 두면 `else` 의 "팀 신청"으로 떨어진다.

### 2. `applyToTeam` JS (`{% block scripts %}` `{% if not deleted %}` 안)
home.html 의 `applyToTeam` 최소 복제 (~18줄). `fetch('/api/me/team-applications', POST {team_id})`, 성공 시 `showToast`(있으면) + 600ms 후 `location.reload()`, 실패 시 `detail` 토스트/alert fallback. `team_portal.html` 에는 다른 JS 가 없어 독립 복제가 적절 (surgical — 버튼 1개).

### 3. 주석 정리
- 상단 docstring(#13)에 #14 버튼 분기 표를 명시. "#14 에서 로그인 UI 분기 추가" 같은 미루기 문구 제거.
- `.portal-hero-actions` 안의 `{# #14 범위: ... #13 에서 구현하지 않는다. #}` 미루기 주석 제거.

## 건드리지 않은 것
데이터 노출/탭/패널 5종/탭 전환 IIFE/CSS — #13 구현 그대로. deleted 안내 분기 변경 없음.
