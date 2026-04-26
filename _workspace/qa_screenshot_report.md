# 간트 차트 다크 테마 스타일 문제 보고서

- 점검 일시: 2026-04-26
- 점검 도구: Playwright (Chromium / channel=chrome) — `_workspace/gantt_dark_capture.js`
- 점검 환경: `https://192.168.0.18:8443/gantt`, `localStorage.theme = 'dark'`, `data-theme="dark"` 적용 후 캡처
- 로그인 계정: 기존 세션(`shin`)이 이미 활성화되어 있어 별도 로그인 불필요 (admin/admin123, admin/admin 모두 시도하지 않은 채 통과)

## 캡처 결과

| 파일 | 설명 |
|---|---|
| `_workspace/gantt_screenshot.png` | 다크 테마 적용 후 간트 전체 화면 |
| `_workspace/gantt_child_closeup.png` | `FW개발` 프로젝트의 하위 일정 영역 클로즈업 |
| `_workspace/gantt_styles.json` | 주요 셀렉터의 computed style 덤프 |
| `_workspace/gantt_theme_vars.json` | `:root`에서 읽은 CSS 변수 값 |
| `_workspace/gantt_dom_inspection.json` | 행/바/하위 요소 클래스 인덱스 |
| `_workspace/gantt_subtask_fragment.html` | 하위 일정 영역 HTML 일부 |

## 핵심 발견 — 다크 테마와 충돌하는 단일 규칙

다크 테마(`data-theme="dark"`)가 적용된 상태에서 **하위 일정(서브태스크) 행만** 좌측 이름 패널이 밝은 크림색으로 강조되어 다크 배경과 격렬히 충돌한다. 그 외 헤더/일반 일정 행/간트 바/그리드선/네비게이션은 모두 정상적인 다크 톤(`#0d1117`, `#161b22`, `#21262d`, 텍스트 `#e6edf3`)으로 렌더링된다.

### 영향 범위

`/gantt` (= `templates/project.html` 렌더). 다음 클래스를 가진 모든 노드:
- 좌측 이름 패널의 하위 일정 행: `div.event-name-row.row-subtask-name`
  - 예: `하위 업무 1`, `하위 업무3`, `하위 업무 2a`, `하위 테스트 B1`, `하위 테스트 A`

### 문제 요소 / 인라인 아닌 정적 CSS

**파일:** `templates/project.html`
**위치:** 191~195줄

```css
.row-subtask-name {
  background: var(--surface-1, #f8f9fa);
  font-size: 0.73rem;
  border-left: 2px solid #a29bfe;
}
```

문제점은 두 가지다.

1. **`--surface-1` 변수가 다크 테마에서 정의되어 있지 않다.**
   - `gantt_theme_vars.json`에서 확인: `:root[data-theme="dark"]`에서 `--surface-1`은 빈 문자열. 다크 변수 세트는 `--bg=#0d1117`, `--surface=#161b22`, `--surface-2=#21262d`, `--text=#e6edf3`, `--border=#30363d`, `--accent=#58a6ff`만 정의되어 있다.
   - 결과적으로 fallback인 하드코드 라이트 컬러 `#f8f9fa`로 그려진다.
   - computed style 실측 값: `background: rgb(248, 249, 250)` (`gantt_styles.json`의 `.event-name-row.row-subtask-name` 4개 인스턴스 모두 동일).

2. **`border-left: 2px solid #a29bfe`가 다크 토큰이 아닌 라이트 액센트 컬러 하드코드.**
   - 다크 테마에서 사용되는 액센트 변수는 `--accent: #58a6ff`인데, 위 규칙은 보라색 `#a29bfe`로 고정되어 있다.
   - 토글된 부모 행(`has-subtasks`) 배경은 투명(`rgba(0,0,0,0)`)이라 자식 행만 도드라져 시각적 분리가 더 부자연스럽다.

### 시각적 영향 (스크린샷에서 직접 확인)

다크 모드 컨텍스트:
- body 배경 `rgb(13, 17, 23)`
- 일반 row-event 배경 `rgb(22, 27, 34)` 또는 `srgb 0.0897 0.1093 0.1368`
- row-proj-header 배경 `rgb(33, 38, 45)`
- 하위 일정(`row-subtask-name`) 배경 `rgb(248, 249, 250)` ← 명도차 약 235

→ 스크린샷에서 `하위 업무 1`, `하위 업무3`, `하위 업무 2a`, `하위 테스트 B1`, `하위 테스트 A` 행만 흰 줄무늬처럼 노출된다.

부수 영향:
- 같은 행 내 텍스트 색상은 `--text-muted` (`rgb(139, 148, 158)`)로 그대로 유지되어, 라이트 배경 위에 회색 텍스트가 올라가 **명도 대비도 낮아진다**(WCAG AA 본문 기준 미달 가능).
- `border-bottom`은 `1px solid rgb(33, 38, 45)`(다크 토큰 `--border-light`)이 그대로 적용되어 흰 박스 하단에 어두운 선이 그려지는 것 또한 어색하다.

## 그 외 다크 테마 점검 결과 — 이상 없음

`gantt_styles.json`에서 함께 점검한 항목:
- `body`, `#gantt-timeline`, `#gantt-months`, `.gantt-team-rows`, `.gantt-name-rows`, `.gantt-rows`: 모두 투명/다크 톤. 정상.
- `.gantt-row.row-proj-header`: `rgb(33,38,45)` (다크 표면). 정상.
- `.gantt-row.row-event`: `rgb(22,27,34)` 또는 동등한 srgb. 정상.
- `.event-name-row.has-subtasks`: 투명. 정상.
- `.gantt-bar` / `.gantt-bar.overdue`: 보라/녹색 배경 + 흰 텍스트 + 어두운 음영. 두 테마 공통으로 사용해도 무방.
- `.btn-parent-goto`: `rgb(33,38,45)` 표면 + `--text` 텍스트. 정상.
- `.btn-subtask`: `rgb(108,92,231)` 보라 + 흰 텍스트. (라이트/다크 공통 보라 액센트, 단독으론 문제 없음.)

따라서 **이번 다크 테마 부조화는 사실상 `.row-subtask-name` 단일 규칙 한 줄에서 발생**한다.

## 권장 수정 (참고)

다크/라이트 양쪽에서 안전한 토큰을 사용하도록 변경하거나, 다크 테마에서만 표면 변수를 명시적으로 재정의하면 된다. 예시는 두 가지 중 택일.

A. 이미 다크에 정의된 `--surface-2`로 변경:
```css
.row-subtask-name {
  background: var(--surface-2, #f8f9fa);
  font-size: 0.73rem;
  border-left: 2px solid var(--accent, #a29bfe);
}
```

B. `--surface-1`을 다크 테마 토큰 셋에 추가(전역 변수 정의 위치, base.html 또는 style.css의 `[data-theme="dark"]` 블록):
```css
[data-theme="dark"] {
  --surface-1: #161b22; /* 또는 #1c2129 등 */
}
```

두 방법 모두 `border-left`의 `#a29bfe`도 토큰화(`var(--accent)` 등)할 것을 같이 권장한다.

## 재현 절차

1. `https://192.168.0.18:8443/`에 로그인.
2. 우상단 다크 모드 토글을 켜거나 콘솔에서 `localStorage.setItem('theme','dark'); location.reload();`.
3. `/gantt` 진입.
4. 좌측 이름 패널에서 `▾`로 펼쳐진 부모 일정 아래 행을 확인 → 흰 배경의 가로줄로 표시된다.

자동화 재현 스크립트: `_workspace/gantt_dark_capture.js` (Node 24, `node _workspace/gantt_dark_capture.js`).

## 후속 조치 제안

- 담당: frontend-dev — `templates/project.html`의 `.row-subtask-name` 규칙을 다크 테마 토큰 기반으로 교체.
- QA 회귀: 다크/라이트 양쪽에서 `data-theme` 토글 후 `.event-name-row.row-subtask-name`의 computed `background`/`border-left`가 양 테마에 어울리는지 확인.
