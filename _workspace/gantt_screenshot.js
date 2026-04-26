// Gantt screenshot capture script for QA review
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SERVER_URL = 'https://192.168.0.18:8443';
const SCREENSHOT_DIR = 'D:\\Github\\WhatUdoin\\_workspace';
const FULL_PNG = path.join(SCREENSHOT_DIR, 'gantt_screenshot.png');
const CHILD_PNG = path.join(SCREENSHOT_DIR, 'gantt_child_closeup.png');
const DEBUG_LOGIN_PNG = path.join(SCREENSHOT_DIR, 'gantt_debug_login.png');
const DEBUG_AFTER_LOGIN_PNG = path.join(SCREENSHOT_DIR, 'gantt_debug_after_login.png');
const DEBUG_GANTT_RAW_PNG = path.join(SCREENSHOT_DIR, 'gantt_debug_raw.png');
const DUMP_HTML = path.join(SCREENSHOT_DIR, 'gantt_dom.html');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();

  // Collect console messages
  page.on('console', (msg) => {
    console.log(`[browser:${msg.type()}]`, msg.text());
  });
  page.on('pageerror', (err) => console.log('[pageerror]', err.message));

  try {
    console.log(`[1] Navigating to ${SERVER_URL}/`);
    await page.goto(`${SERVER_URL}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: DEBUG_LOGIN_PNG, fullPage: true });
    console.log('  saved', DEBUG_LOGIN_PNG);

    // Try login flow used in existing tests: open login modal, fill, submit
    const loginBtn = page.locator('button:has-text("로그인")').first();
    const loginBtnVisible = await loginBtn.isVisible().catch(() => false);
    console.log(`[2] login button visible: ${loginBtnVisible}`);

    const credentialSets = [
      { name: 'admin', password: 'admin123' },
      { name: 'admin', password: 'admin' },
    ];

    let loggedIn = false;
    for (const cred of credentialSets) {
      try {
        // re-evaluate the login button each attempt
        const btn = page.locator('button:has-text("로그인")').first();
        if (!(await btn.isVisible().catch(() => false))) {
          // Maybe already logged in
          const userBadge = await page.locator('text=admin').first().isVisible().catch(() => false);
          if (userBadge) {
            loggedIn = true;
            console.log('  appears already logged in');
            break;
          }
        } else {
          await btn.click();
          await page.waitForTimeout(300);
        }
        // Fill credentials
        await page.fill('#login-name', cred.name);
        await page.fill('#login-password', cred.password);
        const submit = page.locator('button[type="submit"]:has-text("로그인")');
        await submit.click();
        await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => {});
        await page.waitForTimeout(1500);
        // Detect logged-in indicator: presence of logout button or absence of login modal
        const loggedInIndicator = await page.locator('button:has-text("로그아웃")').first().isVisible().catch(() => false);
        const loginModalStillOpen = await page.locator('#login-name').isVisible().catch(() => false);
        if (loggedInIndicator || !loginModalStillOpen) {
          console.log(`  login success with ${cred.name}/${cred.password}`);
          loggedIn = true;
          break;
        } else {
          console.log(`  login failed with ${cred.name}/${cred.password}`);
        }
      } catch (e) {
        console.log(`  login attempt threw: ${e.message}`);
      }
    }

    if (!loggedIn) {
      console.log('[!] Could not log in with provided credentials. Continuing as guest.');
    }

    await page.screenshot({ path: DEBUG_AFTER_LOGIN_PNG, fullPage: true });
    console.log('  saved', DEBUG_AFTER_LOGIN_PNG);

    // Navigate to gantt page
    console.log(`[3] Navigating to ${SERVER_URL}/gantt`);
    await page.goto(`${SERVER_URL}/gantt`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(2500);

    await page.screenshot({ path: DEBUG_GANTT_RAW_PNG, fullPage: true });
    console.log('  saved', DEBUG_GANTT_RAW_PNG);

    // Save full page screenshot
    await page.screenshot({ path: FULL_PNG, fullPage: true });
    console.log('  saved', FULL_PNG);

    // Dump full DOM for inspection
    const html = await page.content();
    fs.writeFileSync(DUMP_HTML, html, 'utf8');
    console.log('  saved', DUMP_HTML);

    // Inspect gantt rows / bars to find a parent with children
    // Common conventions: parent rows have a toggle to expand, child rows are nested
    const inspection = await page.evaluate(() => {
      const out = { rows: [], bars: [], classes: {}, candidateChildSelectors: [] };
      const rows = document.querySelectorAll('.gantt-row, [class*="gantt"][class*="row"], [data-parent], [data-child], [data-event-id]');
      out.rowCount = rows.length;
      rows.forEach((r, i) => {
        if (i < 25) {
          out.rows.push({
            i,
            tag: r.tagName,
            classes: r.className,
            dataset: { ...r.dataset },
            text: (r.innerText || '').slice(0, 80),
          });
        }
      });
      const bars = document.querySelectorAll('.gantt-bar');
      out.barCount = bars.length;
      bars.forEach((b, i) => {
        if (i < 30) {
          const cs = window.getComputedStyle(b);
          out.bars.push({
            i,
            classes: b.className,
            dataset: { ...b.dataset },
            text: (b.innerText || '').slice(0, 60),
            bg: cs.backgroundColor,
            color: cs.color,
            border: cs.border,
            opacity: cs.opacity,
          });
        }
      });
      // Look for any element with "child" in class name
      const childCandidates = document.querySelectorAll('[class*="child"], [class*="sub"], [class*="parent"]');
      childCandidates.forEach((el, i) => {
        if (i < 30) {
          out.candidateChildSelectors.push({
            tag: el.tagName,
            classes: el.className,
            text: (el.innerText || '').slice(0, 80),
          });
        }
      });
      return out;
    });
    fs.writeFileSync(path.join(SCREENSHOT_DIR, 'gantt_dom_inspection.json'), JSON.stringify(inspection, null, 2), 'utf8');
    console.log('[4] DOM inspection complete');
    console.log('   rows=', inspection.rowCount, 'bars=', inspection.barCount);
    console.log('   childCandidates=', inspection.candidateChildSelectors.length);

    // Try to find a parent row with subtasks. Heuristic: an element marked as parent/group, or a row that has a chevron/caret to expand
    // First try common selectors
    const triedSelectors = [
      '.gantt-row.has-children',
      '.gantt-row[data-has-children]',
      '.gantt-row.parent',
      '.gantt-parent',
      '.gantt-group',
      '[data-parent="true"]',
      '.gantt-row[data-children-count]',
    ];
    let childCloseupSaved = false;
    for (const sel of triedSelectors) {
      const el = page.locator(sel).first();
      const ok = await el.isVisible().catch(() => false);
      if (ok) {
        console.log(`[5] Found parent via ${sel}`);
        // expand if there's a toggle button
        const toggle = el.locator('button, .toggle, .chevron, [class*="caret"], [class*="expand"]').first();
        if (await toggle.isVisible().catch(() => false)) {
          await toggle.click().catch(() => {});
          await page.waitForTimeout(400);
        }
        const box = await el.boundingBox();
        if (box) {
          // Capture parent row + a few more rows below for the children
          const clip = {
            x: Math.max(0, box.x - 10),
            y: Math.max(0, box.y - 10),
            width: Math.min(1580, box.width + 20),
            height: Math.min(800, box.height * 6 + 20),
          };
          await page.screenshot({ path: CHILD_PNG, clip });
          console.log('  saved closeup', CHILD_PNG);
          childCloseupSaved = true;
          break;
        }
      }
    }

    if (!childCloseupSaved) {
      // fallback: pick the first gantt-bar and expand context around it
      const firstBar = page.locator('.gantt-bar').first();
      if (await firstBar.isVisible().catch(() => false)) {
        const box = await firstBar.boundingBox();
        if (box) {
          const clip = {
            x: Math.max(0, box.x - 250),
            y: Math.max(0, box.y - 30),
            width: Math.min(1580, box.width + 600),
            height: Math.min(600, box.height * 8 + 60),
          };
          await page.screenshot({ path: CHILD_PNG, clip });
          console.log('  saved fallback closeup', CHILD_PNG);
        } else {
          console.log('  no bounding box for first bar');
        }
      } else {
        console.log('  no gantt-bar found at all');
      }
    }

    console.log('[done]');
  } catch (err) {
    console.error('FATAL:', err.message);
    try {
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'gantt_error.png'), fullPage: true });
    } catch (_) {}
    process.exitCode = 2;
  } finally {
    await browser.close();
  }
})();
