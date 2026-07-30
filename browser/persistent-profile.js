// Playwright helper for the persistent-profile Chrome install set up by
// INS-20260725-054500. Deliberately does NOT ship its own package.json /
// node_modules: it requires the `playwright` package straight out of the
// existing doc-worker browser-automation install at
// /opt/veridian/workspace/browser-tools (package.json pins
// "playwright": "^1.62.0-alpha-1783623505000", already installed there for
// the Playwright MCP server used by doc-worker-entrypoint.sh) so this task
// does not create a second, parallel Playwright install on the box.
'use strict';

const path = require('path');

const BROWSER_TOOLS_DIR = '/opt/veridian/workspace/browser-tools';
const { chromium } = require(path.join(BROWSER_TOOLS_DIR, 'node_modules', 'playwright'));

const CHROME_ROOT = '/opt/veridian/browser/chrome';
const CHROME_EXECUTABLE = path.join(CHROME_ROOT, 'opt/google/chrome/chrome');
const PROFILE_DIR = '/opt/veridian/browser/profile';
const LOCAL_LIBS = '/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu';
const EXTRA_LIBS = path.join(CHROME_ROOT, 'extra-libs/usr/lib/x86_64-linux-gnu');

// Same LD_LIBRARY_PATH composition as /opt/veridian/browser/chrome/google-chrome
// (the CLI launcher used for the headless smoke test) -- kept in sync so the
// Playwright path and the plain-CLI path resolve the same shared libraries.
function chromeEnv() {
  return Object.assign({}, process.env, {
    LD_LIBRARY_PATH: [
      path.join(CHROME_ROOT, 'opt/google/chrome'),
      EXTRA_LIBS,
      LOCAL_LIBS,
      process.env.LD_LIBRARY_PATH || '',
    ].filter(Boolean).join(':'),
  });
}

/**
 * Launch the real, installed Chrome binary against the shared persistent
 * profile directory. Returns the BrowserContext -- callers own its
 * lifecycle and must call context.close() when done so profile state
 * (cookies, local storage, any Google session the Owner has signed into)
 * flushes to disk.
 */
async function launchPersistentChrome(overrides = {}) {
  return chromium.launchPersistentContext(PROFILE_DIR, Object.assign({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
    args: ['--no-sandbox'],
    env: chromeEnv(),
  }, overrides));
}

module.exports = { launchPersistentChrome, CHROME_EXECUTABLE, PROFILE_DIR };
