// Inspect computed styles of gantt elements that may be off-theme
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SERVER_URL = 'https://192.168.0.18:8443';
const OUT = 'D:\\Github\\WhatUdoin\\_workspace\\gantt_styles.json';
const TIGHT_CLOSEUP = 'D:\\Github\\WhatUdoin\\_workspace\\gantt_child_closeup.png';
const DARK_VARS_OUT = 'D:\\Github\\WhatUdoin\\_workspace\\gantt_theme_vars.json';

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();
  page.on('console', (m) => console.log('[browser]', m.text()));

  await page.goto(`${SERVER_URL}/gantt`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(2500);

  // Detect theme
  const theme = await page.evaluate(() => {
    return {
      bodyClass: document.body.className,
      htmlClass: document.documentElement.className,
      dataTheme: document.documentElement.getAttribute('data-theme'),
      bodyBg: getComputedStyle(document.body).backgroundColor,
      bodyColor: getComputedStyle(document.body).color,
    };
  });
  console.log('THEME', theme);

  // For all interesting selectors, dump computed style
  const selectors = [
    '#gantt-timeline',
    '#gantt-months',
    '.gantt-team-rows',
    '.gantt-name-rows',
    '.gantt-rows',
    '.gantt-row.row-proj-header',
    '.gantt-row.row-event',
    '.event-name-row.has-subtasks',
    '.event-name-row.row-subtask-name',
    '.gantt-bar',
    '.gantt-bar.overdue',
    '.gantt-toggle, [class*="toggle"]',
  ];

  const styles = {};
  for (const sel of selectors) {
    const data = await page.evaluate((s) => {
      const els = document.querySelectorAll(s);
      const out = [];
      for (let i = 0; i < Math.min(els.length, 4); i++) {
        const e = els[i];
        const cs = getComputedStyle(e);
        out.push({
          selector: s,
          index: i,
          classes: e.className,
          tag: e.tagName,
          text: (e.innerText || '').slice(0, 80),
          background: cs.backgroundColor,
          backgroundImage: cs.backgroundImage,
          color: cs.color,
          borderTop: cs.borderTopColor + ' ' + cs.borderTopWidth + ' ' + cs.borderTopStyle,
          borderBottom: cs.borderBottomColor + ' ' + cs.borderBottomWidth + ' ' + cs.borderBottomStyle,
          borderLeft: cs.borderLeftColor + ' ' + cs.borderLeftWidth + ' ' + cs.borderLeftStyle,
          borderRight: cs.borderRightColor + ' ' + cs.borderRightWidth + ' ' + cs.borderRightStyle,
          fontWeight: cs.fontWeight,
          opacity: cs.opacity,
          paddingLeft: cs.paddingLeft,
        });
      }
      return out;
    }, sel);
    styles[sel] = data;
  }

  // Inspect CSS variables for theming
  const themeVars = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const interesting = [
      '--bg', '--bg-1', '--bg-2', '--bg-3',
      '--surface', '--surface-1', '--surface-2',
      '--text', '--text-1', '--text-2', '--muted',
      '--border', '--border-1',
      '--gantt-bg', '--gantt-row-bg', '--gantt-grid-line',
      '--row-hover',
      '--accent', '--brand',
    ];
    const out = {};
    interesting.forEach((k) => { out[k] = root.getPropertyValue(k).trim(); });
    return out;
  });

  fs.writeFileSync(OUT, JSON.stringify({ theme, styles }, null, 2), 'utf8');
  fs.writeFileSync(DARK_VARS_OUT, JSON.stringify(themeVars, null, 2), 'utf8');
  console.log('saved', OUT, DARK_VARS_OUT);

  // Tighter closeup centered on FW개발 parent + children
  const parentRow = page.locator('.event-name-row.has-subtasks').first();
  if (await parentRow.isVisible().catch(() => false)) {
    const ganttArea = page.locator('#gantt-timeline, .gantt-team-rows').first();
    const box = await ganttArea.boundingBox();
    const parentBox = await parentRow.boundingBox();
    if (parentBox) {
      const clip = {
        x: 0,
        y: Math.max(0, parentBox.y - 40),
        width: 1200,
        height: 380,
      };
      await page.screenshot({ path: TIGHT_CLOSEUP, clip });
      console.log('saved tight closeup', TIGHT_CLOSEUP);
    }
  }

  await browser.close();
})();
