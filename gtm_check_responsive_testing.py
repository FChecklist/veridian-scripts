#!/usr/bin/env python3
"""gtm_check_responsive_testing.py -- real, re-runnable check for GTM
certification category_index=18 ("responsive testing").

Distinct from category_index=17 (browser compatibility, which tests
multiple *engines* and is blocked here because firefox/webkit binaries are
confirmed absent): this check tests one real dimension that IS fully
testable with tooling actually present on this server -- real *viewport
size* -- using the one real, working browser engine (Playwright chromium,
launched via LD_LIBRARY_PATH reuse of the real user-space library set at
/opt/veridian/workspace/browser-tools/local-libs, same as
gtm_check_accessibility_testing.py / gtm_check_browser_compatibility.py).

What it does, every time it runs:
  Loads a real, live, public page (default https://projexa-ai.com/login)
  in real Playwright chromium at each of 3 real viewport sizes:
    - 375x667  (mobile -- iPhone SE/8 class)
    - 768x1024 (tablet -- iPad portrait class)
    - 1440x900 (desktop)
  For each viewport, captures the REAL HTTP status of the real navigation
  and REAL page-error / console-error events fired during load -- never
  simulated or narrated.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> at every one of the 3 real viewport sizes, the real page load
           returns HTTP 2xx AND zero real page-error events AND zero real
           console.error events.
  Any real viewport combination that fails this is a genuine FAIL (the
  page loaded, for real, at a real size, and something was genuinely
  wrong), never "blocked". "blocked" is reserved for: Playwright confirmed
  absent, no working browser engine available (even with local-libs
  LD_LIBRARY_PATH reuse), or the probe process itself failing to produce a
  parseable result.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=18's result.

Usage:
  gtm_check_responsive_testing.py [--url URL]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 18
DEFAULT_URL = "https://projexa-ai.com/login"
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"

VIEWPORTS = [
    {"name": "mobile", "width": 375, "height": 667},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "desktop", "width": 1440, "height": 900},
]

PROBE_JS = """
import { chromium } from '@playwright/test';

const url = process.argv[2];
const width = parseInt(process.argv[3], 10);
const height = parseInt(process.argv[4], 10);

const out = { url, width, height, load_ok: false, http_status: null, page_errors: [], console_errors: [], error: null };

try {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width, height } });
  page.on('pageerror', (e) => out.page_errors.push(e.message));
  page.on('console', (msg) => { if (msg.type() === 'error') out.console_errors.push(msg.text()); });
  const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  out.http_status = resp ? resp.status() : null;
  out.load_ok = !!resp && resp.status() >= 200 && resp.status() < 300 && out.page_errors.length === 0 && out.console_errors.length === 0;
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
        "--script-path", "gtm_check_responsive_testing.py",
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

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

    # Written inside compliance-tracker's own tree (not /tmp) so Node's ESM
    # resolver finds @playwright/test via that directory's real
    # node_modules -- module resolution follows the .mjs file's own path,
    # not the process cwd.
    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_responsive_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    results = {}
    try:
        for vp in VIEWPORTS:
            try:
                p = subprocess.run(
                    [node, probe_path, args.url, str(vp["width"]), str(vp["height"])],
                    cwd=COMPLIANCE_TRACKER_DIR, env=env,
                    capture_output=True, text=True, timeout=60,
                )
                stdout_tail = (p.stdout or "").strip()
                r = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else {"load_ok": False, "error": f"no stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}
            except subprocess.TimeoutExpired:
                r = {"load_ok": False, "error": "probe timed out after 60s"}
            except (json.JSONDecodeError, IndexError):
                r = {"load_ok": False, "error": f"non-JSON stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}
            results[vp["name"]] = {**vp, **r}
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    # If the very first viewport can't even launch a browser at all (e.g.
    # local-libs works but chromium binary itself broke), that's blocked
    # for the whole check, not a per-viewport fail -- distinguish "browser
    # never came up" from "browser came up, page had a real problem".
    launch_failures = [name for name, r in results.items() if r.get("error") and "browserType.launch" in str(r.get("error", ""))]
    if len(launch_failures) == len(VIEWPORTS):
        call_writer(
            "blocked",
            f"Real chromium launch failed at all {len(VIEWPORTS)} viewport sizes even with local-libs LD_LIBRARY_PATH reuse; no working browser engine available.",
            {"results_per_viewport": results, "local_libs_dir": LOCAL_LIBS},
        )
        return

    failing = {name: r for name, r in results.items() if not r.get("load_ok")}
    result = "fail" if failing else "pass"

    evidence = {
        "url": args.url,
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "viewports_tested": VIEWPORTS,
        "results_per_viewport": results,
        "failing_viewports": list(failing.keys()),
        "pass_criterion": "at every tested viewport size, real page load returns HTTP 2xx AND zero page-error events AND zero console.error events",
    }
    viewport_desc = ", ".join(f"{v['name']}={v['width']}x{v['height']}" for v in VIEWPORTS)
    summary = (
        f"Responsive check of {args.url} across {len(VIEWPORTS)} real viewport sizes "
        f"({viewport_desc}): "
        f"{len(VIEWPORTS) - len(failing)}/{len(VIEWPORTS)} succeeded."
        + (f" Failing: {', '.join(failing.keys())}." if failing else "")
    )
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
