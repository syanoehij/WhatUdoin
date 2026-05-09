---
name: qa
description: WhatUdoin QA 스킬. Playwright E2E 테스트 작성, 백엔드-프론트엔드 경계면 정합성 검증 시 사용. tests/phaseN_*.spec.js 네이밍 컨벤션 준수. 서버 재시작이 필요하면 반드시 사용자에게 요청.
---

# WhatUdoin QA 스킬

## 핵심 제약: 서버 수동 재시작

서버는 VSCode 디버깅 모드로 실행 중. **자동 재시작 불가**.
- 코드 변경 후 테스트 실행 전, 서버 재시작이 필요하면 **사용자에게 요청**하고 확인 후 진행
- 서버 프로세스를 직접 kill/start 시도 금지

## 테스트 파일 구조

```
tests/
  phase1.spec.js          — 기본 CRUD
  phase2.spec.js          — 인증, 권한
  phase3_debug.spec.js    — 디버깅
  phase4_*.spec.js        — 간트, 자동완료, 반복 일정, 캘린더 UX 등
  check_notice.spec.js    — 체크/공지
```

**네이밍 규칙:** `phase{N}_{기능명}.spec.js`
- 신규 기능: 현재 최고 phase 번호 이후로 추가 (현재 phase4까지 존재)
- 단순 버그 검증: 기존 파일에 test 케이스 추가

## Playwright 테스트 기본 구조

```javascript
import { test, expect } from '@playwright/test';

test.describe('기능명', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:8000');
    // 로그인 필요 시
    await page.fill('#username', 'testuser');
    await page.fill('#password', 'testpass');
    await page.click('#login-btn');
    await page.waitForURL('**/');
  });

  test('정상 흐름 — 설명', async ({ page }) => {
    // 1. 액션
    await page.click('.some-button');
    // 2. 검증
    await expect(page.locator('.result')).toHaveText('기대값');
  });

  test('엣지 케이스 — 빈 입력', async ({ page }) => {
    // ...
  });
});
```

## 경계면 교차 비교 패턴

단순 "존재 확인"이 아닌 API 응답과 UI 렌더링을 동시에 검증한다.

```javascript
test('이벤트 생성 후 캘린더에 반영', async ({ page }) => {
  // 1. API 응답 인터셉트
  const [response] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/events') && r.request().method() === 'POST'),
    page.click('#create-event-btn'),
    page.fill('#event-title', '테스트 일정'),
    page.click('#save-btn'),
  ]);

  // 2. API 응답 검증
  const data = await response.json();
  expect(data.id).toBeDefined();

  // 3. UI 렌더링 검증 (API id와 DOM 일치)
  const eventEl = page.locator(`[data-event-id="${data.id}"]`);
  await expect(eventEl).toBeVisible();
  await expect(eventEl).toContainText('테스트 일정');
});
```

## 권한 경계 검증

```javascript
test('viewer는 편집 버튼 없음', async ({ page }) => {
  // viewer로 로그인
  await loginAs(page, 'viewer_user');
  await page.goto('http://localhost:8000/doc/1');
  await expect(page.locator('#edit-btn')).not.toBeVisible();
});
```

## 자주 쓰는 셀렉터 패턴

```javascript
// 텍스트로 찾기
page.getByText('저장')
page.getByRole('button', { name: '삭제' })

// CSS 셀렉터
page.locator('.event-card')
page.locator('[data-id="123"]')

// 폼 요소
page.getByLabel('제목')
page.getByPlaceholder('날짜 선택')
```

## playwright.config.js 설정 참조

```javascript
// 프로젝트의 playwright.config.js 기준
// baseURL: http://localhost:8000
// 브라우저: chromium (기본)
// 타임아웃: 기본값 사용
```

## 검증 보고서 (`_workspace/qa_report.md`) 형식

```markdown
## QA 보고서 — {기능명}

### 통과 ✅
- [ ] 정상 흐름: 이벤트 생성 후 캘린더 반영
- [ ] 권한: viewer는 편집 불가

### 실패 ❌
- [ ] 문제: 이벤트 삭제 후 DOM 미갱신
  - 재현: 이벤트 우클릭 → 삭제 → 캘린더에 여전히 표시
  - 원인 추정: `calendar.refetchEvents()` 미호출
  - 담당: frontend-dev

### 회귀 확인
- 이전 phase4 테스트 모두 통과
```
