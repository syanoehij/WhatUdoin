// realtime.js — SSE 연결 + 자동 재조회 트리거
// (a) SSE 재연결 직후 1회: _everConnected 플래그로 구현
// (b) idle 후 visibilitychange/focus 복귀 시 1회: _lastActivityAt + threshold로 구현
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

  // (b) idle 후 visibility/focus 복귀 시 재조회
  var IDLE_REFETCH_THRESHOLD_MS = 60000;  // 60초 이상 idle 후 복귀 시 trigger
  var MIN_REFETCH_INTERVAL_MS = 5000;     // 연속 visibilitychange 중복 방지

  var _lastActivityAt = Date.now();
  var _lastRefetchAt = 0;

  function _idleRefetch() {
    var now = Date.now();
    if (window.wuShouldSuppressRefetch()) return;
    if (now - _lastRefetchAt < MIN_REFETCH_INTERVAL_MS) return;
    if (now - _lastActivityAt < IDLE_REFETCH_THRESHOLD_MS) return;
    _lastRefetchAt = now;
    _dispatch('wu:events:changed', { reason: 'idle_visibility' });
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') _idleRefetch();
  });

  window.addEventListener('focus', function () {
    _idleRefetch();
  });

  // 사용자 활동 추적 — _lastActivityAt 갱신
  ['click', 'keydown', 'scroll', 'mousemove'].forEach(function (type) {
    document.addEventListener(type, function () {
      _lastActivityAt = Date.now();
    }, { passive: true });
  });

  // 운영 진단용 디버그 도구
  window.__wuRealtimeDebug = {
    lastActivityAt: function () { return _lastActivityAt; },
    lastRefetchAt:  function () { return _lastRefetchAt; },
    idleMs:         function () { return Date.now() - _lastActivityAt; }
  };

  _connect();
})();
