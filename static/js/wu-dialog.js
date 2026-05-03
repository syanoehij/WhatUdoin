/* =====================================================================
 * wu-dialog.js — WhatUdoin 커스텀 confirm/alert/prompt + toast 헬퍼
 *
 * 노출:
 *   window.wuDialog.confirm({title, message, okText, cancelText, danger}) → Promise<boolean>
 *   window.wuDialog.alert  ({title, message, okText})                    → Promise<void>
 *   window.wuDialog.prompt ({title, message, value, readonly, copyButton}) → Promise<string|null>
 *
 *   window.wuToast.show({message, type, duration})
 *   window.wuToast.success(msg) / error(msg) / warning(msg) / info(msg)
 *
 * 의존: base.html에 wu-dialog-overlay 마크업 + style.css의 wu-dialog/toast 스타일.
 * ESC 처리: base.html 전역 keydown 핸들러가 fallback으로 .modal-overlay에
 *           hidden 클래스를 추가한다. 별도 keydown 리스너를 등록하지 않고
 *           MutationObserver로 hidden 클래스가 추가되는 시점을 감지해
 *           pending Promise를 cancel로 resolve한다.
 * ==================================================================== */
(function () {
  'use strict';

  // ─── 큐잉 상태 ─────────────────────────────────────────────────────
  // 동시에 여러 wuDialog 호출이 들어와도 순차적으로 표시되도록 큐 관리.
  const _queue = [];
  let _busy = false;       // 현재 dialog가 화면에 떠 있는지
  let _current = null;     // { task, resolved } — 현재 처리 중인 task

  // ─── DOM 참조 (lazy) ──────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function els() {
    return {
      overlay: $('wu-dialog-overlay'),
      box:     $('wu-dialog-box'),
      title:   $('wu-dialog-title'),
      message: $('wu-dialog-message'),
      input:   $('wu-dialog-input'),
      cancel:  $('wu-dialog-cancel'),
      copy:    $('wu-dialog-copy'),
      ok:      $('wu-dialog-ok'),
      toastBox: $('wu-toast-container'),
    };
  }

  // ─── MutationObserver: overlay가 hidden되면 cancel로 자동 해소 ──
  // base.html의 ESC fallback이나 외부에서 강제로 hidden을 추가할 때를 대비.
  let _hiddenObserver = null;
  function _ensureObserver() {
    if (_hiddenObserver) return;
    const e = els();
    if (!e.overlay) return;
    _hiddenObserver = new MutationObserver(function (muts) {
      // overlay가 hidden으로 바뀐 순간 pending Promise가 있으면 cancel 처리.
      if (!_current || _current.resolved) return;
      for (const m of muts) {
        if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
        if (e.overlay.classList.contains('hidden')) {
          _resolveCurrent('cancel');
          break;
        }
      }
    });
    _hiddenObserver.observe(e.overlay, { attributes: true, attributeFilter: ['class'] });
  }

  // ─── 큐 진행자 ────────────────────────────────────────────────────
  function _enqueue(task) {
    _queue.push(task);
    _drain();
  }
  function _drain() {
    if (_busy) return;
    const next = _queue.shift();
    if (!next) return;
    _busy = true;
    _current = { task: next, resolved: false };
    _ensureObserver();
    _showTask(next);
  }

  function _showTask(task) {
    const e = els();
    if (!e.overlay) {
      // 마크업이 아직 없으면 native fallback으로 안전 처리.
      _busy = false;
      _current = null;
      task.fallback();
      _drain();
      return;
    }

    // 헤더/메시지
    e.title.textContent = task.title || '';
    e.message.textContent = task.message || '';

    // 입력 필드 (prompt 전용)
    if (task.kind === 'prompt') {
      e.input.classList.remove('hidden');
      e.input.value = task.value || '';
      e.input.readOnly = !!task.readonly;
    } else {
      e.input.classList.add('hidden');
      e.input.value = '';
      e.input.readOnly = false;
    }

    // 취소 버튼: alert에서는 숨김
    if (task.kind === 'alert') {
      e.cancel.style.display = 'none';
    } else {
      e.cancel.style.display = '';
      e.cancel.textContent = task.cancelText || '취소';
    }

    // 복사 버튼: prompt + copyButton일 때만
    if (task.kind === 'prompt' && task.copyButton) {
      e.copy.classList.remove('hidden');
      e.copy.textContent = '복사';
      e.copy.disabled = false;
    } else {
      e.copy.classList.add('hidden');
    }

    // OK 버튼
    e.ok.textContent = task.okText || '확인';
    e.ok.classList.toggle('btn-danger', !!task.danger);
    e.ok.classList.toggle('btn-primary', !task.danger);

    // 표시
    e.overlay.classList.remove('hidden');

    // 포커스 — prompt면 입력, 아니면 OK
    setTimeout(function () {
      try {
        if (task.kind === 'prompt' && !task.readonly) {
          e.input.focus();
          e.input.select();
        } else {
          e.ok.focus();
        }
      } catch (_) { /* noop */ }
    }, 30);
  }

  function _hideOverlay() {
    const e = els();
    if (e.overlay) e.overlay.classList.add('hidden');
  }

  // 현재 task를 type에 따라 resolve. 여러 번 호출되어도 안전.
  // type: 'ok' | 'cancel'
  function _resolveCurrent(type) {
    if (!_current || _current.resolved) return;
    _current.resolved = true;
    const task = _current.task;
    const e = els();

    let value;
    if (task.kind === 'confirm') {
      value = (type === 'ok');
    } else if (task.kind === 'alert') {
      value = undefined;
    } else if (task.kind === 'prompt') {
      if (type === 'ok') {
        value = e.input ? e.input.value : '';
      } else {
        value = null;
      }
    }

    // overlay가 아직 떠있으면 닫음 (Mutation으로 들어온 경우엔 이미 hidden 상태)
    _hideOverlay();

    // 다음 tick에 큐 진행
    const resolve = task.resolve;
    _current = null;
    _busy = false;
    try { resolve(value); } catch (err) { console.error('[wuDialog] resolve error', err); }

    // 다음 task 처리
    setTimeout(_drain, 0);
  }

  // ─── 공개 API: wuDialog ───────────────────────────────────────────
  const wuDialog = {
    confirm: function (opts) {
      opts = opts || {};
      return new Promise(function (resolve) {
        _enqueue({
          kind: 'confirm',
          title: opts.title || '확인',
          message: opts.message || '',
          okText: opts.okText || '확인',
          cancelText: opts.cancelText || '취소',
          danger: !!opts.danger,
          resolve: resolve,
          fallback: function () { resolve(window.confirm(opts.message || '')); },
        });
      });
    },
    alert: function (opts) {
      opts = opts || {};
      return new Promise(function (resolve) {
        _enqueue({
          kind: 'alert',
          title: opts.title || '알림',
          message: opts.message || '',
          okText: opts.okText || '확인',
          resolve: resolve,
          fallback: function () { window.alert(opts.message || ''); resolve(); },
        });
      });
    },
    prompt: function (opts) {
      opts = opts || {};
      return new Promise(function (resolve) {
        _enqueue({
          kind: 'prompt',
          title: opts.title || '입력',
          message: opts.message || '',
          value: opts.value != null ? String(opts.value) : '',
          readonly: !!opts.readonly,
          copyButton: !!opts.copyButton,
          okText: opts.okText || '확인',
          cancelText: opts.cancelText || '취소',
          resolve: resolve,
          fallback: function () {
            if (opts.readonly) { window.alert(opts.value || ''); resolve(opts.value || ''); }
            else resolve(window.prompt(opts.message || '', opts.value || ''));
          },
        });
      });
    },

    // ─── 내부 핸들러 (HTML onclick에서 호출) ─────────────────────
    _onOk:           function () { _resolveCurrent('ok'); },
    _onCancel:       function () { _resolveCurrent('cancel'); },
    _onBackdropClick: function (event) {
      const e = els();
      if (!_current) return;
      if (event.target !== e.overlay) return;       // 박스 내부 클릭 무시
      if (_current.task.kind === 'alert') return;   // alert: 백드롭 무반응
      _resolveCurrent('cancel');
    },
    _onCopy: async function () {
      if (!_current || _current.task.kind !== 'prompt') return;
      const e = els();
      const text = e.input ? e.input.value : '';
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          // legacy fallback
          e.input.focus(); e.input.select();
          document.execCommand('copy');
        }
        const orig = e.copy.textContent;
        e.copy.textContent = '복사됨!';
        e.copy.disabled = true;
        setTimeout(function () {
          if (e.copy) {
            e.copy.textContent = orig || '복사';
            e.copy.disabled = false;
          }
        }, 1000);
      } catch (err) {
        console.error('[wuDialog] copy failed', err);
      }
    },
  };

  // ─── 입력 필드 Enter 처리 (prompt) ────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    const e = els();
    if (e.input) {
      e.input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.isComposing) {
          ev.preventDefault();
          _resolveCurrent('ok');
        }
      });
    }
    _ensureObserver();
  });

  // ─── 공개 API: wuToast ───────────────────────────────────────────
  const TOAST_DURATION = { success: 2000, error: 4000, warning: 2500, info: 2000 };

  function _ensureToastBox() {
    let box = document.getElementById('wu-toast-container');
    if (!box) {
      box = document.createElement('div');
      box.id = 'wu-toast-container';
      document.body.appendChild(box);
    }
    return box;
  }

  function _showToast(opts) {
    opts = opts || {};
    const type = opts.type || 'info';
    const duration = (typeof opts.duration === 'number') ? opts.duration : (TOAST_DURATION[type] || 2000);
    const box = _ensureToastBox();
    const el = document.createElement('div');
    el.className = 'wu-toast ' + type;
    el.textContent = opts.message || '';
    el.title = '클릭하여 닫기';

    let removed = false;
    function remove() {
      if (removed) return;
      removed = true;
      el.classList.add('fade-out');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 320);
    }
    el.addEventListener('click', remove);
    box.appendChild(el);
    if (duration > 0) setTimeout(remove, duration);
    return { close: remove };
  }

  const wuToast = {
    show:    function (opts) { return _showToast(opts || {}); },
    success: function (msg)  { return _showToast({ message: msg, type: 'success', duration: 2000 }); },
    error:   function (msg)  { return _showToast({ message: msg, type: 'error',   duration: 4000 }); },
    warning: function (msg)  { return _showToast({ message: msg, type: 'warning', duration: 2500 }); },
    info:    function (msg)  { return _showToast({ message: msg, type: 'info',    duration: 2000 }); },
  };

  // 전역 등록
  window.wuDialog = wuDialog;
  window.wuToast = wuToast;
})();
