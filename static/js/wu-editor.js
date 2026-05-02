/* wu-editor.js — WUEditor 공용 편집기 모듈 (Tiptap 기반) */
/* 사용법: const inst = WUEditor.create(options)  →  inst.save / inst.destroy 등 */
(function (global) {
  'use strict';

  /* ── 헬퍼 ─────────────────────────────────────── */

  /* Tiptap이 각주 id/target 속성을 제거하므로 렌더링 후 DOM을 직접 복원 */
  function _fixFootnoteAnchors(container) {
    // 각주 참조 <a href="#fn-X"><sup> → <a id="fnref-X"> 추가
    container.querySelectorAll('a[href^="#fn-"]').forEach(a => {
      if (a.querySelector('sup') && !a.id) {
        a.id = 'fnref-' + a.getAttribute('href').slice(4);
      }
    });
    // 각주 정의 <li> → id="fn-X" 추가 (wu-fn-back의 href에서 추출)
    container.querySelectorAll('a.wu-fn-back[href^="#fnref-"]').forEach(a => {
      const li = a.closest('li');
      if (li && !li.id) {
        li.id = 'fn-' + a.getAttribute('href').slice(7);
      }
    });
    // 앵커 링크의 target="_blank" / rel 제거 → 같은 페이지 스크롤 이동
    container.querySelectorAll('a[href^="#"]').forEach(a => {
      a.removeAttribute('target');
      a.removeAttribute('rel');
    });
  }

  /* 뷰어 모드에서 [^label] 각주를 <sup> + <section> HTML로 변환 */
  function _preprocessViewerFootnotes(md) {
    const defs = {};
    // tiptap-markdown이 [를 \[로 이스케이프하므로 \[^label\]: 와 [^label]: 모두 매칭
    const defRe = /^\\?\[\^([^\\\]]+)\\?\]:\s+(.+)$/gm;
    let m;
    while ((m = defRe.exec(md)) !== null) defs[m[1]] = m[2];
    if (!Object.keys(defs).length) return md;

    let result = md.replace(/^\\?\[\^([^\\\]]+)\\?\]:\s+.+$/gm, '').replace(/\n{3,}/g, '\n\n').trim();

    const labels = [];
    result = result.replace(/\\?\[\^([^\\\]]+)\\?\]/g, (match, label) => {
      if (!defs[label]) return match;
      if (!labels.includes(label)) labels.push(label);
      const n = labels.indexOf(label) + 1;
      return `<sup class="wu-fn-ref" id="fnref-${label}"><a href="#fn-${label}">[${n}]</a></sup>`;
    });

    if (labels.length) {
      const items = labels.map(label =>
        `<li id="fn-${label}"><p>${defs[label]} <a href="#fnref-${label}" class="wu-fn-back">↩</a></p></li>`
      ).join('\n');
      result += `\n\n<hr />\n<section class="wu-footnotes"><ol>\n${items}\n</ol></section>`;
    }
    return result;
  }

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function sel(s) {
    return typeof s === 'string' ? document.querySelector(s) : s;
  }

  /* ── 툴바 정의 ──────────────────────────────────── */
  const TOOLBAR_DEFS = [
    { group: ['heading', 'bold', 'italic', 'strike', 'highlight'] },
    { group: ['hr', 'quote'] },
    { group: ['ul', 'ol', 'task'] },
    { group: ['table', 'link', 'image'] },
    { group: ['code', 'codeblock'] },
  ];

  const TOOLBAR_LABELS = {
    heading:   { icon: 'H',   title: '제목' },
    bold:      { icon: '<b>B</b>', title: '굵게' },
    italic:    { icon: '<i>I</i>', title: '기울임' },
    strike:    { icon: '<s>S</s>', title: '취소선' },
    highlight: { icon: '<mark>H</mark>', title: '하이라이트 (==text==)' },
    hr:        { icon: '—',   title: '구분선' },
    quote:     { icon: '❝',   title: '인용' },
    ul:        { icon: '• —', title: '목록' },
    ol:        { icon: '1.',  title: '번호 목록' },
    task:      { icon: '☑',  title: '할일 목록' },
    table:     { icon: '⊞',  title: '표 삽입' },
    link:      { icon: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>', title: '링크' },
    image:     { icon: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>', title: '이미지' },
    code:      { icon: '`',   title: '인라인 코드' },
    codeblock: { icon: '{ }', title: '코드 블록' },
  };

  /* ── 슬래시 커맨드 항목 ─────────────────────────── */
  const SLASH_ITEMS = [
    { id: 'h1',    label: '제목 1',    icon: 'H1',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 1 }).run() },
    { id: 'h2',    label: '제목 2',    icon: 'H2',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 2 }).run() },
    { id: 'h3',    label: '제목 3',    icon: 'H3',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 3 }).run() },
    { id: 'h4',    label: '제목 4',    icon: 'H4',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 4 }).run() },
    { id: 'h5',    label: '제목 5',    icon: 'H5',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 5 }).run() },
    { id: 'h6',    label: '제목 6',    icon: 'H6',   hint: 'h heading',       cmd: ed => ed.chain().focus().setHeading({ level: 6 }).run() },
    { id: 'ul',    label: '목록',      icon: '• —',  hint: 'ul list bullet',   cmd: ed => ed.chain().focus().toggleBulletList().run() },
    { id: 'ol',    label: '번호 목록', icon: '1.',   hint: 'ol number list',   cmd: ed => ed.chain().focus().toggleOrderedList().run() },
    { id: 'task',  label: '할일 목록', icon: '☑',   hint: 'task todo check',  cmd: ed => ed.chain().focus().toggleTaskList().run() },
    { id: 'quote', label: '인용',      icon: '❝',   hint: 'quote blockquote', cmd: ed => ed.chain().focus().toggleBlockquote().run() },
    { id: 'code',  label: '코드 블록', icon: '{ }', hint: 'code block',       cmd: ed => ed.chain().focus().toggleCodeBlock().run() },
    { id: 'table', label: '표',        icon: '⊞',   hint: 'table grid',       cmd: ed => ed.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
    { id: 'hr',    label: '구분선',    icon: '—',   hint: 'hr divider line',  cmd: ed => ed.chain().focus().setHorizontalRule().run() },
    { id: 'highlight', label: '하이라이트', icon: '<mark>H</mark>', hint: 'highlight mark hl 형광펜', cmd: ed => ed.chain().focus().toggleHighlight().run() },
    { id: 'footnote', label: '각주',  icon: '[^]', hint: 'footnote fn ref',   cmd: ed => {
        const label = String((ed.getText().match(/\[\^[^\]]+\]:/g) || []).length + 1);
        ed.chain()
          .focus()
          .insertContent(`[^${label}]`)
          .command(({ tr, dispatch }) => {
            const schema = tr.doc.type.schema;
            const para = schema.nodes.paragraph.createChecked(null, [schema.text(`[^${label}]: `)]);
            if (dispatch) tr.insert(tr.doc.content.size, para);
            return true;
          })
          .run();
      },
    },
  ];
  function _matchSlash(query) {
    if (!query) return SLASH_ITEMS;
    const q = query.toLowerCase();
    return SLASH_ITEMS.filter(it => it.id.startsWith(q) || it.label.includes(q) || it.hint.includes(q));
  }

  /* ── 코드블록 언어 목록 ─────────────────────────── */
  const LANG_OPTIONS = [
    { value: '',           label: '자동 감지' },
    { value: 'javascript', label: 'JavaScript' },
    { value: 'typescript', label: 'TypeScript' },
    { value: 'python',     label: 'Python' },
    { value: 'java',       label: 'Java' },
    { value: 'c',          label: 'C' },
    { value: 'cpp',        label: 'C++' },
    { value: 'csharp',     label: 'C#' },
    { value: 'go',         label: 'Go' },
    { value: 'rust',       label: 'Rust' },
    { value: 'html',       label: 'HTML' },
    { value: 'css',        label: 'CSS' },
    { value: 'sql',        label: 'SQL' },
    { value: 'bash',       label: 'Bash / Shell' },
    { value: 'json',       label: 'JSON' },
    { value: 'yaml',       label: 'YAML' },
    { value: 'markdown',   label: 'Markdown' },
    { value: 'xml',        label: 'XML' },
    { value: 'kotlin',     label: 'Kotlin' },
    { value: 'swift',      label: 'Swift' },
    { value: 'php',        label: 'PHP' },
    { value: 'ruby',       label: 'Ruby' },
  ];

  /* ── 팩토리 ────────────────────────────────────── */
  function create(opts) {
    opts = opts || {};
    const feat   = opts.features || {};
    const hooks  = opts.hooks    || {};

    /* ── 내부 상태 ── */
    let _editor       = null;
    let _dirty        = false;
    let _cooldown     = false;
    let _cooldownTimer = null;
    let _autoSaveTimer = null;
    let _lockHeartbeat = null;
    let _idleTimer    = null;
    let _isDragging   = false;
    let _slashActive   = false;
    let _slashStart    = -1;
    let _slashQuery    = '';
    let _slashSelIdx   = 0;
    let _slashFiltered = [];
    const _listeners  = [];   // { target, type, fn, capture } — destroy 시 일괄 해제

    /* ── listener 등록 헬퍼 ── */
    function on(target, type, fn, capture) {
      if (!target) return;
      target.addEventListener(type, fn, !!capture);
      _listeners.push({ target, type, fn, capture: !!capture });
    }

    /* ── DOM 준비 ─────────────────────────────────── */
    const containerEl = sel(opts.el);
    const bodyEl      = opts.bodyEl ? sel(opts.bodyEl) : null;

    /* 테이블 그리드 피커 동적 생성 */
    const PICKER_MAX = 20, PICKER_INIT = 5;
    let _tablePickerEl = null;
    let _tablePickerOpen = false;
    let _pickerRows = PICKER_INIT, _pickerCols = PICKER_INIT;
    if (opts.canEdit !== false) {
      _tablePickerEl = document.createElement('div');
      _tablePickerEl.className = 'wu-table-picker';
      _tablePickerEl.innerHTML = `
        <div class="wu-table-picker-grid"></div>
        <div class="wu-table-picker-label">표 삽입</div>`;
      const gridEl  = _tablePickerEl.querySelector('.wu-table-picker-grid');
      const labelEl = _tablePickerEl.querySelector('.wu-table-picker-label');

      function _renderGrid(hR, hC) {
        gridEl.style.gridTemplateColumns = `repeat(${_pickerCols}, 1fr)`;
        const frag = document.createDocumentFragment();
        for (let r = 1; r <= _pickerRows; r++) {
          for (let c = 1; c <= _pickerCols; c++) {
            const cell = document.createElement('div');
            cell.className = 'wu-table-picker-cell' + (r <= hR && c <= hC ? ' active' : '');
            cell.dataset.row = r;
            cell.dataset.col = c;
            frag.appendChild(cell);
          }
        }
        gridEl.innerHTML = '';
        gridEl.appendChild(frag);
      }
      _renderGrid(0, 0);

      gridEl.addEventListener('mousemove', e => {
        const cell = e.target.closest('.wu-table-picker-cell');
        if (!cell) return;
        const r = +cell.dataset.row, c = +cell.dataset.col;
        let changed = false;
        if (r >= _pickerRows && _pickerRows < PICKER_MAX) { _pickerRows = Math.min(r + 1, PICKER_MAX); changed = true; }
        if (c >= _pickerCols && _pickerCols < PICKER_MAX) { _pickerCols = Math.min(c + 1, PICKER_MAX); changed = true; }
        if (changed) {
          _renderGrid(r, c);
        } else {
          gridEl.querySelectorAll('.wu-table-picker-cell').forEach(el => {
            el.classList.toggle('active', +el.dataset.row <= r && +el.dataset.col <= c);
          });
        }
        labelEl.textContent = `${c}열 × ${r}행`;
      });
      /* 마우스가 그리드 밖으로 나가면 하이라이트만 클리어 — 크기는 유지 */
      gridEl.addEventListener('mouseleave', () => {
        gridEl.querySelectorAll('.wu-table-picker-cell.active')
          .forEach(el => el.classList.remove('active'));
        labelEl.textContent = '표 삽입';
      });
      gridEl.addEventListener('click', e => {
        const cell = e.target.closest('.wu-table-picker-cell');
        if (!cell || !_editor) return;
        const rows = +cell.dataset.row, cols = +cell.dataset.col;
        _editor.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
        _tablePickerEl.style.display = 'none';
        _tablePickerOpen = false;
        _pickerRows = PICKER_INIT;
        _pickerCols = PICKER_INIT;
        _renderGrid(0, 0);
      });
      document.body.appendChild(_tablePickerEl);
    }

    function _pickerReset() {
      _pickerRows = PICKER_INIT;
      _pickerCols = PICKER_INIT;
    }

    function _showTablePicker(btnEl) {
      if (!_tablePickerEl) return;
      if (_tablePickerOpen) {
        _tablePickerEl.style.display = 'none';
        _tablePickerOpen = false;
        _pickerReset();
        return;
      }
      const r = btnEl.getBoundingClientRect();
      _tablePickerEl.style.display = 'block';
      _tablePickerEl.style.top  = (r.bottom + window.scrollY + 4) + 'px';
      _tablePickerEl.style.left = r.left + 'px';
      _tablePickerOpen = true;
    }

    /* 테이블 컨텍스트 메뉴 동적 생성 */
    let _tableMenuEl = null;
    if (opts.canEdit !== false) {
      _tableMenuEl = document.createElement('div');
      _tableMenuEl.className = 'wu-table-menu';
      _tableMenuEl.style.display = 'none';
      _tableMenuEl.innerHTML = [
        { cmd: 'addRowBefore',    icon: 'Row ↑',  title: '위에 행 추가' },
        { cmd: 'addRowAfter',     icon: 'Row ↓',  title: '아래에 행 추가' },
        { cmd: 'deleteRow',       icon: '− Row',  title: '행 삭제' },
        { cmd: 'sep' },
        { cmd: 'addColumnBefore', icon: 'Col ←',  title: '왼쪽에 열 추가' },
        { cmd: 'addColumnAfter',  icon: 'Col →',  title: '오른쪽에 열 추가' },
        { cmd: 'deleteColumn',    icon: '− Col',  title: '열 삭제' },
        { cmd: 'sep' },
        { cmd: 'mergeOrSplit',    icon: '⊞ Merge', title: '셀 병합/분리' },
        { cmd: 'deleteTable',     icon: '🗑️ Table', title: '표 삭제' },
      ].map(item => item.cmd === 'sep'
        ? '<span class="wu-table-menu-sep"></span>'
        : `<button class="wu-table-menu-btn" data-cmd="${item.cmd}" title="${item.title}">${item.icon}</button>`
      ).join('');
      document.body.appendChild(_tableMenuEl);
    }

    /* 이미지 리사이즈 툴바 동적 생성 */
    let _tbEl = null;
    if (feat.imageResize !== false) {
      _tbEl = document.createElement('div');
      _tbEl.id = 'wu-img-resize-toolbar';
      _tbEl.className = 'wu-img-resize-toolbar';
      _tbEl.innerHTML =
        ['25','50','75','원본','100','125','150','175','200'].map(v =>
          `<button data-pct="${v}">${v}${v === '원본' ? '' : '%'}</button>`
        ).join('');
      document.body.appendChild(_tbEl);
    }

    /* 이탈 확인 모달 동적 생성 */
    let _leaveOverlay = null;
    let _leaveMsgTitle = null;
    let _leaveMsgDesc  = null;
    if (feat.leaveConfirm) {
      _leaveOverlay = document.createElement('div');
      _leaveOverlay.className = 'wu-leave-overlay';
      _leaveOverlay.style.display = 'none';
      _leaveOverlay.innerHTML = `
        <div class="wu-leave-box">
          <h3 id="wu-leave-title">저장하지 않고 나가시겠습니까?</h3>
          <p  id="wu-leave-desc">변경사항이 저장되지 않습니다.</p>
          <div class="modal-actions">
            <button id="wu-leave-cancel"  class="btn btn-sm">취소</button>
            <button id="wu-leave-nosave"  class="btn btn-sm btn-danger">저장 안 함</button>
            <button id="wu-leave-save"    class="btn btn-sm btn-primary">저장하고 나가기</button>
          </div>
        </div>`;
      document.body.appendChild(_leaveOverlay);
      _leaveMsgTitle = _leaveOverlay.querySelector('#wu-leave-title');
      _leaveMsgDesc  = _leaveOverlay.querySelector('#wu-leave-desc');
    }

    /* 링크 삽입 모달 동적 생성 */
    let _linkOverlay = null;
    if (opts.canEdit !== false) {
      _linkOverlay = document.createElement('div');
      _linkOverlay.className = 'wu-leave-overlay';
      _linkOverlay.style.display = 'none';
      _linkOverlay.innerHTML = `
        <div class="wu-leave-box" style="max-width:440px;width:90%">
          <h3 style="margin:0 0 12px;font-size:1rem">링크 삽입</h3>
          <input id="wu-link-input" type="text" placeholder="https://"
            style="width:100%;box-sizing:border-box;padding:7px 10px;border:1px solid var(--border);border-radius:5px;background:var(--surface);color:var(--text);font-size:0.9rem;outline:none;margin-bottom:16px">
          <div class="modal-actions">
            <button id="wu-link-remove" class="btn btn-sm btn-danger" style="margin-right:auto">링크 제거</button>
            <button id="wu-link-cancel" class="btn btn-sm">취소</button>
            <button id="wu-link-confirm" class="btn btn-sm btn-primary">확인</button>
          </div>
        </div>`;
      document.body.appendChild(_linkOverlay);
    }

    /* 슬래시 커맨드 메뉴 동적 생성 */
    let _slashMenuEl = null;
    if (opts.canEdit !== false) {
      _slashMenuEl = document.createElement('div');
      _slashMenuEl.className = 'wu-slash-menu';
      _slashMenuEl.style.display = 'none';
      document.body.appendChild(_slashMenuEl);
    }

    /* 헤딩 드롭다운 동적 생성 */
    let _headingDropEl = null;
    if (opts.canEdit !== false) {
      _headingDropEl = document.createElement('div');
      _headingDropEl.className = 'wu-heading-drop';
      _headingDropEl.style.display = 'none';
      _headingDropEl.innerHTML = `
        <button class="wu-heading-drop-btn" data-level="0">기본 텍스트</button>
        <button class="wu-heading-drop-btn" data-level="1" style="font-size:1.15em;font-weight:700">제목 1</button>
        <button class="wu-heading-drop-btn" data-level="2" style="font-size:1.05em;font-weight:600">제목 2</button>
        <button class="wu-heading-drop-btn" data-level="3" style="font-size:0.95em;font-weight:600">제목 3</button>
        <button class="wu-heading-drop-btn" data-level="4" style="font-size:0.88em;font-weight:600">제목 4</button>
        <button class="wu-heading-drop-btn" data-level="5" style="font-size:0.82em;font-weight:600">제목 5</button>
        <button class="wu-heading-drop-btn" data-level="6" style="font-size:0.78em;font-weight:500">제목 6</button>`;
      document.body.appendChild(_headingDropEl);
    }

    /* 자동완성 드롭다운 동적 생성 */
    let _acDd = null;
    let _acCb = null;
    let _acProjects = [];
    let _acMembers  = [];
    if (feat.autocomplete) {
      _acDd = document.createElement('ul');
      _acDd.className = 'ac-dropdown hidden';
      _acDd.style.position = 'fixed';
      _acDd.style.zIndex = '99999';
      document.body.appendChild(_acDd);

      // 데이터 로드
      const { projectsEndpoint, membersEndpoint } = feat.autocomplete;
      Promise.all([
        projectsEndpoint ? fetch(projectsEndpoint).then(r => r.json()).catch(() => []) : Promise.resolve([]),
        membersEndpoint  ? fetch(membersEndpoint).then(r  => r.json()).catch(() => []) : Promise.resolve([]),
      ]).then(([pr, mb]) => { _acProjects = pr; _acMembers = mb; });
    }

    /* ── 이미지 리사이즈 ────────────────────────────── */
    let _activeImg  = null;
    let _imgWidthMap = {};
    let _imgResizeObs = null;

    function _getProseMirrorEl() {
      return containerEl && containerEl.querySelector('.ProseMirror');
    }

    function _applyImgWidths() {
      const pmEl = _getProseMirrorEl();
      if (!pmEl) return;
      pmEl.querySelectorAll('img').forEach(img => {
        const src = img.getAttribute('src');
        if (!src || !(src in _imgWidthMap)) return;
        const px = _imgWidthMap[src];
        if (px != null) {
          img.style.setProperty('width', px + 'px', 'important');
          img.style.setProperty('max-width', 'none', 'important');
        } else {
          img.style.removeProperty('width');
          img.style.removeProperty('max-width');
        }
      });
    }

    function _parseInitialWidths(md) {
      let m;
      // Obsidian format: ![alt|NNN](src)
      const regObs = /!\[([^\]]*)\]\(([^)]+)\)/g;
      while ((m = regObs.exec(md || '')) !== null) {
        const pi = m[1].lastIndexOf('|');
        if (pi !== -1) {
          const w = m[1].slice(pi + 1).trim();
          if (/^\d+$/.test(w)) _imgWidthMap[m[2]] = parseInt(w);
        }
      }
      // Legacy HTML format: <img style="width:Npx">
      const regHtml = /<img[^>]*src="([^"]+)"[^>]*style="[^"]*width\s*:\s*(\d+)px[^"]*"[^>]*>/g;
      while ((m = regHtml.exec(md || '')) !== null) {
        if (!_imgWidthMap[m[1]]) _imgWidthMap[m[1]] = parseInt(m[2]);
      }
      if (Object.keys(_imgWidthMap).length) setTimeout(_applyImgWidths, 200);
    }

    function _initImgResize() {
      if (!_tbEl) return;
      _parseInitialWidths(opts.initialMarkdown || '');

      const pmEl = _getProseMirrorEl();
      if (pmEl) {
        _imgResizeObs = new MutationObserver(_applyImgWidths);
        _imgResizeObs.observe(pmEl, { childList: true, subtree: true });
      }

      on(document, 'pointerdown', e => {
        const edEl = _getProseMirrorEl();
        if (e.target.tagName === 'IMG' && edEl && edEl.contains(e.target)) {
          _activeImg = e.target;
        } else if (!_tbEl.contains(e.target)) {
          _activeImg = null;
        }
      }, true);

      on(document, 'click', e => {
        const edEl = _getProseMirrorEl();
        if (e.target.tagName === 'IMG' && edEl && edEl.contains(e.target)) {
          const r = e.target.getBoundingClientRect();
          _tbEl.style.top  = (r.bottom + window.scrollY + 6) + 'px';
          _tbEl.style.left = Math.max(4, r.left) + 'px';
          _tbEl.style.display = 'flex';
        } else if (!_tbEl.contains(e.target)) {
          _tbEl.style.display = 'none';
        }
      }, true);

      _tbEl.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', () => {
          if (!_activeImg) return;
          const src = _activeImg.getAttribute('src');
          if (!src) return;
          const pct = btn.dataset.pct;
          if (pct === '원본') {
            _imgWidthMap[src] = null;
          } else {
            const p  = parseInt(pct);
            const nw = _activeImg.naturalWidth;
            _imgWidthMap[src] = nw > 0 ? Math.round(nw * p / 100) : null;
          }
          _applyImgWidths();
          _tbEl.style.display = 'none';
          _activeImg = null;
          _setDirty(true);
        });
      });
    }

    /* 저장 전 마크다운에 img width 주입 (Obsidian 포맷: ![alt|NNN](src)) */
    function _injectImgStyles(md) {
      for (const [src, px] of Object.entries(_imgWidthMap)) {
        const esc2 = src.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        if (px === null) {
          // 너비 제거: ![alt|NNN](src) → ![alt](src)
          md = md.replace(new RegExp(`!\\[([^\\]]*?)\\|\\d+\\]\\(${esc2}\\)`, 'g'),
            (_, alt) => `![${alt}](${src})`);
          // Legacy HTML → 일반 마크다운
          md = md.replace(new RegExp(`<img[^>]*src="${esc2}"[^>]*>`, 'g'),
            m => { const a = m.match(/alt="([^"]*)"/); return `![${a ? a[1] : ''}](${src})`; });
        } else {
          // 기존 Obsidian 포맷 너비 갱신: ![alt|OLD](src) → ![alt|NEW](src)
          md = md.replace(new RegExp(`!\\[([^\\]]*?)\\|\\d+\\]\\(${esc2}\\)`, 'g'),
            (_, alt) => `![${alt}|${px}](${src})`);
          // 일반 마크다운에 너비 추가: ![alt](src) → ![alt|NNN](src)
          md = md.replace(new RegExp(`!\\[([^\\]\\|]*)\\]\\(${esc2}\\)`, 'g'),
            (_, alt) => `![${alt}|${px}](${src})`);
          // Legacy HTML → Obsidian 포맷으로 마이그레이션
          md = md.replace(new RegExp(`<img[^>]*src="${esc2}"[^>]*>`, 'g'),
            m => { const a = m.match(/alt="([^"]*)"/); return `![${a ? a[1] : ''}|${px}](${src})`; });
        }
      }
      return md;
    }

    /* ── Dirty 상태 ─────────────────────────────────── */
    function _setDirty(v) {
      if (_dirty === v) return;
      _dirty = v;
      if (hooks.onDirtyChange) hooks.onDirtyChange(v);
    }

    /* ── 편집 잠금 ──────────────────────────────────── */
    function _lockUrl() {
      const lk = feat.editLock;
      return lk.tabToken ? `${lk.endpoint}?tab_token=${lk.tabToken}` : lk.endpoint;
    }

    function _acquireLock() {
      const lk = feat.editLock;
      if (!lk || !lk.endpoint) return;
      const url = _lockUrl();
      fetch(url, { method: 'POST' })
        .then(res => {
          if (res.status === 423) {
            if (lk.onLockFailed) lk.onLockFailed();
            return;
          }
          _lockHeartbeat = setInterval(() => {
            fetch(url, { method: 'PUT' })
              .then(r => { if (r.status === 423 && lk.onLockLost) lk.onLockLost(); })
              .catch(() => {});
          }, lk.heartbeatMs || 30000);
        })
        .catch(() => {});
    }

    function _releaseLock() {
      const lk = feat.editLock;
      if (!lk || !lk.endpoint) return;
      clearInterval(_lockHeartbeat);
      fetch(_lockUrl(), { method: 'DELETE', keepalive: true }).catch(() => {});
    }

    /* ── beforeunload ───────────────────────────────── */
    function _onBeforeUnload(e) {
      _releaseLock();
      const shouldWarn = hooks.shouldWarnOnUnload
        ? hooks.shouldWarnOnUnload()
        : _dirty;
      if (shouldWarn) { e.preventDefault(); e.returnValue = ''; }
    }

    /* ── 자동 저장 ──────────────────────────────────── */
    function _scheduleAutosave() {
      const as = feat.autosave;
      if (!as) return;
      clearTimeout(_autoSaveTimer);
      _autoSaveTimer = setTimeout(() => {
        const respectCooldown = as.respectCooldown !== false;
        if (_dirty && (!respectCooldown || !_cooldown)) {
          _triggerSave(true);
        }
      }, as.intervalMs || 120000);
    }

    /* ── 유휴 타이머 (사용자 키 입력 없을 때 자동 종료) ── */
    function _scheduleIdleTimer() {
      const it = feat.idleTimeout;
      if (!it || !it.ms || !it.onIdle) return;
      clearTimeout(_idleTimer);
      _idleTimer = setTimeout(it.onIdle, it.ms);
    }

    /* ── 저장 쿨다운 ────────────────────────────────── */
    function _startCooldown() {
      const cd = feat.saveCooldown;
      if (!cd) return;
      _cooldown = true;
      const btn = sel(cd.saveBtnSelector || '[data-wu-save]');
      const orig = btn ? btn.textContent : null;
      const secs = cd.seconds || 10;
      if (btn) { btn.disabled = true; btn.textContent = `✓ 저장됨 (${secs}s)`; }
      let remaining = secs;
      _cooldownTimer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
          clearInterval(_cooldownTimer);
          _cooldownTimer = null;
          _cooldown = false;
          if (btn) { btn.textContent = orig; btn.disabled = false; }
        } else {
          if (btn) btn.textContent = `✓ 저장됨 (${remaining}s)`;
        }
      }, 1000);
    }

    /* ── 저장 트리거 ────────────────────────────────── */
    function _triggerSave(auto) {
      const md = _editor ? _injectImgStyles(_getMarkdown()) : '';
      if (hooks.onSave) hooks.onSave(md, { auto: !!auto });
    }

    /* ── afterSave (성공 후 템플릿이 호출) ──────────── */
    function _afterSave() {
      _setDirty(false);
      clearTimeout(_autoSaveTimer);
      _startCooldown();
    }

    /* ── 이탈 확인 ──────────────────────────────────── */
    let _leaveTarget = null;

    function _initLeaveConfirm() {
      if (!_leaveOverlay) return;

      // 페이지를 벗어나는 모든 <a> 클릭을 가로채 예쁜 모달로 처리
      on(document, 'click', e => {
        const anchor = e.target.closest('a[href]');
        if (!anchor || anchor.target === '_blank') return;
        const rawHref = anchor.getAttribute('href');
        if (!rawHref || rawHref.startsWith('javascript:') || rawHref === '#' || rawHref.startsWith('#')) return;
        try {
          const url = new URL(anchor.href, location.href);
          if (url.origin !== location.origin) return; // 외부 링크
          if (url.pathname === location.pathname && url.search === location.search) return; // 같은 페이지
        } catch (_) { return; }
        if (!_dirty) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        _leaveTarget = anchor.href;
        _leaveOverlay.style.display = 'flex';
      }, true);

      const cancelBtn  = _leaveOverlay.querySelector('#wu-leave-cancel');
      const nosaveBtn  = _leaveOverlay.querySelector('#wu-leave-nosave');
      const saveBtn    = _leaveOverlay.querySelector('#wu-leave-save');

      cancelBtn.addEventListener('click', () => { _leaveOverlay.style.display = 'none'; });
      on(document, 'keydown', e => {
        if (e.key === 'Escape' && _leaveOverlay.style.display !== 'none') {
          _leaveOverlay.style.display = 'none';
        }
      });
      nosaveBtn.addEventListener('click', () => {
        _setDirty(false);
        _releaseLock();
        if (_leaveTarget) location.href = _leaveTarget;
      });
      saveBtn.addEventListener('click', () => {
        _leaveOverlay.style.display = 'none';
        const md = _editor ? _injectImgStyles(_getMarkdown()) : '';
        if (hooks.onSave) hooks.onSave(md, { auto: false, leaveTarget: _leaveTarget || '/' });
      });
    }

    /* ── 자동완성 ───────────────────────────────────── */
    function _showAc(items, inputEl, cb) {
      if (!_acDd) return;
      _acCb = cb;
      if (!items.length) { _acDd.classList.add('hidden'); return; }
      const rect = inputEl.getBoundingClientRect();
      _acDd.style.left   = rect.left + 'px';
      _acDd.style.top    = (rect.bottom + 2) + 'px';
      _acDd.style.width  = rect.width + 'px';
      _acDd.innerHTML = items.map(v =>
        `<li onmousedown="event.preventDefault()">${esc(v)}</li>`
      ).join('');
      _acDd.classList.remove('hidden');
    }

    if (_acDd) {
      _acDd.addEventListener('mousedown', e => {
        const li = e.target.closest('li');
        if (!li) return;
        e.preventDefault();
        if (_acCb) _acCb(li.textContent);
        _acDd.classList.add('hidden');
      });
    }

    const ac = {
      showProjects: (inputEl, onSelect) => {
        const v = inputEl.value.trim();
        _showAc(
          v ? _acProjects.filter(p => p.toLowerCase().includes(v.toLowerCase())) : _acProjects,
          inputEl, onSelect
        );
      },
      showMembers: (inputEl, onSelect) => {
        const v = inputEl.value.trim();
        _showAc(
          v ? _acMembers.filter(m => m.toLowerCase().includes(v.toLowerCase())) : _acMembers,
          inputEl, onSelect
        );
      },
      hide: () => { if (_acDd) _acDd.classList.add('hidden'); },
    };

    /* ── TOC ────────────────────────────────────────── */
    let _tocObserver   = null;
    let _tocActiveObs  = null;
    let _tocHeadingRefs = [];

    function _findHeadingsRoot() {
      if (!containerEl) return null;
      return containerEl.querySelector('.ProseMirror')
          || containerEl;
    }

    function _findScrollParent(el) {
      let node = el ? el.parentElement : null;
      while (node && node !== document.body) {
        const oy = getComputedStyle(node).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight) return node;
        node = node.parentElement;
      }
      return null;
    }

    function _buildToc() {
      const tc = feat.toc;
      if (!tc) return;
      const listEl  = document.getElementById('toc-list');
      const emptyEl = document.getElementById('toc-empty');
      const root    = _findHeadingsRoot();
      if (!root || !listEl) return;

      const headings = Array.from(root.querySelectorAll('h1, h2, h3, h4, h5, h6'));
      _tocHeadingRefs = headings;

      if (!headings.length) {
        listEl.innerHTML = '';
        if (emptyEl) emptyEl.classList.remove('hidden');
        return;
      }
      if (emptyEl) emptyEl.classList.add('hidden');
      listEl.innerHTML = headings.map((h, idx) =>
        `<a class="toc-item toc-lv${parseInt(h.tagName.slice(1))}" data-idx="${idx}">${esc(h.textContent)}</a>`
      ).join('');
      listEl.querySelectorAll('.toc-item').forEach((a, idx) => {
        a.addEventListener('click', e => { e.preventDefault(); _tocJumpTo(idx); });
      });
      _rebindActiveObserver(headings);
    }

    function _tocJumpTo(idx) {
      let target = _tocHeadingRefs[idx];
      if (!target || !target.isConnected) {
        _buildToc();
        target = _tocHeadingRefs[idx];
        if (!target || !target.isConnected) return;
      }
      const scrollRoot = _findScrollParent(target);
      if (scrollRoot) {
        const rr = scrollRoot.getBoundingClientRect();
        const tr = target.getBoundingClientRect();
        scrollRoot.scrollBy({ top: tr.top - rr.top - 16, behavior: 'smooth' });
      } else {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    function _rebindActiveObserver(headingEls) {
      if (_tocActiveObs) { _tocActiveObs.disconnect(); _tocActiveObs = null; }
      const scrollRoot = headingEls.length ? _findScrollParent(headingEls[0]) : null;
      if (!scrollRoot) return;
      _tocActiveObs = new IntersectionObserver(entries => {
        entries.forEach(ent => {
          if (!ent.isIntersecting) return;
          const idx = _tocHeadingRefs.indexOf(ent.target);
          if (idx < 0) return;
          document.querySelectorAll('.toc-item').forEach((a, i) =>
            a.classList.toggle('active', i === idx));
        });
      }, { root: scrollRoot, rootMargin: '0px 0px -70% 0px', threshold: 0 });
      headingEls.forEach(h => _tocActiveObs.observe(h));
    }

    function _startTocWatch() {
      if (_tocObserver) return;
      const root = _findHeadingsRoot();
      if (!root) return;
      let debounce;
      _tocObserver = new MutationObserver(() => {
        clearTimeout(debounce);
        debounce = setTimeout(_buildToc, 200);
      });
      _tocObserver.observe(root, { childList: true, subtree: true, characterData: true });
    }

    function _toggleToc() {
      const tc = feat.toc;
      if (!tc) return;
      const panelEl = document.getElementById('toc-panel');
      const btnEl   = tc.toggleBtnSelector ? sel(tc.toggleBtnSelector) : document.getElementById('btn-toc-toggle');
      if (!panelEl) return;
      const open = panelEl.classList.contains('hidden');
      panelEl.classList.toggle('hidden', !open);
      if (bodyEl) bodyEl.classList.toggle('toc-open', open);
      if (btnEl)  btnEl.classList.toggle('active', open);
      localStorage.setItem(tc.storageKey || 'wu_toc_open', open ? '1' : '0');
      if (open) { _buildToc(); _startTocWatch(); }
    }

    function _initToc() {
      const tc = feat.toc;
      if (!tc) return;
      const key = tc.storageKey || 'wu_toc_open';
      if (localStorage.getItem(key) === '1') setTimeout(_toggleToc, 400);
    }

    let _tocRebuildTimer = null;
    function _scheduleTocRebuild() {
      if (!feat.toc) return;
      const panelEl = document.getElementById('toc-panel');
      if (!panelEl || panelEl.classList.contains('hidden')) return;
      clearTimeout(_tocRebuildTimer);
      _tocRebuildTimer = setTimeout(_buildToc, 200);
    }

    /* ── 코드블록 헤더 (fixed overlay — ProseMirror DOM 외부) ── */
    const ICON_COPY  = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    const ICON_CHECK = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    let _codeHeaderMap = new Map(); /* preEl → headerEl */
    let _codeHeaderObs = null;
    let _codeHeaderPending = false;

    function _buildCodeHeader(preEl) {
      const canEdit = !!opts.canEdit;
      const header = document.createElement('div');
      header.className = 'wu-code-header';
      header.style.cssText = 'position:fixed;z-index:100;pointer-events:none;display:none;';

      if (canEdit) {
        const langWrap = document.createElement('div');
        langWrap.className = 'wu-code-lang-wrap';
        langWrap.style.pointerEvents = 'auto';

        const select = document.createElement('select');
        select.className = 'wu-code-lang-select';
        select.innerHTML = LANG_OPTIONS.map(o =>
          `<option value="${o.value}">${o.label}</option>`
        ).join('');

        const codeEl = preEl.querySelector('code');
        const langMatch = codeEl ? codeEl.className.match(/language-(\w+)/) : null;
        const curLang = langMatch ? langMatch[1] : '';
        if (curLang && !select.querySelector(`option[value="${curLang}"]`)) {
          const extra = document.createElement('option');
          extra.value = curLang; extra.textContent = curLang;
          select.insertBefore(extra, select.firstChild);
        }
        select.value = curLang;

        select.addEventListener('mousedown', e => e.stopPropagation());
        select.addEventListener('change', () => {
          if (!_editor) return;
          const lang = select.value || null;
          const { state, view } = _editor;
          state.doc.descendants((node, pos) => {
            if (node.type.name !== 'codeBlock') return;
            const dom = view.nodeDOM(pos);
            if (dom !== preEl) return;
            view.dispatch(state.tr.setNodeMarkup(pos, null, { ...node.attrs, language: lang }));
            return false;
          });
        });

        langWrap.appendChild(select);
        header.appendChild(langWrap);
      }

      const copyBtn = document.createElement('button');
      copyBtn.type = 'button';
      copyBtn.className = 'wu-code-copy-btn';
      copyBtn.innerHTML = ICON_COPY;
      copyBtn.title = '코드 복사';
      copyBtn.style.pointerEvents = 'auto';
      copyBtn.addEventListener('mousedown', e => e.preventDefault());
      copyBtn.addEventListener('click', () => {
        const codeEl = preEl.querySelector('code') || preEl;
        const text = codeEl.textContent;
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
          .catch(() => {
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta); ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
          });
        copyBtn.innerHTML = ICON_CHECK;
        copyBtn.classList.add('copied');
        setTimeout(() => { copyBtn.innerHTML = ICON_COPY; copyBtn.classList.remove('copied'); }, 1500);
      });
      header.appendChild(copyBtn);
      document.body.appendChild(header);
      return header;
    }

    function _syncCodeHeaders() {
      _codeHeaderPending = false;
      const pmEl = _getProseMirrorEl();
      if (!pmEl) return;

      const pres = Array.from(pmEl.querySelectorAll('pre'));
      const preSet = new Set(pres);

      /* 삭제된 pre의 헤더 제거 */
      for (const [preEl, headerEl] of _codeHeaderMap) {
        if (!preSet.has(preEl)) {
          headerEl.remove();
          _codeHeaderMap.delete(preEl);
        }
      }

      /* 새 pre 헤더 생성 + 모든 헤더 위치 갱신 */
      for (const pre of pres) {
        if (!_codeHeaderMap.has(pre)) {
          _codeHeaderMap.set(pre, _buildCodeHeader(pre));
        }
        const header = _codeHeaderMap.get(pre);
        const rect = pre.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          header.style.top   = (rect.top + 8) + 'px';
          header.style.right = (window.innerWidth - rect.right + 10) + 'px';
          header.style.display = 'flex';
        } else {
          header.style.display = 'none';
        }
      }
    }

    function _scheduleCodeHeaderSync() {
      if (!_codeHeaderPending) {
        _codeHeaderPending = true;
        requestAnimationFrame(_syncCodeHeaders);
      }
    }

    function _initCodeHeaders() {
      _scheduleCodeHeaderSync();
      const pmEl = _getProseMirrorEl() || containerEl;
      if (!pmEl) return;
      _codeHeaderObs = new MutationObserver(_scheduleCodeHeaderSync);
      _codeHeaderObs.observe(pmEl, { childList: true, subtree: true });
      on(window, 'scroll', _scheduleCodeHeaderSync, { capture: true, passive: true });
      on(window, 'resize', _scheduleCodeHeaderSync);
    }

    function _destroyCodeHeaders() {
      if (_codeHeaderObs) { _codeHeaderObs.disconnect(); _codeHeaderObs = null; }
      for (const [, headerEl] of _codeHeaderMap) headerEl.remove();
      _codeHeaderMap.clear();
    }

    /* ── 슬래시 커맨드 ──────────────────────────────── */
    function _renderSlashItems(items) {
      if (!_slashMenuEl) return;
      _slashMenuEl.innerHTML = items.map((it, i) =>
        `<div class="wu-slash-item${i === 0 ? ' selected' : ''}" data-idx="${i}">
          <span class="wu-slash-icon">${it.icon}</span>
          <span class="wu-slash-label">${it.label}</span>
        </div>`
      ).join('');
    }

    function _positionSlashMenu() {
      if (!_editor || !_slashMenuEl) return;
      const coords = _editor.view.coordsAtPos(_editor.state.selection.from);
      const mh = _slashMenuEl.offsetHeight || 200;
      const top = (window.innerHeight - coords.bottom > mh)
        ? coords.bottom + 4
        : coords.top - mh - 4;
      _slashMenuEl.style.top  = (top + window.scrollY) + 'px';
      _slashMenuEl.style.left = Math.min(coords.left, window.innerWidth - (_slashMenuEl.offsetWidth || 200) - 8) + 'px';
    }

    function _applySlashItem(item) {
      if (!_editor) return;
      const to = _editor.state.selection.from;
      const from = _slashStart;
      _hideSlashMenu();
      _editor.chain().focus().deleteRange({ from, to }).run();
      item.cmd(_editor);
    }

    function _hideSlashMenu() {
      if (_slashMenuEl) _slashMenuEl.style.display = 'none';
      _slashActive = false;
      _slashStart  = -1;
      _slashQuery  = '';
      _slashFiltered = [];
    }

    function _updateSlashSelUi() {
      if (!_slashMenuEl) return;
      _slashMenuEl.querySelectorAll('.wu-slash-item').forEach((el, i) => {
        el.classList.toggle('selected', i === _slashSelIdx);
        if (i === _slashSelIdx) el.scrollIntoView({ block: 'nearest' });
      });
    }

    function _checkSlashCommand() {
      if (!_editor || !_slashMenuEl) return;
      const { $from } = _editor.state.selection;
      const textBefore = $from.parent.textContent.slice(0, $from.parentOffset);
      const m = textBefore.match(/(^|\s)(\/[^\s]*)$/);
      if (!m) { if (_slashActive) _hideSlashMenu(); return; }
      const slashText = m[2];
      _slashStart  = $from.pos - slashText.length;
      _slashActive = true;
      _slashQuery  = slashText.slice(1);
      const filtered = _matchSlash(_slashQuery);
      _slashFiltered = filtered;
      if (!filtered.length) { _slashMenuEl.style.display = 'none'; return; }
      _slashSelIdx = 0;
      _renderSlashItems(filtered);
      _slashMenuEl.style.display = 'block';
      _positionSlashMenu();
    }

    function _initSlashMenu() {
      if (!_slashMenuEl) return;
      _slashMenuEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const item = e.target.closest('.wu-slash-item');
        if (!item) return;
        const idx = +item.dataset.idx;
        if (_slashFiltered[idx]) _applySlashItem(_slashFiltered[idx]);
      });
      _slashMenuEl.addEventListener('mousemove', e => {
        const item = e.target.closest('.wu-slash-item');
        if (!item) return;
        _slashSelIdx = +item.dataset.idx;
        _updateSlashSelUi();
      });
      on(document, 'keydown', e => {
        if (!_slashActive || !_slashFiltered.length) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault(); e.stopPropagation();
          _slashSelIdx = (_slashSelIdx + 1) % _slashFiltered.length;
          _updateSlashSelUi();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault(); e.stopPropagation();
          _slashSelIdx = (_slashSelIdx - 1 + _slashFiltered.length) % _slashFiltered.length;
          _updateSlashSelUi();
        } else if (e.key === 'Enter') {
          e.preventDefault(); e.stopPropagation();
          if (_slashFiltered[_slashSelIdx]) _applySlashItem(_slashFiltered[_slashSelIdx]);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          _hideSlashMenu();
        }
      }, true);
    }

    /* ── 헤딩 드롭다운 ───────────────────────────────── */
    function _showHeadingDrop(btnEl) {
      if (!_headingDropEl) return;
      if (_headingDropEl.style.display !== 'none') {
        _headingDropEl.style.display = 'none';
        return;
      }
      const curLevel = [1, 2, 3].find(l => _editor && _editor.isActive('heading', { level: l })) || 0;
      _headingDropEl.querySelectorAll('[data-level]').forEach(btn => {
        btn.classList.toggle('is-active', +btn.dataset.level === curLevel);
      });
      const r = btnEl.getBoundingClientRect();
      _headingDropEl.style.top  = (r.bottom + 4) + 'px';
      _headingDropEl.style.left = r.left + 'px';
      _headingDropEl.style.display = 'block';
    }

    function _initHeadingDrop() {
      if (!_headingDropEl) return;
      _headingDropEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const btn = e.target.closest('[data-level]');
        if (!btn || !_editor) return;
        const level = +btn.dataset.level;
        if (level === 0) _editor.chain().focus().setParagraph().run();
        else _editor.chain().focus().setHeading({ level }).run();
        _headingDropEl.style.display = 'none';
      });
    }

    /* ── 테이블 컨텍스트 메뉴 ──────────────────────── */
    function _clearCellFocus() {
      if (!containerEl) return;
      containerEl.querySelectorAll('td.wu-cell-focus, th.wu-cell-focus')
        .forEach(el => el.classList.remove('wu-cell-focus'));
    }

    /* ProseMirror 문서 트리를 순회해 현재 커서가 속한 셀 DOM 반환 */
    function _getFocusedCellDom() {
      if (!_editor) return null;
      const { view } = _editor;
      const { $from } = view.state.selection;
      for (let d = $from.depth; d > 0; d--) {
        const name = $from.node(d).type.name;
        if (name === 'tableCell' || name === 'tableHeader') {
          const pos = $from.before(d);
          return view.nodeDOM(pos) || null;
        }
      }
      return null;
    }

    function _updateTableMenu() {
      if (!_tableMenuEl || !_editor) return;
      if (_isDragging) return;
      if (!_editor.isActive('table')) {
        _tableMenuEl.style.display = 'none';
        return;
      }

      /* 컨텍스트 메뉴 위치 — 테이블 위에 고정 */
      const tableEl = containerEl.querySelector('table');
      if (!tableEl) { _tableMenuEl.style.display = 'none'; return; }
      const tr = tableEl.getBoundingClientRect();
      _tableMenuEl.style.display = 'flex';
      const mw = _tableMenuEl.offsetWidth;
      _tableMenuEl.style.top  = (tr.top + window.scrollY - _tableMenuEl.offsetHeight - 6) + 'px';
      _tableMenuEl.style.left = Math.min(tr.left, window.innerWidth - mw - 8) + 'px';
    }

    function _initTableMenu() {
      if (!_tableMenuEl) return;
      _tableMenuEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const btn = e.target.closest('[data-cmd]');
        if (!btn || !_editor) return;
        const chain = _editor.chain().focus();
        switch (btn.dataset.cmd) {
          case 'addRowBefore':    chain.addRowBefore().run(); break;
          case 'addRowAfter':     chain.addRowAfter().run(); break;
          case 'deleteRow':       chain.deleteRow().run(); break;
          case 'addColumnBefore': chain.addColumnBefore().run(); break;
          case 'addColumnAfter':  chain.addColumnAfter().run(); break;
          case 'deleteColumn':    chain.deleteColumn().run(); break;
          case 'mergeOrSplit':    chain.mergeOrSplit().run(); break;
          case 'deleteTable':     chain.deleteTable().run(); break;
        }
        setTimeout(_updateTableMenu, 50);
      });
      /* 버블 페이즈(capture:false)로 등록 — ProseMirror 드래그 감지 이후 실행 */
      on(document, 'mousedown', e => {
        /* 에디터 내부 클릭이면 드래그 시작으로 간주 — onTransaction DOM 수정 억제 */
        const pmEl = _getProseMirrorEl();
        if (pmEl && pmEl.contains(e.target)) _isDragging = true;

        if (_tableMenuEl && !_tableMenuEl.contains(e.target)) {
          _tableMenuEl.style.display = 'none';
        }
        if (_tablePickerEl && !_tablePickerEl.contains(e.target)) {
          _tablePickerEl.style.display = 'none';
          _tablePickerOpen = false;
          _pickerReset();
        }
        if (_slashMenuEl && !_slashMenuEl.contains(e.target)) _hideSlashMenu();
        if (_headingDropEl && !_headingDropEl.contains(e.target)) _headingDropEl.style.display = 'none';
      }, false);

      /* mouseup에서 드래그 종료 — 이 시점에 한 번만 메뉴·포커스 갱신 */
      on(document, 'mouseup', () => {
        if (_isDragging) {
          _isDragging = false;
          setTimeout(_updateTableMenu, 50);
        }
      }, false);
    }

    /* ── syntax highlight 적용 ──────────────────────── */
    function _applyHighlight() {
      if (typeof window.hljs === 'undefined' || !containerEl) return;
      containerEl.querySelectorAll('pre code').forEach(el => {
        if (!el.dataset.highlighted) window.hljs.highlightElement(el);
      });
    }

    /* ── Markdown 읽기 ──────────────────────────────── */
    function _getMarkdown() {
      if (!_editor) return '';
      try {
        return _editor.storage.markdown.getMarkdown();
      } catch (e) {
        return '';
      }
    }

    /* ── 툴바 구성 ──────────────────────────────────── */
    function _buildToolbar(wrapEl, toolbarItems) {
      const tbEl = document.createElement('div');
      tbEl.className = 'wu-toolbar';

      // toolbarItems가 없으면 기본 TOOLBAR_DEFS 사용
      const groups = (toolbarItems && toolbarItems.length) ? toolbarItems : TOOLBAR_DEFS.map(d => d.group);

      groups.forEach((group, gi) => {
        if (gi > 0) {
          const sep = document.createElement('span');
          sep.className = 'wu-toolbar-sep';
          tbEl.appendChild(sep);
        }
        group.forEach(cmd => {
          const def = TOOLBAR_LABELS[cmd];
          if (!def) return;
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'wu-toolbar-btn';
          btn.title = def.title;
          btn.innerHTML = def.icon;
          btn.dataset.cmd = cmd;
          tbEl.appendChild(btn);
        });
      });

      wrapEl.appendChild(tbEl);
      return tbEl;
    }

    function _bindToolbarEvents(tbEl) {
      tbEl.addEventListener('mousedown', e => {
        e.preventDefault(); // 포커스 뺏지 않음
        const btn = e.target.closest('[data-cmd]');
        if (!btn || !_editor) return;
        const cmd = btn.dataset.cmd;
        const chain = _editor.chain().focus();

        switch (cmd) {
          case 'heading':
            e.stopPropagation();
            _showHeadingDrop(btn);
            return;
          case 'bold':      chain.toggleBold().run(); break;
          case 'italic':    chain.toggleItalic().run(); break;
          case 'strike':    chain.toggleStrike().run(); break;
          case 'highlight': chain.toggleHighlight().run(); break;
          case 'hr':        chain.setHorizontalRule().run(); break;
          case 'quote':     chain.toggleBlockquote().run(); break;
          case 'ul':        chain.toggleBulletList().run(); break;
          case 'ol':        chain.toggleOrderedList().run(); break;
          case 'task':      chain.toggleTaskList().run(); break;
          case 'table':
            e.stopPropagation(); // document mousedown이 피커를 즉시 닫지 않도록 차단
            _showTablePicker(btn);
            return; /* chain.run() 생략 — 피커가 처리 */
          case 'link':      _promptLink(); break;
          case 'image':     _promptImage(); break;
          case 'code':      chain.toggleCode().run(); break;
          case 'codeblock': chain.toggleCodeBlock().run(); break;
        }
        _updateToolbarState(tbEl);
      });
    }

    function _updateToolbarState(tbEl) {
      if (!_editor || !tbEl) return;
      tbEl.querySelectorAll('[data-cmd]').forEach(btn => {
        const cmd = btn.dataset.cmd;
        let active = false;
        switch (cmd) {
          case 'bold':      active = _editor.isActive('bold'); break;
          case 'italic':    active = _editor.isActive('italic'); break;
          case 'strike':    active = _editor.isActive('strike'); break;
          case 'highlight': active = _editor.isActive('highlight'); break;
          case 'quote':     active = _editor.isActive('blockquote'); break;
          case 'ul':        active = _editor.isActive('bulletList'); break;
          case 'ol':        active = _editor.isActive('orderedList'); break;
          case 'task':      active = _editor.isActive('taskList'); break;
          case 'code':      active = _editor.isActive('code'); break;
          case 'codeblock': active = _editor.isActive('codeBlock'); break;
          case 'heading':
            active = _editor.isActive('heading'); break;
          case 'link':      active = _editor.isActive('link'); break;
        }
        btn.classList.toggle('is-active', active);
      });
    }

    function _initLinkModal() {
      if (!_linkOverlay) return;
      const input      = _linkOverlay.querySelector('#wu-link-input');
      const removeBtn  = _linkOverlay.querySelector('#wu-link-remove');
      const cancelBtn  = _linkOverlay.querySelector('#wu-link-cancel');
      const confirmBtn = _linkOverlay.querySelector('#wu-link-confirm');

      function _apply() {
        _linkOverlay.style.display = 'none';
        const url = input.value.trim();
        if (url) {
          _editor.chain().focus().setLink({ href: url, target: '_blank' }).run();
        } else {
          _editor.chain().focus().unsetLink().run();
        }
      }

      removeBtn.addEventListener('click', () => {
        _linkOverlay.style.display = 'none';
        _editor.chain().focus().unsetLink().run();
      });
      cancelBtn.addEventListener('click', () => { _linkOverlay.style.display = 'none'; });
      confirmBtn.addEventListener('click', _apply);
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter')  { e.preventDefault(); _apply(); }
        if (e.key === 'Escape') { _linkOverlay.style.display = 'none'; }
      });
      _linkOverlay.addEventListener('click', e => {
        if (e.target === _linkOverlay) _linkOverlay.style.display = 'none';
      });
    }

    function _promptLink() {
      if (!_editor || !_linkOverlay) return;
      const prev = _editor.getAttributes('link').href || '';
      const input     = _linkOverlay.querySelector('#wu-link-input');
      const removeBtn = _linkOverlay.querySelector('#wu-link-remove');
      input.value = prev;
      removeBtn.style.display = prev ? '' : 'none';
      _linkOverlay.style.display = 'flex';
      setTimeout(() => input.focus(), 30);
    }

    function _promptImage() {
      if (!_editor) return;
      const url = window.prompt('이미지 URL을 입력하세요:');
      if (!url || !url.trim()) return;
      _editor.chain().focus().setImage({ src: url.trim() }).run();
    }

    /* ── 이미지 붙여넣기/드롭 업로드 ───────────────── */
    function _initImageUpload(pmEl) {
      if (!feat.imageUpload || !feat.imageUpload.endpoint) return;
      const endpoint = feat.imageUpload.endpoint;

      async function _uploadAndInsert(blob) {
        const form = new FormData();
        form.append('file', blob, blob.name || 'image.png');
        try {
          const res = await fetch(endpoint, { method: 'POST', body: form });
          if (!res.ok) throw new Error('upload failed');
          const { url } = await res.json();
          if (_editor) _editor.chain().focus().setImage({ src: url }).run();
        } catch {
          if (typeof wuToast !== 'undefined') wuToast.error('이미지 업로드에 실패했습니다.');
        }
      }

      pmEl.addEventListener('paste', async (e) => {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (const item of items) {
          if (item.type.startsWith('image/')) {
            e.preventDefault();
            e.stopImmediatePropagation();
            const blob = item.getAsFile();
            if (blob) await _uploadAndInsert(blob);
            return;
          }
        }
      }, true);

      pmEl.addEventListener('drop', async (e) => {
        const files = e.dataTransfer && e.dataTransfer.files;
        if (!files || !files.length) return;
        for (const file of files) {
          if (file.type.startsWith('image/')) {
            e.preventDefault();
            await _uploadAndInsert(file);
            return;
          }
        }
      });
    }

    /* ── Tiptap 에디터 초기화 ───────────────────────── */
    function _initEditor() {
      if (!containerEl) return;
      if (typeof TiptapBundle === 'undefined') {
        console.error('[WUEditor] TiptapBundle이 로드되지 않았습니다. tiptap-bundle.min.js를 먼저 로드하세요.');
        return;
      }

      const {
        Editor,
        StarterKit,
        CodeBlock,
        Table,
        TableRow,
        TableHeader,
        TableCell,
        TaskList,
        TaskItem,
        Link,
        Image,
        Markdown,
        Paragraph,
        Highlight,
        markdownItMark,
        Superscript,
      } = TiptapBundle;

      const canEdit = !!opts.canEdit;

      if (!canEdit) {
        /* 뷰어 모드 — 에디터 영역에 바로 렌더 */
        _editor = new Editor({
          element: containerEl,
          extensions: _buildExtensions({ StarterKit, CodeBlock, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark, Superscript }),
          content: _preprocessViewerFootnotes(opts.initialMarkdown || ''),
          editable: false,
          injectCSS: false,
        });
        if (hooks.onReady) hooks.onReady(_editor);
        setTimeout(_applyHighlight, 150);
        setTimeout(_initCodeHeaders, 100);
        setTimeout(() => _fixFootnoteAnchors(containerEl), 200);
        return;
      }

      /* 편집 모드 */
      // 래퍼 구성: 툴바 + 에디터
      const wrapEl = document.createElement('div');
      wrapEl.className = 'wu-editor-wrap';
      containerEl.appendChild(wrapEl);

      const tbEl = _buildToolbar(wrapEl, opts.toolbarItems);

      const edEl = document.createElement('div');
      edEl.className = 'wu-editor-content';
      wrapEl.appendChild(edEl);

      _editor = new Editor({
        element: edEl,
        extensions: _buildExtensions({ StarterKit, CodeBlock, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark }),
        content: opts.initialMarkdown || '',
        editable: true,
        injectCSS: false,
        onUpdate: () => {
          _setDirty(true);
          if (hooks.onChange) hooks.onChange();
          _scheduleAutosave();
          _scheduleIdleTimer();
          _scheduleTocRebuild();
          _updateToolbarState(tbEl);
          _checkSlashCommand();
          _scheduleCodeHeaderSync();
        },
        onSelectionUpdate: () => {
          _updateToolbarState(tbEl);
          _updateTableMenu();
          _checkSlashCommand();
        },
        onTransaction: () => {
          _updateToolbarState(tbEl);
          _updateTableMenu();
        },
      });

      _bindToolbarEvents(tbEl);

      const pmEl = edEl.querySelector('.ProseMirror');
      if (pmEl && feat.imageUpload && feat.imageUpload.endpoint) {
        _initImageUpload(pmEl);
      }

      if (feat.imageResize !== false) {
        setTimeout(() => _initImgResize(), 300);
      }

      _initTableMenu();
      _initLinkModal();
      _initSlashMenu();
      _initHeadingDrop();
      setTimeout(_initCodeHeaders, 100);

      if (hooks.onReady) hooks.onReady(_editor);
      setTimeout(_applyHighlight, 150);
    }

    function _buildExtensions({ StarterKit, CodeBlock, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark, Superscript }) {
      // @tiptap/extension-link은 markdown.serialize가 없어 tiptap-markdown이 <a href> HTML로
      // 직렬화한다. 이를 [text](href) 마크다운 형식으로 고정해 eid: round-trip을 보장한다.
      const LinkMd = Link.extend({
        addStorage() {
          const parent = this.parent?.() ?? {};
          return {
            ...parent,
            markdown: {
              serialize: {
                open() { return '['; },
                close(_state, mark) {
                  const href = (mark.attrs.href || '').replace(/[\(\)"]/g, '\\$&');
                  const title = mark.attrs.title
                    ? ` "${mark.attrs.title.replace(/"/g, '\\"')}"`
                    : '';
                  return `](${href}${title})`;
                },
              },
              parse: {},
            },
          };
        },
      });

      // defaultMarkdownSerializer.nodes.image는 closeBlock을 호출하지 않아
      // 이미지 다음 단락이 같은 줄에 붙는 문제가 있다. closeBlock을 추가해 수정.
      const ImageMd = Image.extend({
        addStorage() {
          return {
            markdown: {
              serialize(state, node) {
                const title = node.attrs.title ? ` "${node.attrs.title}"` : '';
                state.write("![" + state.esc(node.attrs.alt || "") + "](" +
                  state.esc(node.attrs.src) + title + ")");
                state.closeBlock(node);
              },
              parse: {},
            },
          };
        },
      });

      // 빈 단락을 <p></p> HTML 블록으로 직렬화해 CommonMark round-trip 시 보존한다.
      // prosemirror-markdown의 기본 paragraph serializer는 빈 단락에서 아무것도 쓰지 않아
      // 연속 빈 단락 전체가 \n\n 하나로 합쳐진다.
      const ParagraphMd = Paragraph.extend({
        addStorage() {
          return {
            markdown: {
              serialize(state, node) {
                if (node.childCount === 0) {
                  state.write('<p></p>');
                } else {
                  state.renderInline(node);
                }
                state.closeBlock(node);
              },
              parse: {},
            },
          };
        },
      });

      const HighlightMd = Highlight.configure({ multicolor: false }).extend({
        addStorage() {
          return {
            markdown: {
              serialize: { open: '==', close: '==', mixable: true, expelEnclosingWhitespace: true },
              parse: { setup(md) { md.use(markdownItMark); } },
            },
          };
        },
      });

      return [
        StarterKit.configure({ link: false, paragraph: false }),
        ParagraphMd,
        HighlightMd,
        Superscript,
        Table.configure({ resizable: true }),
        TableRow,
        TableHeader,
        TableCell,
        TaskList,
        TaskItem.configure({ nested: true }),
        LinkMd.configure({ openOnClick: false, autolink: true, protocols: ['eid'], validate: () => true }),
        ImageMd,
        Markdown.configure({
          html: true,          // <img style="width:..."> 등 raw HTML 허용
          tightLists: true,
          linkify: false,
          breaks: false,
        }),
      ];
    }

    /* ── 글로벌 이벤트 등록 ─────────────────────────── */
    function _bindGlobalEvents() {
      on(window, 'beforeunload', _onBeforeUnload);

      if (feat.shortcutSave) {
        on(document, 'keydown', e => {
          if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            e.stopPropagation();
            if (opts.canEdit) _triggerSave(false);
          }
        }, true);
      }

      _scheduleIdleTimer();
    }

    /* ── 초기화 실행 ────────────────────────────────── */
    _initEditor();
    _bindGlobalEvents();
    if (feat.editLock && feat.editLock.endpoint) _acquireLock();
    if (feat.leaveConfirm) _initLeaveConfirm();
    _initToc();

    /* ── 공개 인스턴스 API ──────────────────────────── */
    return {
      get editor()   { return _editor; },
      get dirty()    { return _dirty; },
      get cooldown() { return _cooldown; },
      setDirty:     _setDirty,
      save:         () => _triggerSave(false),
      afterSave:    _afterSave,
      getMarkdown:  () => _editor ? _injectImgStyles(_getMarkdown()) : '',
      setContent:   (md) => {
        if (_editor) {
          _editor.commands.setContent(md || '');
          _imgWidthMap = {};
          _parseInitialWidths(md || '');
          setTimeout(_applyHighlight, 150);
        }
      },
      applyHighlight: _applyHighlight,
      toggleToc:    _toggleToc,
      rebuildToc:   _buildToc,
      releaseLock:  _releaseLock,
      setLeaveMsg:  (title, desc) => {
        if (_leaveMsgTitle) _leaveMsgTitle.textContent = title || '';
        if (_leaveMsgDesc)  _leaveMsgDesc.textContent  = desc  || '';
      },
      showLeaveModal: (target) => {
        if (!_leaveOverlay) return;
        _leaveTarget = target || '/';
        _leaveOverlay.style.display = 'flex';
      },
      ac,
      destroy: () => {
        _listeners.forEach(({ target, type, fn, capture }) =>
          target.removeEventListener(type, fn, capture));
        _listeners.length = 0;
        clearInterval(_lockHeartbeat);
        clearInterval(_cooldownTimer);
        clearTimeout(_idleTimer);
        clearTimeout(_autoSaveTimer);
        clearTimeout(_tocRebuildTimer);
        if (_tocObserver)    { _tocObserver.disconnect();    _tocObserver    = null; }
        if (_tocActiveObs)   { _tocActiveObs.disconnect();   _tocActiveObs   = null; }
        if (_imgResizeObs)   { _imgResizeObs.disconnect();   _imgResizeObs   = null; }
        _destroyCodeHeaders();
        if (_editor && typeof _editor.destroy === 'function') _editor.destroy();
        _editor = null;
        if (_tablePickerEl && _tablePickerEl.parentNode) _tablePickerEl.parentNode.removeChild(_tablePickerEl);
        if (_tableMenuEl  && _tableMenuEl.parentNode)  _tableMenuEl.parentNode.removeChild(_tableMenuEl);
        if (_tbEl       && _tbEl.parentNode)       _tbEl.parentNode.removeChild(_tbEl);
        if (_leaveOverlay && _leaveOverlay.parentNode) _leaveOverlay.parentNode.removeChild(_leaveOverlay);
        if (_linkOverlay   && _linkOverlay.parentNode)   _linkOverlay.parentNode.removeChild(_linkOverlay);
        if (_slashMenuEl   && _slashMenuEl.parentNode)   _slashMenuEl.parentNode.removeChild(_slashMenuEl);
        if (_headingDropEl && _headingDropEl.parentNode) _headingDropEl.parentNode.removeChild(_headingDropEl);
        if (_acDd && _acDd.parentNode)             _acDd.parentNode.removeChild(_acDd);
        // 래퍼 제거
        const wrapEl = containerEl && containerEl.querySelector('.wu-editor-wrap');
        if (wrapEl && wrapEl.parentNode) wrapEl.parentNode.removeChild(wrapEl);
      },
    };
  }

  /* ── 하위 호환 renderer 스텁 ──────────────────────────────────────────
     home.html 의 toastui.Editor.factory({ customHTMLRenderer: WUEditor?.renderer })
     는 TUI Editor가 있을 때만 실행되며, undefined를 넘기면 TUI 기본 렌더러가 사용됨.
     Tiptap 기반으로 전환 후 별도 커스텀 렌더러가 없으므로 undefined 전달이 안전함. */
  global.WUEditor = { create, renderer: undefined };
})(window);
