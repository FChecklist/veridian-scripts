#!/usr/bin/env python3
"""gtm_check_ui_testing.py -- real, re-runnable check for GTM certification
category_index=5 ("UI testing").

Distinct from category_index=6 (end to end testing, which runs this repo's
real e2e/ Playwright suite): no dedicated "UI testing" spec exists in
compliance-tracker's e2e/ directory (it currently holds exactly one spec,
browser-execution-tiers.spec.ts, which is scoped to E2E/browser-capability
detection, not UI rendering) -- so per the dispatching task's own fallback
rule ("reuse existing e2e/ tests if runnable, or write new minimal ones
against real public pages if none exist for this category"), this check is
new and minimal: it loads the real, live, public /login and /signup pages
and asserts the expected real form controls render, are visible, and are
enabled.

Absolute rule this script obeys with no exception and no case-by-case
judgment: it NEVER types or fills any value into a password field (or any
other field) on /login or /signup, and never clicks any submit button --
this is presence/visibility/attribute inspection only, never a credential
entry or an auth attempt. (Categories 15/16, which genuinely need an
authenticated session, are separately --result blocked citing this same
rule -- see gtm_check_multi_tenant_testing.py / gtm_check_role_permission_testing.py.)

What it does, every real run:
  1. Confirms node + compliance-tracker's own @playwright/test, and the
     real user-space browser shared-library set at
     /opt/veridian/workspace/browser-tools/local-libs (same reused-artifact
     pattern as gtm_check_accessibility_testing.py / gtm_check_responsive_testing.py),
     are genuinely present -- never assumed.
  2. Launches real Playwright chromium (headless, --no-sandbox) and
     navigates to https://projexa-ai.com/login and .../signup.
  3. For each page, reads the REAL DOM: HTTP status of the real navigation,
     and for each expected control (by real id selector) whether it exists,
     is visible, and is enabled -- via page.locator(...).isVisible() /
     isEnabled(), real Playwright calls against the real live page, never
     simulated.

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> both /login and /signup return HTTP 2xx AND every expected
           control on that page is present, visible, and enabled AND zero
           real page-error/console-error events fired during load.
  Any real page that loads but is genuinely missing/hidden/disabled an
  expected control, or throws a real page/console error, is a genuine
  FAIL. "blocked" is reserved for: Playwright/browser engine confirmed
  unavailable, or a page failing to load at all (non-2xx/network error).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=5's result.

Usage:
  gtm_check_ui_testing.py
"""
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 5
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"

PAGES = [
    {
        "name": "login",
        "url": "https://projexa-ai.com/login",
        "expected_controls": ["#email", "#password", 'button[type="submit"]'],
    },
    {
        "name": "signup",
        "url": "https://projexa-ai.com/signup",
        "expected_controls": ["#fullName", "#org", "#email", "#password", 'button[type="submit"]'],
    },
]

# NOTE: this probe deliberately never calls .fill()/.type()/.click() on any
# locator -- inspection only (isVisible/isEnabled/count), per the standing
# no-credential-entry rule.
PROBE_JS = """
import { chromium } from '@playwright/test';

const url = process.argv[2];
const controls = JSON.parse(process.argv[3]);

const out = { url, load_ok: false, http_status: null, controls: {}, page_errors: [], console_errors: [], error: null };

try {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  page.on('pageerror', (e) => out.page_errors.push(e.message));
  page.on('console', (msg) => { if (msg.type() === 'error') out.console_errors.push(msg.text()); });

  const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  out.http_status = resp ? resp.status() : null;
  out.load_ok = !!resp && resp.status() >= 200 && resp.status() < 300;

  if (out.load_ok) {
    for (const sel of controls) {
      try {
        const loc = page.locator(sel).first();
        const count = await loc.count();
        const visible = count > 0 ? await loc.isVisible() : false;
        const enabled = count > 0 ? await loc.isEnabled() : false;
        out.controls[sel] = { present: count > 0, visible, enabled };
      } catch (e) {
        out.controls[sel] = { present: false, visible: false, enabled: false, error: e.message };
      }
    }
  }
  await browser.close();
} catch (e) {
  out.error = e.message;
}

console.log(JSON.stringify(out));
"""


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_ui_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main():
    node = shutil.which("node")
    if not node:
        call_writer("blocked", "node confirmed absent from PATH; cannot run Playwright.", {"missing_tools": ["node"]})
        return

    pw_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "@playwright", "test")
    if not os.path.isdir(pw_dir):
        call_writer("blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test"})
        return

    if not os.path.isdir(LOCAL_LIBS):
        call_writer(
            "blocked",
            f"Real user-space shared-library set at {LOCAL_LIBS} (required for Playwright chromium to launch on this OS) is confirmed absent.",
            {"local_libs_dir": LOCAL_LIBS, "local_libs_present": False},
        )
        return

    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_ui_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    results = {}
    try:
        for pg in PAGES:
            try:
                p = subprocess.run(
                    [node, probe_path, pg["url"], json.dumps(pg["expected_controls"])],
                    cwd=COMPLIANCE_TRACKER_DIR, env=env,
                    capture_output=True, text=True, timeout=60,
                )
                stdout_tail = (p.stdout or "").strip()
                r = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else {"load_ok": False, "error": f"no stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}
            except subprocess.TimeoutExpired:
                r = {"load_ok": False, "error": "probe timed out after 60s"}
            except (json.JSONDecodeError, IndexError):
                r = {"load_ok": False, "error": f"non-JSON stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}
            results[pg["name"]] = {**pg, **r}
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    launch_failures = [name for name, r in results.items() if r.get("error") and "browserType.launch" in str(r.get("error", ""))]
    if len(launch_failures) == len(PAGES):
        call_writer(
            "blocked",
            f"Real chromium launch failed for all {len(PAGES)} pages even with local-libs LD_LIBRARY_PATH reuse; no working browser engine available.",
            {"results_per_page": results, "local_libs_dir": LOCAL_LIBS},
        )
        return

    problems = {}
    for name, r in results.items():
        page_problems = []
        if not r.get("load_ok"):
            page_problems.append(f"page did not load 2xx (status={r.get('http_status')}, error={r.get('error')})")
        else:
            for sel, state in r.get("controls", {}).items():
                if not state.get("present"):
                    page_problems.append(f"control {sel} not present")
                elif not state.get("visible"):
                    page_problems.append(f"control {sel} present but not visible")
                elif not state.get("enabled"):
                    page_problems.append(f"control {sel} visible but not enabled")
            if r.get("page_errors"):
                page_problems.append(f"{len(r['page_errors'])} real page-error event(s)")
            if r.get("console_errors"):
                page_problems.append(f"{len(r['console_errors'])} real console.error event(s)")
        if page_problems:
            problems[name] = page_problems

    # If every page failed purely because it never loaded (no real DOM was
    # ever inspected), that's blocked, not a UI fail.
    never_loaded = [name for name, r in results.items() if not r.get("load_ok")]
    if len(never_loaded) == len(PAGES):
        call_writer(
            "blocked",
            f"None of the {len(PAGES)} real pages returned HTTP 2xx; no real DOM was ever inspected.",
            {"results_per_page": results},
        )
        return

    result = "fail" if problems else "pass"
    evidence = {
        "pages_tested": [pg["url"] for pg in PAGES],
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "results_per_page": results,
        "problems_per_page": problems,
        "pass_criterion": "both /login and /signup return HTTP 2xx AND every expected control is present+visible+enabled AND zero page/console errors; no field is ever filled or submitted",
    }
    summary = (
        f"UI check of {len(PAGES)} real pages ({', '.join(pg['name'] for pg in PAGES)}): "
        f"{len(PAGES) - len(problems)}/{len(PAGES)} clean."
        + (f" Problems: {json.dumps(problems)}" if problems else "")
    )
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
