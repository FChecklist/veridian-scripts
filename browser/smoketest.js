// Smoke test for INS-20260725-054500: proves the
// Task Gateway -> Playwright -> Chrome Profile -> Navigate -> Extract
// mechanism works end to end using the real, just-installed Chrome binary
// and the real persistent profile directory. Deliberately uses a public,
// non-sensitive page (https://example.com) -- no Google login is attempted
// here; that step is exclusively Owner-manual (see
// ai-os/BROWSER_AUTOMATION_PROFILE_2026-07-25.yaml).
'use strict';

const { launchPersistentChrome } = require('./persistent-profile');

async function main() {
  const context = await launchPersistentChrome();
  try {
    const page = await context.newPage();
    await page.goto('https://example.com/', { waitUntil: 'load' });
    const title = await page.title();
    const heading = await page.locator('h1').innerText();
    const paragraph = await page.locator('p').first().innerText();
    const result = { ok: true, url: page.url(), title, heading, paragraph };
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await context.close();
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: String(err && err.stack || err) }));
  process.exit(1);
});
