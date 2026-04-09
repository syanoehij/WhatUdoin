let calendar;
let currentDetailEventId = null;
let allProjects = [];
let fpInstance = null;
let _fpSavedDates = ['', ''];  // 달력 열기 전 날짜 백업

// ── FullCalendar 초기화 ─────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('calendar');
  calendar = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    locale: 'ko',
    height: 'auto',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay',
    },
    buttonText: { today: '오늘', month: '월', week: '주', day: '일' },
    events: '/api/events',
    eventClick(info) { showDetail(info.event); },
    dateClick(info) { openModal(info.dateStr); },
  });
  calendar.render();
  initDatePicker();
  document.getElementById('event-form').addEventListener('submit', saveEvent);
});

// ── flatpickr 날짜 아이콘 클릭 핸들러 ─────────────────────
function openStartDatePicker() {
  _fpSavedDates = [
    document.getElementById('f-start-date').value,
    document.getElementById('f-end-date').value,
  ];
  fpInstance.open();
  if (_fpSavedDates[0]) fpInstance.jumpToDate(_fpSavedDates[0]);
}

function openEndDatePicker() {
  _fpSavedDates = [
    document.getElementById('f-start-date').value,
    document.getElementById('f-end-date').value,
  ];
  document.getElementById('f-end-date').focus();
  fpInstance.open();
  if (_fpSavedDates[1])      fpInstance.jumpToDate(_fpSavedDates[1]);
  else if (_fpSavedDates[0]) fpInstance.jumpToDate(_fpSavedDates[0]);
}

// ── flatpickr 범위 선택 ───────────────────────────────────
function initDatePicker() {
  fpInstance = flatpickr('#f-start-date', {
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
    onClose() {
      // onClose는 blur보다 먼저 실행됨
      // blur → rangePlugin이 selectedDates.length===1 감지 → endInput 클리어 순서로 진행되므로
      // blur가 끝난 뒤에 체크해야 정확히 복원할 수 있음
      const saved = [..._fpSavedDates];
      _fpSavedDates = ['', ''];
      if (!saved[0]) return;

      setTimeout(() => {
        const startInput = document.getElementById('f-start-date');
        const endInput   = document.getElementById('f-end-date');
        if (!startInput.value || !endInput.value) {
          fpInstance.setDate(
            saved[1] ? [saved[0], saved[1]] : [saved[0]],
            false
          );
          startInput.value = saved[0];
          endInput.value   = saved[1] || saved[0];
        }
      }, 0);
    },
  });
}

// ── 프로젝트 자동완성 ─────────────────────────────────────
async function loadProjects() {
  const res = await fetch('/api/projects');
  allProjects = await res.json();
}

function filterProjects(val) {
  const dropdown = document.getElementById('project-dropdown');
  const filtered = val
    ? allProjects.filter(p => p.toLowerCase().includes(val.toLowerCase()))
    : allProjects;

  if (!filtered.length) { dropdown.classList.add('hidden'); return; }
  dropdown.innerHTML = filtered.map(p =>
    `<li onmousedown="selectProject('${p.replace(/'/g, "\\'")}')">${p}</li>`
  ).join('');
  dropdown.classList.remove('hidden');
}

function selectProject(val) {
  document.getElementById('f-project').value = val;
  document.getElementById('project-dropdown').classList.add('hidden');
}

function hideDropdown() {
  setTimeout(() => document.getElementById('project-dropdown').classList.add('hidden'), 150);
}

// ── 모달 열기 ──────────────────────────────────────────
function openModal(dateStr = '', eventData = null) {
  const overlay = document.getElementById('modal-overlay');
  const today = dateStr || getToday();

  document.getElementById('event-id').value       = '';
  document.getElementById('f-title').value        = '';
  document.getElementById('f-project').value      = '';
  const [defStart, defEnd] = getDefaultTimes();
  fpInstance.setDate([today, today], true);
  document.getElementById('f-start-date').value   = today;
  document.getElementById('f-end-date').value     = today;
  document.getElementById('f-start-time').value   = defStart;
  document.getElementById('f-end-time').value     = defEnd;
  document.getElementById('f-allday').checked     = false;
  document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active'));
  document.getElementById('f-location').value     = '';
  document.getElementById('f-assignee').value     = '';
  document.getElementById('f-description').value  = '';
  document.getElementById('btn-delete').classList.add('hidden');
  document.getElementById('modal-title').textContent = '일정 추가';
  toggleAllDay();

  if (eventData) {
    document.getElementById('modal-title').textContent = '일정 수정';
    document.getElementById('event-id').value      = eventData.id;
    document.getElementById('f-title').value       = eventData.title || '';
    document.getElementById('f-project').value     = eventData.project || '';
    document.getElementById('f-location').value    = eventData.location || '';
    document.getElementById('f-assignee').value    = eventData.assignee || '';
    document.getElementById('f-description').value = eventData.description || '';

    const allDay = !!eventData.all_day;
    document.getElementById('f-allday').checked = allDay;

    const [startDate, startTime] = splitDatetime(eventData.start_datetime);
    const [endDate,   endTime]   = splitDatetime(eventData.end_datetime);

    fpInstance.setDate([startDate, endDate || startDate], true);
    document.getElementById('f-start-date').value = startDate;
    document.getElementById('f-end-date').value   = endDate || startDate;
    document.getElementById('f-start-time').value = allDay ? '' : startTime;
    document.getElementById('f-end-time').value   = allDay ? '' : endTime;

    toggleAllDay();
    document.getElementById('btn-delete').classList.remove('hidden');
  }

  loadProjects();
  overlay.classList.remove('hidden');
  setTimeout(() => document.getElementById('f-title').focus(), 50);
}

function closeModal(e) {
  if (e && e.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.add('hidden');
}

// ── 종일 토글 ──────────────────────────────────────────
function toggleAllDay() {
  const allDay = document.getElementById('f-allday').checked;
  document.querySelectorAll('.time-row').forEach(el => {
    el.style.display = allDay ? 'none' : '';
  });
}

// ── 저장 ───────────────────────────────────────────────
async function saveEvent(e) {
  e.preventDefault();
  const id     = document.getElementById('event-id').value;
  const allDay = document.getElementById('f-allday').checked;

  const startDate = document.getElementById('f-start-date').value;
  const endDate   = document.getElementById('f-end-date').value;
  const startTime = document.getElementById('f-start-time').value || '00:00';
  const endTime   = document.getElementById('f-end-time').value   || '00:00';

  if (!startDate) {
    alert('시작 날짜를 선택해주세요.');
    return;
  }
  if (endDate && endDate < startDate) {
    alert('종료 날짜는 시작 날짜보다 이전일 수 없습니다.');
    return;
  }
  if (!allDay && endDate === startDate && endTime < startTime) {
    alert('종료 시간은 시작 시간보다 이전일 수 없습니다.');
    return;
  }

  const payload = {
    title:          document.getElementById('f-title').value,
    project:        document.getElementById('f-project').value || null,
    start_datetime: `${startDate}T${allDay ? '00:00' : startTime}`,
    end_datetime:   endDate ? `${endDate}T${allDay ? '00:00' : endTime}` : null,
    all_day:        allDay ? 1 : 0,
    location:       document.getElementById('f-location').value || null,
    assignee:       document.getElementById('f-assignee').value || null,
    description:    document.getElementById('f-description').value || null,
    created_by:     'editor',
    source:         'manual',
  };

  if (id) {
    await fetch(`/api/events/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } else {
    await fetch('/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  document.getElementById('modal-overlay').classList.add('hidden');
  calendar.refetchEvents();
}

// ── 삭제 ───────────────────────────────────────────────
async function deleteEvent() {
  const id = document.getElementById('event-id').value;
  if (!id || !confirm('이 일정을 삭제할까요?')) return;
  await fetch(`/api/events/${id}`, { method: 'DELETE' });
  document.getElementById('modal-overlay').classList.add('hidden');
  calendar.refetchEvents();
}

// ── 상세 보기 ──────────────────────────────────────────
function showDetail(fcEvent) {
  currentDetailEventId = fcEvent.id;
  const p = fcEvent.extendedProps;

  document.getElementById('detail-title').textContent = fcEvent.title;

  const rows = [
    ['시작',    formatDatetime(fcEvent.startStr, p.all_day)],
    ['종료',    fcEvent.endStr ? formatDatetime(fcEvent.endStr, p.all_day) : '-'],
    ['프로젝트', p.project    || '-'],
    ['장소',    p.location    || '-'],
    ['담당자',  p.assignee    || '-'],
    ['내용',    p.description || '-'],
  ];

  document.getElementById('detail-body').innerHTML = rows.map(([label, val]) => `
    <div class="detail-row">
      <span class="detail-label">${label}</span>
      <span class="detail-val">${esc(val)}</span>
    </div>
  `).join('');

  document.getElementById('detail-overlay').classList.remove('hidden');
}

function closeDetail(e) {
  if (e && e.target !== document.getElementById('detail-overlay')) return;
  document.getElementById('detail-overlay').classList.add('hidden');
  currentDetailEventId = null;
}

async function editFromDetail() {
  if (!currentDetailEventId) return;
  const res = await fetch(`/api/events/${currentDetailEventId}`);
  const data = await res.json();
  document.getElementById('detail-overlay').classList.add('hidden');
  openModal('', data);
}

// ── 시간 프리셋 ────────────────────────────────────────
function getDefaultTimes() {
  const now = new Date();
  const startHour = (now.getHours() + 1) % 24;
  const endHour   = (now.getHours() + 2) % 24;
  const fmt = h => `${String(h).padStart(2, '0')}:00`;
  return [fmt(startHour), fmt(endHour)];
}

function setTimePreset(preset) {
  const presets = {
    am:      ['08:00', '12:00'],
    pm:      ['13:00', '17:00'],
    evening: ['18:00', '22:00'],
  };
  const [start, end] = presets[preset];
  document.getElementById('f-start-time').value = start;
  document.getElementById('f-end-time').value   = end;
  document.getElementById('f-allday').checked   = false;
  toggleAllDay();

  document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active'));
  document.querySelector(`.btn-preset[onclick="setTimePreset('${preset}')"]`).classList.add('active');
}

// ── 유틸 ───────────────────────────────────────────────
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
