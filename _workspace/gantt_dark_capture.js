// Set dark theme and re-capture gantt screenshots + computed styles
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SERVER_URL = 'https://192.168.0.18:8443';
const DIR = 'D:\\Github\\WhatUdoin\\_workspace';
const FULL_PNG = path.join(DIR, 'gantt_screenshot.png');
const CHILD_PNG = path.join(DIR, 'gantt_child_closeup.png');
const STYLES_OUT = path.join(DIR, 'gantt_styles.json');
const THEME_VARS_OUT = path.join(DIR, 'gantt_theme_vars.json');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();
  page.on('console', (m) => console.log('[browser]', m.text()));

  // Visit root first to seed localStorage on this origin
  await page.goto(`${SERVER_URL}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.evaluate(() => localStorage.setItem('theme', 'dark'));
  // Reload to apply theme
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);

  // Now go to gantt
  await page.goto(`${SERVER_URL}/gantt`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(2500);

  // Verify dark mode applied
  const themeCheck = await page.evaluate(() => ({
    dataTheme: document.documentElement.getAttribute('data-theme'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    bodyColor: getComputedStyle(document.body).color,
    htmlBg: getComputedStyle(document.documentElement).backgroundColor,
  }));
  console.log('THEME APPLIED:', themeCheck);

  // Full screenshot
  await page.screenshot({ path: FULL_PNG, fullPage: true });
  console.log('saved', FULL_PNG);

  // Computed styles for relevant selectors
  const selectors = [
    'body',
    '#gantt-timeline',
    '#gantt-months',
    '.gantt-team-rows',
    '.gantt-name-rows',
    '.gantt-rows',
    '.gantt-row.row-proj-header',
    '.gantt-row.row-event',
    '.event-name-row',
    '.event-name-row.has-subtasks',
    '.event-name-row.row-subtask-name',
    '.row-subtask-name .ev-date, .row-subtask-name .date',
    '.gantt-bar',
    '.gantt-bar.overdue',
    '.btn-subtask',
    '.btn-parent-goto',
    '.subtask-list',
    '.subtask-list li',
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
          index: i,
          classes: e.className,
          tag: e.tagName,
          text: (e.innerText || '').slice(0, 80),
          background: cs.backgroundColor,
          backgroundImage: cs.backgroundImage,
          color: cs.color,
          borderTop: `${cs.borderTopWidth} ${cs.borderTopStyle} ${cs.borderTopColor}`,
          borderBottom: `${cs.borderBottomWidth} ${cs.borderBottomStyle} ${cs.borderBottomColor}`,
          borderLeft: `${cs.borderLeftWidth} ${cs.borderLeftStyle} ${cs.borderLeftColor}`,
          fontWeight: cs.fontWeight,
          opacity: cs.opacity,
          paddingLeft: cs.paddingLeft,
          boxShadow: cs.boxShadow,
        });
      }
      return out;
    }, sel);
    styles[sel] = data;
  }
  fs.writeFileSync(STYLES_OUT, JSON.stringify({ theme: themeCheck, styles }, null, 2), 'utf8');
  console.log('saved', STYLES_OUT);

  // CSS variables
  const themeVars = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const interesting = [
      '--bg', '--bg-1', '--bg-2', '--bg-3',
      '--surface', '--surface-1', '--surface-2',
      '--text', '--text-1', '--text-2', '--muted',
      '--border', '--border-1', '--border-color',
      '--gantt-bg', '--gantt-row-bg', '--gantt-grid-line',
      '--row-hover',
      '--accent', '--brand', '--primary',
      '--card-bg', '--panel-bg',
    ];
    const out = {};
    interesting.forEach((k) => { out[k] = root.getPropertyValue(k).trim(); });
    return out;
  });
  fs.writeFileSync(THEME_VARS_OUT, JSON.stringify(themeVars, null, 2), 'utf8');
  console.log('saved', THEME_VARS_OUT);

  // Closeup near a parent-with-subtasks row
  const parent = page.locator('.event-name-row.has-subtasks').first();
  const isVisible = await parent.isVisible().catch(() => false);
  if (isVisible) {
    const box = await parent.boundingBox();
    if (box) {
      const clip = {
        x: 0,
        y: Math.max(0, box.y - 50),
        width: 1200,
        height: 420,
      };
      await page.screenshot({ path: CHILD_PNG, clip });
      console.log('saved closeup', CHILD_PNG);
    }
  }

  // Also dump rendered fragment for the parent + subtask block as HTML
  const fragmentHtml = await page.evaluate(() => {
    const parent = document.querySelector('.event-name-row.has-subtasks');
    if (!parent) return null;
    // climb up a couple of levels to get the surrounding rows container
    let parentRow = parent;
    for (let k = 0; k < 3; k++) if (parentRow.parentElement) parentRow = parentRow.parentElement;
    return parentRow.outerHTML.slice(0, 10000);
  });
  fs.writeFileSync(path.join(DIR, 'gantt_subtask_fragment.html'), fragmentHtml || '', 'utf8');

  await browser.close();
})();
