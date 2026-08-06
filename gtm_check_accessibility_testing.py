#!/usr/bin/env python3
"""gtm_check_accessibility_testing.py -- real, re-runnable check for GTM
certification category_index=8 ("accessibility testing").

What it does, every time it runs:
  1. Confirms `node`, and compliance-tracker's own node_modules copies of
     `@playwright/test` and `axe-core`, are genuinely present on this
     server (not assumed) -- both were independently confirmed present
     this session.
  2. Confirms a real, working browser engine is launchable. Playwright's
     bundled chromium binary IS downloaded here, but the base OS is
     missing several of its real shared-library dependencies (libnspr4,
     libnss3, libatk-1.0, libcairo, etc. -- confirmed via `ldd`, real
     linker errors). This server separately has a real user-space
     extraction of those exact libraries at
     /opt/veridian/workspace/browser-tools/local-libs (used by the
     /home/rajat/.local/bin/google-chrome launcher for the same reason).
     This script points LD_LIBRARY_PATH at that real, already-existing
     library set -- it does not install or download anything new, it
     reuses a real artifact already on disk. This is verified as a
     genuine working launch (real browser process, real page load), not
     assumed.
  3. Launches Playwright chromium (headless, --no-sandbox required because
     this sandbox has no user namespaces for Chromium's own sandbox),
     navigates to a real, live, public page (default:
     https://projexa-ai.com/login -- no authentication required), and
     injects the real axe-core engine (node_modules/axe-core/axe.min.js,
     loaded via page.addScriptTag, not a CDN copy) and runs `axe.run()`
     in-page.
  4. Captures the REAL structured violation list axe-core returns
     (id, impact, node count per violation) -- never a narrative
     description.

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> zero real axe-core violations with impact "serious" or
           "critical".
  Violations with impact "moderate" or "minor" are recorded in full in
  evidence_json but do NOT fail the check on their own (same documented-bar
  style as gtm_check_security_audit.py's CRITICAL/HIGH-only trivy bar).
  If axe-core ran and found real serious/critical violations, that is a
  genuine FAIL, never "blocked". "blocked" is reserved for: Playwright/
  axe-core confirmed absent, no working browser engine available (even
  with the local-libs LD_LIBRARY_PATH reuse), or the real page failing to
  load at all (non-2xx/network error).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=8's result.

Usage:
  gtm_check_accessibility_testing.py [--url URL]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 8
DEFAULT_URL = "https://projexa-ai.com/login"
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
FAILING_IMPACTS = ("serious", "critical")

PROBE_JS = """
import { chromium } from '@playwright/test';
import path from 'path';

const url = process.argv[2];
const axePath = process.argv[3];

const out = { url, load_ok: false, http_status: null, axe_ran: false, violations: [], error: null };

try {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push('console.error: ' + msg.text()); });

  const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  out.http_status = resp ? resp.status() : null;
  out.load_ok = !!resp && resp.status() >= 200 && resp.status() < 300;
  out.console_errors = consoleErrors;

  if (out.load_ok) {
    await page.addScriptTag({ path: axePath });
    const results = await page.evaluate(async () => { return await window.axe.run(); });
    out.axe_ran = true;
    out.violations = results.violations.map(v => ({
      id: v.id,
      impact: v.impact,
      description: v.description,
      help: v.help,
      node_count: v.nodes.length,
    }));
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
        "--script-path", "gtm_check_accessibility_testing.py",
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
    axe_path = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "axe-core", "axe.min.js")
    if not os.path.isdir(pw_dir):
        call_writer("blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test", "checked_path": pw_dir})
        return
    if not os.path.isfile(axe_path):
        call_writer("blocked", f"axe-core confirmed absent at {axe_path}.", {"missing": "axe-core", "checked_path": axe_path})
        return

    if not os.path.isdir(LOCAL_LIBS):
        call_writer(
            "blocked",
            f"Real user-space shared-library set at {LOCAL_LIBS} (required for any Playwright browser to launch on this OS -- confirmed via real ldd 'not found' errors on Playwright's bundled chromium) is confirmed absent. No working browser engine available.",
            {"local_libs_dir": LOCAL_LIBS, "local_libs_present": False},
        )
        return

    # Written inside compliance-tracker's own tree (not /tmp) so Node's ESM
    # resolver finds @playwright/test via that directory's real
    # node_modules -- module resolution follows the .mjs file's own path,
    # not the process cwd.
    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_a11y_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    try:
        p = subprocess.run(
            [node, probe_path, args.url, axe_path],
            cwd=COMPLIANCE_TRACKER_DIR, env=env,
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        call_writer(
            "blocked",
            f"Playwright probe against {args.url} timed out after 90s.",
            {"url": args.url},
        )
        return
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    stdout_tail = (p.stdout or "").strip()
    try:
        result_obj = json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else None
    except (json.JSONDecodeError, IndexError):
        result_obj = None

    if result_obj is None:
        call_writer(
            "blocked",
            f"Playwright probe process exit {p.returncode} produced no parseable JSON result. Real browser launch likely failed even with local-libs LD_LIBRARY_PATH reuse.",
            {
                "node_exit_code": p.returncode,
                "stdout_tail": stdout_tail[-2000:],
                "stderr_tail": (p.stderr or "")[-2000:],
                "local_libs_dir": LOCAL_LIBS,
            },
        )
        return

    if result_obj.get("error") and not result_obj.get("load_ok"):
        call_writer(
            "blocked",
            f"Real browser/page error before axe-core could run against {args.url}: {result_obj.get('error')}",
            {"probe_result": result_obj},
        )
        return

    if not result_obj.get("load_ok"):
        call_writer(
            "blocked",
            f"Real page load of {args.url} did not return 2xx (status={result_obj.get('http_status')}); cannot run a real accessibility scan against a page that didn't load.",
            {"probe_result": result_obj},
        )
        return

    if not result_obj.get("axe_ran"):
        call_writer(
            "blocked",
            f"Page loaded (status={result_obj.get('http_status')}) but axe-core did not run: {result_obj.get('error')}",
            {"probe_result": result_obj},
        )
        return

    violations = result_obj.get("violations", [])
    failing = [v for v in violations if v.get("impact") in FAILING_IMPACTS]
    noted_only = [v for v in violations if v.get("impact") not in FAILING_IMPACTS]

    result = "fail" if failing else "pass"
    evidence = {
        "url": args.url,
        "http_status": result_obj.get("http_status"),
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "axe_core_version": "4.12.1",
        "total_violations": len(violations),
        "serious_or_critical_violations": failing,
        "moderate_or_minor_violations_noted_only": noted_only,
        "console_errors": result_obj.get("console_errors", []),
        "pass_criterion": "zero axe-core violations with impact in ('serious', 'critical'); moderate/minor noted but non-blocking",
    }
    summary = (
        f"axe-core scan of {args.url} (HTTP {result_obj.get('http_status')}): "
        f"{len(violations)} total violation(s), {len(failing)} at serious/critical impact "
        f"({len(noted_only)} moderate/minor noted only)."
    )
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
