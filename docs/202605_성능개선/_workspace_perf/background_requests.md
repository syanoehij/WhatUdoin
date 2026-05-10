# 백그라운드 요청 인벤토리 (M1a-4)

작성일: 2026-05-09  
조사 기준: 운영 코드 변경 없음 — grep/read 전용 분석

---

## 1. 개요

WhatUdoin의 클라이언트 측 백그라운드 요청은 두 범주로 나뉜다.

| 범주 | 설명 |
|------|------|
| **setInterval 폴링** | 주기적으로 API를 직접 호출. 탭 수에 정비례하여 서버 부하 증가 |
| **SSE 트리거 refetch** | 서버 이벤트(`EventSource`)를 받아 API를 재조회. 쓰기 이벤트가 없으면 refetch 없음 |

SSE 연결(`/api/stream`)은 탭당 1개이며, 이 자체도 지속 연결 리소스다.

---

## 2. 페이지 × 요청 매트릭스

### 범례
- **트리거**: `interval` = setInterval, `sse` = SSE 이벤트 수신, `once` = 페이지 로드 1회
- **조건**: `always` = 해당 페이지에 접속해 있는 한 항상, `viewer` = viewer 모드일 때만, `editor` = 편집 잠금 획득 후
- **중첩**: 같은 사용자가 동일 페이지를 N개 탭 열면 N배 발생 여부

---

### 2.1 전체 페이지 공통 (base.html 포함)

| 요청 (메서드/경로) | 트리거 | 주기(초) | 코드 위치 (파일:라인) | 다중 탭 중첩 | 조건 |
|-------------------|--------|----------|----------------------|------------|------|
| SSE `GET /api/stream` | 페이지 로드 | 지속 연결 (재연결 최대 30s backoff) | `static/js/realtime.js:45` | 탭당 1개 연결 | always |
| `GET /api/notifications/count` | interval | **60** | `templates/base.html:1332` | 탭 수 × 1/분 | always |
| `GET /api/notifications/pending` | interval (조건부) | 60 (count 증가 시에만) | `templates/base.html:1176,1213` | 탭 수 × 최대 1/분 | always (count > prev 시) |

> **§15 항목 #1**: 알림 뱃지 1분 폴링 → `base.html:1332` `setInterval(_updateBadge, 60000)` 확인

---

### 2.2 `/` (홈, home.html)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `GET /api/kanban` | sse `wu:events:changed` | 이벤트 기반 | `templates/home.html:529` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/my-meetings` | sse `wu:events:changed` | 이벤트 기반 | `templates/home.html:529` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/my-milestones` | sse `wu:events:changed` | 이벤트 기반 | `templates/home.html:529` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/notice` | sse `wu:events:changed` | 이벤트 기반 | `templates/home.html:529` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/project-colors` | once (페이지 로드) | — | `templates/home.html:251` | 탭 수 × 1회 | always |

---

### 2.3 `/calendar` (calendar.html)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `GET /api/events` | sse `wu:events:changed` / `wu:projects:changed` | 이벤트 기반 | `templates/calendar.html:604` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/doc/calendar` | sse `wu:events:changed` / `wu:projects:changed` | 이벤트 기반 | `templates/calendar.html:604` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/project-milestones/calendar` | sse `wu:events:changed` / `wu:projects:changed` | 이벤트 기반 | `templates/calendar.html:605` | 탭 수 × 이벤트 횟수 | always |

---

### 2.4 `/kanban` (kanban.html)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `GET /api/kanban` | sse `wu:events:changed` | 이벤트 기반 | `templates/kanban.html:593` | 탭 수 × 이벤트 횟수 | always |

---

### 2.5 `/project/{id}` (project.html / 간트)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `GET /api/project-timeline` | sse `wu:events:changed` | 이벤트 기반 | `templates/project.html:1200` | 탭 수 × 이벤트 횟수 | always |

> **비고**: `project.html:1103`의 `setInterval(..., 60000)`은 오늘 날짜 선 DOM 업데이트 전용 — API 호출 없음. 인벤토리 제외.

---

### 2.6 `/check` (check.html — viewer 모드)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `GET /api/checklists/{id}/lock` | interval (`_pollTimer`) | **30** | `templates/check.html:1697,1699` | 탭 수 × 2/분 | viewer |
| `GET /api/checklists/{id}` | interval (조건부, lock 변화 또는 updated_at 변화 시) | 30 (변화 감지 시) | `templates/check.html:1699` | 탭 수 × 최대 2/분 | viewer |
| `GET /api/checklists` | sse `wu:checks:changed` | 이벤트 기반 | `templates/check.html:1806` | 탭 수 × 이벤트 횟수 | always |
| `GET /api/checklists/{id}` (SSE) | sse `wu:checks:changed` (해당 ID 변경 시) | 이벤트 기반 | `templates/check.html:1806` | 탭 수 × 이벤트 횟수 | always |

> **§15 항목 #2**: 체크리스트 lock heartbeat — 아래 2.7 참조 (viewer 모드에서는 폴링, editor 모드에서는 heartbeat)

---

### 2.7 `/check` (check.html — editor 모드)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `PUT /api/checklists/{id}/lock` | interval (`_heartbeatInterval`) | **120** | `templates/check.html:1679` | 탭 수 × 0.5/분 | editor (`_editMode === true`) |

> **§15 항목 #2 확인**: `check.html:1679` `setInterval(() => { if (_curId && _editMode) fetch(...lock, PUT) }, 120000)`

---

### 2.8 `/doc/{id}/edit` (doc_editor.html — editor 모드)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `PUT /api/doc/{id}/lock` | interval (wu-editor.js `_lockHeartbeat`) | **30** | `static/js/wu-editor.js:1485` (heartbeatMs=30000, `doc_editor.html:474`) | 탭 수 × 2/분 | editor (lock 획득 후) |

> **§15 항목 #3 확인**: `wu-editor.js:1485` `setInterval(() => fetch(url, PUT)..., lk.heartbeatMs||30000)` — doc_editor는 heartbeatMs=30000

---

### 2.9 `/check/{id}/edit` (check_editor.html — editor 모드)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `PUT /api/checklists/{id}/lock` | interval (wu-editor.js `_lockHeartbeat`) | **120** | `static/js/wu-editor.js:1485` (heartbeatMs=120000, `templates/check_editor.html:633`) | 탭 수 × 0.5/분 | editor (lock 획득 후) |

> **§15 항목 #2 보완**: check_editor에서도 wu-editor.js 경유로 동일 heartbeat

---

### 2.10 `/doc` (doc_list.html)

| 요청 | 트리거 | 주기(초) | 코드 위치 | 다중 탭 중첩 | 조건 |
|------|--------|----------|-----------|------------|------|
| `location.reload()` (전체 페이지 재로드) | sse `wu:docs:changed` | 이벤트 기반 | `templates/doc_list.html:800` | 탭 수 × 이벤트 횟수 | always |

---

### 2.11 `/project-manage/{id}`, `/trash` (정적 페이지)

setInterval 없음, SSE 리스너 없음. base.html 알림 폴링만 동작.

---

## 3. §15 항목 점검표

| §15 항목 | 내용 | 상태 | 코드 위치 |
|---------|------|------|-----------|
| #1 | 알림 뱃지 1분 폴링 | **확인** | `templates/base.html:1332` |
| #2 | 체크리스트 lock heartbeat | **확인** | `templates/check.html:1679` (120s, editor), `templates/check_editor.html:633` + `wu-editor.js:1485` (120s) |
| #3 | 에디터 lock heartbeat | **확인** | `templates/doc_editor.html:474` + `wu-editor.js:1485` (30s) |
| #4 | SSE `events.changed` → calendar/kanban refetch | **확인** | `templates/calendar.html:604-605`, `templates/kanban.html:593` |
| #5 | 프로젝트 색상/팀 메타 polling | **코드에서 미확인** — `loadProjColors()` (`home.html:251`)는 페이지 로드 1회뿐. 팀 메타(`/api/teams/members`)도 `kanban.html:605`, `calendar.html:571`에서 페이지 로드 1회뿐. 두 항목 모두 setInterval 없음 |

---

## 4. 다중 탭 정량 모델

### 가정
- 총 50 VU (가상 사용자), 각 1개 탭 기준이 단일 탭 시나리오
- 다중 탭 시나리오: 25%인 12.5 VU(≈12명)가 `/check`(viewer) + `/calendar` 동시 오픈 → 탭당 2개 = 해당 사용자는 2탭

### 4.1 분당 setInterval 폴링 API 호출 수 (단일 탭, 50 VU)

아래는 **폴링 전용** 합계 (SSE refetch 제외, 이벤트 빈도 예측 불가).  
`/api/notifications/pending`은 count 증가 시에만 호출되므로 하한/상한으로 구분한다.

| 요청 | 주기 | 50 VU 모두 해당 페이지 가정 | 분당 호출 수 |
|------|------|---------------------------|------------|
| `GET /api/notifications/count` | 60s | 50 VU × 1/분 | **50** (하한 = 상한 동일) |
| `GET /api/notifications/pending` | 60s (조건부: count 증가 시) | 0 ~ 50 VU | **0 ~ 50** |
| `GET /api/checklists/{id}/lock` (viewer) | 30s | — `/check` 열람자만 해당 | — |
| `PUT /api/checklists/{id}/lock` (editor) | 120s | — `/check` 편집자만 해당 | — |
| `PUT /api/doc/{id}/lock` (editor) | 30s | — doc 편집자만 해당 | — |

**단일 탭 50 VU 기준**: 하한 50건/분 (count 전용) — 상한 100건/분 (count + pending 모두 발화)

### 4.2 다중 탭 시나리오 — 상한 기준 (12 VU가 `/check`viewer + `/calendar` 동시 오픈)

아래 수치는 `/api/notifications/pending`이 매 사이클 발화한다고 가정한 **상한값**이다.

> **M1a-7 갱신 (2026-05-09)**: locustfile이 `MultiTabUser.on_start`에서 `PUT /api/doc/{id}/lock` 30s heartbeat greenlet을 항상 스폰함을 반영. 이는 worst-case 모델링을 위한 의도적 선택 — 운영 코드에서 doc lock heartbeat는 편집 잠금 획득 후에만 발화하지만, 부하 측정은 상한 기준으로 설계됨. 이전 표의 **148 건/분은 doc_lock 24건/분을 누락한 값이었음**.

| VU 그룹 | 탭 구성 | 분당 호출 분해 | 합계 (상한) |
|---------|---------|--------------|-----------|
| 38 VU (단일 탭) | 알림 count(1) + pending(최대 1) × 1탭 | 38 × 2 = 76 | **76** |
| 12 VU (2탭: check viewer + calendar) | 알림 count+pending × 2탭 | 12 × 4 = 48 | |
| | check lock poll × 1탭 (check 탭, 30s) | 12 × 2 = 24 | |
| | doc lock heartbeat × always-on (30s) | 12 × 2 = 24 | **96** |
| **합계 (상한)** | | | **172 건/분** |
| **합계 (하한, pending 0건)** | | 38×1 + 12×(1×2 + 2 + 2) = 38 + 72 = | **110 건/분** |

**단일 탭 상한(100건) 대비**: +72% (상한 기준).

> SSE는 탭당 1 연결 추가. 12 VU 2탭 = 12개 SSE 추가 (total 62 연결 vs 단일 탭 50 연결).

### 4.3 핵심 관찰

1. **알림 뱃지 폴링(60s)이 탭 수에 정비례** — 사용자가 여러 탭을 열면 각 탭에서 독립적으로 60s마다 `/api/notifications/count`를 호출한다. 10명이 5탭씩 열면 50탭 × 1/분 = **50건/분** (10명 기준).

2. **check.html viewer poll(30s)이 가장 짧은 주기 폴링** — 편집자가 없는 상태에서 다수 사용자가 동일 체크리스트를 viewer 탭으로 열면 각 탭이 30s마다 lock 상태를 확인한다.

3. **doc lock heartbeat(30s)는 운영 코드에서 편집 잠금 획득 후에만 발화** — 그러나 locustfile M1a-5는 worst-case 모델링을 위해 `MultiTabUser`에서 항상-on으로 스폰한다(2/분 × 12 VU = **24건/분**). 실제 측정 환경에서 403/404 응답이 대부분이지만 CSRF/auth 경로는 매 요청 탄다.

4. **SSE refetch는 이벤트 기반** — 쓰기 활동이 없으면 0건. 활발한 팀일수록 SSE 이벤트가 많아지고 calendar(3 endpoints) + kanban + home(4 endpoints) refetch가 multiplied된다.

---

## 5. 제외 항목 (setInterval이지만 API 호출 없음)

코드베이스 전체 `setInterval` 출현 목록을 grep하여 교차 검증함. 아래 항목은 모두 DOM 전용이며 네트워크 호출 없음.

| 항목 | 코드 위치 | 이유 |
|------|-----------|------|
| `project.html:1103` setInterval | `templates/project.html:1103` | DOM 전용 (오늘 날짜 선 위치 계산) |
| `base.html` `_titleTimer` | `templates/base.html:1100` | DOM 전용 (탭 비활성 시 제목 깜빡임 애니메이션) |
| `check.html` `_countdownTimer` | `templates/check.html:1776` | DOM 전용 (viewer poll 남은 시간 뱃지 표시) |
| `wu-editor.js` `_cooldownTimer` | `static/js/wu-editor.js:1541` | DOM 전용 (저장 버튼 쿨다운 카운트다운) |
| `GET /api/project-colors` | `templates/home.html:251` | 페이지 로드 1회, setInterval 없음 |
| `GET /api/teams/members` | `templates/kanban.html:605`, `templates/calendar.html:571` | 페이지 로드 1회, setInterval 없음 |
| FullCalendar 자체 내부 타이머 | 외부 라이브러리 | 앱 레벨 제어 외 |

---

*참조: 성능 개선 계획.md §15, 조사 파일 목록 — `static/js/realtime.js`, `templates/base.html`, `templates/check.html`, `templates/check_editor.html`, `templates/doc_editor.html`, `templates/calendar.html`, `templates/kanban.html`, `templates/home.html`, `templates/project.html`, `templates/doc_list.html`, `static/js/wu-editor.js`*
