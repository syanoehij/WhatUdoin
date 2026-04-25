/* wu-editor.js — WUEditor 공용 편집기 모듈 */
/* 사용법: const inst = WUEditor.create(options)  →  inst.save / inst.destroy 등 */
(function (global) {
  'use strict';

  /* ── 헬퍼 ─────────────────────────────────────── */
  function esc(s) {
    return String(s ?? '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function sel(s) {
    return typeof s === 'string' ? document.querySelector(s) : s;
  }

  /* ── 공용 HTML 렌더러 (링크 새창 + 코드블록 hljs 클래스) ── */
  const WU_HTML_RENDERER = {
    link(node, { entering }) {
      if (entering) {
        const attrs = { href: node.destination || '' };
        if (node.title) attrs.title = node.title;
        attrs.target = '_blank';
        attrs.rel = 'noopener noreferrer';
        return { type: 'openTag', tagName: 'a', attributes: attrs };
      }
      return { type: 'closeTag', tagName: 'a' };
    },
    codeBlock(node) {
      const lang = ((node.info || '').split(/\s/)[0] || '').toLowerCase();
      const cls  = lang ? `hljs language-${lang}` : 'hljs';
      const src  = (node.literal || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return [
        { type: 'openTag', tagName: 'pre', selfClose: false },
        { type: 'openTag', tagName: 'code', attributes: { class: cls }, selfClose: false },
        { type: 'html', content: src },
        { type: 'closeTag', tagName: 'code' },
        { type: 'closeTag', tagName: 'pre' },
      ];
    },
  };

  /* ── 팩토리 ────────────────────────────────────── */
  function create(opts) {
    opts = opts || {};
    const feat   = opts.features || {};
    const hooks  = opts.hooks    || {};

    /* ── 내부 상태 ── */
    let _editor       = null;
    let _dirty        = false;
    let _cooldown     = false;
    let _autoSaveTimer = null;
    let _lockHeartbeat = null;
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

    function _applyImgWidths() {
      if (!containerEl) return;
      containerEl.querySelectorAll('.toastui-editor-ww-container img').forEach(img => {
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
      const reg = /<img[^>]*src="([^"]+)"[^>]*style="[^"]*width\s*:\s*(\d+)px[^"]*"[^>]*>/g;
      let m;
      while ((m = reg.exec(md || '')) !== null) _imgWidthMap[m[1]] = parseInt(m[2]);
      if (Object.keys(_imgWidthMap).length) setTimeout(_applyImgWidths, 200);
    }

    function _initImgResize() {
      if (!_tbEl) return;
      _parseInitialWidths(opts.initialMarkdown || '');

      const wwEl = containerEl && containerEl.querySelector('.toastui-editor-ww-container');
      if (wwEl) {
        _imgResizeObs = new MutationObserver(_applyImgWidths);
        _imgResizeObs.observe(wwEl, { childList: true, subtree: true });
      }

      on(document, 'pointerdown', e => {
        const edEl = containerEl && containerEl.querySelector('.toastui-editor-ww-container');
        if (e.target.tagName === 'IMG' && edEl && edEl.contains(e.target)) {
          _activeImg = e.target;
        } else if (!_tbEl.contains(e.target)) {
          _activeImg = null;
        }
      }, true);

      on(document, 'click', e => {
        const edEl = containerEl && containerEl.querySelector('.toastui-editor-ww-container');
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

    /* 저장 전 마크다운에 img width 주입 */
    function _injectImgStyles(md) {
      for (const [src, px] of Object.entries(_imgWidthMap)) {
        const esc2 = src.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        if (px === null) {
          md = md.replace(new RegExp(`<img[^>]*src="${esc2}"[^>]*>`, 'g'),
            m => { const a = m.match(/alt="([^"]*)"/); return `![${a ? a[1] : ''}](${src})`; });
        } else {
          md = md
            .replace(new RegExp(`!\\[([^\\]]*)\\]\\(${esc2}\\)`, 'g'),
              (_, alt) => `<img src="${src}" alt="${alt}" style="width:${px}px">`)
            .replace(new RegExp(`<img[^>]*src="${esc2}"[^>]*>`, 'g'),
              m => { const a = m.match(/alt="([^"]*)"/); return `<img src="${src}" alt="${a ? a[1] : ''}" style="width:${px}px">`; });
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
      const timer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
          clearInterval(timer);
          _cooldown = false;
          if (btn) { btn.textContent = orig; btn.disabled = false; }
        } else {
          if (btn) btn.textContent = `✓ 저장됨 (${remaining}s)`;
        }
      }, 1000);
    }

    /* ── 저장 트리거 ────────────────────────────────── */
    function _triggerSave(auto) {
      const md = _editor ? _injectImgStyles(_editor.getMarkdown()) : '';
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
        const md = _editor ? _injectImgStyles(_editor.getMarkdown()) : '';
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
      // li 클릭 이벤트는 _acDd 위임으로 처리
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
      return containerEl.querySelector('.toastui-editor-ww-container')
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

      const headings = Array.from(root.querySelectorAll('h1, h2, h3'));
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
      if (_editor) _editor.resize?.();
    }

    function _initToc() {
      const tc = feat.toc;
      if (!tc) return;
      const key = tc.storageKey || 'wu_toc_open';
      if (localStorage.getItem(key) === '1') setTimeout(_toggleToc, 400);
    }

    // TUI Editor의 change 이벤트(체크박스 토글 등 포함)에서 호출되는
    // 디바운스된 TOC 재빌드 트리거. MutationObserver로 잡히지 않는
    // 변경(텍스트 편집 직후, 체크박스 토글 attribute 변경 등)을 보완한다.
    let _tocRebuildTimer = null;
    function _scheduleTocRebuild() {
      if (!feat.toc) return;
      const panelEl = document.getElementById('toc-panel');
      // 패널이 닫혀 있으면 재빌드 불필요(다음 toggle 시 _buildToc 호출됨)
      if (!panelEl || panelEl.classList.contains('hidden')) return;
      clearTimeout(_tocRebuildTimer);
      _tocRebuildTimer = setTimeout(_buildToc, 200);
    }

    /* ── syntax highlight 적용 ──────────────────────── */
    function _applyHighlight() {
      if (typeof window.hljs === 'undefined' || !containerEl) return;
      containerEl.querySelectorAll('pre code').forEach(el => {
        if (!el.dataset.highlighted) window.hljs.highlightElement(el);
      });
    }

    /* ── Toast UI Editor 초기화 ─────────────────────── */
    function _initEditor() {
      if (!containerEl) return;
      if (!opts.canEdit) {
        // 뷰어 모드
        if (typeof toastui !== 'undefined') {
          _editor = toastui.Editor.factory({
            el: containerEl,
            viewer: true,
            initialValue: opts.initialMarkdown || '',
            customHTMLSanitizer: feat.sanitizerOff ? html => html : undefined,
            customHTMLRenderer: WU_HTML_RENDERER,
          });
        }
        if (hooks.onReady) hooks.onReady(_editor);
        setTimeout(_applyHighlight, 150);
        return;
      }

      const editorOpts = {
        el: containerEl,
        height: '100%',
        initialEditType: 'wysiwyg',
        initialValue: opts.initialMarkdown || '',
        placeholder: opts.placeholder || '',
        hideModeSwitch: false,
        toolbarItems: opts.toolbarItems || [
          ['heading', 'bold', 'italic', 'strike'],
          ['hr', 'quote'],
          ['ul', 'ol', 'task'],
          ['table', 'link', 'image'],
          ['code', 'codeblock'],
        ],
        usageStatistics: false,
        events: {
          change: () => {
            _setDirty(true);
            if (hooks.onChange) hooks.onChange();
            _scheduleAutosave();
            _scheduleTocRebuild();
          },
        },
      };

      if (feat.sanitizerOff) {
        editorOpts.customHTMLSanitizer = html => html;
      }
      editorOpts.customHTMLRenderer = WU_HTML_RENDERER;

      if (feat.imageUpload && feat.imageUpload.endpoint) {
        editorOpts.hooks = {
          addImageBlobHook: async (blob, callback) => {
            const form = new FormData();
            form.append('file', blob, blob.name || 'image.png');
            try {
              const res = await fetch(feat.imageUpload.endpoint, { method: 'POST', body: form });
              if (!res.ok) throw new Error('upload failed');
              const { url } = await res.json();
              callback(url, '');
            } catch {
              wuToast.error('이미지 업로드에 실패했습니다.');
            }
          },
        };
      }

      _editor = new toastui.Editor(editorOpts);

      if (feat.imageResize !== false) setTimeout(() => _initImgResize(), 300);
      if (hooks.onReady) hooks.onReady(_editor);
      setTimeout(_applyHighlight, 150);
    }

    /* ── 글로벌 이벤트 등록 ─────────────────────────── */
    function _bindGlobalEvents() {
      on(window, 'beforeunload', _onBeforeUnload);

      if (feat.shortcutSave) {
        document.addEventListener('keydown', e => {
          if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            e.stopPropagation();
            if (opts.canEdit) _triggerSave(false);
          }
        }, true);
      }
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
      getMarkdown:  () => _editor ? _injectImgStyles(_editor.getMarkdown()) : '',
      setContent:   (md) => {
        if (_editor && typeof _editor.setMarkdown === 'function') {
          _editor.setMarkdown(md || '');
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
        clearTimeout(_autoSaveTimer);
        clearTimeout(_tocRebuildTimer);
        if (_tocObserver)    { _tocObserver.disconnect();    _tocObserver    = null; }
        if (_tocActiveObs)   { _tocActiveObs.disconnect();   _tocActiveObs   = null; }
        if (_imgResizeObs)   { _imgResizeObs.disconnect();   _imgResizeObs   = null; }
        if (_editor && typeof _editor.destroy === 'function') _editor.destroy();
        _editor = null;
        if (_tbEl       && _tbEl.parentNode)       _tbEl.parentNode.removeChild(_tbEl);
        if (_leaveOverlay && _leaveOverlay.parentNode) _leaveOverlay.parentNode.removeChild(_leaveOverlay);
        if (_acDd && _acDd.parentNode)             _acDd.parentNode.removeChild(_acDd);
      },
    };
  }

  global.WUEditor = { create, renderer: WU_HTML_RENDERER };
})(window);
