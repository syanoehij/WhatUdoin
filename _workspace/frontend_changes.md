# 프론트엔드 변경 사항

## 히든 프로젝트 A단계 UI 구현 (2026-05-08)

### 수정 파일

- `templates/project_manage.html`

---

### 변경 내용

#### 1. CSS 추가

- `.pm-hidden-badge` — 히든 프로젝트에 표시되는 "히든" 뱃지 스타일 (보라색 계열, `#6c5ce7`)
- `.pm-new-hidden-btn` — "히든 프로젝트 생성" 버튼 스타일 (기존 `.pm-new-btn`과 유사하되 보라색 점선 테두리로 시각 구분)

#### 2. HTML 추가

**"히든 프로젝트 생성" 버튼** (왼쪽 사이드바, "새 프로젝트" 버튼 바로 아래)
- `🔒 히든 프로젝트 생성` 레이블, 클릭 시 `showHiddenCreatePanel()` 호출

**히든 프로젝트 생성 패널** (`#pm-hidden-create-panel`)
- 기존 `pm-create-panel` 패턴 재사용 (`.visible` 클래스 토글)
- 입력 필드: 이름(`hcr-name`, 필수), 색상(`hcr-color`), 메모(`hcr-memo`)
- 헤더: "🔒 히든 프로젝트 생성" 으로 명확히 표시
- 기존 일반 생성 패널과 상호 배타적으로 열림/닫힘

**상세 패널 히든 뱃지** (`#det-hidden-badge`)
- 프로젝트 이름 우측에 "히든" 뱃지 동적 삽입 (히든 프로젝트 선택 시 표시)

#### 3. JavaScript 추가/수정

**추가된 변수**
- `_hiddenCanManage` — 히든 프로젝트별 can_manage 결과 캐시 (`{ [name]: { can_manage, is_owner, is_admin } }`)

**추가된 함수**
- `_fetchHiddenCanManage(hiddenProjects)` — 히든 프로젝트에 대해서만 `GET /api/manage/hidden-projects/{name}/can-manage` 배치 호출 (N+1 방지, Promise.all 병렬)
- `showHiddenCreatePanel()` — 히든 생성 패널 열기 (기존 패널들 닫고 전용 필드 초기화)
- `hideHiddenCreatePanel()` — 히든 생성 패널 닫기
- `renderHiddenPresets(selected)` — 히든 패널 색상 프리셋 렌더
- `pickHiddenPreset(color)` — 히든 패널 색상 프리셋 선택
- `syncHiddenColorPresets(color)` — 히든 패널 색상 피커 동기화
- `createHiddenProject()` — `POST /api/manage/hidden-projects` 호출, 422/비ok 응답 시 `detail` 필드 toast 표시, 성공 시 패널 닫고 목록 새로고침 후 자동 선택

**수정된 함수**
- `loadProjectList()` — 목록 로드 후 히든 프로젝트에 대해 `_fetchHiddenCanManage` 배치 호출
- `renderList()` — 히든 프로젝트에 "히든" 뱃지(`pm-hidden-badge`) 표시 (기존 🔒는 `is_private` 전용 유지)
- `renderDetail()` — 히든 프로젝트 "히든" 뱃지 동적 삽입/표시; `can_manage=false` 멤버에게 편집·종료·삭제·선택삭제 버튼 숨김 (내보내기 버튼만 표시)
- `setFilter()` — 필터 변경 시 히든 생성 패널도 함께 닫기
- `selectProjItem()` — 프로젝트 선택 시 히든 생성 패널 닫기
- `showCreatePanel()` — 일반 생성 패널 열 때 히든 패널 닫기
- `window._blockAutoRefresh` — 히든 생성 패널이 열려있을 때도 자동 새로고침 차단

**키보드 이벤트**
- `hcr-name` 필드: Enter → `createHiddenProject()`, Escape → `hideHiddenCreatePanel()`

---

### API 호출 패턴

```
POST /api/manage/hidden-projects
  요청: { name, color, memo }
  성공: 패널 닫기 + 목록 새로고침 + 생성된 프로젝트 자동 선택
  실패(비ok): response.json().detail → toast 표시

GET /api/manage/hidden-projects/{name}/can-manage
  호출 시점: loadProjectList() 완료 후, 히든 프로젝트에 대해서만 배치 호출
  결과: _hiddenCanManage[name] = { can_manage, is_owner, is_admin }
  에러(네트워크 포함): { can_manage: false } 로 fallback (읽기 전용 표시)
```

---

### 미구현 (B단계 이후)

- 멤버 관리 UI (멤버 추가/삭제/이양 버튼) → B단계에서 구현 완료
- 히든 프로젝트 내 항목 is_public 변경 차단 UI
- 이동 확인 모달 (히든↔일반 이동 시)

---

## B단계 변경 (2026-05-08)

### 수정 파일

- `templates/project_manage.html`

---

### 변경 내용

#### 1. HTML 추가 — 세 개의 모달

**멤버 관리 모달** (`#pm-member-overlay`)
- 현재 멤버 목록: 이름 + owner 뱃지 + 제거 버튼 (owner 행에는 제거 버튼 없음)
- 추가 가능 사용자 드롭다운 (`GET .../addable-members`) + "추가" 버튼
- z-index: 3000, 오버레이 클릭 시 닫힘

**관리 권한 이양 모달** (`#pm-transfer-overlay`, owner 전용)
- owner가 아닌 멤버 목록을 radio 버튼으로 표시
- "이양" 버튼 → `wuDialog.confirm` 확인 문구("이양 후 귀하는 일반 멤버가 됩니다.") 후 실행

**관리자 변경 모달** (`#pm-changeowner-overlay`, admin 전용)
- owner가 아닌 멤버 목록을 radio 버튼으로 표시
- "변경" 버튼 → `wuDialog.confirm` 확인 후 실행

#### 2. JS 추가/수정 — renderDetail 내 버튼 조건부 렌더

`canManage=true` 분기에서 `_hiddenCanManage[p.name]` 값을 참조하여:
- `can_manage=true` → "멤버 관리" 버튼 (idx 전달, 이름 문자열 인라인 JS 제거)
- `is_owner=true` → "관리 권한 이양" 버튼
- `is_admin=true` → "관리자 변경" 버튼

#### 3. JS 추가 — 모달 관련 함수

| 함수 | 설명 |
|------|------|
| `openMemberModal(idx)` | 멤버 관리 모달 열기, idx로 프로젝트 이름 조회 |
| `closeMemberModal()` | 멤버 관리 모달 닫기 |
| `_refreshMemberModal()` | `/members` + `/addable-members` 병렬 조회 후 목록/드롭다운 갱신 |
| `addMember()` | `POST .../members`, 403→"같은 팀", 409→"이미 멤버" toast, 성공 후 목록만 재조회 |
| `removeMember(userId)` | `DELETE .../members/{userId}`, confirm 후 실행, 성공 후 목록만 재조회 |
| `openTransferModal(idx)` | 이양 모달 열기 |
| `closeTransferModal()` | 이양 모달 닫기 |
| `_refreshTransferModal()` | `/members`에서 non-owner 목록 표시, radio 이벤트 리스너 바인딩 |
| `doTransferOwner()` | `POST .../transfer-owner`, 성공 후 `loadProjectList()` (캐시 재조회 포함) |
| `openChangeOwnerModal(idx)` | 관리자 변경 모달 열기 |
| `closeChangeOwnerModal()` | 관리자 변경 모달 닫기 |
| `_refreshChangeOwnerModal()` | `/members`에서 non-owner 목록 표시, radio 이벤트 리스너 바인딩 |
| `doChangeOwner()` | `POST .../change-owner`, 성공 후 `loadProjectList()` |

#### 4. Escape 키 핸들러 확장

기존 단일 `_pmConfirmCancel()` 핸들러에 세 모달 닫기 함수 추가.

---

### API 호출 패턴

```
GET  .../members           → 멤버 목록 { members: [{id, name, is_owner}] }
GET  .../addable-members   → 추가 가능 목록 { addable_members: [{id, name}] }
POST .../members           → 멤버 추가 { user_id }
  403 → "같은 팀 사용자만 추가할 수 있습니다."
  409 → "이미 멤버입니다."
DELETE .../members/{id}    → 멤버 제거
POST .../transfer-owner    → 권한 이양 { user_id }  → 성공 시 loadProjectList()
POST .../change-owner      → 관리자 변경 { user_id } → 성공 시 loadProjectList()
```

---

### 미구현 (C단계 이후)

- 히든 프로젝트 내 항목 is_public 변경 차단 UI (버튼 비활성화 등)
- 이동 확인 모달 (히든↔일반 이동 시)

---

## C단계 변경 (2026-05-08)

### 수정 파일

- `templates/project.html`
- `templates/check.html`
- `templates/project_manage.html`
- `templates/check_editor.html`
- `templates/admin.html`

---

### 변경 내용

#### 1. 히든→일반 이동 확인 모달 (C-1)

이벤트/체크리스트를 히든 프로젝트 밖으로 이동할 때 API가 400 + `requires_confirm: true`를 반환하면 `wuDialog.confirm`으로 확인 모달을 표시하고, 사용자가 확인하면 `confirm: true`를 추가해 재요청한다.

**적용 위치 및 헬퍼:**

| 파일 | 호출 위치 | 처리 방식 |
|------|----------|----------|
| `project.html` | `_onBarDragEnd` (간트 드래그) | `_patchEventProject(eventId, newProj)` 헬퍼로 추출 |
| `check.html` | `saveRename` (이름변경 패널) | `_patchChecklistProject(id, title, project)` 헬퍼로 추출 |
| `project_manage.html` | `excludeChecklist` (프로젝트 제외) | 인라인 처리 (이미 confirm 흐름이 있어 별도 함수 불필요) |
| `check_editor.html` | `saveContent` (에디터 저장) | 인라인 처리 |

**범위 외:** `event-modal.js`의 `PUT /api/events/{id}` 경로는 의도적으로 제외. 백엔드 C-1은 `PATCH /api/events/{id}/project`에만 가드를 추가했으며 전체 PUT에는 미적용.

#### 2. 일반→히든 이동 시 toast (C-1, B단계 누락분)

API 응답에 `hidden_forced: true`가 있으면 `wuToast.info('외부 공개가 비공개로 전환되었습니다.')` 표시. 위 4개 적용 위치 모두에 포함.

#### 3. 팀원 비활성화 시 히든 프로젝트 owner 경고 (C-3)

`admin.html`의 `toggleActive` 함수에서 `PUT /api/admin/users/{id}` 응답에 `warning: true`가 있으면:
- 히든 프로젝트 이름 목록을 포함한 `wuDialog.confirm` 경고 표시
- 확인 시: `force: true` 추가하여 재요청
- 취소 시: 체크박스를 원래 상태로 복원 (DB 변경 없음)

---

### API 호출 패턴

```
PATCH /api/events/{id}/project
  400 { requires_confirm: true, message } → wuDialog.confirm → confirm: true 재요청
  200 { hidden_forced: true } → wuToast.info

PATCH /api/checklists/{id}
  400 { requires_confirm: true, message } → wuDialog.confirm → confirm: true 재요청
  200 { hidden_forced: true } → wuToast.info

PUT /api/admin/users/{id}
  200 { warning: true, hidden_projects: [...] } → wuDialog.confirm(danger) → force: true 재요청
```
