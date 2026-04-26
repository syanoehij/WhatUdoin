# Frontend Changes — Phase A: 커스텀 dialog/toast 공통 인프라

## 범위
브라우저 기본 `confirm()`/`alert()`/`prompt()` 91건을 커스텀 UI로 교체하기 위한 공통 헬퍼 레이어 구축. **본 Phase에서 실제 호출부는 교체하지 않음.** Phase B(confirm→모달), Phase C(alert→토스트)에서 사용할 API만 제공.

## 변경/생성 파일

| 구분 | 경로 | 설명 |
|------|------|------|
| 신규 | `static/js/wu-dialog.js` | Promise 기반 dialog/toast 헬퍼. `window.wuDialog`, `window.wuToast` 노출. |
| 수정 | `templates/base.html` | wu-dialog.js 스크립트 로드 + wu-dialog 오버레이/wu-toast 컨테이너 마크업 추가. |
| 수정 | `static/css/style.css` | wu-toast / wu-dialog 스타일 추가. (`btn-danger`는 기존 정의 재사용) |

## 공개 API

```js
// Promise<boolean> — true: 확인, false: 취소/ESC/백드롭
wuDialog.confirm({ title, message, okText='확인', cancelText='취소', danger=false })

// Promise<void> — ESC/× 클릭/확인 모두 resolve
wuDialog.alert({ title, message, okText='확인' })

// Promise<string|null> — null: 취소
wuDialog.prompt({ title, message, value='', readonly=false, copyButton=false })

wuToast.show({ message, type='info', duration=2000 })
wuToast.success(msg)   // 2000ms
wuToast.error(msg)     // 4000ms
wuToast.warning(msg)   // 2500ms
wuToast.info(msg)      // 2000ms
```

## 동작 규칙 (구현됨)

- **큐잉**: 다이얼로그가 이미 열려 있으면 다음 호출은 큐에 쌓이고, 현재 다이얼로그가 닫힌 뒤 순차적으로 표시.
- **ESC**: confirm/prompt → `false` / `null`, alert → `void`. 모두 다이얼로그가 닫힘. (구현 방식은 아래 참조)
- **백드롭 클릭**: confirm/prompt → 취소(`false`/`null`), alert → 무반응(닫히지 않음).
- **× 버튼 / 취소 버튼**: 취소로 처리 (alert는 취소 버튼 자동 숨김 처리).
- **`danger=true`**: OK 버튼에 `btn-danger` 클래스 추가(빨간 버튼). 기본은 `btn-primary`.
- **`copyButton=true`** (prompt): "복사" 버튼이 표시되며 클릭 시 `navigator.clipboard.writeText(input.value)` 호출. 성공 시 버튼 텍스트 "복사됨!"으로 1초간 변경. clipboard API 미지원 환경은 `document.execCommand('copy')`로 폴백.
- **포커스**: prompt(쓰기 가능) → input focus + 전체 선택, 그 외 → OK 버튼 focus. Enter 키로 OK.
- **toast**: 클릭 시 즉시 닫기 (fade-out 0.3s 후 DOM 제거). type별 좌측 보더/배경색.
- **마크업 부재 시 fallback**: 어떤 사유로 `wu-dialog-overlay` 마크업이 없으면 native `confirm`/`alert`/`prompt`로 안전하게 동작.

## ESC 처리 방식 (핵심 설계)

`base.html` 899-915 라인의 기존 전역 `keydown` 핸들러는 모든 `.modal-overlay:not(.hidden)` 중 가장 위(나중에 열린) overlay를 ID로 분기하고, 매칭되는 닫기 함수가 없으면 fallback으로 `top.classList.add('hidden')`을 실행함. `wu-dialog-overlay`도 `.modal-overlay`이므로 이 fallback이 적용됨.

따라서 `wu-dialog.js` 내부에서 별도 keydown 리스너를 등록하지 않고, **`MutationObserver`로 `wu-dialog-overlay`의 `class` 속성 변경을 감시**해 `hidden` 클래스가 추가되는 순간 pending Promise를 cancel로 resolve하는 방식을 채택했음. 이 방식의 이점:

1. base.html ESC 핸들러를 수정하지 않아도 됨 (다른 모달과의 결합 최소화).
2. 외부 코드가 어떤 경로로 overlay에 `hidden`을 추가해도(미래 코드 포함) Promise leak 없이 cleanup 됨.
3. 단일 진입점(`_resolveCurrent`)에서 모든 종료 케이스(OK/Cancel/×/백드롭/ESC)를 일관되게 처리.

`_resolveCurrent`는 idempotent — 같은 cycle에서 여러 번 호출되어도 첫 호출만 처리되고 이후는 무시됨. 큐의 다음 task는 `setTimeout(_drain, 0)`으로 다음 tick에 시작해 race condition을 방지.

## DOM 요소 ID

`wu-dialog-overlay`, `wu-dialog-box`, `wu-dialog-title`, `wu-dialog-message`, `wu-dialog-input`, `wu-dialog-cancel`, `wu-dialog-copy`, `wu-dialog-ok`, `wu-toast-container`.

z-index: `wu-dialog-overlay` = 2200 (체크 바인딩 sub-modal `2100` 위에 표시), `wu-toast-container` = 3000.

## CSS 충돌 점검 / 누락 클래스 처리

기존 `style.css`에 정의된 클래스: `.modal-overlay`, `.modal`, `.modal-sm`, `.modal-header`(h2 한정 스타일), `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.icon-btn`, `.modal-actions`.

**미정의 클래스** (사용자 지정 마크업이 사용): `.modal-close-btn`, `.modal-body`, `.modal-footer`, `.modal-title`, `.form-control`. base.html의 다른 모달은 이 클래스들을 쓰지 않고 `.icon-btn` / `.modal-actions` / 인라인 스타일을 사용함.

→ 기존 모달의 마크업 패턴을 변경하지 않기 위해 **`#wu-dialog-overlay` 스코프 한정**으로 누락 클래스에 최소 스타일 추가:
- `.modal-title` — 기존 `<h2>`와 유사한 폰트 사이즈/색상.
- `.modal-close-btn` — `.icon-btn`과 유사한 hover 동작.
- `.modal-body` — 기존 `.modal`이 이미 padding을 가지므로 `padding:0`.
- `.form-control` — 기존 `.form-row-inline input` 스타일과 유사.
- `.wu-dialog-actions` — flex 우측 정렬, gap 8px, padding:0 + margin-top:16px (기존 `.modal-footer` 미정의로 충돌 없음).

`.btn-danger`는 `style.css:352-353`에 이미 정의되어 있어 재사용. 따라서 사양에 명시된 추가 정의는 생략.

## 검증 방법 (브라우저 콘솔)

서버 재시작 후 페이지를 열고 DevTools 콘솔에서:

```js
// confirm
await wuDialog.confirm({ title: '삭제', message: '정말 삭제할까요?\n되돌릴 수 없습니다.', okText: '삭제', danger: true });
// → 확인: true / 취소·ESC·백드롭: false

// alert
await wuDialog.alert({ title: '저장 완료', message: '변경사항이 저장되었습니다.' });
// → undefined (확인/×/ESC 모두 동일)

// prompt (입력)
await wuDialog.prompt({ title: '이름 변경', message: '새 이름을 입력하세요.', value: '기본값' });
// → 입력 문자열 / 취소·ESC: null

// prompt (read-only + 복사)
await wuDialog.prompt({ title: '공유 링크', message: '아래 링크를 복사해서 사용하세요.', value: 'https://example.com/share/abc', readonly: true, copyButton: true });

// 큐잉 동작
wuDialog.alert({ message: '첫 번째' }); wuDialog.alert({ message: '두 번째' });
// → 첫 번째 닫으면 두 번째 자동 표시

// toast
wuToast.success('저장되었습니다.');
wuToast.error('네트워크 오류가 발생했습니다.');
wuToast.warning('변경사항이 있습니다.');
wuToast.info('동기화 중...');
wuToast.show({ message: '커스텀', type: 'info', duration: 5000 });
// → 우상단에 토스트 표시. 클릭 시 즉시 닫힘.
```

검증 포인트:
- ESC가 base.html 핸들러를 거쳐도 Promise가 cancel로 resolve 되는지 (`_resolveCurrent` 1회만 호출).
- 다이얼로그 위에 다이얼로그를 띄우려고 시도하면 큐잉되어 순차 표시되는지.
- danger 옵션 시 OK 버튼이 빨간색(`btn-danger`)으로 표시되는지.
- prompt readonly 시 input이 read-only로 표시되고, copyButton 시 복사 버튼이 동작하는지.

## 다음 Phase 가이드

- **Phase B (confirm→모달)**: 91건 중 confirm 호출부를 `await wuDialog.confirm({...})`로 치환. 삭제 같은 위험 액션은 `danger:true` 권장.
- **Phase C (alert→토스트)**: 단순 알림은 `wuToast.success/error/info/warning`으로, 상세 안내가 필요한 경우만 `wuDialog.alert`로 치환.
- prompt 호출부도 발견되면 `wuDialog.prompt`로 치환 가능 (Phase 정의 외이지만 동일 인프라 사용).

---

# Frontend Changes — Phase B: confirm() / prompt() → wuDialog 전면 교체

## 범위
브라우저 native `confirm()` / `prompt()` 호출부를 모두 `wuDialog.confirm()` / `wuDialog.prompt()`로 교체. **`alert()`는 본 Phase에서 건드리지 않음(Phase C 대상)**. 클립보드 fallback 용도의 `prompt()` 3건은 `readonly:true, copyButton:true` 옵션으로 변환.

전체 교체 건수: **confirm 28건 + prompt 3건 = 31건** (총 11개 파일).

## 변경 파일 목록 및 건수

| 파일 | confirm | prompt | 비고 |
|------|---------|--------|------|
| `templates/base.html` | 1 | 0 | 외부 링크 삭제 (`_deleteLink`) — danger |
| `templates/calendar.html` | 1 | 0 | 일정 삭제 (`_ctxDeleteEvent`) — danger |
| `templates/trash.html` | 2 | 0 | 프로젝트/항목 복원 |
| `templates/check_editor.html` | 2 | 1 | 체크리스트 삭제(danger), 강제 등록, 클립보드 prompt |
| `templates/check.html` | 4 | 1 | 페이지 이동 ×2, 체크리스트 삭제(danger), 버전 복원, 클립보드 prompt |
| `templates/check_history.html` | 1 | 0 | 버전 복원 |
| `templates/doc_history.html` | 1 | 0 | 버전 복원 |
| `templates/doc_editor.html` | 3 | 1 | 문서 삭제(danger), 강제 등록, 연결 해제, 클립보드 prompt |
| `templates/project_manage.html` | 6 | 0 | 병합/재개/제외×2/재개·종료(동적)/삭제(danger)+ZIP백업 |
| `templates/admin.html` | 2 | 0 | 거절, 팀 삭제(danger) |
| `static/js/event-modal.js` | 4 | 0 | 하위 일정 생성, 일정 삭제(danger), 완료 처리, 바인딩 해제 |
| **합계** | **27** | **3** | — |

> 주: 명세상 28건이라 했으나 실제 grep 결과는 27 confirm + 3 prompt + 1건 (`project_manage.html` line 1371 ZIP 백업 confirm은 단일 함수 안에서 두 번째 confirm)이라 실효 28건.

## async화한 함수

기존이 일반 함수였으나 `await wuDialog.confirm/prompt(...)` 사용 위해 `async` 키워드를 추가한 함수 목록:

| 파일 | 함수명 | 변경 |
|------|--------|------|
| `templates/check.html` | `switchToViewer()` | `function` → `async function` |
| `static/js/event-modal.js` | `unbindCheck()` | `function` → `async function` |

그 외 confirm/prompt가 들어간 함수는 모두 이미 `async` 였음 (`_deleteLink`, `_ctxDeleteEvent`, `doRestoreProject`, `doRestore`, `deleteChecklist`, `confirmCkAiEvents`, `openChecklist`, `restoreFromHistory`, `restoreHistory`, `deleteDoc`, `confirmAiEvents`, `unlinkLinkedEvent`, `saveEdit`, `toggleEventStatus`, `excludeEvent`, `excludeChecklist`, `toggleStatus`, `deleteProject`, `rejectPending`, `deleteTeam`, `openAddSubtaskModal`, `deleteEvent`, `completeKanbanEvent`).

클립보드 fallback의 `.catch(() => prompt(...))` 콜백은 화살표 함수에 `async` 키워드를 추가하여 `.catch(async () => { await wuDialog.prompt(...) })` 형태로 변환.

## 변환 규칙 적용 결과

### confirm 일반 패턴
```js
// before
if (!confirm('메시지')) return;
// after
if (!await wuDialog.confirm({ title: '동작명', message: '메시지' })) return;
```

### confirm danger 패턴 (삭제·제거·초기화)
- **danger:true 적용**: 일정 삭제, 체크리스트 삭제, 문서 삭제, 팀 삭제, 외부 링크 삭제, 프로젝트 삭제 → 빨간 OK 버튼

### prompt → readonly + copyButton 패턴 (클립보드 fallback 3건)
```js
// before
.catch(() => { prompt('링크를 복사하세요:', url); })
// after
.catch(async () => { await wuDialog.prompt({ title: '링크 복사', message: '링크를 복사하세요:', value: url, readonly: true, copyButton: true }); })
```

## 특이사항

### 1. `project_manage.html` `deleteProject()` 연속 confirm 2건
한 함수에서 confirm을 두 번 호출하던 패턴을 두 번 await로 변환:
```js
if (!await wuDialog.confirm({ title: '프로젝트 삭제', message: msg, danger: true })) return;
if (withEvents && await wuDialog.confirm({ title: 'ZIP 백업', message: '삭제 전에 옵시디언 ZIP으로 먼저 저장할까요?\n(취소 시 바로 삭제)' })) {
  exportProjectMd(idx);
  ...
}
```
ZIP 백업은 추가 옵션이라 `danger:false`로 유지.

### 2. `project_manage.html` `toggleStatus()` 동적 title
`activate` 인자에 따라 메시지가 "재개" 또는 "종료"로 바뀌는 함수 → title도 삼항 연산자로 동적 적용:
```js
await wuDialog.confirm({ title: activate ? '재개' : '종료', message: msg })
```

### 3. `calendar.html` eventDrop async 변환 — 불필요했음
명세상 "FullCalendar eventDrop async 콜백 지원"이라고 적혔지만, 실제로 eventDrop 내부에는 confirm 호출이 없었음(이미 `async eventDrop(info)`로 작성된 핸들러). 별도 변환 작업 없음. 캘린더의 confirm은 `_ctxDeleteEvent` 함수 내부 1건뿐.

### 4. `static/js/event-modal.js` `unbindCheck()`
`base.html`에서 `onclick="unbindCheck()"`로 호출되는 fire-and-forget 핸들러. async로 변환했지만 onclick 호출부에서 await을 못 받으므로 이상 동작 없음 — 다이얼로그 취소 시 후속 코드만 실행되지 않으면 됨.

### 5. `admin.html` 명세 vs 실제 confirm 수
명세에는 "라인 273~551 사이의 모든 confirm" + "승인/PW 초기화" 언급이 있었으나, 실제 코드의 `openApprove()`/`openResetPw()`는 모달 기반(별도 폼/모달 사용)이라 confirm 호출 자체가 없었음. 따라서 실제 교체 대상은 `rejectPending`(거절), `deleteTeam`(팀 삭제 danger) 2건.

### 6. `check.html` 명세 라인 1336/2208 → alert만 존재
명세에는 "라인 1336, 2208 근처 그 외 confirm" 언급이 있었으나 실제 grep 결과 두 라인 모두 confirm이 아닌 alert였음. Phase C 작업이라 본 Phase에서 건드리지 않음.

### 7. fallback 호출 보존
`static/js/wu-dialog.js` 내부에는 `window.confirm(...)` / `window.prompt(...)` 호출이 fallback 코드로 의도적으로 남아 있음 (DOM 마크업 부재 시 안전 장치). 교체 대상 아님.

## 최종 검증

```
grep '\bconfirm\s*\(|\bprompt\s*\(' templates/ static/js/
```
→ wuDialog.confirm/prompt 호출만 남고 native 호출은 wu-dialog.js의 fallback 외 모두 제거됨.

## 테스트해야 할 사용자 흐름 (qa 인계)

| 시나리오 | 경로 |
|----------|------|
| 외부 링크 삭제 | 사이드바 외부링크 → 삭제 → 빨간 확인 버튼 모달 → 확인/취소 |
| 일정 삭제 (캘린더 컨텍스트 메뉴) | 캘린더 → 이벤트 우클릭 → 삭제 |
| 일정 삭제 (모달 내 휴지통 버튼) | 일정 클릭 → 모달 → 삭제 |
| 일정 완료 처리 | 칸반 카드 → 상세 모달 → 완료 |
| 체크 바인딩 해제 | 일정 모달 → 바인딩 해제 |
| 체크리스트 삭제 (뷰어/에디터 양쪽) | check.html, check_editor.html에서 삭제 |
| 체크리스트 페이지 이동 차단 | 편집 중 dirty 상태로 다른 항목 클릭 / 뷰어 전환 |
| 체크리스트 클립보드 복사 (HTTPS-only 환경 시뮬레이트) | 링크 내보내기 → 클립보드 거부 시 readonly prompt |
| 체크리스트 버전 복원 | 이력 모달 → 버전 복원 |
| 문서 삭제 / 연결 해제 / 강제 등록 / 클립보드 | doc_editor.html 시나리오 |
| 휴지통 복원 (프로젝트/단일항목) | trash.html에서 복원 |
| 프로젝트 병합/재개/종료/제외/삭제(+ZIP백업) | project_manage.html |
| 관리자 가입 거절 / 팀 삭제 | admin.html |
| 하위 일정 생성 dirty 경고 | 일정 모달에서 제목 수정 후 하위일정 생성 |

각 시나리오에서 확인할 것:
1. 모달이 우상단/중앙에 정상 표시되는가
2. danger 옵션이 적용된 케이스에서 OK 버튼이 빨간색인가
3. ESC, 백드롭 클릭, ✕ 버튼이 모두 취소로 처리되는가
4. 클립보드 prompt에서 입력란이 readonly이고 "복사" 버튼이 동작하는가
5. 연속 confirm (프로젝트 삭제 + ZIP 백업)이 큐잉되어 순차 표시되는가

---

# Frontend Changes — Phase C: alert() → wuToast / wuDialog.alert 전면 교체

## 범위
브라우저 native `alert()` 호출부를 모두 `wuToast.success/error/warning/info` 또는 (여러 줄 통계 메시지에 한해) `wuDialog.alert()`로 교체. **`confirm()` / `prompt()`는 Phase B에서 이미 처리되어 본 Phase에서는 손대지 않음.**

전체 교체 건수: **67건** (총 16개 파일 — project_manage.html 은 사전 점검 결과 native alert 없음 확인 → 실제 수정 15개 파일).

## 파일별 교체 건수

| 파일 | success | error | warning | info | wuDialog.alert | 합계 |
|------|---------|-------|---------|------|----------------|------|
| `templates/base.html` | 0 | 2 | 0 | 0 | 0 | 2 |
| `templates/calendar.html` | 0 | 5 | 0 | 0 | 0 | 5 |
| `templates/trash.html` | 0 | 4 | 0 | 0 | 0 | 4 |
| `templates/check_history.html` | 1 | 1 | 0 | 0 | 0 | 2 |
| `templates/doc_history.html` | 1 | 1 | 0 | 0 | 0 | 2 |
| `templates/check_editor.html` | 2 | 4 | 7 | 0 | 1 | 14 (원래 13, skipped 분기로 1건 추가) |
| `templates/check.html` | 1 | 4 | 4 | 0 | 0 | 9 |
| `templates/doc_editor.html` | 2 | 3 | 4 | 0 | 1 | 10 (원래 9, skipped 분기로 1건 추가) |
| `templates/admin.html` | 2 | 6 | 6 | 0 | 0 | 14 |
| `templates/ai_import.html` | 0 | 0 | 1 | 0 | 1 | 2 |
| `templates/kanban.html` | 0 | 1 | 0 | 1 | 0 | 2 |
| `templates/doc_list.html` | 0 | 0 | 1 | 0 | 0 | 1 |
| `templates/notice.html` | 0 | 1 | 0 | 0 | 0 | 1 |
| `static/js/wu-editor.js` | 0 | 1 | 0 | 0 | 0 | 1 |
| `static/js/event-modal.js` | 0 | 4 | 5 | 0 | 0 | 9 |
| `templates/project_manage.html` | — | — | — | — | — | 0 (대상 없음) |
| **합계** | **9** | **38** | **27** | **1** | **3** | **78** |

> 원래 native alert 호출 76건 → 교체 후 78건 (check_editor / doc_editor 의 `confirmAiEvents` 결과 처리에서 단일 alert를 'skipped 있음 → wuDialog.alert' / 'skipped 없음 → wuToast.success' 두 분기로 분리하면서 각 1건씩 +2).

> 주: 본 task 명세상 "약 67건" 이었으나 grep 결과 실제 native alert 호출은 76건이었음(템플릿 73 + JS 3). 모두 교체 완료.

## wuDialog.alert 예외 사용 목록 (3건)

여러 줄 `\n` 포함 통계 메시지 또는 페이지 이동 직전 사용자 확인이 필요한 경우 한정:

| 파일·라인 | 컨텍스트 | 사유 |
|-----------|---------|------|
| `templates/ai_import.html:210` | `confirmAll()` 후 `${data.saved}개 일정이 등록되었습니다.` | 직후 `location.href = '/'`로 페이지 이동 → 토스트가 이동 직전 사라지므로 사용자가 결과 확인 후 진행할 수 있게 wuDialog.alert로 블록. |
| `templates/check_editor.html:1218` | `confirmCkAiEvents()` skipped 건수 표시 | 등록건수 + 건너뜀 사유 다중 줄 통계 메시지(`\n` 포함). 토스트 가독성 떨어져 모달 사용. skipped 없으면 success 토스트로 분기. |
| `templates/doc_editor.html:862` | `confirmAiEvents()` skipped 건수 표시 | 동일 — 다중 줄 통계 메시지. skipped 없으면 success 토스트로 분기. |

## 변환 규칙 적용 결과

### success (2초 자동 소멸)
- `복원됐습니다.`(check_history, doc_history)
- `승인 완료`, `초기화 완료`(admin)
- `링크가 복사되었습니다:\n${url}`(check_editor, doc_editor, check) — 토스트가 다중 줄을 표시하지만 단순 안내라 success 유지
- `${data.saved}개 일정이 등록되었습니다.`(check_editor·doc_editor의 skipped 없는 분기)

### error (4초)
- `저장 실패`, `삭제 실패`, `복원 실패`, `변경 실패`, `연결 해제 실패`
- `서버 연결 오류`, `상태 변경 실패`, `편집 잠금을 획득하지 못했습니다 …`
- `이미지 업로드에 실패했습니다.`(notice, wu-editor)
- `오류`, `오류가 발생했습니다.`, `오류 발생: ${e.message}`

### warning (2.5초) — 입력 검증
- `제목을/이름을/팀 이름을/URL을/비밀번호를/날짜를/기준 날짜를/텍스트를/체크리스트를 …` 입력·선택하세요 류
- `등록할 일정이 없습니다 …`, `체크리스트 내용을 먼저 입력하세요`, `문서 내용을 먼저 입력하세요`, `체크리스트를 먼저 저장하세요`, `문서를 먼저 저장하세요`
- `반복 요일이 선택된 경우 반복 종료일을 설정해주세요.`
- `종료 날짜는 시작 날짜보다 …`, `종료 시간은 시작 시간보다 …`
- `첫 줄에 제목(H1)을 입력하세요`, `첫 번째 줄이 제목이 됩니다 …`
- `AI 초안을 찾을 수 없습니다 …`(check_editor) — 검증성 안내라 warning
- `관리자 계정은 비활성화할 수 없습니다.`(admin) — 동작 차단 경고

### info (2초)
- `로그인이 필요합니다.`(kanban) — 중립 안내

### 페이지 이동 직전 패턴
`check_history.html`, `doc_history.html`의 복원 성공 alert는 `location.href`로 직후 이동하지만, success 토스트는 페이지 전환 후에도 동일 base.html 컨테이너에서 잠깐이라도 표시될 수 있고 자동 소멸되어 차단되지 않으므로 `setTimeout` 없이 그대로 두는 패턴을 적용 (명세 권장사항 준수).

`ai_import.html`의 등록 결과만 wuDialog.alert로 블록한 이유: skipped 정보가 없어도 사용자가 "몇 건 등록됐는지"를 확인하고 능동적으로 메인으로 이동하는 흐름이 본래 alert 차단 의도였기 때문에 모달 유지가 UX 적합.

## async 컨텍스트 점검

`wuDialog.alert(...)`는 Promise 반환 → `await`이 필요. 본 Phase에서 wuDialog.alert를 추가한 호출 컨텍스트는 모두 이미 `async` 함수 내부였음:

| 파일 | 함수 | 상태 |
|------|------|------|
| `ai_import.html` | `confirmAll()` | 이미 async |
| `check_editor.html` | `confirmCkAiEvents(force)` | 이미 async |
| `doc_editor.html` | `confirmAiEvents(force)` | 이미 async |

→ 별도 async 변환 작업 없음.

## 보존된 호출

`static/js/wu-dialog.js:208,226`의 `window.alert(...)`는 마크업 부재 시 fallback 코드로 의도적으로 남아 있음(Phase A 설계 기준). 교체 대상 아님.

## 주의·비고

1. **changelog/git commit 미수행** — 사용자 정책에 따라 본 작업에서는 커밋·changelog 갱신 안 함.
2. **HTTP 다중 줄 토스트 가독성** — 링크 복사 success 토스트는 `\n${url}` 포함이지만 토스트가 줄바꿈을 그대로 렌더링(`white-space: pre-line`로 동작). 기존 동작 변경 없음.
3. **검증 grep 결과** — 작업 후 `grep -E "alert\("` 실행 시 native 호출은 모두 사라지고 `wuDialog.alert` 3건 + `wu-dialog.js`의 fallback 2건만 남음을 확인.

## 테스트해야 할 사용자 흐름 (qa 인계)

| 시나리오 | 기대 표시 |
|---------|----------|
| 외부 링크 저장/삭제 실패 | error 토스트 |
| 캘린더 일정 드래그 후 저장 실패 | error 토스트 |
| 일정 삭제 실패(컨텍스트/모달) | error 토스트 |
| 휴지통 복원 실패 | error 토스트 |
| 체크리스트/문서 버전 복원 성공 | success 토스트 → 페이지 이동 |
| 체크리스트/문서 편집기 저장 시 제목 누락 | warning 토스트 |
| 체크리스트/문서 편집 잠금 실패 | error 토스트 + 페이지 이동 |
| 체크리스트/문서 링크 복사 (HTTPS clipboard 가능) | success 토스트 |
| AI 초안 누락 | warning 토스트 |
| 관리자 가입 승인/거절/PW초기화/팀 삭제 | success 또는 error 토스트 |
| 관리자가 자신 비활성 시도 | warning 토스트 |
| LLM URL 빈 값 저장 | warning 토스트 |
| AI Import 등록 완료 | wuDialog.alert 모달 (확인 후 / 이동) |
| 체크리스트/문서 AI 일정 등록 with skipped | wuDialog.alert 모달 (skipped 통계) |
| 체크리스트/문서 AI 일정 등록 without skipped | success 토스트 |
| 칸반 비로그인 드롭 | info 토스트 |
| 칸반 상태 변경 실패 | error 토스트 |
| 주간 보고서 기준일 미선택 | warning 토스트 |
| 공지사항/문서 이미지 업로드 실패 | error 토스트 |
| 일정 모달 검증 실패 (제목/날짜/시간/반복) | warning 토스트 |
| 일정 저장/삭제 실패 (반복 포함) | error 토스트 |

각 시나리오에서 확인할 것:
1. 토스트가 우상단에 표시되고 정해진 시간(2/2.5/4초) 후 자동 소멸
2. wuDialog.alert로 띄운 모달이 ESC/×/확인으로 모두 닫히고 직후 후속 동작이 정상 진행
3. 페이지 이동 직전 success 토스트가 끊김 없이 보이는지(이동 후 토스트 컨테이너는 base.html이라 새 페이지에서도 일시적으로 잔존)

---

# Frontend Changes — 편집 모드 10분 유휴 자동 종료

## 범위
편집 모드(WUEditor `canEdit:true`) 사용 중 사용자 키 입력이 10분(600,000ms) 동안 없으면:
1. dirty 상태면 자동 저장
2. 편집 잠금 해제(releaseLock)
3. 안내 토스트(`wuToast.info`) 표시
4. 800ms 후 뷰어 상태로 복귀(reload 또는 뷰어 URL로 이동)

자동 저장(autosave 60초)은 idle 타이머의 reset 트리거가 아님. 오직 사용자 입력에서 발생하는 TUI Editor `change` 이벤트만 idle 타이머를 재시작함.

## 변경 파일

| 경로 | 내용 |
|------|------|
| `static/js/wu-editor.js` | `_idleTimer` 상태 변수 추가, `_scheduleIdleTimer()` 신규 함수, change 이벤트·`_bindGlobalEvents()`·`destroy()` 연동 |
| `templates/doc_editor.html` | 팀 문서(`DOC_ID && IS_TEAM_DOC`)에 한해 `idleTimeout` 옵션 전달 |
| `templates/check_editor.html` | 기존 체크리스트(`CKE_ID` 존재)에 한해 `idleTimeout` 옵션 전달 |

## 변경 상세 (wu-editor.js)

### 1) 내부 상태 변수
```js
let _autoSaveTimer = null;
let _lockHeartbeat = null;
let _idleTimer    = null;   // ← 추가
```

### 2) _scheduleIdleTimer() 신규 함수 (`_scheduleAutosave` 바로 뒤)
```js
function _scheduleIdleTimer() {
  const it = feat.idleTimeout;
  if (!it || !it.ms || !it.onIdle) return;
  clearTimeout(_idleTimer);
  _idleTimer = setTimeout(it.onIdle, it.ms);
}
```
옵션 미지정(`feat.idleTimeout` falsy 또는 `ms`/`onIdle` 누락) 시 no-op.

### 3) TUI Editor change 핸들러 연결
```js
change: () => {
  _setDirty(true);
  if (hooks.onChange) hooks.onChange();
  _scheduleAutosave();
  _scheduleIdleTimer();   // ← 추가 (사용자 입력마다 idle 타이머 reset)
  _scheduleTocRebuild();
},
```

### 4) `_bindGlobalEvents()` 끝에 최초 1회 타이머 시작
```js
_scheduleIdleTimer();
```

### 5) `destroy()`에서 cleanup
```js
clearInterval(_lockHeartbeat);
clearTimeout(_idleTimer);    // ← 추가
clearTimeout(_autoSaveTimer);
clearTimeout(_tocRebuildTimer);
```

## 템플릿 옵션 (doc_editor.html)

```js
idleTimeout: (DOC_ID && IS_TEAM_DOC) ? {
  ms: 600000,
  onIdle: async () => {
    if (wuInst?.dirty) await saveDoc();
    wuInst?.releaseLock?.();
    wuToast.info('10분 동안 수정이 없어 편집 모드를 종료합니다.');
    setTimeout(() => location.reload(), 800);
  },
} : false,
```

- 신규 문서(`DOC_ID === null`) 또는 개인 문서(`!IS_TEAM_DOC`)는 잠금 자체가 없으므로 적용 제외.
- 종료 후 `location.reload()` — 서버 측 잠금 상태가 해제된 채로 페이지가 다시 로드되어 자연스럽게 뷰어 모드로 진입.

## 템플릿 옵션 (check_editor.html)

```js
idleTimeout: CKE_ID ? {
  ms: 600000,
  onIdle: async () => {
    if (wuInst?.dirty) await saveContent(true);
    wuInst?.releaseLock?.();
    wuToast.info('10분 동안 수정이 없어 편집 모드를 종료합니다.');
    setTimeout(() => { location.href = `/check?id=${CKE_ID}`; }, 800);
  },
} : false,
```

- `saveContent(auto = false, leaveTarget = null)` 시그니처에 맞춰 `auto=true`로 호출(쿨다운/dirty 처리 일관성).
- 신규 항목(`CKE_ID === null`) 자동 미적용 — 잠금 없음.
- 뷰어로 명시적 이동(`/check?id=${CKE_ID}`) — check.html은 본 task에서 건드리지 않음.

## 동작 흐름

1. 편집 페이지 로드 → `_bindGlobalEvents()` 끝에서 `_scheduleIdleTimer()` 최초 호출 → 600,000ms 타이머 시작.
2. 사용자가 키를 입력하면 TUI Editor `change` 이벤트 발생 → `_scheduleIdleTimer()`가 기존 타이머 clear 후 재시작.
3. 10분 동안 `change` 이벤트가 없으면 `onIdle` 콜백 실행:
   a. dirty 상태일 때만 저장(중복 저장 방지)
   b. `releaseLock()` 호출 → 서버에 DELETE 요청으로 잠금 해제
   c. 토스트 안내 표시
   d. 800ms 대기 후 뷰어로 복귀(토스트가 잠깐이라도 보일 수 있도록)
4. 페이지 이탈/destroy 시 `clearTimeout(_idleTimer)`로 누수 방지.

## 주의사항

- **autosave와 분리**: autosave는 dirty 상태에서 60초마다 발화하지만, 이는 사용자 입력 이벤트가 아니므로 idle 타이머 reset 트리거가 아님. 즉, 사용자가 한 번 입력하고 10분 동안 가만히 있으면 autosave가 한 번 돌고 idle 종료 콜백이 다시 저장(이미 dirty=false면 저장 건너뜀).
- **null 가드**: `wuInst?.dirty`, `wuInst?.releaseLock?.()` 옵셔널 체이닝으로 race condition(콜백 실행 시점에 인스턴스 destroy된 경우) 방어.
- **서버 코드 변경 없음**: 잠금 해제 API/저장 API 모두 기존 엔드포인트 재사용.
- **check.html 미수정**: 뷰어 페이지는 편집 잠금이 없어 idle 자동 종료가 의미 없음.

## 테스트해야 할 사용자 흐름 (qa 인계)

| 시나리오 | 기대 동작 |
|---------|----------|
| 팀 문서 편집 모드 진입 후 10분 무입력 (dirty 없음) | 10분 후 토스트 표시 → 800ms 후 reload → 뷰어 모드 진입, 잠금 해제 |
| 팀 문서 편집 중 일부 입력 후 10분 무입력 (dirty) | 자동 저장 → 잠금 해제 → 토스트 → reload, 입력 내용 보존 확인 |
| 9분 59초에 1글자 입력 → 다시 10분 대기 | 입력 시점에서 타이머 재시작되어 추가 10분 후 종료 |
| 개인 문서 편집 (IS_TEAM_DOC=false) | idle 종료 동작 없음 (적용 제외) |
| 신규 문서 (DOC_ID=null) | idle 종료 동작 없음 |
| 체크리스트 편집 모드 10분 무입력 | dirty면 saveContent(true) 호출 후 `/check?id={CKE_ID}` 이동, 잠금 해제 |
| 신규 체크리스트 (CKE_IS_NEW) | idle 종료 동작 없음 |
| 페이지 이탈 직전 destroy | _idleTimer clearTimeout으로 콜백 미발화 |
| autosave(60초) 단독 실행 | idle 타이머는 reset되지 않음 (autosave 후에도 9분 후 종료 진행) |

검증 시 60만ms 대기는 비현실적이므로 콘솔에서 `feat.idleTimeout.ms`를 임시로 5000ms로 줄여 확인 권장 (또는 wu-editor.js의 `it.ms`를 일시적으로 작게 변경).
