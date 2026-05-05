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

  /* 뷰어 마크다운에서 callout 헤더 다음 body 줄마다 빈 '>' 줄을 삽입해
   * Tiptap-markdown이 헤더와 body를 별도 <p>로 파싱하도록 유도 */
  function _preprocessViewerCallouts(md) {
    const lines = md.split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^>\s+\\?\[!/.test(line)) {
        out.push(line);
        i++;
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          out.push('>');
          out.push(lines[i]);
          i++;
        }
      } else {
        out.push(line);
        i++;
      }
    }
    return out.join('\n');
  }

  function _getCalloutCssType(text) {
    const m = /^\\?\[!([^\\\]]+?)\\?\]/.exec((text || '').trim());
    if (!m) return null;
    const rawType = m[1].trim().toLowerCase();
    return CALLOUT_TYPES[rawType] ? rawType : 'note';
  }

  function _unescapeMarkdownText(text) {
    return String(text ?? '').replace(/\\([\\`*{}\[\]()#+\-.!_>])/g, '$1');
  }

  function _unescapeMarkdownLinkDestination(url) {
    return String(url ?? '').replace(/\\([\\`*{}\[\]()#+\-.!_>])/g, '$1');
  }

  function _escapeMarkdownLinkDestination(url) {
    const src = String(url ?? '');
    if (/[<>\s]/.test(src)) return '<' + src.replace(/[<>]/g, encodeURIComponent) + '>';
    return src.replace(/([()\\])/g, '\\$1');
  }

  function _normalizeMarkdownImageDest(rawDest) {
    const raw = String(rawDest ?? '');
    const leading = (raw.match(/^\s*/) || [''])[0];
    const rest = raw.slice(leading.length);
    if (rest.startsWith('<')) {
      const end = rest.indexOf('>');
      if (end !== -1) {
        return leading + '<' + _unescapeMarkdownLinkDestination(rest.slice(1, end)) + '>' + rest.slice(end + 1);
      }
    }
    const m = /^(\S+)([\s\S]*)$/.exec(rest);
    if (!m) return raw;
    return leading + _unescapeMarkdownLinkDestination(m[1]) + m[2];
  }

  function _extractMarkdownImageSrc(rawDest) {
    const normalized = _normalizeMarkdownImageDest(rawDest);
    const trimmed = normalized.trim();
    if (trimmed.startsWith('<')) {
      const end = trimmed.indexOf('>');
      if (end !== -1) return trimmed.slice(1, end);
    }
    const m = /^(\S+)/.exec(trimmed);
    return m ? m[1] : '';
  }

  function _normalizeImageMarkdownSources(md) {
    return String(md ?? '')
      .replace(/(!\[[^\]]*\]\()([^)\n]+)(\))/g,
        (_, pre, dest, post) => pre + _normalizeMarkdownImageDest(dest) + post)
      .replace(/(<img\b[^>]*\bsrc=["'])([^"']+)(["'][^>]*>)/gi,
        (_, pre, src, post) => pre + _unescapeMarkdownLinkDestination(src) + post);
  }

  function _markdownImageToHtml(altFull, rawDest) {
    const pi = altFull.lastIndexOf('|');
    const alt = pi !== -1 ? altFull.slice(0, pi) : altFull;
    const src = _extractMarkdownImageSrc(rawDest);
    const width = pi !== -1 ? altFull.slice(pi + 1).trim() : '';
    const widthAttr = /^\d+$/.test(width) ? ` style="width: ${width}px"` : '';
    return `<img src="${esc(src)}" alt="${esc(alt)}"${widthAttr}>`;
  }

  function _splitTitleContent(md) {
    const lines = String(md ?? '').split('\n');
    const rawTitle = (lines[0] || '').replace(/^#{1,6}[ \t]+/, '').trim();
    return {
      title: _unescapeMarkdownText(rawTitle).trim(),
      content: lines.slice(1).join('\n').replace(/^\n+/, '').trimEnd(),
    };
  }

  /* Tiptap이 blockquote로 렌더링한 > [!type] 패턴을 콜아웃 div로 변환.
   * _preprocessViewerCallouts가 빈 > 줄을 삽입하면 multi-p로 파싱됨(primary path).
   * 단일 <p>인 경우 ']' 위치로 헤더/body 분리(fallback). */
  function _fixCallouts(container) {
    container.querySelectorAll('blockquote').forEach(bq => {
      const firstP = bq.querySelector('p');
      if (!firstP) return;
      const bqPs = Array.from(bq.children);
      const firstPText = firstP.textContent.trim();

      let headerText = firstPText;
      let fallbackBody = '';
      if (bqPs.length === 1) {
        /* 단일 <p>: ']' 위치로 [!type] 부분과 나머지 body 분리 */
        const rb = firstPText.indexOf(']');
        if (rb >= 0) {
          headerText = firstPText.slice(0, rb + 1);
          fallbackBody = firstPText.slice(rb + 1).trim();
        }
      }

      const m = /^\\?\[!([^\\\]]+?)\\?\](?:\s+(.+))?$/.exec(headerText);
      if (!m) return;
      const rawType = m[1].trim().toLowerCase();
      const customTitle = (bqPs.length > 1 && m[2]) ? m[2].trim() : '';
      const cfg = CALLOUT_TYPES[rawType] || CALLOUT_TYPES['note'];
      const cssType = CALLOUT_TYPES[rawType] ? rawType : 'note';
      const title = customTitle || cfg.title;

      const div = document.createElement('div');
      div.className = `wu-callout wu-callout-${cssType}`;

      const titleDiv = document.createElement('div');
      titleDiv.className = 'wu-callout-title';
      const iconSpan = document.createElement('span');
      iconSpan.className = 'wu-callout-icon';
      iconSpan.innerHTML = cfg.icon;   // cfg.icon은 정적 SVG 상수 — 사용자 입력 아님
      const labelSpan = document.createElement('span');
      labelSpan.textContent = title;   // XSS 방지: textContent로 삽입
      titleDiv.appendChild(iconSpan);
      titleDiv.appendChild(labelSpan);

      const bodyDiv = document.createElement('div');
      bodyDiv.className = 'wu-callout-body';
      if (bqPs.length > 1) {
        bqPs.slice(1).forEach(child => bodyDiv.appendChild(child.cloneNode(true)));
      } else if (fallbackBody) {
        const p = document.createElement('p');
        p.textContent = fallbackBody;
        bodyDiv.appendChild(p);
      }

      div.appendChild(titleDiv);
      div.appendChild(bodyDiv);
      bq.replaceWith(div);
    });
  }

  /* ── 콜아웃 타입 설정 ─────────────────────────────── */
  /* Lucide SVG (뷰어 내 아이콘): info / flame / check / alert-triangle / x-circle / zap / help-circle / quote / clipboard-list / list / bug */
  const _IC_CO_INFO  = _ic('<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>');
  const _IC_CO_FLAME = _ic('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 3z"/>');
  const _IC_CO_CHECK = _ic('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>');
  const _IC_CO_WARN  = _ic('<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/>');
  const _IC_CO_ERR   = _ic('<circle cx="12" cy="12" r="10"/><line x1="15" x2="9" y1="9" y2="15"/><line x1="9" x2="15" y1="9" y2="15"/>');
  const _IC_CO_ZAP   = _ic('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>');
  const _IC_CO_HELP  = _ic('<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" x2="12.01" y1="17" y2="17"/>');
  const _IC_CO_QUOTE = _ic('<g transform="translate(3.6,3.6) scale(0.7)"><path d="M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/><path d="M5 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/></g>');
  const _IC_CO_CLIP  = _ic('<rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>');
  const _IC_CO_LIST  = _ic('<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>');
  const _IC_CO_BUG   = _ic('<rect x="8" y="6" width="8" height="14" rx="4"/><path d="m19 7-3 2"/><path d="m5 7 3 2"/><path d="m19 19-3-2"/><path d="m5 19 3-2"/><path d="M20 13h-4"/><path d="M4 13h4"/><path d="m10 4 1 2h2l1-2"/>');
  const _IC_CO_TODO  = _ic('<rect x="3" y="5" width="6" height="6" rx="1"/><path d="m3 17 2 2 4-4"/><line x1="13" x2="21" y1="6" y2="6"/><line x1="13" x2="21" y1="18" y2="18"/><line x1="13" x2="21" y1="12" y2="12"/>');

  const CALLOUT_TYPES = {
    note:       { title: 'Note',       icon: _IC_CO_INFO  },
    info:       { title: 'Info',       icon: _IC_CO_INFO  },
    tip:        { title: 'Tip',        icon: _IC_CO_FLAME },
    hint:       { title: 'Hint',       icon: _IC_CO_FLAME },
    success:    { title: 'Success',    icon: _IC_CO_CHECK },
    check:      { title: 'Check',      icon: _IC_CO_CHECK },
    done:       { title: 'Done',       icon: _IC_CO_CHECK },
    warning:    { title: 'Warning',    icon: _IC_CO_WARN  },
    caution:    { title: 'Caution',    icon: _IC_CO_WARN  },
    attention:  { title: 'Attention',  icon: _IC_CO_WARN  },
    danger:     { title: 'Danger',     icon: _IC_CO_ERR   },
    error:      { title: 'Error',      icon: _IC_CO_ERR   },
    failure:    { title: 'Failure',    icon: _IC_CO_ERR   },
    fail:       { title: 'Fail',       icon: _IC_CO_ERR   },
    important:  { title: 'Important',  icon: _IC_CO_ZAP   },
    question:   { title: 'Question',   icon: _IC_CO_HELP  },
    faq:        { title: 'FAQ',        icon: _IC_CO_HELP  },
    help:       { title: 'Help',       icon: _IC_CO_HELP  },
    quote:      { title: 'Quote',      icon: _IC_CO_QUOTE },
    cite:       { title: 'Cite',       icon: _IC_CO_QUOTE },
    abstract:   { title: 'Abstract',   icon: _IC_CO_CLIP  },
    summary:    { title: 'Summary',    icon: _IC_CO_CLIP  },
    tldr:       { title: 'TL;DR',      icon: _IC_CO_CLIP  },
    example:    { title: 'Example',    icon: _IC_CO_LIST  },
    bug:        { title: 'Bug',        icon: _IC_CO_BUG   },
    todo:       { title: 'Todo',       icon: _IC_CO_TODO  },
  };

  const CALLOUT_DROP_ITEMS = [
    { type: 'note',     desc: '메모'       },
    { type: 'abstract', desc: '문서 요약'  },
    { type: 'info',     desc: '참고 정보'  },
    { type: 'todo',     desc: '할 일'      },
    { type: 'tip',      desc: '팁'         },
    { type: 'success',  desc: '완료'       },
    { type: 'question', desc: '질문'       },
    { type: 'warning',  desc: '주의'       },
    { type: 'failure',  desc: '실패/누락'  },
    { type: 'danger',   desc: '위험'       },
    { type: 'bug',      desc: '버그'       },
    { type: 'example',  desc: '예제'       },
    { type: 'quote',    desc: '인용문'     },
  ];

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

  /* Tiptap 로드 전: HTML 컨텍스트 내 <p>![alt|NNN](url)</p> 텍스트를
   * <img src="url" style="width:NNNpx"> HTML 태그로 변환한다.
   * HTML 블록 안에서는 markdown-it 인라인 파서가 동작하지 않으므로
   * 마크다운 이미지 구문이 텍스트로 남는다 — 이를 로드 전에 처리한다. */
  function _preprocessHtmlTableImages(md) {
    return md
      .replace(/<p>!\[([^\]]*)\]\(([^)\s][^)]*)\)<\/p>/g,
        (_, altFull, src) => _markdownImageToHtml(altFull, src))
      .replace(/!\[([^\]]*\|\d+)\]\(([^)\s][^)]*)\)/g,
        (_, altFull, src) => _markdownImageToHtml(altFull, src));
  }

  /* HTML 테이블 셀 등 HTML 컨텍스트에 잘못 저장된 마크다운 이미지 텍스트를
   * 실제 <img> 태그로 변환 (저장 형식 마이그레이션 과도기 대응) */
  function _fixInlineMarkdownImages(container) {
    const re = /^!\[([^\]]*)\]\(([^)]+)\)$/;
    container.querySelectorAll('p').forEach(p => {
      if (p.childNodes.length !== 1 || p.childNodes[0].nodeType !== Node.TEXT_NODE) return;
      const text = p.textContent.trim();
      const m = re.exec(text);
      if (!m) return;
      const altFull = m[1], src = _unescapeMarkdownLinkDestination(m[2]);
      const pi = altFull.lastIndexOf('|');
      const img = document.createElement('img');
      img.src = src;
      if (pi !== -1 && /^\d+$/.test(altFull.slice(pi + 1).trim())) {
        img.style.width = altFull.slice(pi + 1).trim() + 'px';
        img.alt = altFull.slice(0, pi);
      } else {
        img.alt = altFull;
      }
      p.innerHTML = '';
      p.appendChild(img);
    });
  }

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function sel(s) {
    return typeof s === 'string' ? document.querySelector(s) : s;
  }

  /* ── Lucide SVG 아이콘 헬퍼 ── */
  function _ic(inner) {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  }
  const _IC_UL       = _ic('<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>');
  const _IC_OL       = _ic('<line x1="10" x2="21" y1="6" y2="6"/><line x1="10" x2="21" y1="12" y2="12"/><line x1="10" x2="21" y1="18" y2="18"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>');
  const _IC_TASK     = _ic('<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><line x1="13" x2="21" y1="8" y2="8"/><line x1="13" x2="21" y1="12" y2="12"/><line x1="13" x2="21" y1="16" y2="16"/>');
  const _IC_HEADING  = _ic('<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="m17 12 3-2v8"/>');
  const _IC_BOLD     = _ic('<path d="M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8"/>');
  const _IC_ITALIC   = _ic('<line x1="19" x2="10" y1="4" y2="4"/><line x1="14" x2="5" y1="20" y2="20"/><line x1="15" x2="9" y1="4" y2="20"/>');
  const _IC_STRIKE   = _ic('<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><line x1="4" x2="20" y1="12" y2="12"/>');
  const _IC_HL       = _ic('<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>');
  const _IC_CLEARFMT = _ic('<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>');
  const _IC_CODE     = _ic('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>');
  const _IC_HR       = _ic('<path d="M5 12h14"/>');
  const _IC_QUOTE    = _ic('<g transform="translate(3.6,3.6) scale(0.7)"><path d="M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/><path d="M5 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/></g>');
  const _IC_TABLE    = _ic('<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>');
  const _IC_CODEBLK  = _ic('<path d="m10 9-3 3 3 3"/><path d="m14 15 3-3-3-3"/><rect x="3" y="3" width="18" height="18" rx="2"/>');
  const _IC_SIGMA    = _ic('<path d="M18 7V5a1 1 0 0 0-1-1H6.5a.5.5 0 0 0-.4.8l4.5 6a2 2 0 0 1 0 2.4l-4.5 6a.5.5 0 0 0 .4.8H17a1 1 0 0 0 1-1v-2"/>');
  const _IC_PERCENT  = _ic('<line x1="19" x2="5" y1="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>');
  const _IC_INTEG    = _ic('<path d="M6 20a2 2 0 0 0 2 2c2 0 4-8 4-16a2 2 0 0 1 4 0"/>');  /* 수식 ∫ 모양 */
  const _IC_FOOTNOTE = _ic('<path d="M6 4H4v16h2"/><path d="M18 4h2v16h-2"/><path d="M9 16 L12 8 L15 16"/>');
  /* 콜아웃 툴바 아이콘 (megaphone) */
  const _IC_CALLOUT  = _ic('<path d="m3 11 19-9-9 19-2-8-8-2z"/>');

  /* ── 툴바 정의 ──────────────────────────────────── */
  const TOOLBAR_DEFS = [
    { group: ['heading', 'bold', 'italic', 'strike', 'highlight', 'clearformat'] },
    { group: ['code', 'inlinemath'] },
    { group: ['hr', 'quote', 'callout', 'footnote'] },
    { group: ['ul', 'ol', 'task'] },
    { group: ['table', 'link', 'image'] },
    { group: ['codeblock', 'math', 'comment'] },
  ];

  const TOOLBAR_LABELS = {
    heading:    { icon: _IC_HEADING,  title: '제목' },
    bold:       { icon: _IC_BOLD,     title: '굵게' },
    italic:     { icon: _IC_ITALIC,   title: '기울임' },
    strike:     { icon: _IC_STRIKE,   title: '취소선' },
    highlight:   { icon: _IC_HL,       title: '하이라이트 (==text==)' },
    clearformat: { icon: _IC_CLEARFMT, title: '서식 지우기' },
    code:        { icon: _IC_CODE,     title: '인라인 코드' },
    inlinemath: { icon: _IC_INTEG,    title: '인라인 수식 (선택 영역을 $...$로 변환)' },
    hr:         { icon: _IC_HR,       title: '구분선' },
    quote:      { icon: _IC_QUOTE,    title: '인용' },
    ul:         { icon: _IC_UL,       title: '목록' },
    ol:         { icon: _IC_OL,       title: '번호 목록' },
    task:       { icon: _IC_TASK,     title: '할일 목록' },
    table:      { icon: _IC_TABLE,    title: '표 삽입' },
    link:       { icon: _ic('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'), title: '링크' },
    image:      { icon: _ic('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'), title: '이미지' },
    codeblock:  { icon: _IC_CODEBLK,  title: '코드 블록' },
    math:       { icon: _IC_SIGMA,    title: '수식 블록' },
    comment:    { icon: _IC_PERCENT,  title: '주석 (%% ... %%)' },
    footnote:   { icon: _IC_FOOTNOTE, title: '각주 ([^N])' },
    callout:    { icon: _IC_CALLOUT,  title: '콜아웃 (> [!note])' },
  };

  /* 슬래시 커맨드의 math/footnote/link 항목이 create() 스코프 내 함수를 호출하기 위한 참조 */
  let _mathSlashCmdFn     = null;
  let _footnoteSlashCmdFn = null;
  let _promptLinkFn       = null;

  /* ── 슬래시 커맨드 트리 ─────────────────────────── */
  const SLASH_GROUPS = [
    {
      id: 'heading', label: '제목', icon: _IC_HEADING,
      children: [
        { id: 'h1', label: '제목 1', icon: 'H1', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 1 }).run() },
        { id: 'h2', label: '제목 2', icon: 'H2', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 2 }).run() },
        { id: 'h3', label: '제목 3', icon: 'H3', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 3 }).run() },
        { id: 'h4', label: '제목 4', icon: 'H4', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 4 }).run() },
        { id: 'h5', label: '제목 5', icon: 'H5', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 5 }).run() },
        { id: 'h6', label: '제목 6', icon: 'H6', hint: 'h heading', cmd: ed => ed.chain().focus().setHeading({ level: 6 }).run() },
        { id: 'paragraph', label: '본문', icon: _IC_UL, hint: 'paragraph text 본문', cmd: ed => ed.chain().focus().setParagraph().run() },
      ],
    },
    {
      id: 'format', label: '서식', icon: _IC_BOLD,
      children: [
        { id: 'bold',      label: '볼드체',    icon: _IC_BOLD,   hint: 'bold strong 굵게',       cmd: ed => ed.chain().focus().toggleBold().run() },
        { id: 'italic',    label: '기울이기',  icon: _IC_ITALIC, hint: 'italic em 기울임',       cmd: ed => ed.chain().focus().toggleItalic().run() },
        { id: 'strike',    label: '취소선',    icon: _IC_STRIKE, hint: 'strike del 취소',        cmd: ed => ed.chain().focus().toggleStrike().run() },
        { id: 'highlight', label: '하이라이트', icon: _IC_HL,    hint: 'highlight mark hl 형광펜', cmd: ed => ed.chain().focus().toggleHighlight().run() },
        { id: 'code',      label: '인라인 코드', icon: _IC_CODE, hint: 'code inline 인라인',      cmd: ed => ed.chain().focus().toggleCode().run() },
        { id: 'inlinemath', label: '인라인 수식', icon: _IC_INTEG, hint: 'inline math latex katex 인라인 수식',
          cmd: ed => {
            const pmSel = ed.state.selection;
            if (pmSel.empty) {
              ed.chain().focus().insertContent({ type: 'inlineMath', attrs: { latex: 'x' } }).run();
            } else {
              const text = ed.state.doc.textBetween(pmSel.from, pmSel.to, '');
              ed.chain().focus().deleteSelection().insertContent({ type: 'inlineMath', attrs: { latex: text } }).run();
            }
          },
        },
      ],
    },
    {
      id: 'callout', label: '콜아웃', icon: _IC_CALLOUT,
      children: CALLOUT_DROP_ITEMS.map(it => ({
        id: 'callout-' + it.type,
        label: it.type.charAt(0).toUpperCase() + it.type.slice(1),
        icon: (CALLOUT_TYPES[it.type] && CALLOUT_TYPES[it.type].icon) || _IC_CALLOUT,
        hint: 'callout ' + it.type + ' ' + it.desc,
        desc: it.desc,
        cmd: ed => ed.chain().focus().insertContent({
          type: 'blockquote',
          content: [
            { type: 'paragraph', content: [{ type: 'text', text: '[!' + it.type + ']' }] },
            { type: 'paragraph' },
          ],
        }).run(),
      })),
    },
    {
      id: 'block', label: '단락', icon: _IC_UL,
      children: [
        { id: 'ul',   label: '글머리 목록', icon: _IC_UL,   hint: 'ul list bullet 글머리',   cmd: ed => ed.chain().focus().toggleBulletList().run() },
        { id: 'ol',   label: '숫자 목록',   icon: _IC_OL,   hint: 'ol number list 번호',      cmd: ed => ed.chain().focus().toggleOrderedList().run() },
        { id: 'task', label: '체크박스',    icon: _IC_TASK, hint: 'task todo check 체크박스',  cmd: ed => ed.chain().focus().toggleTaskList().run() },
      ],
    },
    {
      id: 'insert', label: '삽입', icon: _IC_TABLE,
      children: [
        { id: 'table', label: '표',      icon: _IC_TABLE,   hint: 'table grid 표',            cmd: ed => ed.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
        { id: 'link',  label: '링크',    icon: _ic('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'), hint: 'link url 링크', cmd: ed => { if (_promptLinkFn) _promptLinkFn(); } },
        { id: 'codeblock', label: '코드 블록', icon: _IC_CODEBLK, hint: 'code block codeblock', cmd: ed => ed.chain().focus().toggleCodeBlock().run() },
        { id: 'math',  label: '수식 블록', icon: _IC_SIGMA, hint: 'math latex katex 수식 블록', cmd: ed => { if (_mathSlashCmdFn) _mathSlashCmdFn(ed); } },
        { id: 'comment', label: '주석',   icon: _IC_PERCENT, hint: 'comment obsidian 주석 hidden',
          cmd: ed => {
            const pmSel = ed.state.selection;
            if (pmSel.empty) {
              ed.chain().focus().insertContent('<span data-type="obsidian-comment">주석 내용</span>').run();
            } else {
              ed.chain().focus().toggleMark('obsidianComment').run();
            }
          },
        },
      ],
    },
    // 단독 항목 (서브메뉴 없음)
    { id: 'hr',       label: '구분선', icon: _IC_HR,    hint: 'hr divider line 구분선', cmd: ed => ed.chain().focus().setHorizontalRule().run() },
    { id: 'quote',    label: '인용',   icon: _IC_QUOTE, hint: 'quote blockquote 인용',  cmd: ed => ed.chain().focus().toggleBlockquote().run() },
    { id: 'footnote', label: '각주',   icon: _IC_FOOTNOTE, hint: 'footnote fn ref 각주', cmd: ed => { if (_footnoteSlashCmdFn) _footnoteSlashCmdFn(ed); } },
  ];

  /* 리프 아이템 전체를 평탄화 (검색 필터링용) */
  function _slashLeaves() {
    const leaves = [];
    for (const g of SLASH_GROUPS) {
      if (g.children) {
        for (const c of g.children) leaves.push(c);
      } else {
        leaves.push(g);
      }
    }
    return leaves;
  }

  function _matchSlashFlat(query) {
    const q = query.toLowerCase();
    return _slashLeaves().filter(it =>
      it.id.startsWith(q) || it.label.includes(q) || (it.hint || '').includes(q) || (it.desc || '').includes(q)
    );
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
    let _slashActive    = false;
    let _slashStart     = -1;
    let _slashQuery     = '';
    let _slashSelIdx    = 0;   // 왼쪽 패널(그룹 목록) 선택 인덱스
    let _slashSubIdx    = 0;   // 오른쪽 패널(서브메뉴) 선택 인덱스
    let _slashFocus     = 'left';  // 'left' | 'right'
    let _slashMode      = 'groups'; // 'groups' | 'flat'
    let _slashFiltered  = [];  // flat 모드 결과
    let _slashSubPanel  = null; // 현재 열린 서브메뉴의 그룹 참조
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

    /* 각주 입력 모달 동적 생성 */
    let _footnoteModal = null;
    let _footnoteModalCallback = null;
    if (opts.canEdit !== false) {
      _footnoteModal = document.createElement('div');
      _footnoteModal.className = 'wu-leave-overlay';
      _footnoteModal.style.display = 'none';
      _footnoteModal.innerHTML = `
        <div class="wu-leave-box" style="max-width:460px;width:90%">
          <h3 id="wu-fn-title" style="margin:0 0 12px;font-size:1rem">각주 추가</h3>
          <textarea id="wu-fn-input" rows="3" placeholder="각주 내용을 입력하세요"
            style="width:100%;box-sizing:border-box;padding:7px 10px;border:1px solid var(--border);border-radius:5px;background:var(--surface);color:var(--text);font-size:0.9rem;outline:none;resize:vertical;margin-bottom:16px;font-family:inherit"></textarea>
          <div class="modal-actions">
            <span id="wu-fn-hint" style="margin-right:auto;font-size:0.78rem;color:var(--text-muted)">Ctrl+Enter로 삽입</span>
            <button id="wu-fn-cancel" class="btn btn-sm">취소</button>
            <button id="wu-fn-confirm" class="btn btn-sm btn-primary">삽입</button>
          </div>
        </div>`;
      document.body.appendChild(_footnoteModal);
    }

    /* 수식 입력 모달 동적 생성 */
    let _mathModal = null;
    let _mathModalCallback = null;
    let _mathCurrentAlign = 'center';
    if (opts.canEdit !== false) {
      _mathModal = document.createElement('div');
      _mathModal.className = 'wu-leave-overlay wu-math-modal-overlay';
      _mathModal.style.display = 'none';
      _mathModal.innerHTML = `
        <div class="wu-leave-box wu-math-modal-box">
          <h3 class="wu-math-modal-title">수식 입력</h3>
          <div class="wu-math-modal-body">
            <label class="wu-math-modal-label">LaTeX</label>
            <input id="wu-math-input" type="text" class="wu-math-modal-input" placeholder="예: E=mc^2, \\sum_{i=0}^n i^2" autocomplete="off" spellcheck="false">
            <label class="wu-math-modal-label">정렬</label>
            <div class="wu-math-align-row">
              <button class="wu-math-align-btn active" data-align="left">◀ 왼쪽</button>
              <button class="wu-math-align-btn" data-align="center">■ 가운데</button>
              <button class="wu-math-align-btn" data-align="right">▶ 오른쪽</button>
            </div>
            <label class="wu-math-modal-label">미리보기</label>
            <div id="wu-math-preview" class="wu-math-modal-preview"></div>
          </div>
          <div class="modal-actions">
            <button id="wu-math-cancel" class="btn btn-sm">취소</button>
            <button id="wu-math-confirm" class="btn btn-sm btn-primary">삽입</button>
          </div>
        </div>`;
      document.body.appendChild(_mathModal);

      const _mathInput   = _mathModal.querySelector('#wu-math-input');
      const _mathPreview = _mathModal.querySelector('#wu-math-preview');
      const _mathConfirm = _mathModal.querySelector('#wu-math-confirm');
      const _mathCancel  = _mathModal.querySelector('#wu-math-cancel');
      const _mathAlignBtns = Array.from(_mathModal.querySelectorAll('.wu-math-align-btn'));

      _mathAlignBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          _mathCurrentAlign = btn.dataset.align;
          _mathAlignBtns.forEach(b => b.classList.toggle('active', b === btn));
          _mathPreview.dataset.align = _mathCurrentAlign;
        });
      });

      function _renderMathPreview() {
        const val = _mathInput.value.trim();
        if (!val) { _mathPreview.textContent = ''; return; }
        const _katex = window.TiptapBundle && window.TiptapBundle.katex;
        if (!_katex) { _mathPreview.textContent = val; return; }
        try {
          _katex.render(val, _mathPreview, { throwOnError: false, displayMode: true });
          _mathPreview.classList.remove('wu-math-preview-error');
        } catch (e) {
          _mathPreview.textContent = e.message;
          _mathPreview.classList.add('wu-math-preview-error');
        }
      }

      _mathInput.addEventListener('input', _renderMathPreview);

      function _closeMathModal() {
        _mathModal.style.display = 'none';
        _mathModalCallback = null;
      }

      _mathCancel.addEventListener('click', _closeMathModal);
      _mathConfirm.addEventListener('click', () => {
        const val = _mathInput.value.trim();
        if (val && _mathModalCallback) _mathModalCallback(val, _mathCurrentAlign);
        _closeMathModal();
      });
      _mathModal.addEventListener('click', e => {
        if (e.target === _mathModal) _closeMathModal();
      });
      _mathInput.addEventListener('keydown', e => {
        if (e.key === 'Escape') _closeMathModal();
        if (e.key === 'Enter') { e.preventDefault(); _mathConfirm.click(); }
      });
    }

    function _showMathModal(initialLatex, confirmLabel, initialAlign, onConfirm) {
      if (!_mathModal) {
        const latex = window.prompt('LaTeX 수식:', initialLatex || '') || '';
        if (latex) onConfirm(latex, initialAlign || 'center');
        return;
      }
      const _mathInput   = _mathModal.querySelector('#wu-math-input');
      const _mathPreview = _mathModal.querySelector('#wu-math-preview');
      const _mathConfirm = _mathModal.querySelector('#wu-math-confirm');
      const _mathAlignBtns = Array.from(_mathModal.querySelectorAll('.wu-math-align-btn'));

      // 정렬 초기화
      _mathCurrentAlign = initialAlign || 'center';
      _mathAlignBtns.forEach(b => b.classList.toggle('active', b.dataset.align === _mathCurrentAlign));

      _mathInput.value = initialLatex || '';
      _mathConfirm.textContent = confirmLabel || '삽입';
      _mathPreview.textContent = '';
      if (initialLatex) {
        const _katex = window.TiptapBundle && window.TiptapBundle.katex;
        if (_katex) try { _katex.render(initialLatex, _mathPreview, { throwOnError: false, displayMode: true }); } catch (_) {}
      }
      _mathModalCallback = onConfirm;
      _mathModal.style.display = 'flex';
      setTimeout(() => _mathInput.focus(), 30);
    }

    _mathSlashCmdFn = ed => {
      _showMathModal('', '삽입', 'center', (latex, align) => {
        ed.chain().focus().insertBlockMath({ latex, align }).run();
      });
    };

    function _showFootnoteModal(onConfirm, options = {}) {
      const initialContent = options.initialContent || '';
      const title = options.title || '각주 추가';
      const confirmLabel = options.confirmLabel || '삽입';
      const hint = options.hint || `Ctrl+Enter로 ${confirmLabel}`;
      if (!_footnoteModal) {
        const content = window.prompt('각주 내용:', initialContent) || '';
        onConfirm(content);
        return;
      }
      const input = _footnoteModal.querySelector('#wu-fn-input');
      const titleEl = _footnoteModal.querySelector('#wu-fn-title');
      const confirmBtn = _footnoteModal.querySelector('#wu-fn-confirm');
      const hintEl = _footnoteModal.querySelector('#wu-fn-hint');
      input.value = initialContent;
      if (titleEl) titleEl.textContent = title;
      if (confirmBtn) confirmBtn.textContent = confirmLabel;
      if (hintEl) hintEl.textContent = hint;
      _footnoteModalCallback = onConfirm;
      _footnoteModal.style.display = 'flex';
      setTimeout(() => { input.focus(); input.select(); }, 30);
    }

    function _insertFootnoteWithContent(ed, content) {
      const label = String((ed.getText().match(/\[\^[^\]]+\]:/g) || []).length + 1);
      ed.chain()
        .focus()
        .insertContent(`[^${label}]`)
        .command(({ tr, dispatch }) => {
          const schema = tr.doc.type.schema;
          const text = content ? `[^${label}]: ${content}` : `[^${label}]: `;
          const para = schema.nodes.paragraph.createChecked(null, [schema.text(text)]);
          if (dispatch) tr.insert(tr.doc.content.size, para);
          return true;
        })
        .run();
    }

    function _escapeRegExp(text) {
      return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function _findFootnoteDefinition(label) {
      if (!_editor || !label) return null;
      const defRe = new RegExp(`^\\\\?\\[\\^${_escapeRegExp(label)}\\\\?\\]:\\s?(.*)$`);
      let found = null;
      _editor.state.doc.descendants((node, pos) => {
        if (found || node.type.name !== 'paragraph') return;
        const text = node.textContent || '';
        const match = defRe.exec(text);
        if (!match) return;
        found = { node, pos, content: match[1] || '' };
        return false;
      });
      return found;
    }

    function _setFootnoteDefinition(label, content) {
      if (!_editor || !label) return;
      const text = `[^${label}]: ${content || ''}`;
      const found = _findFootnoteDefinition(label);
      if (found) {
        const from = found.pos + 1;
        const to = from + found.node.content.size;
        _editor.chain().focus().command(({ tr, dispatch }) => {
          if (dispatch) tr.insertText(text, from, to);
          return true;
        }).run();
        return;
      }

      _editor.chain()
        .focus()
        .command(({ tr, dispatch }) => {
          const schema = tr.doc.type.schema;
          const para = schema.nodes.paragraph.createChecked(null, [schema.text(text)]);
          if (dispatch) tr.insert(tr.doc.content.size, para);
          return true;
        })
        .run();
    }

    function _editFootnoteByLabel(label) {
      if (!_editor || !label) return;
      const found = _findFootnoteDefinition(label);
      _showFootnoteModal(
        content => _setFootnoteDefinition(label, content),
        {
          initialContent: found ? found.content : '',
          title: `각주 ${label} 수정`,
          confirmLabel: '수정',
          hint: 'Ctrl+Enter로 수정',
        }
      );
    }

    function _initFootnoteRefEditing(pmEl) {
      if (!pmEl || opts.canEdit === false) return;
      pmEl.addEventListener('click', e => {
        const target = e.target.closest && e.target.closest('.wu-editor-footnote-ref');
        if (!target || !pmEl.contains(target)) return;
        const match = (target.textContent || '').match(/\\?\[\^([^\\\]]+)\\?\]/);
        if (!match) return;
        e.preventDefault();
        e.stopPropagation();
        _editFootnoteByLabel(match[1]);
      });
    }

    _footnoteSlashCmdFn = ed => {
      _showFootnoteModal(content => _insertFootnoteWithContent(ed, content));
    };

    /* 슬래시 커맨드 메뉴 동적 생성 (두 패널 구조) */
    let _slashMenuEl  = null;  // 전체 컨테이너
    let _slashLeftEl  = null;  // 왼쪽 그룹 패널
    let _slashRightEl = null;  // 오른쪽 서브메뉴 패널
    if (opts.canEdit !== false) {
      _slashMenuEl = document.createElement('div');
      _slashMenuEl.className = 'wu-slash-menu';
      _slashMenuEl.style.display = 'none';

      _slashLeftEl = document.createElement('div');
      _slashLeftEl.className = 'wu-slash-panel wu-slash-panel--left';

      _slashRightEl = document.createElement('div');
      _slashRightEl.className = 'wu-slash-panel wu-slash-panel--right';
      _slashRightEl.style.display = 'none';

      _slashMenuEl.appendChild(_slashLeftEl);
      _slashMenuEl.appendChild(_slashRightEl);
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

    /* 콜아웃 타입 드롭다운 동적 생성 */
    let _calloutDropEl = null;
    if (opts.canEdit !== false) {
      _calloutDropEl = document.createElement('div');
      _calloutDropEl.className = 'wu-callout-drop';
      _calloutDropEl.style.display = 'none';
      CALLOUT_DROP_ITEMS.forEach(({ type, desc }) => {
        const cfg = CALLOUT_TYPES[type];
        const btn = document.createElement('button');
        btn.className = 'wu-callout-drop-btn';
        btn.dataset.calloutType = type;
        btn.innerHTML =
          `<span class="wu-callout-drop-icon">${cfg.icon}</span>` +
          `<span class="wu-callout-drop-name">${cfg.title}</span>` +
          `<span class="wu-callout-drop-desc">${desc}</span>`;
        _calloutDropEl.appendChild(btn);
      });
      document.body.appendChild(_calloutDropEl);
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
      md = _normalizeImageMarkdownSources(md || '');
      let m;
      // Obsidian format: ![alt|NNN](src)
      const regObs = /!\[([^\]]*)\]\(([^)]+)\)/g;
      while ((m = regObs.exec(md || '')) !== null) {
        const pi = m[1].lastIndexOf('|');
        if (pi !== -1) {
          const w = m[1].slice(pi + 1).trim();
          if (/^\d+$/.test(w)) _imgWidthMap[_normalizeMarkdownImageDest(m[2])] = parseInt(w);
        }
      }
      // Legacy HTML format: <img style="width:Npx">
      const regHtml = /<img[^>]*src="([^"]+)"[^>]*style="[^"]*width\s*:\s*(\d+)px[^"]*"[^>]*>/g;
      while ((m = regHtml.exec(md || '')) !== null) {
        const src = _unescapeMarkdownLinkDestination(m[1]);
        if (!_imgWidthMap[src]) _imgWidthMap[src] = parseInt(m[2]);
      }
      if (Object.keys(_imgWidthMap).length) setTimeout(_applyImgWidths, 200);
    }

    function _placeImgResizeToolbar(imgEl) {
      if (!_tbEl || !imgEl) return;
      const gap = 6;
      const margin = 8;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const r = imgEl.getBoundingClientRect();

      _tbEl.style.display = 'flex';
      const tw = _tbEl.offsetWidth;
      const th = _tbEl.offsetHeight;
      const maxLeft = vw ? Math.max(margin, vw - tw - margin) : margin;
      const maxTop = vh ? Math.max(margin, vh - th - margin) : margin;
      const imageLeft = _clampToRange(r.left, margin, maxLeft);
      const sideTop = _clampToRange(r.top, margin, maxTop);
      const placements = [
        {
          fits: r.bottom + th + gap <= vh - margin,
          top: r.bottom + gap,
          left: imageLeft,
        },
        {
          fits: r.top >= th + gap + margin,
          top: r.top - th - gap,
          left: imageLeft,
        },
        {
          fits: r.right + tw + gap <= vw - margin,
          top: sideTop,
          left: r.right + gap,
        },
        {
          fits: r.left >= tw + gap + margin,
          top: sideTop,
          left: r.left - tw - gap,
        },
      ];
      const chosen = placements.find(item => item.fits) || {
        top: _clampToRange(r.bottom + gap, margin, maxTop),
        left: imageLeft,
      };
      _tbEl.style.top = _clampToRange(chosen.top, margin, maxTop) + 'px';
      _tbEl.style.left = _clampToRange(chosen.left, margin, maxLeft) + 'px';
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
        if (e.target.tagName === 'IMG' && edEl && edEl.contains(e.target) && !e.target.closest('td, th')) {
          _activeImg = e.target;
        } else if (!_tbEl.contains(e.target)) {
          _activeImg = null;
        }
      }, true);

      let imgResizeFrame = 0;
      const scheduleImgResizeToolbarUpdate = () => {
        if (!_activeImg || _tbEl.style.display === 'none' || imgResizeFrame) return;
        imgResizeFrame = requestAnimationFrame(() => {
          imgResizeFrame = 0;
          if (!_activeImg || !document.body.contains(_activeImg)) {
            _tbEl.style.display = 'none';
            _activeImg = null;
            return;
          }
          _placeImgResizeToolbar(_activeImg);
        });
      };

      on(document, 'click', e => {
        const edEl = _getProseMirrorEl();
        if (e.target.tagName === 'IMG' && edEl && edEl.contains(e.target) && !e.target.closest('td, th')) {
          _placeImgResizeToolbar(e.target);
        } else if (!_tbEl.contains(e.target)) {
          _tbEl.style.display = 'none';
        }
      }, true);
      on(window, 'scroll', scheduleImgResizeToolbarUpdate, { capture: true, passive: true });
      on(window, 'resize', scheduleImgResizeToolbarUpdate);

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
          // HTML img 태그에서 width style 제거 (마크다운으로 변환 안 함)
          md = md.replace(new RegExp(`<img([^>]*)src="${esc2}"([^>]*)>`, 'g'),
            (_, pre, post) => {
              const attrs = (pre + post).replace(/\s*style="([^"]*)"/, (__, s) => {
                const noW = s.replace(/\bwidth\s*:\s*[^;]+;?\s*/g, '').trim();
                return noW ? ` style="${noW}"` : '';
              });
              return `<img${attrs}src="${src}">`;
            });
        } else {
          // 기존 Obsidian 포맷 너비 갱신: ![alt|OLD](src) → ![alt|NEW](src)
          md = md.replace(new RegExp(`!\\[([^\\]]*?)\\|\\d+\\]\\(${esc2}\\)`, 'g'),
            (_, alt) => `![${alt}|${px}](${src})`);
          // 일반 마크다운에 너비 추가: ![alt](src) → ![alt|NNN](src)
          md = md.replace(new RegExp(`!\\[([^\\]\\|]*)\\]\\(${esc2}\\)`, 'g'),
            (_, alt) => `![${alt}|${px}](${src})`);
          // HTML img 태그 너비 추가/갱신 (HTML 컨텍스트에서 마크다운으로 변환 안 함)
          md = md.replace(new RegExp(`<img([^>]*)src="${esc2}"([^>]*)>`, 'g'),
            (_, pre, post) => {
              let attrs = pre + post;
              if (/style\s*=\s*"/.test(attrs)) {
                attrs = attrs.replace(/style\s*=\s*"([^"]*)"/, (__, s) => {
                  const noW = s.replace(/\bwidth\s*:\s*[^;]+;?\s*/g, '').trim();
                  return `style="${noW ? noW + '; ' : ''}width: ${px}px"`;
                });
              } else {
                attrs = attrs.trimEnd() + ` style="width: ${px}px"`;
              }
              return `<img${attrs}src="${src}">`;
            });
        }
      }
      return md;
    }

    /* ── 첫 번째 줄 H1 강제 ────────────────────────────── */
    function _enforceH1Title() {
      if (!_editor) return;
      const state = _editor.state;
      const first = state.doc.firstChild;
      if (!first) return;
      if (first.type.name === 'heading' && first.attrs.level === 1) return;
      const tr = state.tr.setNodeMarkup(0, state.schema.nodes.heading, { level: 1 });
      tr.setMeta('titleEnforce', true);
      _editor.view.dispatch(tr);
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
        if (hooks.onSave) hooks.onSave(md, { auto: false, ignoreCooldown: true, leaveTarget: _leaveTarget || '/' });
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

    /* ── 슬래시 커맨드 (계층형 그룹 메뉴) ──────────────────────────────── */

    function _applySlashItem(item) {
      if (!_editor) return;
      const to   = _editor.state.selection.from;
      const from = _slashStart;
      _hideSlashMenu();
      _editor.chain().focus().deleteRange({ from, to }).run();
      item.cmd(_editor);
    }

    function _hideSlashMenu() {
      if (_slashMenuEl)  _slashMenuEl.style.display  = 'none';
      if (_slashRightEl) _slashRightEl.style.display = 'none';
      _slashActive   = false;
      _slashStart    = -1;
      _slashQuery    = '';
      _slashFiltered = [];
      _slashSubPanel = null;
      _slashFocus    = 'left';
      _slashMode     = 'groups';
    }

    /* 왼쪽 패널 선택 하이라이트 갱신 */
    function _updateLeftSelUi() {
      if (!_slashLeftEl) return;
      _slashLeftEl.querySelectorAll('.wu-slash-row').forEach((el, i) => {
        const active = (i === _slashSelIdx);
        el.classList.toggle('selected', active);
        if (active) el.scrollIntoView({ block: 'nearest' });
      });
    }

    /* 오른쪽 패널 선택 하이라이트 갱신 */
    function _updateRightSelUi() {
      if (!_slashRightEl) return;
      _slashRightEl.querySelectorAll('.wu-slash-row').forEach((el, i) => {
        const active = (i === _slashSubIdx);
        el.classList.toggle('selected', active);
        if (active) el.scrollIntoView({ block: 'nearest' });
      });
    }

    /* flat 모드(검색) 선택 UI 갱신 */
    function _updateFlatSelUi() {
      if (!_slashLeftEl) return;
      _slashLeftEl.querySelectorAll('.wu-slash-row').forEach((el, i) => {
        const active = (i === _slashSelIdx);
        el.classList.toggle('selected', active);
        if (active) el.scrollIntoView({ block: 'nearest' });
      });
    }

    /* 오른쪽 서브메뉴 패널 열기 */
    function _openSubPanel(group, rowEl) {
      if (!_slashRightEl || !group.children) return;
      _slashSubPanel = group;
      _slashSubIdx   = -1;
      _slashRightEl.classList.toggle('wu-slash-panel--grid', group.id === 'callout');
      _slashRightEl.innerHTML = group.children.map((it, i) => {
        const iconHtml = typeof it.icon === 'string' && it.icon.startsWith('<svg')
          ? it.icon
          : `<span style="font-weight:600;font-size:0.8rem">${it.icon || ''}</span>`;
        const descHtml = it.desc ? `<span class="wu-slash-desc">${esc(it.desc)}</span>` : '';
        return `<div class="wu-slash-row" data-idx="${i}">
          <span class="wu-slash-icon">${iconHtml}</span>
          <span class="wu-slash-label">${esc(it.label)}</span>${descHtml}
        </div>`;
      }).join('');
      _slashRightEl.style.display = _slashRightEl.classList.contains('wu-slash-panel--grid') ? 'grid' : 'block';
      _positionSubPanel(rowEl);
    }

    /* 서브메뉴 패널 닫기 */
    function _closeSubPanel() {
      if (_slashRightEl) _slashRightEl.style.display = 'none';
      _slashSubPanel = null;
      _slashFocus    = 'left';
    }

    /* 왼쪽 패널 렌더링 (그룹 모드) */
    function _renderGroupPanel() {
      if (!_slashLeftEl) return;
      _slashLeftEl.innerHTML = SLASH_GROUPS.map((g, i) => {
        const hasChildren = !!g.children;
        const iconHtml = typeof g.icon === 'string' && g.icon.startsWith('<svg')
          ? g.icon
          : `<span style="font-weight:600;font-size:0.8rem">${g.icon || ''}</span>`;
        return `<div class="wu-slash-row${hasChildren ? ' wu-slash-row--has-sub' : ''}${i === 0 ? ' selected' : ''}" data-gidx="${i}">
          <span class="wu-slash-icon">${iconHtml}</span>
          <span class="wu-slash-label">${esc(g.label)}</span>
        </div>`;
      }).join('');
    }

    /* 왼쪽 패널 렌더링 (flat 검색 모드) */
    function _renderFlatPanel(items) {
      if (!_slashLeftEl) return;
      _slashLeftEl.innerHTML = items.map((it, i) => {
        const iconHtml = typeof it.icon === 'string' && it.icon.startsWith('<svg')
          ? it.icon
          : `<span style="font-weight:600;font-size:0.8rem">${it.icon || ''}</span>`;
        const descHtml = it.desc ? `<span class="wu-slash-desc">${esc(it.desc)}</span>` : '';
        return `<div class="wu-slash-row${i === 0 ? ' selected' : ''}" data-fidx="${i}">
          <span class="wu-slash-icon">${iconHtml}</span>
          <span class="wu-slash-label">${esc(it.label)}</span>${descHtml}
        </div>`;
      }).join('');
    }

    /* 전체 메뉴 위치 지정 (커서 아래/위) */
    function _positionSlashMenu() {
      if (!_editor || !_slashMenuEl) return;
      const coords = _editor.view.coordsAtPos(_editor.state.selection.from);
      const mh = _slashMenuEl.offsetHeight || 240;
      const top = (window.innerHeight - coords.bottom > mh)
        ? coords.bottom + 4
        : coords.top - mh - 4;
      _slashMenuEl.style.top  = Math.max(4, top + window.scrollY) + 'px';
      _slashMenuEl.style.left = Math.min(coords.left, window.innerWidth - (_slashMenuEl.offsetWidth || 180) - 8) + 'px';
    }

    /* 서브메뉴 패널 위치: 선택된 행 오른쪽 정렬 */
    function _positionSubPanel(rowEl) {
      if (!_slashRightEl || !_slashMenuEl) return;
      const mr = _slashMenuEl.getBoundingClientRect();
      const rw = _slashRightEl.offsetWidth || 220;
      // 오른쪽 공간이 충분한지 확인
      const spaceRight = window.innerWidth - mr.right;
      if (spaceRight >= rw + 4) {
        _slashRightEl.style.left = '100%';
        _slashRightEl.style.right = '';
      } else {
        _slashRightEl.style.right = '100%';
        _slashRightEl.style.left = '';
      }
      // rowEl이 있으면 상단 정렬
      if (rowEl) {
        const rr = rowEl.getBoundingClientRect();
        const mm = _slashMenuEl.getBoundingClientRect();
        _slashRightEl.style.top = (rr.top - mm.top) + 'px';
      } else {
        _slashRightEl.style.top = '0';
      }
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

      if (!_slashQuery) {
        // 쿼리 없음 → 그룹 모드
        _slashMode   = 'groups';
        _slashSelIdx = 0;
        _closeSubPanel();
        _renderGroupPanel();
        // 첫 번째 그룹의 서브메뉴를 자동으로 열지 않음 — hover/→키로 열도록
      } else {
        // 쿼리 있음 → flat 검색 모드
        const filtered = _matchSlashFlat(_slashQuery);
        _slashFiltered = filtered;
        if (!filtered.length) { _slashMenuEl.style.display = 'none'; return; }
        _slashMode   = 'flat';
        _slashSelIdx = 0;
        _closeSubPanel();
        _renderFlatPanel(filtered);
      }

      _slashMenuEl.style.display = 'flex';
      _positionSlashMenu();
    }

    function _initSlashMenu() {
      if (!_slashMenuEl) return;

      /* 왼쪽 패널: mousedown으로 항목 실행 또는 서브메뉴 열기 */
      _slashLeftEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const row = e.target.closest('.wu-slash-row');
        if (!row) return;
        if (_slashMode === 'flat') {
          const idx = +row.dataset.fidx;
          if (_slashFiltered[idx]) _applySlashItem(_slashFiltered[idx]);
          return;
        }
        const gidx = +row.dataset.gidx;
        const group = SLASH_GROUPS[gidx];
        if (!group) return;
        if (group.children) {
          _slashSelIdx = gidx;
          _updateLeftSelUi();
          _slashFocus = 'left';
          _openSubPanel(group, row);
        } else {
          _applySlashItem(group);
        }
      });

      /* 왼쪽 패널: mousemove로 그룹 서브메뉴 자동 열기 */
      _slashLeftEl.addEventListener('mousemove', e => {
        const row = e.target.closest('.wu-slash-row');
        if (!row || _slashMode === 'flat') return;
        const gidx = +row.dataset.gidx;
        if (gidx === _slashSelIdx && _slashRightEl.style.display !== 'none') return; // 이미 열림
        _slashSelIdx = gidx;
        _updateLeftSelUi();
        const group = SLASH_GROUPS[gidx];
        if (group && group.children) {
          _openSubPanel(group, row);
        } else {
          _closeSubPanel();
        }
      });

      /* 오른쪽 패널: mousedown으로 항목 실행 */
      _slashRightEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const row = e.target.closest('.wu-slash-row');
        if (!row || !_slashSubPanel) return;
        const idx = +row.dataset.idx;
        if (_slashSubPanel.children[idx]) _applySlashItem(_slashSubPanel.children[idx]);
      });

      /* 오른쪽 패널: mouseenter로 선택 하이라이트 */
      _slashRightEl.addEventListener('mouseenter', e => {
        const row = e.target.closest('.wu-slash-row');
        if (!row) return;
        _slashSubIdx = +row.dataset.idx;
        _updateRightSelUi();
      }, true);

      /* 키보드 내비게이션 */
      on(document, 'keydown', e => {
        if (!_slashActive) return;

        if (_slashMode === 'flat') {
          if (e.key === 'ArrowDown') {
            e.preventDefault(); e.stopPropagation();
            _slashSelIdx = (_slashSelIdx + 1) % _slashFiltered.length;
            _updateFlatSelUi();
          } else if (e.key === 'ArrowUp') {
            e.preventDefault(); e.stopPropagation();
            _slashSelIdx = (_slashSelIdx - 1 + _slashFiltered.length) % _slashFiltered.length;
            _updateFlatSelUi();
          } else if (e.key === 'Enter') {
            e.preventDefault(); e.stopPropagation();
            if (_slashFiltered[_slashSelIdx]) _applySlashItem(_slashFiltered[_slashSelIdx]);
          } else if (e.key === 'Escape') {
            e.preventDefault(); _hideSlashMenu();
          }
          return;
        }

        // 그룹 모드
        if (_slashFocus === 'left') {
          if (e.key === 'ArrowDown') {
            e.preventDefault(); e.stopPropagation();
            _slashSelIdx = (_slashSelIdx + 1) % SLASH_GROUPS.length;
            _updateLeftSelUi();
            const g = SLASH_GROUPS[_slashSelIdx];
            if (g && g.children) {
              const rowEl = _slashLeftEl.querySelectorAll('.wu-slash-row')[_slashSelIdx];
              _openSubPanel(g, rowEl || null);
            } else {
              _closeSubPanel();
            }
          } else if (e.key === 'ArrowUp') {
            e.preventDefault(); e.stopPropagation();
            _slashSelIdx = (_slashSelIdx - 1 + SLASH_GROUPS.length) % SLASH_GROUPS.length;
            _updateLeftSelUi();
            const g = SLASH_GROUPS[_slashSelIdx];
            if (g && g.children) {
              const rowEl = _slashLeftEl.querySelectorAll('.wu-slash-row')[_slashSelIdx];
              _openSubPanel(g, rowEl || null);
            } else {
              _closeSubPanel();
            }
          } else if (e.key === 'ArrowRight') {
            e.preventDefault(); e.stopPropagation();
            const g = SLASH_GROUPS[_slashSelIdx];
            if (g && g.children) {
              if (_slashRightEl.style.display === 'none') {
                const rowEl = _slashLeftEl.querySelectorAll('.wu-slash-row')[_slashSelIdx];
                _openSubPanel(g, rowEl || null);
              }
              _slashFocus = 'right';
              _slashSubIdx = 0;
              _updateRightSelUi();
            } else if (g) {
              _applySlashItem(g);
            }
          } else if (e.key === 'Enter') {
            e.preventDefault(); e.stopPropagation();
            const g = SLASH_GROUPS[_slashSelIdx];
            if (!g) return;
            if (g.children) {
              const rowEl = _slashLeftEl.querySelectorAll('.wu-slash-row')[_slashSelIdx];
              _openSubPanel(g, rowEl || null);
              _slashFocus = 'right';
              _slashSubIdx = 0;
              _updateRightSelUi();
            } else {
              _applySlashItem(g);
            }
          } else if (e.key === 'Escape') {
            e.preventDefault(); _hideSlashMenu();
          }
        } else {
          // _slashFocus === 'right'
          const children = _slashSubPanel ? _slashSubPanel.children : [];
          const cols = _slashRightEl.classList.contains('wu-slash-panel--grid') ? 2 : 1;
          if (e.key === 'ArrowDown') {
            e.preventDefault(); e.stopPropagation();
            _slashSubIdx = (_slashSubIdx + cols) % children.length;
            _updateRightSelUi();
          } else if (e.key === 'ArrowUp') {
            e.preventDefault(); e.stopPropagation();
            _slashSubIdx = (_slashSubIdx - cols + children.length) % children.length;
            _updateRightSelUi();
          } else if (e.key === 'ArrowRight' && cols === 2) {
            e.preventDefault(); e.stopPropagation();
            const next = _slashSubIdx + 1;
            if (next < children.length) { _slashSubIdx = next; _updateRightSelUi(); }
          } else if (e.key === 'ArrowLeft') {
            e.preventDefault(); e.stopPropagation();
            if (cols === 2 && _slashSubIdx % 2 === 1) {
              _slashSubIdx -= 1;
              _updateRightSelUi();
            } else {
              _slashFocus = 'left';
              _slashSubIdx = -1;
              _updateRightSelUi();
              _updateLeftSelUi();
            }
          } else if (e.key === 'Enter') {
            e.preventDefault(); e.stopPropagation();
            if (children[_slashSubIdx]) _applySlashItem(children[_slashSubIdx]);
          } else if (e.key === 'Escape') {
            e.preventDefault(); _hideSlashMenu();
          }
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

    /* ── 콜아웃 드롭다운 ───────────────────────────── */
    function _showCalloutDrop(btnEl) {
      if (!_calloutDropEl) return;
      if (_calloutDropEl.style.display !== 'none') {
        _calloutDropEl.style.display = 'none';
        return;
      }
      const r = btnEl.getBoundingClientRect();
      _calloutDropEl.style.top  = (r.bottom + 4) + 'px';
      _calloutDropEl.style.left = r.left + 'px';
      _calloutDropEl.style.display = 'grid';
    }

    function _initCalloutDrop() {
      if (!_calloutDropEl) return;
      _calloutDropEl.addEventListener('mousedown', e => {
        e.preventDefault();
        const btn = e.target.closest('[data-callout-type]');
        if (!btn || !_editor) return;
        const type = btn.dataset.calloutType;
        _editor.chain().focus().insertContent({
          type: 'blockquote',
          content: [
            { type: 'paragraph', content: [{ type: 'text', text: `[!${type}]` }] },
            { type: 'paragraph' },
          ],
        }).run();
        _calloutDropEl.style.display = 'none';
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

    function _clampToRange(value, min, max) {
      if (max < min) return min;
      return Math.min(Math.max(value, min), max);
    }

    function _placeTableMenu(tableEl) {
      const gap = 6;
      const margin = 8;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const tr = tableEl.getBoundingClientRect();
      const focusedCell = _getFocusedCellDom();
      const cellRect = focusedCell && tableEl.contains(focusedCell)
        ? focusedCell.getBoundingClientRect()
        : null;

      _tableMenuEl.style.display = 'flex';
      const mw = _tableMenuEl.offsetWidth;
      const mh = _tableMenuEl.offsetHeight;
      const maxLeft = vw ? Math.max(margin, vw - mw - margin) : margin;
      const maxTop = vh ? Math.max(margin, vh - mh - margin) : margin;
      const tableLeft = _clampToRange(tr.left, margin, maxLeft);
      const cellLeft = cellRect ? _clampToRange(cellRect.left, margin, maxLeft) : tableLeft;
      const sideTopBase = cellRect ? cellRect.top : tr.top;
      const sideTop = _clampToRange(sideTopBase, margin, maxTop);
      const placements = [
        {
          fits: tr.top >= mh + gap + margin,
          top: tr.top - mh - gap,
          left: tableLeft,
        },
        {
          fits: tr.bottom + mh + gap <= vh - margin,
          top: tr.bottom + gap,
          left: tableLeft,
        },
        {
          fits: cellRect && cellRect.bottom + mh + gap <= vh - margin,
          top: cellRect ? cellRect.bottom + gap : margin,
          left: cellLeft,
        },
        {
          fits: cellRect && cellRect.top >= mh + gap + margin,
          top: cellRect ? cellRect.top - mh - gap : margin,
          left: cellLeft,
        },
        {
          fits: tr.right + mw + gap <= vw - margin,
          top: sideTop,
          left: tr.right + gap,
        },
        {
          fits: tr.left >= mw + gap + margin,
          top: sideTop,
          left: tr.left - mw - gap,
        },
      ];
      const fallbackTop = cellRect
        ? _clampToRange(cellRect.bottom + gap, margin, maxTop)
        : _clampToRange(Math.max(tr.top, margin), margin, maxTop);
      const chosen = placements.find(item => item.fits) || {
        top: fallbackTop,
        left: cellLeft,
      };
      _tableMenuEl.style.top = _clampToRange(chosen.top, margin, maxTop) + 'px';
      _tableMenuEl.style.left = _clampToRange(chosen.left, margin, maxLeft) + 'px';
    }

    function _updateTableMenu() {
      if (!_tableMenuEl || !_editor) return;
      if (_isDragging) return;
      if (!_editor.isActive('table')) {
        _tableMenuEl.style.display = 'none';
        return;
      }

      /* 컨텍스트 메뉴 위치 — 화면에 보이는 후보 위치를 우선 선택 */
      const { from } = _editor.state.selection;
      const domRef = _editor.view.domAtPos(from);
      const domNode = domRef.node;
      const tableEl = (domNode.nodeType === 1 ? domNode : domNode.parentElement)?.closest('table');
      if (!tableEl) { _tableMenuEl.style.display = 'none'; return; }
      _placeTableMenu(tableEl);
    }

    function _initTableMenu() {
      if (!_tableMenuEl) return;
      let tableMenuFrame = 0;
      const scheduleTableMenuUpdate = () => {
        if (_tableMenuEl.style.display === 'none' || tableMenuFrame) return;
        tableMenuFrame = requestAnimationFrame(() => {
          tableMenuFrame = 0;
          _updateTableMenu();
        });
      };
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
        if (_calloutDropEl && !_calloutDropEl.contains(e.target)) _calloutDropEl.style.display = 'none';
      }, false);

      /* mouseup에서 드래그 종료 — 이 시점에 한 번만 메뉴·포커스 갱신 */
      on(document, 'mouseup', () => {
        if (_isDragging) {
          _isDragging = false;
          setTimeout(_updateTableMenu, 50);
        }
      }, false);
      on(window, 'scroll', scheduleTableMenuUpdate, { capture: true, passive: true });
      on(window, 'resize', scheduleTableMenuUpdate);
    }

    /* ── syntax highlight 적용 ──────────────────────── */
    function _applyHighlight() {
      // lowlight (CodeBlockLowlight)가 Tiptap 레벨에서 토큰화를 담당하므로 별도 hljs 호출 불필요
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
          case 'highlight':    chain.toggleHighlight().run(); break;
          case 'clearformat':  chain.clearNodes().unsetAllMarks().run(); break;
          case 'hr':           chain.setHorizontalRule().run(); break;
          case 'quote':     chain.toggleBlockquote().run(); break;
          case 'callout':
            e.stopPropagation();
            _showCalloutDrop(btn);
            return;
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
          case 'inlinemath': {
            const pmSel = _editor.state.selection;
            if (pmSel.empty) {
              _editor.chain().focus().insertContent({ type: 'inlineMath', attrs: { latex: 'x' } }).run();
            } else {
              const text = _editor.state.doc.textBetween(pmSel.from, pmSel.to, '');
              _editor.chain().focus().deleteSelection().insertContent({ type: 'inlineMath', attrs: { latex: text } }).run();
            }
            return;
          }
          case 'math':
            _showMathModal('', '삽입', 'center', (latex, align) => {
              _editor.chain().focus().insertBlockMath({ latex, align }).run();
            });
            return;
          case 'comment': {
            const pmSel = _editor.state.selection;
            if (pmSel.empty) {
              _editor.chain().focus().insertContent('<span data-type="obsidian-comment">주석 내용</span>').run();
            } else {
              chain.toggleMark('obsidianComment').run();
            }
            return;
          }
          case 'footnote':
            _showFootnoteModal(content => _insertFootnoteWithContent(_editor, content));
            return;
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
          case 'comment':   active = _editor.isActive('obsidianComment'); break;
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

    function _initFootnoteModal() {
      if (!_footnoteModal) return;
      const input      = _footnoteModal.querySelector('#wu-fn-input');
      const cancelBtn  = _footnoteModal.querySelector('#wu-fn-cancel');
      const confirmBtn = _footnoteModal.querySelector('#wu-fn-confirm');

      function _apply() {
        _footnoteModal.style.display = 'none';
        if (_footnoteModalCallback) _footnoteModalCallback(input.value.trim());
        _footnoteModalCallback = null;
      }
      function _cancel() {
        _footnoteModal.style.display = 'none';
        _footnoteModalCallback = null;
      }

      cancelBtn.addEventListener('click', _cancel);
      confirmBtn.addEventListener('click', _apply);
      input.addEventListener('keydown', e => {
        if (e.key === 'Escape') _cancel();
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _apply(); }
      });
      _footnoteModal.addEventListener('click', e => {
        if (e.target === _footnoteModal) _cancel();
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
    _promptLinkFn = _promptLink;

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
        Extension,
        StarterKit,
        CodeBlockLowlight,
        lowlight,
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
        InlineMath,
        BlockMath,
        markdownItMath,
        InputRule,
        Mark,
        Plugin,
        PluginKey,
        Decoration,
        DecorationSet,
        getHTMLFromFragment,
        Fragment,
        katex,
      } = TiptapBundle;

      const canEdit = !!opts.canEdit;

      if (!canEdit) {
        /* 뷰어 모드 — 에디터 영역에 바로 렌더 */
        /* ProseMirror가 selection 등 트랜잭션마다 DOM을 재조정하므로
           onTransaction에서 _fixCallouts를 재실행해 callout div를 유지 */
        _editor = new Editor({
          element: containerEl,
          extensions: _buildExtensions({ StarterKit, CodeBlockLowlight, lowlight, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark, Superscript, InlineMath, BlockMath, markdownItMath, InputRule, Mark, Extension, Plugin, PluginKey, Decoration, DecorationSet, getHTMLFromFragment, Fragment, editableMath: false }),
          content: _preprocessHtmlTableImages(_preprocessViewerFootnotes(_preprocessViewerCallouts(_normalizeImageMarkdownSources(opts.initialMarkdown || '')))),
          editable: false,
          injectCSS: false,
          onTransaction: () => { _fixFootnoteAnchors(containerEl); _fixCallouts(containerEl); },
        });
        if (hooks.onReady) hooks.onReady(_editor);
        setTimeout(_applyHighlight, 150);
        setTimeout(_initCodeHeaders, 100);
        setTimeout(() => {
          _fixFootnoteAnchors(containerEl);
          _fixCallouts(containerEl);
          _fixInlineMarkdownImages(containerEl);
        }, 200);
        _parseInitialWidths(_normalizeImageMarkdownSources(opts.initialMarkdown || ''));
        if (Object.keys(_imgWidthMap).length) {
          const _viewerImgObs = new MutationObserver(_applyImgWidths);
          _viewerImgObs.observe(containerEl, { childList: true, subtree: true });
          setTimeout(_applyImgWidths, 300);
        }
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
        extensions: _buildExtensions({ StarterKit, CodeBlockLowlight, lowlight, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark, InlineMath, BlockMath, markdownItMath, InputRule, Mark, Extension, Plugin, PluginKey, Decoration, DecorationSet, getHTMLFromFragment, Fragment, editableMath: true }),
        content: _preprocessHtmlTableImages(_normalizeImageMarkdownSources(opts.initialMarkdown || '')),
        editable: true,
        injectCSS: false,
        onUpdate: ({ transaction }) => {
          if (transaction.getMeta('titleEnforce')) return;
          _enforceH1Title();
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
      if (pmEl) {
        _initFootnoteRefEditing(pmEl);
      }
      if (pmEl && feat.imageUpload && feat.imageUpload.endpoint) {
        _initImageUpload(pmEl);
      }

      if (feat.imageResize !== false) {
        setTimeout(() => _initImgResize(), 300);
      }

      _initTableMenu();
      _initLinkModal();
      _initFootnoteModal();
      _initSlashMenu();
      _initHeadingDrop();
      _initCalloutDrop();
      setTimeout(_initCodeHeaders, 100);

      if (hooks.onReady) hooks.onReady(_editor);
      setTimeout(_applyHighlight, 150);
      setTimeout(_enforceH1Title, 0);
    }

    function _buildExtensions({ StarterKit, CodeBlockLowlight, lowlight, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Link, Image, Markdown, Paragraph, Highlight, markdownItMark, Superscript, InlineMath, BlockMath, markdownItMath, InputRule, Mark, Extension, Plugin, PluginKey, Decoration, DecorationSet, getHTMLFromFragment, Fragment, editableMath }) {
      function markdownItObsidianComment(md) {
        md.inline.ruler.push('obsidian_comment', function(state, silent) {
          if (state.src.charCodeAt(state.pos) !== 0x25 || state.src.charCodeAt(state.pos + 1) !== 0x25) return false;
          const start = state.pos + 2;
          const end = state.src.indexOf('%%', start);
          if (end === -1) return false;
          if (!silent) {
            const token = state.push('obsidian_comment', '', 0);
            token.content = state.src.slice(start, end);
          }
          state.pos = end + 2;
          return true;
        });
        md.renderer.rules.obsidian_comment = (tokens, idx) => {
          const content = tokens[idx].content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          return `<span data-type="obsidian-comment">${content}</span>`;
        };
      }

      const EditorCalloutDecorations = Extension && Plugin && PluginKey && Decoration && DecorationSet
        ? Extension.create({
            name: 'editorCalloutDecorations',
            addProseMirrorPlugins() {
              return [
                new Plugin({
                  key: new PluginKey('editorCalloutDecorations'),
                  props: {
                    decorations(state) {
                      const decorations = [];
                      state.doc.descendants((node, pos) => {
                        if (node.type.name !== 'blockquote') return;
                        const first = node.firstChild;
                        if (!first || first.type.name !== 'paragraph') return;
                        const cssType = _getCalloutCssType(first.textContent);
                        if (!cssType) return;
                        decorations.push(Decoration.node(pos, pos + node.nodeSize, {
                          'data-callout-type': cssType,
                        }));
                      });
                      return DecorationSet.create(state.doc, decorations);
                    },
                  },
                }),
              ];
            },
          })
        : null;
      // @tiptap/extension-link은 markdown.serialize가 없어 tiptap-markdown이 <a href> HTML로
      // 직렬화한다. 이를 [text](href) 마크다운 형식으로 고정해 eid: round-trip을 보장한다.
      const EditorFootnoteDecorations = Extension && Plugin && PluginKey && Decoration && DecorationSet
        ? Extension.create({
            name: 'editorFootnoteDecorations',
            addProseMirrorPlugins() {
              const footnoteDefRe = /^\\?\[\^([^\\\]]+)\\?\]:/;
              const footnoteRefRe = /\\?\[\^([^\\\]\s]+)\\?\]/g;
              return [
                new Plugin({
                  key: new PluginKey('editorFootnoteDecorations'),
                  props: {
                    decorations(state) {
                      const decorations = [];
                      let dividerAdded = false;

                      state.doc.descendants((node, pos, parent) => {
                        if (node.type.name === 'paragraph') {
                          const defMatch = footnoteDefRe.exec(node.textContent || '');
                          if (defMatch) {
                            decorations.push(Decoration.node(pos, pos + node.nodeSize, {
                              'data-wu-footnote-def': 'true',
                            }));
                            if (!dividerAdded) {
                              decorations.push(Decoration.widget(pos, () => {
                                const divider = document.createElement('div');
                                divider.className = 'wu-editor-footnote-divider';
                                divider.setAttribute('contenteditable', 'false');
                                divider.setAttribute('aria-hidden', 'true');
                                return divider;
                              }, {
                                key: 'wu-editor-footnote-divider',
                                side: -1,
                              }));
                              dividerAdded = true;
                            }

                            node.descendants((child, relPos) => {
                              if (!child.isText) return;
                              const text = child.text || '';
                              const labelEnd = text.indexOf(':');
                              if (labelEnd < 0) return;
                              const abs = pos + 1 + relPos;
                              decorations.push(Decoration.inline(abs, abs + labelEnd + 1, {
                                class: 'wu-editor-footnote-label',
                              }));
                            });
                            return false;
                          }
                        }

                        if (!node.isText || !parent) return;
                        if (parent.type.name === 'codeBlock') return;
                        if (parent.type.name === 'paragraph' && footnoteDefRe.test(parent.textContent || '')) return;
                        if (node.marks && node.marks.some(mark => mark.type && mark.type.name === 'code')) return;

                        const text = node.text || '';
                        footnoteRefRe.lastIndex = 0;
                        let match;
                        while ((match = footnoteRefRe.exec(text))) {
                          decorations.push(Decoration.inline(pos + match.index, pos + match.index + match[0].length, {
                            class: 'wu-editor-footnote-ref',
                          }));
                        }
                      });

                      return DecorationSet.create(state.doc, decorations);
                    },
                  },
                }),
              ];
            },
          })
        : null;

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
                  _escapeMarkdownLinkDestination(node.attrs.src) + title + ")");
                state.closeBlock(node);
              },
              parse: {},
            },
          };
        },
      });

      function _nodeChildren(node) {
        return node?.content?.content || [];
      }

      function _hasSpan(cell) {
        return (cell.attrs.colspan || 1) > 1 || (cell.attrs.rowspan || 1) > 1;
      }

      function _hasDescendant(node, typeName) {
        let found = false;
        node.descendants(child => {
          if (child.type.name === typeName) {
            found = true;
            return false;
          }
          return undefined;
        });
        return found;
      }

      function _isInlineOnlyTableCell(cell) {
        if (!cell || _hasSpan(cell) || _hasDescendant(cell, 'image')) return false;
        return cell.childCount === 1 && cell.firstChild?.type?.name === 'paragraph';
      }

      function _isMarkdownSerializableTable(node) {
        const rows = _nodeChildren(node);
        const firstRow = rows[0];
        if (!firstRow) return false;
        if (_nodeChildren(firstRow).some(cell => cell.type.name !== 'tableHeader' || !_isInlineOnlyTableCell(cell))) {
          return false;
        }
        return rows.slice(1).every(row =>
          _nodeChildren(row).every(cell => cell.type.name === 'tableCell' && _isInlineOnlyTableCell(cell))
        );
      }

      function _formatHtmlBlock(html) {
        if (typeof document === 'undefined') return html;
        const template = document.createElement('template');
        template.innerHTML = String(html || '').trim();
        const element = template.content.firstElementChild;
        if (!element) return html;
        element.innerHTML = element.innerHTML.trim() ? `\n${element.innerHTML}\n` : `\n`;
        return element.outerHTML;
      }

      function _serializeNodeAsHtml(state, node) {
        if (typeof getHTMLFromFragment !== 'function' || !Fragment) {
          console.warn('[WUEditor] HTML table serialization helper is unavailable.');
          state.write('[table]');
          if (node.isBlock) state.closeBlock(node);
          return;
        }
        const html = getHTMLFromFragment(Fragment.from(node), node.type.schema);
        state.write(_formatHtmlBlock(html));
        if (node.isBlock) state.closeBlock(node);
      }

      const TableMd = Table.extend({
        addStorage() {
          return {
            markdown: {
              serialize(state, node) {
                if (!_isMarkdownSerializableTable(node)) {
                  _serializeNodeAsHtml(state, node);
                  return;
                }

                state.inTable = true;
                try {
                  node.forEach((row, p, i) => {
                    state.write('| ');
                    row.forEach((col, p2, j) => {
                      if (j) state.write(' | ');
                      const cellContent = col.firstChild;
                      if (cellContent?.textContent?.trim()) {
                        state.renderInline(cellContent);
                      }
                    });
                    state.write(' |');
                    state.ensureNewLine();
                    if (!i) {
                      const delimiterRow = Array.from({ length: row.childCount }).map(() => '---').join(' | ');
                      state.write(`| ${delimiterRow} |`);
                      state.ensureNewLine();
                    }
                  });
                  state.closeBlock(node);
                } finally {
                  state.inTable = false;
                }
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

      const InlineMathMd = InlineMath && InlineMath.extend({
        addInputRules() {
          // Obsidian 스타일: $content$ (단일 달러, 공백 없이 시작·끝)
          return [new InputRule({
            find: /(?<!\$)\$([^\s$][^$\n]*?[^\s$]|[^\s$])\$(?!\$)/,
            handler: ({ state, range, match }) => {
              const { tr } = state;
              tr.replaceWith(range.from, range.to, this.type.create({ latex: match[1] }));
            },
          })];
        },
        addStorage() {
          return {
            markdown: {
              serialize(state, node) {
                state.write('$' + (node.attrs.latex || '') + '$');
              },
              parse: { setup(md) { if (markdownItMath) md.use(markdownItMath); } },
            },
          };
        },
      });

      const BlockMathMd = BlockMath && BlockMath.extend({
        addAttributes() {
          return {
            ...this.parent?.(),
            align: {
              default: 'center',
              parseHTML: el => el.getAttribute('data-align') || 'center',
              renderHTML: attrs => ({ 'data-align': attrs.align || 'center' }),
            },
          };
        },
        addNodeView() {
          const parentView = this.parent?.();
          return (props) => {
            const view = parentView(props);
            // data-align을 wrapper DOM에 반영
            function _applyAlign() {
              const align = props.node.attrs.align || 'center';
              view.dom.setAttribute('data-align', align);
            }
            _applyAlign();
            const origUpdate = view.update;
            view.update = (node) => {
              const result = origUpdate ? origUpdate(node) : false;
              if (node.attrs.align !== props.node.attrs.align) {
                view.dom.setAttribute('data-align', node.attrs.align || 'center');
              }
              return result;
            };
            return view;
          };
        },
        addCommands() {
          return {
            ...this.parent?.(),
            insertBlockMath: (options) => ({ commands, editor }) => {
              const { latex, align, pos } = options;
              if (!latex) return false;
              return commands.insertContentAt(pos != null ? pos : editor.state.selection.from, {
                type: 'blockMath',
                attrs: { latex, align: align || 'center' },
              });
            },
            updateBlockMath: (options) => ({ editor, tr }) => {
              const { latex, align, pos } = options;
              let nodePos = pos != null ? pos : editor.state.selection.$from.pos;
              const node = editor.state.doc.nodeAt(nodePos);
              if (!node || node.type.name !== 'blockMath') return false;
              tr.setNodeMarkup(nodePos, undefined, { ...node.attrs, latex: latex ?? node.attrs.latex, align: align ?? node.attrs.align });
              return true;
            },
          };
        },
        addInputRules() {
          // $$ content $$ 한 줄짜리 블록 수식 → 단일 줄 입력 지원
          return [new InputRule({
            find: /^\$\$([^$\n]+)\$\$$/,
            handler: ({ state, range, match }) => {
              const { tr } = state;
              tr.replaceWith(range.from, range.to, this.type.create({ latex: match[1].trim() }));
            },
          })];
        },
        addStorage() {
          return {
            markdown: {
              serialize(state, node) {
                const latex = (node.attrs.latex || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
                const align = node.attrs.align || 'center';
                if (align !== 'center') {
                  // 비중앙 정렬은 HTML로 저장해야 재로드 시 data-align이 복원됨
                  state.write(`<div data-type="block-math" data-align="${align}" data-latex="${latex}"></div>`);
                } else {
                  state.write('$$\n' + (node.attrs.latex || '') + '\n$$');
                }
                state.closeBlock(node);
              },
              parse: {},
            },
          };
        },
      });

      function _mathOnClick(type) {
        return editableMath ? function(node, pos) {
          const curLatex = node.attrs.latex || '';
          const curAlign = node.attrs.align || 'center';
          _showMathModal(curLatex, '수정', curAlign, (nextLatex, nextAlign) => {
            if (nextLatex !== curLatex || nextAlign !== curAlign) {
              _editor.commands['update' + type + 'Math']({ latex: nextLatex, align: nextAlign, pos });
            }
          });
        } : undefined;
      }

      const mathExtensions = (InlineMathMd && BlockMathMd)
        ? [InlineMathMd.configure({ katexOptions: { throwOnError: false }, onClick: _mathOnClick('Inline') }),
           BlockMathMd.configure({ katexOptions: { throwOnError: false }, onClick: _mathOnClick('Block') })]
        : [];

      const ObsidianCommentMd = Mark.create({
        name: 'obsidianComment',
        parseHTML() {
          return [{ tag: 'span[data-type="obsidian-comment"]' }];
        },
        renderHTML() {
          return ['span', { 'data-type': 'obsidian-comment', class: 'wu-comment' }, 0];
        },
        addStorage() {
          return {
            markdown: {
              serialize: { open: '%%', close: '%%', mixable: true, expelEnclosingWhitespace: false },
              parse: { setup(md) { md.use(markdownItObsidianComment); } },
            },
          };
        },
      });

      return [
        StarterKit.configure({ link: false, paragraph: false, codeBlock: false }),
        CodeBlockLowlight.configure({ lowlight, defaultLanguage: null }),
        ParagraphMd,
        HighlightMd,
        ...(editableMath && EditorCalloutDecorations ? [EditorCalloutDecorations] : []),
        ...(editableMath && EditorFootnoteDecorations ? [EditorFootnoteDecorations] : []),
        ...mathExtensions,
        Superscript,
        TableMd.configure({ resizable: true }),
        TableRow,
        TableHeader,
        TableCell,
        TaskList,
        TaskItem.configure({ nested: true }),
        ObsidianCommentMd,
        LinkMd.configure({ openOnClick: false, autolink: true, protocols: ['eid'], validate: (url) => /^(https?:|\/|#|mailto:|eid:)/i.test(url) }),
        ImageMd,
        Markdown.configure({
          html: true,          // <img style="width:..."> 등 raw HTML 허용. TODO: DOMPurify 적용 필요
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
          const normalizedMd = _normalizeImageMarkdownSources(md || '');
          _editor.commands.setContent(normalizedMd);
          _imgWidthMap = {};
          _parseInitialWidths(normalizedMd);
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
        if (_footnoteModal && _footnoteModal.parentNode) _footnoteModal.parentNode.removeChild(_footnoteModal);
        if (_slashMenuEl   && _slashMenuEl.parentNode)   _slashMenuEl.parentNode.removeChild(_slashMenuEl);
        if (_headingDropEl && _headingDropEl.parentNode) _headingDropEl.parentNode.removeChild(_headingDropEl);
        if (_calloutDropEl && _calloutDropEl.parentNode) _calloutDropEl.parentNode.removeChild(_calloutDropEl);
        if (_acDd && _acDd.parentNode)             _acDd.parentNode.removeChild(_acDd);
        // 래퍼 제거
        const wrapEl = containerEl && containerEl.querySelector('.wu-editor-wrap');
        if (wrapEl && wrapEl.parentNode) wrapEl.parentNode.removeChild(wrapEl);
      },
    };
  }

  global.WUEditor = {
    create,
    splitTitleContent: _splitTitleContent,
    unescapeMarkdownText: _unescapeMarkdownText,
    normalizeImageMarkdownSources: _normalizeImageMarkdownSources,
  };
})(window);
