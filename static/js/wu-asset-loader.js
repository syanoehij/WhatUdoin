/**
 * wu-asset-loader.js — WhatUdoin 공통 lazy asset loader
 *
 * window.WuAssets.load(name)       — Promise<void>: 단일 자산 로드
 * window.WuAssets.ensure(...names) — Promise<void>: 다중 자산 병렬 로드
 * window.WuAssets.isReady(name)    — boolean: 자산 로드 완료 여부
 *
 * readiness 5종 보장:
 *  1. CSS readiness  — link.onload 이벤트로 resolve
 *  2. JS global      — script.onload + 글로벌 객체 polling (max 5초)
 *  3. 의존성 chain   — deps 자산 먼저 resolve 후 본 자산 시작
 *  4. 재시도         — 네트워크 실패 시 최대 2회 재시도 (지수 backoff 200ms/400ms)
 *  5. reentrancy     — 동일 자산 동시 다중 호출 시 promise 1개 공유 (메모이제이션)
 */
(function () {
  'use strict';

  // ── URL base paths + 의존성·글로벌 정의 ──────────────────
  // __WU_ASSET_V 값은 query-string suffix (e.g. "?v=abc123") 형태.
  // base.html에서 이미 window.__WU_ASSET_V가 선언됨.
  function buildDeps() {
    var v = window.__WU_ASSET_V || {};
    return {
      // CSS-only assets
      'highlight-css': {
        css: '/static/lib/highlight-github.min.css' + (v.hlCss || ''),
        deps: []
      },
      'katex-css': {
        css: '/static/lib/katex.min.css' + (v.katex || ''),
        deps: []
      },
      'wu-editor-css': {
        css: '/static/css/wu-editor.css' + (v.wuCss || ''),
        deps: []
      },
      // JS assets
      'highlight': {
        js: '/static/lib/highlight.min.js' + (v.hlJs || ''),
        global: 'hljs',
        deps: []
      },
      'tiptap': {
        js: '/static/lib/tiptap-bundle.min.js' + (v.tiptap || ''),
        global: 'TiptapBundle',
        deps: []
      },
      'mermaid': {
        js: '/static/lib/mermaid-bundle.min.js' + (v.mermaid || ''),
        global: 'mermaid',
        deps: []
      },
      // wu-editor depends on all of the above
      'wu-editor': {
        js: '/static/js/wu-editor.js' + (v.wuJs || ''),
        global: 'WUEditor',
        deps: ['highlight-css', 'katex-css', 'wu-editor-css', 'highlight', 'tiptap', 'mermaid']
      }
    };
  }

  // ── promise cache (reentrancy — readiness #5) ───────────
  var _cache = {};       // name -> Promise<void>
  var _ready = {};       // name -> boolean

  // ── CSS loader (readiness #1) ────────────────────────────
  function loadCss(href) {
    return new Promise(function (resolve, reject) {
      var base = href.split('?')[0];
      if (document.querySelector('link[rel="stylesheet"][href^="' + base + '"]')) {
        return resolve();
      }
      var el = document.createElement('link');
      el.rel = 'stylesheet';
      el.href = href;
      el.onload = function () { resolve(); };
      el.onerror = function () { reject(new Error('CSS load failed: ' + href)); };
      document.head.appendChild(el);
    });
  }

  // ── JS global polling (readiness #2) ────────────────────
  // script.onload 후 글로벌 객체가 실제로 정의될 때까지 polling
  function pollGlobal(globalName, timeoutMs) {
    return new Promise(function (resolve, reject) {
      if (!globalName || window[globalName] !== undefined) {
        return resolve();
      }
      var elapsed = 0;
      var interval = 20;
      var timer = setInterval(function () {
        if (window[globalName] !== undefined) {
          clearInterval(timer);
          resolve();
          return;
        }
        elapsed += interval;
        if (elapsed >= timeoutMs) {
          clearInterval(timer);
          reject(new Error('Global not found after ' + timeoutMs + 'ms: window.' + globalName));
        }
      }, interval);
    });
  }

  // ── JS loader (readiness #2) ─────────────────────────────
  function loadJs(src, globalName) {
    return new Promise(function (resolve, reject) {
      var base = src.split('?')[0];
      var existing = document.querySelector('script[src^="' + base + '"]');
      if (existing) {
        // 태그가 이미 있으면 글로벌 polling만
        if (globalName && window[globalName] !== undefined) {
          return resolve();
        }
        if (globalName) {
          pollGlobal(globalName, 5000).then(resolve, reject);
        } else {
          resolve();
        }
        return;
      }
      var el = document.createElement('script');
      el.src = src;
      el.onload = function () {
        if (globalName) {
          pollGlobal(globalName, 5000).then(resolve, reject);
        } else {
          resolve();
        }
      };
      el.onerror = function () {
        reject(new Error('Script load failed: ' + src));
      };
      document.head.appendChild(el);
    });
  }

  // ── 단일 자산 로드 (재시도 포함, readiness #4) ───────────
  function loadOne(name, DEPS, attempt) {
    attempt = attempt || 0;
    var def = DEPS[name];
    if (!def) {
      return Promise.reject(new Error('Unknown asset: ' + name));
    }

    // 의존성 먼저 resolve (readiness #3)
    var depsPromise = def.deps.length > 0
      ? Promise.all(def.deps.map(function (d) { return _loadWithCache(d, DEPS); }))
      : Promise.resolve();

    return depsPromise.then(function () {
      var tasks = [];
      if (def.css) tasks.push(loadCss(def.css));
      if (def.js)  tasks.push(loadJs(def.js, def.global || null));
      return Promise.all(tasks);
    }).catch(function (err) {
      if (attempt < 2) {
        // 지수 backoff: 200ms, 400ms
        var delay = 200 * Math.pow(2, attempt);
        return new Promise(function (resolve) { setTimeout(resolve, delay); })
          .then(function () { return loadOne(name, DEPS, attempt + 1); });
      }
      throw err;
    });
  }

  // ── 캐시 기반 로드 (reentrancy #5) ──────────────────────
  function _loadWithCache(name, DEPS) {
    if (_cache[name]) return _cache[name];
    _cache[name] = loadOne(name, DEPS, 0).then(function () {
      _ready[name] = true;
    }, function (err) {
      // 실패 시 cache 제거 → 다음 호출에서 재시도 가능 (readiness #4)
      delete _cache[name];
      throw err;
    });
    return _cache[name];
  }

  // ── public API ───────────────────────────────────────────
  window.WuAssets = {
    /**
     * 단일 자산 로드 — Promise<void>
     * name: 'highlight' | 'highlight-css' | 'katex-css' | 'wu-editor-css' |
     *        'tiptap' | 'mermaid' | 'wu-editor'
     */
    load: function (name) {
      var DEPS = buildDeps();
      return _loadWithCache(name, DEPS);
    },

    /**
     * 다중 자산 병렬 로드 — Promise<void>
     * 독립 자산은 병렬, 의존성은 각 자산 내부에서 순서 보장
     */
    ensure: function () {
      var names = Array.prototype.slice.call(arguments);
      var DEPS = buildDeps();
      return Promise.all(names.map(function (n) {
        return _loadWithCache(n, DEPS);
      })).then(function () { /* resolve void */ });
    },

    /**
     * 자산 로드 완료 여부 — boolean
     */
    isReady: function (name) {
      return _ready[name] === true;
    }
  };

})();
