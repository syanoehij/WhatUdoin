---
name: frontend
description: WhatUdoin 프론트엔드 개발 스킬. Jinja2 템플릿(templates/*.html), 임베디드 JavaScript, static/js/ 작업 시 사용. FullCalendar, TUI Editor, Flatpickr 등 기존 라이브러리 연동 패턴 포함.
---

# WhatUdoin 프론트엔드 개발 스킬

## 프로젝트 구조 핵심

```
templates/
  base.html          — 공통 레이아웃 (nav, 사이드바, 토스트)
  home.html          — 메인 대시보드
  calendar.html      — FullCalendar 기반 일정 뷰
  kanban.html        — 칸반 보드
  project.html       — 간트 차트
  doc_list.html      — 문서 목록
  doc_editor.html    — TUI Editor 기반 문서 편집
  check.html         — 체크리스트
  check_editor.html  — 체크리스트 에디터

static/js/
  event-modal.js     — 이벤트 모달 공통 로직
  wu-editor.js       — TUI Editor 래퍼

static/lib/          — 외부 라이브러리 (CDN 아님, 로컬)
```

## Jinja2 템플릿 기본 패턴

```html
{% extends "base.html" %}
{% block content %}

<div class="my-section">
  <!-- 서버 데이터 렌더링 -->
  {% for item in items %}
    <div class="item" data-id="{{ item.id }}">{{ item.name }}</div>
  {% else %}
    <p class="empty-state">항목이 없습니다.</p>
  {% endfor %}
</div>

<script>
// 서버 데이터를 JS로 전달
const ITEMS = {{ items | tojson }};
const CURRENT_USER = {{ user | tojson if user else 'null' }};
</script>

{% endblock %}
```

**항상 주입되는 컨텍스트 변수:**
- `user` — 현재 로그인 사용자 dict (없으면 None)
- `request` — FastAPI Request 객체
- `https_available`, `https_port`, `http_port` — HTTPS 상태

## JSON API 호출 패턴

```javascript
// POST
async function createItem(data) {
  const res = await fetch('/api/my-resource', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    showToast('오류가 발생했습니다.', 'error');
    return null;
  }
  return await res.json();
}

// GET with params
const res = await fetch(`/api/events?team_id=${teamId}&start=${start}`);
```

## 라이브러리 연동

### FullCalendar (calendar.html, project.html)
```javascript
const calendar = new FullCalendar.Calendar(el, {
  locale: 'ko',
  headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek' },
  events: '/api/events',  // 또는 배열
  eventClick: (info) => openEventModal(info.event),
  dateClick: (info) => openNewEventModal(info.dateStr),
});
calendar.render();
```

### TUI Editor (doc_editor.html, wu-editor.js)
```javascript
// wu-editor.js의 WuEditor 클래스 활용
const editor = new WuEditor('#editor-container', {
  initialValue: content,
  height: '500px',
});
const markdown = editor.getMarkdown();
```

### Flatpickr (날짜 선택기)
```javascript
flatpickr('#start-date', {
  locale: 'ko',
  dateFormat: 'Y-m-d',
  defaultDate: new Date(),
});
```

## UI 컴포넌트 패턴

### 담당자 칩
```html
<span class="assignee-chip">{{ event.assignee }}</span>
```

### 상태 뱃지
```html
<span class="badge badge-todo">대기</span>
<span class="badge badge-in_progress">진행중</span>
<span class="badge badge-done">완료</span>
```

### 토스트 알림 (base.html의 전역 함수)
```javascript
showToast('저장되었습니다.', 'success');   // success / error / info / warning
```

### 모달 열기/닫기
```javascript
document.getElementById('my-modal').classList.add('active');
document.getElementById('my-modal').classList.remove('active');
```

## 권한 조건 렌더링

```html
{% if user %}
  <!-- 로그인한 사용자만 -->
  {% if user.role == 'admin' %}
    <!-- 관리자만 -->
  {% endif %}
  {% if user.role in ('admin', 'editor') %}
    <!-- 편집 권한 -->
  {% endif %}
{% endif %}
```

## 체크리스트 / 문서 에디터 주의사항
- TUI Editor의 Ctrl+S 단축키는 `wu-editor.js`에서 처리 (`keydown` 이벤트 인터셉트)
- 밑줄(`<u>`) 마크다운 미지원 → `<u>텍스트</u>` HTML 직접 삽입 방식 사용
- 에디터 저장 시 lock 해제 API도 함께 호출: `POST /api/doc/{id}/unlock`
