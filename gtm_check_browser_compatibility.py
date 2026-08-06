#!/usr/bin/env python3
"""gtm_check_browser_compatibility.py -- real, re-runnable check for GTM
certification category_index=17 ("browser compatibility").

What it does, every time it runs:
  Attempts a REAL Playwright launch + real page load of a real public page
  (default https://projexa-ai.com/login) against all three browser engines
  Playwright supports: chromium, firefox, webkit.

  Fresh, real tool-presence check every run (never assumed):
    - chromium: Playwright's bundled binary IS downloaded on this server,
      but the OS is missing its real shared-library deps. This script
      reuses the real, already-existing user-space library extraction at
      /opt/veridian/workspace/browser-tools/local-libs (same one
      /home/rajat/.local/bin/google-chrome uses) via LD_LIBRARY_PATH --
      confirmed this makes chromium launch and load pages for real.
    - firefox / webkit: their Playwright browser binaries are CONFIRMED
      ABSENT from ~/.cache/ms-playwright (no firefox-*/webkit-* dirs) --
      checked by real path existence, and by a real launch attempt, which
      fails with a real "Executable doesn't exist" error. This script does
      NOT download them (no `playwright install`) -- that would be
      installing new tooling never asked for, same standing rule as the
      "don't npm install a new devDependency" instruction for category 1.

Because a real "browser compatibility" claim requires genuinely testing
more than one distinct engine, and 2 of the 3 real engines this check
depends on are confirmed absent (not merely "found a problem when run" --
they cannot run at all), this category is --result blocked, not
pass/fail -- consistent with "mark blocked for any category where the
required tool is confirmed absent". The one real, working engine
(chromium)'s result is still captured in full in evidence_json so it is
not wasted, and category_index=18 (responsive testing, same page, same
one real working engine, different real check dimension: viewport size
rather than engine identity) is covered by a separate script that can
give a genuine pass/fail using only the tooling actually available.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=17's result.

Usage:
  gtm_check_browser_compatibility.py [--url URL]
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
CATEGORY_INDEX = 17
DEFAULT_URL = "https://projexa-ai.com/login"
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
ENGINES = ("chromium", "firefox", "webkit")

PROBE_JS = """
import { chromium, firefox, webkit } from '@playwright/test';

const url = process.argv[2];
const engineName = process.argv[3];
const engines = { chromium, firefox, webkit };
const engine = engines[engineName];

const out = { engine: engineName, url, launch_ok: false, load_ok: false, http_status: null, page_errors: [], console_errors: [], error: null };

try {
  const browser = await engine.launch({ args: engineName === 'chromium' ? ['--no-sandbox', '--disable-dev-shm-usage'] : [] });
  out.launch_ok = true;
  const page = await browser.newPage();
  page.on('pageerror', (e) => out.page_errors.push(e.message));
  page.on('console', (msg) => { if (msg.type() === 'error') out.console_errors.push(msg.text()); });
  const resp = await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  out.http_status = resp ? resp.status() : null;
  out.load_ok = !!resp && resp.status() >= 200 && resp.status() < 300 && out.page_errors.length === 0;
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
        "--script-path", "gtm_check_browser_compatibility.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def run_engine(node, probe_path, url, engine, env):
    try:
        p = subprocess.run(
            [node, probe_path, url, engine],
            cwd=COMPLIANCE_TRACKER_DIR, env=env,
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"engine": engine, "url": url, "launch_ok": False, "load_ok": False, "error": "probe timed out after 60s"}

    stdout_tail = (p.stdout or "").strip()
    try:
        return json.loads(stdout_tail.splitlines()[-1]) if stdout_tail else {"engine": engine, "launch_ok": False, "load_ok": False, "error": f"no stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}
    except (json.JSONDecodeError, IndexError):
        return {"engine": engine, "launch_ok": False, "load_ok": False, "error": f"non-JSON stdout (exit {p.returncode}); stderr: {(p.stderr or '')[-500:]}"}


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

    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    installed_dirs = os.listdir(cache_dir) if os.path.isdir(cache_dir) else []
    engine_binary_present = {
        "chromium": any(d.startswith("chromium-") for d in installed_dirs),
        "firefox": any(d.startswith("firefox-") for d in installed_dirs),
        "webkit": any(d.startswith("webkit-") for d in installed_dirs),
    }

    # Written inside compliance-tracker's own tree (not /tmp) so Node's ESM
    # resolver finds @playwright/test via that directory's real
    # node_modules -- module resolution follows the .mjs file's own path,
    # not the process cwd.
    probe_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_browser_compat_probe.mjs")
    with open(probe_path, "w") as f:
        f.write(PROBE_JS)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    results = {}
    try:
        for engine in ENGINES:
            if not engine_binary_present[engine]:
                results[engine] = {
                    "engine": engine, "url": args.url, "launch_ok": False, "load_ok": False,
                    "error": f"{engine} browser binary confirmed absent from {cache_dir} (no {engine}-* dir); not installed, not downloaded by this script.",
                }
                continue
            results[engine] = run_engine(node, probe_path, args.url, engine, env)
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)

    working_engines = [e for e, r in results.items() if r.get("load_ok")]
    absent_engines = [e for e in ENGINES if not engine_binary_present[e]]
    tested_but_failed = [e for e in ENGINES if engine_binary_present[e] and not results[e].get("load_ok")]

    evidence = {
        "url": args.url,
        "engines_attempted": list(ENGINES),
        "engine_binary_present": engine_binary_present,
        "results_per_engine": results,
        "working_engine_count": len(working_engines),
        "absent_engine_count": len(absent_engines),
        "local_libs_dir_used_for_chromium": LOCAL_LIBS,
        "pass_criterion": "all 3 real Playwright engines (chromium, firefox, webkit) successfully load the real page; blocked if any required engine binary is confirmed absent rather than genuinely tested",
    }
    summary_base = (
        f"Browser compatibility check against {args.url}: {len(working_engines)}/{len(ENGINES)} engines "
        f"loaded successfully ({', '.join(working_engines) or 'none'}). "
    )

    if absent_engines:
        # Can't genuinely certify cross-browser compatibility when a
        # required engine was never actually tested (binary confirmed
        # absent, not installed by this script) -- blocked, not fail.
        summary = summary_base + (
            f"{len(absent_engines)}/{len(ENGINES)} engine binaries confirmed absent on this server "
            f"({', '.join(absent_engines)}), not installed by this script (would require a new download, "
            f"not just checking present tooling). A real cross-browser compatibility claim needs all 3 "
            f"engines genuinely tested."
        )
        call_writer("blocked", summary, evidence)
    elif tested_but_failed:
        summary = summary_base + f"{len(tested_but_failed)} engine(s) were genuinely tested and failed to load the page: {', '.join(tested_but_failed)}."
        call_writer("fail", summary, evidence)
    else:
        summary = summary_base + "all 3 engines genuinely tested and all succeeded."
        call_writer("pass", summary, evidence)


if __name__ == "__main__":
    main()
