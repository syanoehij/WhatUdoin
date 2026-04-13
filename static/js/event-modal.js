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
let _assigneeTags = [];
let _fpInstance = null;
let _fpSavedDates = ['', ''];
let _currentEditKanbanStatus = null;

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
}

function hideDropdown() {
  setTimeout(() => document.getElementById('project-dropdown').classList.add('hidden'), 150);
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
function openModal(dateStr = '', eventData = null) {
  if (!CURRENT_USER) { openLoginModal(); return; }

  const today = dateStr || getToday();

  document.getElementById('event-id').value      = '';
  document.getElementById('f-title').value       = '';
  document.getElementById('f-project').value     = '';
  document.getElementById('f-location').value    = '';
  document.getElementById('f-description').value = '';
  document.getElementById('f-priority').value    = 'normal';
  document.getElementById('f-kanban').checked    = true;
  _currentEditKanbanStatus = null;
  _activePresets = new Set();

  const [defStart, defEnd] = getDefaultTimes();
  _fpInstance.setDate([today, today], true);
  document.getElementById('f-start-date').value  = today;
  document.getElementById('f-end-date').value    = today;
  document.getElementById('f-start-time').value  = defStart;
  document.getElementById('f-end-time').value    = defEnd;
  document.getElementById('f-allday').checked    = false;
  document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active'));
  setAssigneeTags('');
  document.getElementById('btn-delete').classList.add('hidden');
  document.getElementById('modal-title').textContent = '일정 추가';
  toggleAllDay();

  if (eventData) {
    document.getElementById('modal-title').textContent = '일정 수정';
    document.getElementById('event-id').value      = eventData.id;
    document.getElementById('f-title').value       = eventData.title || '';
    document.getElementById('f-project').value     = eventData.project || '';
    document.getElementById('f-location').value    = eventData.location || '';
    document.getElementById('f-description').value = eventData.description || '';
    setAssigneeTags(eventData.assignee || '');

    const allDay = !!eventData.all_day;
    document.getElementById('f-allday').checked = allDay;

    const [startDate, startTime] = splitDatetime(eventData.start_datetime);
    const [endDate,   endTime]   = splitDatetime(eventData.end_datetime);
    _fpInstance.setDate([startDate, endDate || startDate], true);
    document.getElementById('f-start-date').value = startDate;
    document.getElementById('f-end-date').value   = endDate || startDate;
    document.getElementById('f-start-time').value = allDay ? '' : startTime;
    document.getElementById('f-end-time').value   = allDay ? '' : endTime;
    toggleAllDay();

    document.getElementById('f-priority').value = eventData.priority || 'normal';
    _currentEditKanbanStatus = eventData.kanban_status || null;
    document.getElementById('f-kanban').checked = !!eventData.kanban_status;
    document.getElementById('btn-delete').classList.remove('hidden');
  }

  loadProjects();
  loadMembers();

  const startVal = document.getElementById('f-start-date').value;
  const endVal   = document.getElementById('f-end-date').value;
  if (startVal && window.onModalDateChanged) window.onModalDateChanged(startVal, endVal || startVal);

  document.getElementById('modal-overlay').classList.remove('hidden');
  setTimeout(() => document.getElementById('f-title').focus(), 50);
}

function closeModal(e) {
  if (e && e.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.add('hidden');
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

  if (!startDate) { alert('시작 날짜를 선택해주세요.'); return; }
  if (endDate && endDate < startDate) {
    alert('종료 날짜는 시작 날짜보다 이전일 수 없습니다.'); return;
  }
  if (!allDay && endDate === startDate && endTime < startTime) {
    alert('종료 시간은 시작 시간보다 이전일 수 없습니다.'); return;
  }

  const kanbanChecked = document.getElementById('f-kanban').checked;
  const kanban_status = kanbanChecked
    ? (_currentEditKanbanStatus || 'backlog')
    : null;

  const payload = {
    title:          document.getElementById('f-title').value,
    project:        document.getElementById('f-project').value || null,
    start_datetime: `${startDate}T${allDay ? '00:00' : startTime}`,
    end_datetime:   endDate ? `${endDate}T${allDay ? '00:00' : endTime}` : null,
    all_day:        allDay ? 1 : 0,
    location:       document.getElementById('f-location').value || null,
    assignee:       getAssigneeValue(),
    description:    document.getElementById('f-description').value || null,
    source:         'manual',
    kanban_status,
    priority:       document.getElementById('f-priority').value,
  };

  const method = id ? 'PUT' : 'POST';
  const url    = id ? `/api/events/${id}` : '/api/events';
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
  if (!id || !confirm('이 일정을 삭제할까요?')) return;
  const res = await fetch(`/api/events/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || '삭제 실패');
    return;
  }
  document.getElementById('modal-overlay').classList.add('hidden');
  if (window.onEventSaved) window.onEventSaved();
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
    ['장소',     e.location    || '-'],
    ['내용',     e.description || '-'],
  ];
  document.getElementById('kdetail-body').innerHTML = rows.map(([label, val]) => `
    <div class="detail-row">
      <span class="detail-label">${label}</span>
      <span class="detail-val">${esc(val)}</span>
    </div>`).join('');

  _renderKDetailButtons();

  const canEdit = CURRENT_USER && (CURRENT_USER.role === 'admin' || CURRENT_USER.role === 'editor');
  const editBtn = document.getElementById('kbtn-edit');
  if (editBtn) editBtn.style.display = canEdit ? '' : 'none';
  const completeBtn = document.getElementById('kbtn-complete');
  if (completeBtn) completeBtn.style.display = canEdit ? '' : 'none';

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

// ── 초기화 ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initDatePicker();
  const form = document.getElementById('event-form');
  if (form) form.addEventListener('submit', saveEvent);
});
