// event-modal.js — 공유 일정 등록/수정 모달 + 칸반 상세 모달
// base.html에서 로드되어 calendar, kanban, home 등에서 공유 사용됩니다.
//
// 페이지별 콜백 (각 페이지에서 설정):
//   window.onEventSaved()              — 저장/삭제 후 호출
//   window.onKDetailSaved()            — 칸반 상태/우선순위 변경 후 호출
//   window.onModalDateChanged(s, e)    — 날짜 변경 시 호출 (캘린더 전용)

// ── 칸반 컬럼 정의 (공유) ─────────────────────────────────
var COLUMNS = [
  { status: 'backlog',     label: 'Backlog' },
  { status: 'todo',        label: 'Todo' },
  { status: 'in_progress', label: 'In Progress' },
  { status: 'peer_review', label: 'Peer Review' },
  { status: 'done',        label: 'Done' },
  { status: 'blocked',     label: 'Blocked' },
];

let _allProjects = [];
let _allMembers  = [];
let _allParentEvents = [];  // 상위 업무 오토컴플릿용
let _assigneeTags = [];
let _fpInstance = null;
let _fpSavedDates = ['', ''];
let _currentEditKanbanStatus = null;
let _currentEventType = 'schedule';
let _currentIsRecurring = false;  // 현재 편집 중인 이벤트가 반복 시리즈인지
let _currentOnSaveCallback = null; // AI 경로: fetch 대신 payload를 콜백으로 전달
let _currentEventId = null;       // 현재 편집 중인 이벤트 id
let _currentEventData = null;     // 현재 편집 중인 이벤트 원본 데이터
let _hasSubtasks = false;         // 현재 편집 이벤트가 하위 업무를 보유하는지
let _isRecurringParent = false;   // 반복 일정 부모인지 (하위 생성 불가 표시용)

// ── 체크 바인딩 상태 ─────────────────────────────────────
let _currentBoundChecklistId = null;   // 현재 모달에 바인된 체크리스트 id
let _boundChecklistAll       = [];     // sub-modal 캐시: 활성 체크리스트 전체
let _boundViewerInstance     = null;   // 본문 viewer toastui 인스턴스

// ── 프로젝트 자동완성 ─────────────────────────────────────
async function loadProjects() {
  const res = await fetch('/api/projects');
  _allProjects = await res.json();
}

async function loadMembers() {
  const res = await fetch('/api/members');
  _allMembers = await res.json();
}

function filterProjects(val) {
  const dd = document.getElementById('project-dropdown');
  const filtered = val
    ? _allProjects.filter(p => p.toLowerCase().includes(val.toLowerCase()))
    : _allProjects;
  if (!filtered.length) { dd.classList.add('hidden'); return; }
  dd.innerHTML = filtered.map(p =>
    `<li onmousedown="selectProject('${p.replace(/'/g, "\\'")}')">${esc(p)}</li>`
  ).join('');
  dd.classList.remove('hidden');
}

function selectProject(val) {
  document.getElementById('f-project').value = val;
  document.getElementById('project-dropdown').classList.add('hidden');
  // 하위 업무 유형이면 상위 업무 입력 상태 갱신
  if (_currentEventType === 'subtask') {
    _updateParentInputState();
    // 상위 업무가 다른 프로젝트 것이었으면 초기화
    const fParentId = document.getElementById('f-parent-event-id');
    const fParentEvent = document.getElementById('f-parent-event');
    if (fParentId) fParentId.value = '';
    if (fParentEvent) fParentEvent.value = '';
  }
}

function hideDropdown() {
  setTimeout(() => document.getElementById('project-dropdown').classList.add('hidden'), 150);
}

// ── 상위 업무 자동완성 ────────────────────────────────────
async function filterParentEvents(val) {
  const dd = document.getElementById('parent-event-dropdown');
  const proj = document.getElementById('f-project').value.trim();
  if (!proj) { dd.classList.add('hidden'); return; }
  const excludeId = _currentEventId || '';
  const url = `/api/events/search-parent?project=${encodeURIComponent(proj)}&q=${encodeURIComponent(val || '')}&exclude_id=${excludeId}`;
  const res = await fetch(url);
  const items = await res.json();
  if (!items.length) { dd.classList.add('hidden'); return; }
  dd.innerHTML = items.map(ev =>
    `<li onmousedown="selectParent(${ev.id}, '${esc(ev.title)}')">${esc(ev.title)}</li>`
  ).join('');
  dd.classList.remove('hidden');
}

function selectParent(id, title) {
  document.getElementById('f-parent-event').value    = title;
  document.getElementById('f-parent-event-id').value = id;
  document.getElementById('parent-event-dropdown').classList.add('hidden');
}

function hideParentDropdown() {
  setTimeout(() => {
    const dd = document.getElementById('parent-event-dropdown');
    if (dd) dd.classList.add('hidden');
  }, 150);
}

// ── 하위 일정 목록 렌더 ───────────────────────────────────
async function loadAndRenderSubtasks(parentId) {
  const listEl       = document.getElementById('subtask-list');
  const panelEl      = document.getElementById('subtask-panel');
  const dateEvPanel  = document.getElementById('date-events-panel');
  if (!listEl || !panelEl) return;
  const res  = await fetch(`/api/events/${parentId}/subtasks`);
  const subs = await res.json();
  if (!subs.length) return;
  _hasSubtasks = true;
  // 왼쪽 패널: 해당 기간 일정 숨기고 하위 일정 표시
  if (dateEvPanel) dateEvPanel.style.display = 'none';
  panelEl.style.display = '';
  listEl.innerHTML = subs.map(s => `
    <li class="subtask-list-item" onmousedown="switchToSubtask(${s.id})">
      <span class="subtask-item-title">${esc(s.title)}</span>
      <span class="subtask-item-date">${(s.start_datetime || '').slice(0, 10)}</span>
    </li>
  `).join('');
}

async function switchToSubtask(subtaskId) {
  const res = await fetch(`/api/events/${subtaskId}`);
  if (!res.ok) return;
  const data = await res.json();
  openModal('', data);
}

// ── 상위 업무로 이동 ─────────────────────────────────────
async function gotoParentEvent() {
  const parentId = document.getElementById('f-parent-event-id')?.value;
  if (!parentId) return;
  const res = await fetch(`/api/events/${parentId}`);
  if (!res.ok) return;
  const data = await res.json();
  openModal('', data);
}

// ── 하위 일정 생성 버튼 핸들러 ────────────────────────────
async function openAddSubtaskModal() {
  const ev = _currentEventData;
  if (!ev) return;
  // dirty 체크
  const titleEl = document.getElementById('f-title');
  const isDirty = titleEl && titleEl.value !== (ev.title || '');
  if (isDirty) {
    if (!confirm('저장하지 않은 변경사항이 있습니다. 저장 후 하위 일정을 생성하시겠습니까?')) return;
    // 저장 후 진행
    await new Promise((resolve) => {
      const origSaved = window.onEventSaved;
      window.onEventSaved = () => { window.onEventSaved = origSaved; resolve(); if (origSaved) origSaved(); };
      document.querySelector('#event-modal-form button[type=submit]').click();
    });
    return; // 저장 후 onEventSaved에서 페이지 리프레시됨, 사용자가 다시 열어야 함
  }
  document.getElementById('modal-overlay').classList.add('hidden');
  openModal('', null, null, {
    eventType:        'subtask',
    parentEventId:    ev.id,
    parentEventTitle: ev.title,
    project:          ev.project || '',
    assignee:         ev.assignee || '',
    priority:         ev.priority || 'normal',
  });
}

// ── 담당자 태그 ───────────────────────────────────────────
function renderAssigneeTags() {
  const container = document.getElementById('assignee-tags');
  container.innerHTML = _assigneeTags.map((name, i) => `
    <span class="assignee-tag">
      ${name}
      <button type="button" onclick="removeAssigneeTag(${i})">×</button>
    </span>
  `).join('');
}

function addAssigneeTag(name) {
  name = name.trim();
  if (!name || _assigneeTags.includes(name)) return;
  _assigneeTags.push(name);
  renderAssigneeTags();
  document.getElementById('assignee-input').value = '';
  document.getElementById('assignee-dropdown').classList.add('hidden');
  // 에러 표시 해제
  const errEl = document.getElementById('assignee-error');
  if (errEl) errEl.style.display = 'none';
  const wrap = document.getElementById('assignee-wrap');
  if (wrap) wrap.style.borderColor = '';
}

function removeAssigneeTag(i) {
  _assigneeTags.splice(i, 1);
  renderAssigneeTags();
}

function handleAssigneeKey(e) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const val = document.getElementById('assignee-input').value.trim().replace(/,$/, '');
    if (val) addAssigneeTag(val);
  } else if (e.key === 'Backspace' && !document.getElementById('assignee-input').value && _assigneeTags.length) {
    removeAssigneeTag(_assigneeTags.length - 1);
  }
}

function filterAssignees(val) {
  const dd = document.getElementById('assignee-dropdown');
  const filtered = _allMembers.filter(m =>
    !_assigneeTags.includes(m) && (!val || m.toLowerCase().includes(val.toLowerCase()))
  );
  if (!filtered.length) { dd.classList.add('hidden'); return; }
  dd.innerHTML = filtered.map(m =>
    `<li onmousedown="addAssigneeTag('${m.replace(/'/g, "\\'")}')">${m}</li>`
  ).join('');
  dd.classList.remove('hidden');
}

function hideAssigneeDropdown() {
  setTimeout(() => document.getElementById('assignee-dropdown').classList.add('hidden'), 150);
}

function setAssigneeTags(val) {
  _assigneeTags = val ? val.split(',').map(s => s.trim()).filter(Boolean) : [];
  renderAssigneeTags();
  document.getElementById('assignee-input').value = '';
}

function getAssigneeValue() {
  return _assigneeTags.join(', ') || null;
}

// ── flatpickr 날짜 선택기 ─────────────────────────────────
function openStartDatePicker() {
  _fpSavedDates = [
    document.getElementById('f-start-date').value,
    document.getElementById('f-end-date').value,
  ];
  _fpInstance.open();
  if (_fpSavedDates[0]) _fpInstance.jumpToDate(_fpSavedDates[0]);
}

function openEndDatePicker() {
  _fpSavedDates = [
    document.getElementById('f-start-date').value,
    document.getElementById('f-end-date').value,
  ];
  document.getElementById('f-end-date').focus();
  _fpInstance.open();
  if (_fpSavedDates[1])      _fpInstance.jumpToDate(_fpSavedDates[1]);
  else if (_fpSavedDates[0]) _fpInstance.jumpToDate(_fpSavedDates[0]);
}

function initDatePicker() {
  _fpInstance = flatpickr('#f-start-date', {
    dateFormat: 'Y-m-d',
    locale: 'ko',
    allowInput: true,
    appendTo: document.body,
    plugins: [new rangePlugin({ input: '#f-end-date' })],
    onReady(_, __, fp) {
      fp.calendarContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        fp.changeMonth(e.deltaY > 0 ? 1 : -1);
      }, { passive: false });
    },
    onChange(selectedDates) {
      if (selectedDates.length >= 1) {
        const start = selectedDates[0].toISOString().slice(0, 10);
        const end   = selectedDates.length >= 2
          ? selectedDates[selectedDates.length - 1].toISOString().slice(0, 10)
          : start;
        if (window.onModalDateChanged) window.onModalDateChanged(start, end);
      }
    },
    onClose() {
      const saved = [..._fpSavedDates];
      _fpSavedDates = ['', ''];
      if (!saved[0]) return;
      setTimeout(() => {
        const startInput = document.getElementById('f-start-date');
        const endInput   = document.getElementById('f-end-date');
        if (!startInput.value || !endInput.value) {
          _fpInstance.setDate(saved[1] ? [saved[0], saved[1]] : [saved[0]], false);
          startInput.value = saved[0];
          endInput.value   = saved[1] || saved[0];
        }
      }, 0);
    },
  });
}

// ── 모달 열기/닫기 ────────────────────────────────────────
function openModal(dateStr = '', eventData = null, dragOpts = null, options = null) {
  if (!CURRENT_USER) { openLoginModal(); return; }

  _currentOnSaveCallback = (options && options.onSave) || null;

  const today = dateStr || getToday();

  document.getElementById('event-id').value      = '';
  document.getElementById('f-title').value       = '';
  document.getElementById('f-project').value     = '';
  document.getElementById('f-location').value    = '';
  document.getElementById('f-description').value = '';
  document.getElementById('f-priority').value    = 'normal';
  document.getElementById('f-kanban').checked    = true;
  _currentEditKanbanStatus = 'backlog';
  _activePresets = new Set();
  _currentIsRecurring = false;
  _currentEventId   = null;
  _currentEventData = null;
  _hasSubtasks      = false;
  _isRecurringParent = false;
  // 하위 업무 관련 필드 초기화
  const fParentEvent  = document.getElementById('f-parent-event');
  const fParentId     = document.getElementById('f-parent-event-id');
  const subtaskPanel  = document.getElementById('subtask-panel');
  const subtaskList   = document.getElementById('subtask-list');
  const dateEvPanel   = document.getElementById('date-events-panel');
  const btnAddSub     = document.getElementById('btn-add-subtask');
  const pillSubtask   = document.getElementById('pill-subtask');
  if (fParentEvent) fParentEvent.value = '';
  if (fParentId)    fParentId.value    = '';
  if (subtaskPanel) subtaskPanel.style.display = 'none';
  if (subtaskList)  subtaskList.innerHTML      = '';
  if (dateEvPanel)  dateEvPanel.style.display  = '';
  if (btnAddSub)    btnAddSub.classList.add('hidden');
  const parentGotoRowInit = document.getElementById('parent-goto-row');
  if (parentGotoRowInit) parentGotoRowInit.style.display = 'none';
  // 유형 pill 전체 잠금 해제 + 개별 pill-locked 초기화
  const pillsWrap = document.getElementById('event-type-pills-wrap');
  if (pillsWrap) {
    pillsWrap.style.pointerEvents = '';
    pillsWrap.title = '';
    pillsWrap.querySelectorAll('.event-type-pill').forEach(p => p.classList.remove('pill-locked'));
  }
  // 하위 업무 pill: 기존 이벤트 수정 시 event_type에 따라 표시 여부 결정, 신규 생성 시 항상 숨김
  if (pillSubtask) {
    pillSubtask.style.display = (eventData && eventData.event_type === 'subtask') ? '' : 'none';
  }
  setEventType('schedule');
  setKanbanStatus('backlog');
  setRecurrenceRule('');

  const [defStart, defEnd] = getDefaultTimes();
  _fpInstance.setDate([today, today], true);
  document.getElementById('f-start-date').value  = today;
  document.getElementById('f-end-date').value    = today;
  document.getElementById('f-start-time').value  = defStart;
  document.getElementById('f-end-time').value    = defEnd;
  document.getElementById('f-allday').checked    = false;
  document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active'));
  setAssigneeTags('');
  // 담당자 기본값: 현재 로그인 유저
  if (CURRENT_USER) setAssigneeTags(CURRENT_USER.name);
  document.getElementById('btn-delete').classList.add('hidden');
  document.getElementById('doc-link-row').style.display = 'none';
  // 체크 바인딩 UI 리셋 (신규 모달 또는 수정 모달 진입 공통)
  _currentBoundChecklistId = null;
  if (_boundViewerInstance) { _boundViewerInstance.destroy(); _boundViewerInstance = null; }
  {
    const _descEl    = document.getElementById('f-description');
    const _viewerEl  = document.getElementById('bound-content-viewer');
    const _btnBind   = document.getElementById('btn-bind-check');
    const _btnUnbind = document.getElementById('btn-unbind-check');
    const _linkEl    = document.getElementById('bound-check-link');
    if (_descEl)    _descEl.classList.remove('hidden');
    if (_viewerEl) { _viewerEl.classList.add('hidden'); _viewerEl.innerHTML = ''; }
    if (_btnBind)   _btnBind.classList.remove('hidden');
    if (_btnUnbind) _btnUnbind.classList.add('hidden');
    if (_linkEl)    _linkEl.classList.add('hidden');
  }
  document.getElementById('modal-title').textContent = (options && options.title) || '일정 추가';
  toggleAllDay();

  // 드래그 선택 구간 반영 (드래그-투-크리에이트)
  if (dragOpts && !eventData) {
    const { startStr, endStr, allDay } = dragOpts;
    if (allDay) {
      // FullCalendar all-day endStr은 exclusive — 하루 빼서 실제 종료일 계산
      const endExcl = endStr ? new Date(endStr) : null;
      if (endExcl) endExcl.setDate(endExcl.getDate() - 1);
      const endDate = endExcl ? endExcl.toISOString().slice(0, 10) : today;
      _fpInstance.setDate([today, endDate], true);
      document.getElementById('f-end-date').value = endDate;
      document.getElementById('f-allday').checked = true;
      toggleAllDay();
    } else {
      // 시간 단위 드래그 또는 단일 timeGrid 클릭
      const startTimePart = startStr ? startStr.slice(11, 16) : '';
      if (endStr) {
        const endDatePart = endStr.slice(0, 10);
        const endTimePart = endStr.slice(11, 16);
        _fpInstance.setDate([today, endDatePart], true);
        document.getElementById('f-end-date').value = endDatePart;
        if (endTimePart) document.getElementById('f-end-time').value = endTimePart;
      } else if (startTimePart) {
        // 단일 칸 클릭: 종료 = 시작 + 1시간
        const [sh, sm] = startTimePart.split(':').map(Number);
        const totalMins = sh * 60 + sm + 60;
        const eh = Math.floor(totalMins / 60) % 24;
        const em = totalMins % 60;
        document.getElementById('f-end-time').value =
          `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')}`;
      }
      if (startTimePart) document.getElementById('f-start-time').value = startTimePart;
    }
  }

  if (eventData) {
    document.getElementById('modal-title').textContent = '일정 수정';
    document.getElementById('event-id').value      = eventData.id;
    document.getElementById('f-title').value       = eventData.title || '';
    document.getElementById('f-project').value     = eventData.project || '';
    document.getElementById('f-location').value    = eventData.location || '';
    document.getElementById('f-description').value = eventData.description || '';
    setAssigneeTags(eventData.assignee || '');
    _currentEventId   = eventData.id;
    _currentEventData = eventData;

    const allDay = !!eventData.all_day;
    document.getElementById('f-allday').checked = allDay;

    const [startDate, startTime] = splitDatetime(eventData.start_datetime);
    const [endDate,   endTime]   = splitDatetime(eventData.end_datetime);
    const resolvedStart = startDate || today;
    const resolvedEnd   = endDate || resolvedStart;
    _fpInstance.setDate([resolvedStart, resolvedEnd], true);
    document.getElementById('f-start-date').value = resolvedStart;
    document.getElementById('f-end-date').value   = resolvedEnd;
    document.getElementById('f-start-time').value = allDay ? '' : startTime;
    document.getElementById('f-end-time').value   = allDay ? '' : endTime;
    toggleAllDay();

    document.getElementById('f-priority').value = eventData.priority || 'normal';
    _currentEditKanbanStatus = eventData.kanban_status || null;
    document.getElementById('f-kanban').checked = !!eventData.kanban_status;
    setEventType(eventData.event_type || 'schedule');
    setKanbanStatus(eventData.kanban_status || 'backlog');
    setRecurrenceRule(eventData.recurrence_rule || '');
    if (eventData.recurrence_end) {
      document.getElementById('f-recurrence-end').value = eventData.recurrence_end.slice(0, 10);
    }
    _currentIsRecurring    = !!(eventData.recurrence_rule || eventData.recurrence_parent_id);
    _isRecurringParent     = !!(eventData.recurrence_rule);
    document.getElementById('btn-delete').classList.remove('hidden');

    // 하위 업무 수정: 다른 pill 잠금 / 그 외 수정: 하위 업무 pill 이미 숨김(위에서 처리)
    if (eventData.event_type === 'subtask' && pillsWrap) {
      ['pill-schedule', 'pill-meeting', 'pill-journal'].forEach(id => {
        const p = document.getElementById(id);
        if (p) p.classList.add('pill-locked');
      });
      pillsWrap.title = '하위 업무는 유형을 변경할 수 없습니다';
    }

    // 하위 업무인 경우: 상위 업무 필드 채우기 + 이동 버튼 표시
    const parentGotoRow = document.getElementById('parent-goto-row');
    const btnGotoParent = document.getElementById('btn-goto-parent');
    if (eventData.event_type === 'subtask' && eventData.parent_event_id) {
      if (fParentId) fParentId.value = eventData.parent_event_id;
      fetch(`/api/events/${eventData.parent_event_id}`)
        .then(r => r.json())
        .then(parent => {
          if (fParentEvent) fParentEvent.value = parent.title || '';
          if (btnGotoParent) btnGotoParent.textContent = `↑ ${parent.title || '상위 업무'}으로 이동`;
        });
      if (parentGotoRow) parentGotoRow.style.display = '';
    } else {
      if (parentGotoRow) parentGotoRow.style.display = 'none';
    }

    // 업무 유형이면 하위 일정 목록 비동기 로드
    if (eventData.event_type === 'schedule' || !eventData.event_type) {
      loadAndRenderSubtasks(eventData.id).then(() => {
        // 하위 업무가 있으면 pill 변경 잠금
        if (_hasSubtasks && pillsWrap) {
          pillsWrap.style.pointerEvents = 'none';
          pillsWrap.title = '하위 일정이 있는 업무의 유형은 변경할 수 없습니다';
        }
        // 반복 일정이 아닌 업무이면 "하위 일정 생성" 버튼 표시
        if (!_isRecurringParent && btnAddSub) btnAddSub.classList.remove('hidden');
      });
    }

    const docLinkRow = document.getElementById('doc-link-row');
    const docLinkBtn = document.getElementById('doc-link-btn');
    if (eventData.meeting_id) {
      docLinkBtn.href = `/doc/${eventData.meeting_id}`;
      docLinkRow.style.display = '';
    } else {
      docLinkRow.style.display = 'none';
    }

    // ── 체크 바인딩 상태 복원 ──
    if (eventData.bound_checklist_id) {
      _currentBoundChecklistId = eventData.bound_checklist_id;
      applyBoundState(
        eventData.bound_checklist_title,
        eventData.bound_checklist_content,
        eventData.bound_checklist_id
      );
    }
  }

  loadProjects();
  loadMembers();

  // options로 넘어온 subtask 사전 설정 (하위 일정 생성 버튼 경로)
  if (options && options.eventType === 'subtask') {
    // 하위 일정 생성 경로는 신규지만 subtask pill 필요
    const pillSub2 = document.getElementById('pill-subtask');
    if (pillSub2) pillSub2.style.display = '';
    if (options.project)     document.getElementById('f-project').value = options.project;
    if (options.assignee)    setAssigneeTags(options.assignee);
    if (options.priority)    document.getElementById('f-priority').value = options.priority;
    setEventType('subtask');
    if (fParentId && options.parentEventId)    fParentId.value    = options.parentEventId;
    if (fParentEvent && options.parentEventTitle) fParentEvent.value = options.parentEventTitle;
    // 유형 pill 잠금 (이미 subtask로 고정)
    const pillsWrap2 = document.getElementById('event-type-pills-wrap');
    if (pillsWrap2) {
      pillsWrap2.style.pointerEvents = 'none';
      pillsWrap2.title = '하위 업무 생성 모드입니다';
    }
  }

  const startVal = document.getElementById('f-start-date').value;
  const endVal   = document.getElementById('f-end-date').value;
  if (startVal && window.onModalDateChanged) window.onModalDateChanged(startVal, endVal || startVal);

  document.getElementById('modal-overlay').classList.remove('hidden');
  setTimeout(() => document.getElementById('f-title').focus(), 50);
}

function closeModal(e) {
  if (e && e.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.add('hidden');
  _currentOnSaveCallback = null;
}

// ── 종일 토글 ─────────────────────────────────────────────
function toggleAllDay() {
  const allDay = document.getElementById('f-allday').checked;
  document.querySelectorAll('.time-row').forEach(el => {
    el.style.display = allDay ? 'none' : '';
  });
}

// ── 시간 프리셋 ───────────────────────────────────────────
function getDefaultTimes() {
  const now = new Date();
  const startHour = (now.getHours() + 1) % 24;
  const endHour   = (now.getHours() + 2) % 24;
  const fmt = h => `${String(h).padStart(2, '0')}:00`;
  return [fmt(startHour), fmt(endHour)];
}

let _activePresets = new Set();

const _PRESET_RANGES = {
  am:      { start: '08:00', end: '12:00' },
  pm:      { start: '13:00', end: '17:00' },
  evening: { start: '18:00', end: '22:00' },
};

function setTimePreset(preset) {
  // 멀티셀렉트: 클릭 시 토글
  if (_activePresets.has(preset)) {
    _activePresets.delete(preset);
  } else {
    _activePresets.add(preset);
  }

  // 버튼 active 상태 갱신
  document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active'));
  _activePresets.forEach(p => {
    const btn = document.querySelector(`.btn-preset[data-preset="${p}"]`);
    if (btn) btn.classList.add('active');
  });

  if (_activePresets.size === 0) return;

  // AND 조합: 선택된 프리셋들의 start 최솟값, end 최댓값
  const starts = [..._activePresets].map(p => _PRESET_RANGES[p].start);
  const ends   = [..._activePresets].map(p => _PRESET_RANGES[p].end);
  const start  = starts.reduce((a, b) => a < b ? a : b);
  const end    = ends.reduce((a, b) => a > b ? a : b);

  document.getElementById('f-start-time').value = start;
  document.getElementById('f-end-time').value   = end;
  document.getElementById('f-allday').checked   = false;
  toggleAllDay();
}

// ── 저장 / 삭제 ──────────────────────────────────────────
async function saveEvent(e) {
  e.preventDefault();
  const id     = document.getElementById('event-id').value;
  const allDay = document.getElementById('f-allday').checked;

  const startDate = document.getElementById('f-start-date').value;
  const endDate   = document.getElementById('f-end-date').value;
  const startTime = document.getElementById('f-start-time').value || '00:00';
  const endTime   = document.getElementById('f-end-time').value   || '00:00';

  if (!document.getElementById('f-title').value.trim()) { alert('제목을 입력해주세요.'); return; }
  if (!getAssigneeValue()) {
    const errEl = document.getElementById('assignee-error');
    if (errEl) {
      errEl.style.display = 'block';
      document.getElementById('assignee-wrap').style.borderColor = '#e17055';
      document.getElementById('assignee-input').focus();
    }
    return;
  }
  if (!startDate) { alert('시작 날짜를 선택해주세요.'); return; }
  if (endDate && endDate < startDate) {
    alert('종료 날짜는 시작 날짜보다 이전일 수 없습니다.'); return;
  }
  if (!allDay && endDate === startDate && endTime < startTime) {
    alert('종료 시간은 시작 시간보다 이전일 수 없습니다.'); return;
  }

  const kanban_status = _currentEventType === 'schedule'
    ? (_currentEditKanbanStatus || 'backlog')
    : null;

  const recurrenceRule = getRecurrenceRule();
  const recurrenceEnd  = document.getElementById('f-recurrence-end').value || null;

  if (recurrenceRule && !recurrenceEnd) {
    alert('반복 요일이 선택된 경우 반복 종료일을 설정해주세요.');
    document.getElementById('f-recurrence-end').focus();
    return;
  }

  const parentEventIdEl = document.getElementById('f-parent-event-id');
  const payload = {
    title:            document.getElementById('f-title').value,
    project:          document.getElementById('f-project').value || null,
    start_datetime:   `${startDate}T${allDay ? '00:00' : startTime}`,
    end_datetime:     endDate ? `${endDate}T${allDay ? '00:00' : endTime}` : null,
    all_day:          allDay ? 1 : 0,
    location:         document.getElementById('f-location').value || null,
    assignee:         getAssigneeValue(),
    description:      document.getElementById('f-description').value || null,
    source:           'manual',
    kanban_status,
    priority:         document.getElementById('f-priority').value,
    event_type:       _currentEventType,
    recurrence_rule:  recurrenceRule,
    recurrence_end:   recurrenceEnd,
    parent_event_id:  parentEventIdEl && parentEventIdEl.value ? parseInt(parentEventIdEl.value) : null,
    bound_checklist_id: _currentBoundChecklistId || null,
  };

  // AI 경로: DB 저장 대신 payload를 콜백으로 전달
  if (_currentOnSaveCallback) {
    const cb = _currentOnSaveCallback;
    _currentOnSaveCallback = null;
    document.getElementById('modal-overlay').classList.add('hidden');
    cb(payload);
    return;
  }

  const method = id ? 'PUT' : 'POST';
  const url    = id ? `/api/events/${id}` : '/api/events';

  if (id && _currentIsRecurring) {
    // 반복 일정 수정 → 모드 선택 다이얼로그
    document.getElementById('modal-overlay').classList.add('hidden');
    showRecurrenceDialog('edit', async (editMode) => {
      payload.edit_mode = editMode;
      const res2 = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res2.ok) {
        const err = await res2.json();
        alert(err.detail || '저장 실패');
        return;
      }
      if (window.onEventSaved) window.onEventSaved();
    });
    return;
  }

  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || '저장 실패');
    return;
  }

  document.getElementById('modal-overlay').classList.add('hidden');
  if (window.onEventSaved) window.onEventSaved();
}

async function deleteEvent() {
  const id = document.getElementById('event-id').value;
  if (!id) return;

  if (_currentIsRecurring) {
    document.getElementById('modal-overlay').classList.add('hidden');
    showRecurrenceDialog('delete', async (deleteMode) => {
      const res2 = await fetch(`/api/events/${id}?delete_mode=${deleteMode}`, { method: 'DELETE' });
      if (!res2.ok) {
        const err = await res2.json();
        alert(err.detail || '삭제 실패');
        return;
      }
      if (window.onEventSaved) window.onEventSaved();
    });
    return;
  }

  if (!confirm('이 일정을 삭제할까요?')) return;
  const res = await fetch(`/api/events/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || '삭제 실패');
    return;
  }
  document.getElementById('modal-overlay').classList.add('hidden');
  if (window.onEventSaved) window.onEventSaved();
}

// ── 반복 요일 버튼 ───────────────────────────────────────
const _RECDAY_LABELS = ['월', '화', '수', '목', '금'];
let _recurrenceDays = new Set();  // 선택된 요일 (0=월, 1=화, ..., 4=금)

function toggleRecurrenceDay(d) {
  if (_recurrenceDays.has(d)) {
    _recurrenceDays.delete(d);
  } else {
    _recurrenceDays.add(d);
  }
  _renderRecurrenceDays();
}

function _renderRecurrenceDays() {
  document.querySelectorAll('.recday-btn').forEach(btn => {
    const d = parseInt(btn.dataset.day, 10);
    btn.classList.toggle('active', _recurrenceDays.has(d));
  });
  // 종료일 행은 항상 표시 (회의 타입일 때 반복row 자체가 보이므로)
}

function setRecurrenceRule(rule) {
  // rule 형식: 'weekly:0,2,4'  또는 빈값
  _recurrenceDays = new Set();
  if (rule && rule.startsWith('weekly:')) {
    rule.split(':')[1].split(',').forEach(d => {
      const n = parseInt(d.trim(), 10);
      if (!isNaN(n) && n >= 0 && n <= 4) _recurrenceDays.add(n);
    });
  }
  _renderRecurrenceDays();
  if (!rule) {
    const endInput = document.getElementById('f-recurrence-end');
    if (endInput) endInput.value = '';
  }
}

function getRecurrenceRule() {
  if (_recurrenceDays.size === 0) return null;
  return 'weekly:' + [..._recurrenceDays].sort().join(',');
}

// ── 반복 모드 선택 다이얼로그 ─────────────────────────────
let _recurrenceDialogCallback = null;

function showRecurrenceDialog(action, callback) {
  _recurrenceDialogCallback = callback;
  const overlay = document.getElementById('recurrence-dialog-overlay');
  const title = document.getElementById('recurrence-dialog-title');
  const desc  = document.getElementById('recurrence-dialog-desc');
  const btns  = document.getElementById('recurrence-dialog-btns');

  if (action === 'delete') {
    title.textContent = '반복 일정 삭제';
    desc.textContent  = '어떤 일정을 삭제할까요?';
    btns.innerHTML = `
      <button class="btn btn-outline" onclick="confirmRecurrenceDialog('this')">이것만 삭제</button>
      <button class="btn btn-outline" onclick="confirmRecurrenceDialog('from_here')">이후 전체 삭제</button>
      <button class="btn btn-danger" onclick="confirmRecurrenceDialog('all')">전체 삭제</button>
    `;
  } else {
    title.textContent = '반복 일정 수정';
    desc.textContent  = '어떤 일정을 변경할까요?';
    btns.innerHTML = `
      <button class="btn btn-outline" onclick="confirmRecurrenceDialog('this')">이것만 수정</button>
      <button class="btn btn-outline" onclick="confirmRecurrenceDialog('from_here')">이후 전체 수정</button>
      <button class="btn btn-primary" onclick="confirmRecurrenceDialog('all')">전체 수정</button>
    `;
  }

  overlay.classList.remove('hidden');
}

function confirmRecurrenceDialog(mode) {
  document.getElementById('recurrence-dialog-overlay').classList.add('hidden');
  if (_recurrenceDialogCallback) {
    _recurrenceDialogCallback(mode);
    _recurrenceDialogCallback = null;
  }
}

function closeRecurrenceDialog() {
  document.getElementById('recurrence-dialog-overlay').classList.add('hidden');
  _recurrenceDialogCallback = null;
  // 취소 시 모달 다시 열기
  document.getElementById('modal-overlay').classList.remove('hidden');
}

// ── 일정 유형 토글 ───────────────────────────────────────
function setEventType(type) {
  _currentEventType = type;
  document.getElementById('pill-schedule').classList.toggle('active', type === 'schedule');
  document.getElementById('pill-meeting').classList.toggle('active', type === 'meeting');
  const pillJournal  = document.getElementById('pill-journal');
  const pillSubtask  = document.getElementById('pill-subtask');
  if (pillJournal) pillJournal.classList.toggle('active', type === 'journal');
  if (pillSubtask) pillSubtask.classList.toggle('active', type === 'subtask');

  const kanbanStatusRow  = document.getElementById('kanban-status-row');
  const recurrenceRow    = document.getElementById('recurrence-row');
  const parentEventRow   = document.getElementById('parent-event-row');

  if (type === 'meeting') {
    if (kanbanStatusRow) kanbanStatusRow.style.display = 'none';
    document.getElementById('f-kanban').checked = false;
    if (recurrenceRow)  recurrenceRow.style.display  = 'flex';
    if (parentEventRow) parentEventRow.style.display = 'none';
  } else if (type === 'journal') {
    if (kanbanStatusRow) kanbanStatusRow.style.display = 'none';
    document.getElementById('f-kanban').checked = false;
    if (recurrenceRow)  recurrenceRow.style.display  = 'none';
    if (parentEventRow) parentEventRow.style.display = 'none';
    setRecurrenceRule('');
  } else if (type === 'subtask') {
    // 하위 업무: 칸반 상태·반복 섹션 숨기고 상위 업무 선택 표시
    if (kanbanStatusRow) kanbanStatusRow.style.display = 'none';
    document.getElementById('f-kanban').checked = false;
    if (recurrenceRow)  recurrenceRow.style.display  = 'none';
    if (parentEventRow) {
      parentEventRow.style.display = '';
      _updateParentInputState();
    }
    setRecurrenceRule('');
  } else {
    // 업무: 칸반 상태 선택 표시, 반복·상위 업무 섹션 숨기고 초기화
    if (kanbanStatusRow) kanbanStatusRow.style.display = 'flex';
    document.getElementById('f-kanban').checked = true;
    if (recurrenceRow)  recurrenceRow.style.display  = 'none';
    if (parentEventRow) parentEventRow.style.display = 'none';
    setRecurrenceRule('');
  }
}

function _updateParentInputState() {
  const fParent = document.getElementById('f-parent-event');
  if (!fParent) return;
  const proj = document.getElementById('f-project').value.trim();
  fParent.disabled = false;
  fParent.placeholder = proj ? '상위 업무 검색...' : '프로젝트를 먼저 선택하면 좋습니다';
}

// ── 칸반 상태 선택 ───────────────────────────────────────
function setKanbanStatus(status) {
  _currentEditKanbanStatus = status;
  document.querySelectorAll('.kanban-status-pill').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.status === status);
  });
}

// ── 유틸 ─────────────────────────────────────────────────
function getToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function splitDatetime(str) {
  if (!str) return ['', ''];
  const [d, t] = str.split('T');
  return [d || '', (t || '').slice(0, 5)];
}

function formatDatetime(str, allDay) {
  if (!str) return '-';
  const d = new Date(str);
  if (allDay) return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' });
  return d.toLocaleString('ko-KR', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// HTML 이스케이프 + 줄바꿈(\n, \r\n)을 <br>로 변환. description 등 멀티라인 텍스트 표시용.
function escWithBr(str) {
  return esc(str).replace(/\r\n|\r|\n/g, '<br>');
}

// ── 칸반 상세 모달 (공유) ─────────────────────────────────
let currentKDetailId = null;
let _kdetailData     = null;

function _renderKDetailButtons() {
  const e = _kdetailData;
  if (!e) return;

  const statusEl = document.getElementById('kdetail-status-btns');
  if (statusEl) {
    statusEl.innerHTML = COLUMNS.map(({ status, label }) =>
      `<button class="status-btn ${e.kanban_status === status ? 'active' : ''}"
         onclick="changeStatus('${status}')">${label}</button>`
    ).join('');
  }

  const PRIORITIES = [
    { value: 'urgent', label: '🔴 긴급' },
    { value: 'high',   label: '🟠 높음' },
    { value: 'normal', label: '🔵 보통' },
    { value: 'low',    label: '⚪ 낮음' },
  ];
  const curPriority = e.priority || 'normal';
  const prioEl = document.getElementById('kdetail-priority-btns');
  if (prioEl) {
    prioEl.innerHTML = PRIORITIES.map(p =>
      `<button class="priority-btn ${curPriority === p.value ? 'active' : ''}"
         data-priority="${p.value}"
         onclick="changePriority('${p.value}')">${p.label}</button>`
    ).join('');
  }
}

async function openKDetail(id) {
  if (!CURRENT_USER) return;
  const res = await fetch(`/api/events/${id}`);
  if (!res.ok) return;
  const e = await res.json();
  currentKDetailId = id;
  _kdetailData = e;

  document.getElementById('kdetail-title').textContent = e.title;

  const fmtD = s => s ? s.slice(0, 10) : '';
  const rows = [
    ['프로젝트', e.project     || '-'],
    ['기간',     fmtD(e.start_datetime) + (e.end_datetime ? ' ~ ' + fmtD(e.end_datetime) : '')],
    ['담당자',   e.assignee    || '-'],
    ['내용',     e.description || '-'],
  ];
  document.getElementById('kdetail-body').innerHTML = rows.map(([label, val]) => `
    <div class="detail-row">
      <span class="detail-label">${label}</span>
      <span class="detail-val">${label === '내용' ? escWithBr(val) : esc(val)}</span>
    </div>`).join('');

  _renderKDetailButtons();

  const canEdit = CURRENT_USER && (CURRENT_USER.role === 'admin' || CURRENT_USER.role === 'editor');
  const editBtn = document.getElementById('kbtn-edit');
  if (editBtn) editBtn.style.display = canEdit ? '' : 'none';

  document.getElementById('kdetail-overlay').classList.remove('hidden');
}

function closeKDetail(ev) {
  if (ev && ev.target !== document.getElementById('kdetail-overlay')) return;
  document.getElementById('kdetail-overlay').classList.add('hidden');
  currentKDetailId = null;
  _kdetailData     = null;
}

async function changeStatus(newStatus) {
  if (!currentKDetailId || !_kdetailData) return;
  if (_kdetailData.kanban_status === newStatus) return;
  _kdetailData.kanban_status = newStatus;
  _renderKDetailButtons();
  await fetch(`/api/events/${currentKDetailId}/kanban`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kanban_status: newStatus }),
  });
  if (window.onKDetailSaved) window.onKDetailSaved();
}

async function changePriority(newPriority) {
  if (!currentKDetailId || !_kdetailData) return;
  if (_kdetailData.priority === newPriority) return;
  _kdetailData.priority = newPriority;
  _renderKDetailButtons();
  await fetch(`/api/events/${currentKDetailId}/kanban`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ priority: newPriority }),
  });
  if (window.onKDetailSaved) window.onKDetailSaved();
}

async function editKanbanEvent() {
  if (!currentKDetailId) return;
  const id = currentKDetailId;
  document.getElementById('kdetail-overlay').classList.add('hidden');
  currentKDetailId = null;
  _kdetailData     = null;
  const res = await fetch(`/api/events/${id}`);
  const data = await res.json();
  openModal('', data);
}

async function completeKanbanEvent() {
  if (!currentKDetailId) return;
  if (!confirm('이 일정을 완료 처리하시겠습니까?\n칸반과 간트에서 숨겨집니다.')) return;
  const id = currentKDetailId;
  document.getElementById('kdetail-overlay').classList.add('hidden');
  currentKDetailId = null;
  _kdetailData     = null;
  await fetch(`/api/manage/events/${id}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: false }),
  });
  if (window.onKDetailSaved) window.onKDetailSaved();
}

// ── 체크 바인딩 (Check Binding) ─────────────────────────
//
// 동작:
//  - "🔗 체크 바인딩" 클릭 → openBindCheckPicker(): sub-modal 열고 체크리스트 목록 fetch (캐시)
//  - 체크 선택 → selectBoundCheck() → applyBoundState(): textarea 숨기고 viewer 노출
//  - "바인딩 해제" 클릭 → unbindCheck(): 원래 textarea 복원, _currentBoundChecklistId = null
//  - 저장 시 saveEvent() payload에 bound_checklist_id 동봉 (백엔드에서 null은 해제로 처리)

function applyBoundState(title, content, id) {
  _currentBoundChecklistId = id;
  const descEl    = document.getElementById('f-description');
  const viewerEl  = document.getElementById('bound-content-viewer');
  const btnBind   = document.getElementById('btn-bind-check');
  const btnUnbind = document.getElementById('btn-unbind-check');
  const linkEl    = document.getElementById('bound-check-link');
  if (!descEl || !viewerEl || !btnBind || !btnUnbind || !linkEl) return;

  descEl.classList.add('hidden');
  viewerEl.classList.remove('hidden');
  btnBind.classList.add('hidden');
  btnUnbind.classList.remove('hidden');
  if (id) {
    linkEl.href = `/check?id=${id}`;
    linkEl.classList.remove('hidden');
  } else {
    linkEl.classList.add('hidden');
  }

  // 기존 viewer 인스턴스가 있으면 정리
  if (_boundViewerInstance) { _boundViewerInstance.destroy(); _boundViewerInstance = null; }
  viewerEl.innerHTML = '';

  // toastui 미로드 시 폴백 (이론상 base.html에서 로드되므로 발생 안 함)
  if (typeof toastui === 'undefined' || !toastui.Editor) {
    viewerEl.textContent = content || '(삭제된 체크입니다 — 바인딩 해제 후 다시 선택하세요)';
    return;
  }

  _boundViewerInstance = toastui.Editor.factory({
    el: viewerEl,
    viewer: true,
    initialValue: content || '*(삭제된 체크입니다 — 바인딩 해제 후 다시 선택하세요)*',
    usageStatistics: false,
    customHTMLSanitizer: html => html,
    customHTMLRenderer: (window.WUEditor && window.WUEditor.renderer) || {},
  });
}

function unbindCheck() {
  if (!confirm('체크 바인딩을 해제하시겠습니까?')) return;
  _currentBoundChecklistId = null;
  if (_boundViewerInstance) { _boundViewerInstance.destroy(); _boundViewerInstance = null; }
  const viewerEl  = document.getElementById('bound-content-viewer');
  const descEl    = document.getElementById('f-description');
  const btnBind   = document.getElementById('btn-bind-check');
  const btnUnbind = document.getElementById('btn-unbind-check');
  const linkEl    = document.getElementById('bound-check-link');
  if (viewerEl) { viewerEl.classList.add('hidden'); viewerEl.innerHTML = ''; }
  if (descEl)    descEl.classList.remove('hidden');
  if (btnBind)   btnBind.classList.remove('hidden');
  if (btnUnbind) btnUnbind.classList.add('hidden');
  if (linkEl)    linkEl.classList.add('hidden');
}

async function openBindCheckPicker() {
  const overlay = document.getElementById('bind-check-modal-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');

  // 캐시 없으면 fetch (이후 동일 모달 라이프사이클 동안 재사용)
  if (!_boundChecklistAll.length) {
    try {
      const r = await fetch('/api/checklists?active=1');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _boundChecklistAll = await r.json();
    } catch (e) {
      const list = document.getElementById('bind-check-list');
      if (list) list.innerHTML = '<div style="color:#e17055; padding:24px; text-align:center;">체크리스트를 불러오지 못했습니다.</div>';
      return;
    }
  }

  // 프로젝트 필터 옵션 populate
  const projSel = document.getElementById('bind-check-project-filter');
  const projs = [...new Set(_boundChecklistAll.map(c => c.project).filter(Boolean))].sort();
  const hasUnassigned = _boundChecklistAll.some(c => !c.project);
  if (projSel) {
    projSel.innerHTML = '<option value="">전체 프로젝트</option>' +
      projs.map(p => `<option value="${esc(p).replace(/"/g, '&quot;')}">${esc(p)}</option>`).join('') +
      (hasUnassigned ? '<option value="__unassigned__">미지정</option>' : '');
    // 현재 일정 모달의 프로젝트가 있으면 자동 선택
    const fProj = document.getElementById('f-project');
    const curProj = fProj ? fProj.value : '';
    if (curProj && projs.includes(curProj)) projSel.value = curProj;
    else projSel.value = '';
  }
  const searchEl = document.getElementById('bind-check-search');
  if (searchEl) searchEl.value = '';
  renderBindCheckList();
  // 검색 박스 포커스
  setTimeout(() => { if (searchEl) searchEl.focus(); }, 50);
}

function closeBindCheckPicker(e) {
  // overlay 영역 클릭만 허용 (modal 내부 클릭 시 닫기 방지)
  if (e && e.target !== document.getElementById('bind-check-modal-overlay')) return;
  const overlay = document.getElementById('bind-check-modal-overlay');
  if (overlay) overlay.classList.add('hidden');
}

function renderBindCheckList() {
  const projSel = document.getElementById('bind-check-project-filter');
  const searchEl = document.getElementById('bind-check-search');
  const list = document.getElementById('bind-check-list');
  if (!list) return;
  const proj = projSel ? projSel.value : '';
  const q    = searchEl ? searchEl.value.toLowerCase() : '';
  const items = _boundChecklistAll.filter(c => {
    if (proj === '__unassigned__') { if (c.project) return false; }
    else if (proj && c.project !== proj) return false;
    return !q || (c.title || '').toLowerCase().includes(q) || (c.project || '').toLowerCase().includes(q);
  });
  if (!items.length) {
    list.innerHTML = '<div style="color:#aaa; padding:24px; text-align:center;">검색 결과 없음</div>';
    return;
  }
  list.innerHTML = items.map(c => {
    // title/content는 onclick에 인라인 전달되므로 JSON.stringify로 이스케이프 (XSS 방지)
    // attribute가 single-quote(')로 감싸져 있으므로 ' 이스케이프 + < 이스케이프
    const tArg = JSON.stringify(c.title || '').replace(/</g, '\\u003c').replace(/'/g, '&#39;');
    const cArg = JSON.stringify(c.content || '').replace(/</g, '\\u003c').replace(/'/g, '&#39;');
    return `
      <div class="bind-check-item" onclick='selectBoundCheck(${c.id}, ${tArg}, ${cArg})'>
        <div class="bind-check-item-title">${esc(c.title || '')}</div>
        ${c.project ? `<div class="bind-check-item-proj">${esc(c.project)}</div>` : ''}
      </div>
    `;
  }).join('');
}

function selectBoundCheck(id, title, content) {
  const overlay = document.getElementById('bind-check-modal-overlay');
  if (overlay) overlay.classList.add('hidden');
  applyBoundState(title, content, id);
}

// ── 초기화 ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initDatePicker();
  const form = document.getElementById('event-form');
  if (form) form.addEventListener('submit', saveEvent);
});
