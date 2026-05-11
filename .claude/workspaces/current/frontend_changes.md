# 프론트 변경 — #9 IP 자동 로그인 관리

## templates/base.html
- `#user-settings-panel` `.settings-body`에 새 섹션 `#ip-autologin-section` 추가 (기본 `display:none`, JS로 노출 결정):
  - 제목 "자동 로그인", 토글 `#ip-autologin-toggle` (`onchange="onIpAutologinToggle"`), hint `#ip-autologin-hint`.
- JS:
  - `openUserSettings()` → `loadIpAutologinStatus()` 호출 추가.
  - `IP_AUTOLOGIN_WARNING` 상수 (사양서 §6 L326 문구 정확히).
  - `loadIpAutologinStatus()` — `CURRENT_USER` 없거나 admin이면 섹션 숨김. `GET /api/me/ip-whitelist` → `{enabled, conflict, conflict_user, admin}`. admin이면 숨김. conflict면 토글 disabled + 다른 사용자 안내. enabled면 토글 체크.
  - `onIpAutologinToggle(checked)` — ON: `wuDialog.confirm`(warning, danger) → 확인 시 `POST /api/me/ip-whitelist`. 409 등 실패 시 `wuToast.error(detail)` + 토글 원복 + 재로드. OFF: 모달 없이 `DELETE /api/me/ip-whitelist`. 실패 시 토스트 + 원복.

## templates/admin.html
- IP 모달(`#ip-modal`)에 "등록할 IP 주소" input(`#ip-add-input`) + "whitelist 등록" 버튼(`adminAddIp()`) 추가.
- IP 리스트 각 row에 "삭제" 버튼(`btn-danger-outline`, `deleteIpRow(id)`) 추가.
- JS:
  - `_ipModalUserId` 모듈 변수 + `openIPs(userId, name)`가 저장 + `refreshIpList()` 분리.
  - `refreshIpList()` — `GET /api/admin/users/{userId}/ips` 재조회·재렌더.
  - `toggleWhitelist(ipId, enable)` — 응답 not ok면 `wuToast.error(detail)` + `refreshIpList()` (409 원복).
  - `adminAddIp()` — `POST /api/admin/users/{userId}/ips` `{ip_address}`. 성공 토스트 + 재로드. 409면 토스트.
  - `deleteIpRow(ipId)` — `wuDialog.confirm` → `DELETE /api/admin/ips/{ipId}`. 성공 토스트 + 재로드.

## 검증
- `jinja2` parse-check base.html / admin.html PASS.
- `wuDialog`/`wuToast`는 base.html이 로드하는 `wu-dialog.js` 전역 — admin.html에서도 기존 사용 중.
- 실제 브라우저 동작 검증은 서버 재시작 후 후속.
