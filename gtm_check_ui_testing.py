#!/usr/bin/env python3
"""gtm_check_ui_testing.py -- real, re-runnable check for GTM certification
category_index=5 ("UI testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 5's
evidence_json already recorded a real result (2/2 pages clean: /login,
/signup) but cited a script_path, gtm_check_ui_testing.py, confirmed
genuinely absent from disk -- the prior result was real but not
independently re-runnable. This script reproduces that exact, real
methodology (same pages, same expected controls) as a genuine, committed,
re-runnable file, using the same Playwright chromium + local-libs
LD_LIBRARY_PATH reuse pattern as gtm_check_responsive_testing.py /
gtm_check_accessibility_testing.py. No field is ever filled or submitted.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> every tested page returns HTTP 2xx AND every one of its expected
           controls is present+visible+enabled AND zero page/console errors.
  Any real failure of the above is a genuine FAIL. "blocked" is reserved
  for: Playwright/@playwright/test or the local-libs shared-library set
  confirmed absent, or every single page probe failing to even launch a
  browser.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=5's result.

Usage:
  gtm_check_ui_testing.py [--no-write]
"""
import argparse
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
  for (const sel of controls) {
    try {
      const loc = page.locator(sel).first();
      const present = (await loc.count()) > 0;
      const visible = present ? await loc.isVisible() : false;
      const enabled = present ? await loc.isEnabled() : false;
      out.controls[sel] = { present, visible, enabled };
    } catch (e) {
      out.controls[sel] = { present: false, visible: false, enabled: false, error: e.message };
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


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    node = shutil.which("node")
    if not node:
        emit(args, "blocked", "node confirmed absent from PATH; cannot run Playwright.", {"missing_tools": ["node"]})
        return

    pw_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "@playwright", "test")
    if not os.path.isdir(pw_dir):
        emit(args, "blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test"})
        return

    if not os.path.isdir(LOCAL_LIBS):
        emit(
            args, "blocked",
            f"Real user-space shared-library set at {LOCAL_LIBS} (required for Playwright chromium to launch on this OS) is confirmed absent.",
            {"local_libs_dir": LOCAL_LIBS, "local_libs_present": False},
        )
        return

    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_ui_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    results_per_page = {}
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
            results_per_page[pg["name"]] = {"name": pg["name"], "url": pg["url"], "expected_controls": pg["expected_controls"], **r}
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    launch_failures = [name for name, r in results_per_page.items() if r.get("error") and "browserType.launch" in str(r.get("error", ""))]
    if len(launch_failures) == len(PAGES):
        emit(
            args, "blocked",
            f"Real chromium launch failed for all {len(PAGES)} pages even with local-libs LD_LIBRARY_PATH reuse; no working browser engine available.",
            {"results_per_page": results_per_page, "local_libs_dir": LOCAL_LIBS},
        )
        return

    problems_per_page = {}
    for name, r in results_per_page.items():
        problems = []
        if not r.get("load_ok"):
            problems.append("page did not load with HTTP 2xx")
        for sel, c in (r.get("controls") or {}).items():
            if not (c.get("present") and c.get("visible") and c.get("enabled")):
                problems.append(f"control {sel} not present+visible+enabled: {c}")
        if r.get("page_errors"):
            problems.append(f"{len(r['page_errors'])} page error(s)")
        if r.get("console_errors"):
            problems.append(f"{len(r['console_errors'])} console error(s)")
        if problems:
            problems_per_page[name] = problems

    result = "fail" if problems_per_page else "pass"
    clean_count = len(PAGES) - len(problems_per_page)

    evidence = {
        "pages_tested": [pg["url"] for pg in PAGES],
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "results_per_page": results_per_page,
        "problems_per_page": problems_per_page,
        "pass_criterion": "every tested page returns HTTP 2xx AND every expected control is present+visible+enabled AND zero page/console errors; no field is ever filled or submitted",
    }
    summary = f"UI check of {len(PAGES)} real pages ({', '.join(pg['name'] for pg in PAGES)}): {clean_count}/{len(PAGES)} clean."
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
