(function () {
  window.wuDebounce = function (fn, ms) {
    var t = null;
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms || 300);
    };
  };

  window.wuShouldSuppressRefetch = function () {
    var modals = document.querySelectorAll('.modal-overlay');
    for (var i = 0; i < modals.length; i++) {
      var m = modals[i];
      if (m.classList.contains('hidden')) continue;
      var s = window.getComputedStyle(m);
      if (s.display !== 'none' && s.visibility !== 'hidden') return true;
    }
    var el = document.activeElement;
    if (el) {
      var tag = el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
      if (el.isContentEditable) return true;
    }
    if (typeof window._blockAutoRefresh === 'function') {
      try { if (window._blockAutoRefresh()) return true; } catch (e) {}
    } else if (window._blockAutoRefresh === true) {
      return true;
    }
    return false;
  };

  if (!window.CURRENT_USER) return;

  var _es = null;
  var _backoff = 1000;
  var _everConnected = false;  // 재연결 복구 여부 추적

  function _dispatch(type, detail) {
    try {
      window.dispatchEvent(new CustomEvent(type, { detail: detail || {} }));
    } catch (e) {}
  }

  function _connect() {
    if (_es) { try { _es.close(); } catch (e) {} }
    _es = new EventSource('/api/stream');

    _es.addEventListener('open', function () {
      _backoff = 1000;
      // 최초 연결은 refetch 생략 — 페이지가 이미 정상 fetch 중
      // 재연결(단절 복구)일 때만 refetch해 누락 이벤트 보완
      if (_everConnected) {
        _dispatch('wu:events:changed', {});
      }
      _everConnected = true;
    });

    _es.addEventListener('events.changed', function (e) {
      try {
        var d = JSON.parse(e.data);
        _dispatch('wu:events:changed', d);
      } catch (ex) {}
    });

    _es.addEventListener('projects.changed', function (e) {
      try {
        var d = JSON.parse(e.data);
        _dispatch('wu:projects:changed', d);
        _dispatch('wu:events:changed', d);
      } catch (ex) {}
    });

    _es.addEventListener('docs.changed', function (e) {
      try {
        var d = JSON.parse(e.data);
        _dispatch('wu:docs:changed', d);
      } catch (ex) {}
    });

    _es.addEventListener('checks.changed', function (e) {
      try {
        var d = JSON.parse(e.data);
        _dispatch('wu:checks:changed', d);
      } catch (ex) {}
    });

    _es.onerror = function () {
      try { _es.close(); } catch (e) {}
      _es = null;
      var delay = Math.min(_backoff, 30000);
      setTimeout(_connect, delay);
      _backoff = Math.min(_backoff * 2, 30000);
    };
  }

  // 페이지 이탈 시 명시적 close — 서버 좀비 연결 즉시 정리
  window.addEventListener('beforeunload', function () {
    if (_es) { try { _es.close(); } catch (e) {} _es = null; }
  });

  _connect();
})();
