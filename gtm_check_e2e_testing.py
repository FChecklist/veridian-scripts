#!/usr/bin/env python3
"""gtm_check_e2e_testing.py -- real, re-runnable check for GTM certification
category_index=6 ("end to end testing").

Distinct from category_index=5 (UI testing, a new minimal probe this
session wrote because no dedicated UI spec existed): compliance-tracker's
e2e/ directory DOES already hold a real, runnable Playwright spec
(e2e/browser-execution-tiers.spec.ts) -- per the dispatching task's own
rule ("reuse the existing e2e/ tests if any exist and are runnable
headless"), this check reuses it as-is rather than writing a new one.

What it does, every real run:
  1. Confirms node + compliance-tracker's own @playwright/test, and the
     real user-space browser shared-library set at
     /opt/veridian/workspace/browser-tools/local-libs (same reused-artifact
     pattern as every other Playwright-based gtm_check_*.py in this repo),
     are genuinely present -- never assumed.
  2. Runs the real command `npx playwright test e2e/ --reporter=json`
     inside compliance-tracker, with LD_LIBRARY_PATH pointed at the real
     local-libs set so the bundled chromium can actually launch.
  3. Parses Playwright's own real JSON reporter output (stats.expected /
     stats.unexpected / stats.skipped / stats.flaky) -- never a narrative
     description of "the tests passed".

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> stats.unexpected == 0 AND stats.expected > 0 (at least one real
           test genuinely ran and passed; zero collected tests is treated
           as blocked, not a vacuous pass).
  Any real run producing stats.unexpected > 0 is a genuine FAIL. "blocked"
  is reserved for: Playwright/browser engine confirmed unavailable, the
  `playwright test` process itself failing to produce parseable JSON, or
  zero tests being collected (e2e/ empty or misconfigured).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=6's result.

Usage:
  gtm_check_e2e_testing.py
"""
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 6
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
REPORT_PATH = os.path.join(COMPLIANCE_TRACKER_DIR, ".gtm_e2e_report.json")


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_e2e_testing.py",
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
    npx = shutil.which("npx")
    if not npx:
        call_writer("blocked", "npx confirmed absent from PATH; cannot run `playwright test`.", {"missing_tools": ["npx"]})
        return

    pw_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "node_modules", "@playwright", "test")
    if not os.path.isdir(pw_dir):
        call_writer("blocked", f"@playwright/test confirmed absent at {pw_dir}.", {"missing": "@playwright/test"})
        return

    e2e_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "e2e")
    if not os.path.isdir(e2e_dir):
        call_writer("blocked", f"e2e/ directory confirmed absent at {e2e_dir}.", {"missing": "e2e/"})
        return

    if not os.path.isdir(LOCAL_LIBS):
        call_writer(
            "blocked",
            f"Real user-space shared-library set at {LOCAL_LIBS} (required for Playwright chromium to launch on this OS) is confirmed absent.",
            {"local_libs_dir": LOCAL_LIBS, "local_libs_present": False},
        )
        return

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    if os.path.isfile(REPORT_PATH):
        os.remove(REPORT_PATH)

    cmd = ["npx", "playwright", "test", "e2e/", "--reporter=json"]
    try:
        p = subprocess.run(
            cmd, cwd=COMPLIANCE_TRACKER_DIR, env=env,
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        call_writer("blocked", "`npx playwright test e2e/` timed out after 180s.", {"command": cmd})
        return
    except FileNotFoundError as e:
        call_writer("blocked", f"command not found running playwright test: {e}", {"command": cmd})
        return

    stdout_tail = (p.stdout or "").strip()
    report = None
    if stdout_tail:
        try:
            report = json.loads(stdout_tail)
        except json.JSONDecodeError:
            # some playwright versions print non-JSON progress lines before
            # the final JSON blob -- try the last line, then give up honestly
            try:
                report = json.loads(stdout_tail.splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                report = None

    if report is None:
        call_writer(
            "blocked",
            f"`npx playwright test e2e/ --reporter=json` (exit {p.returncode}) produced no parseable JSON report.",
            {
                "command": cmd,
                "exit_code": p.returncode,
                "stdout_tail": stdout_tail[-3000:],
                "stderr_tail": (p.stderr or "")[-3000:],
            },
        )
        return

    stats = report.get("stats", {})
    expected = stats.get("expected", 0)
    unexpected = stats.get("unexpected", 0)
    skipped = stats.get("skipped", 0)
    flaky = stats.get("flaky", 0)

    if expected == 0 and unexpected == 0:
        call_writer(
            "blocked",
            "playwright test ran but collected zero real tests from e2e/ (expected=0, unexpected=0) -- nothing genuine to pass or fail.",
            {"command": cmd, "exit_code": p.returncode, "stats": stats},
        )
        return

    result = "fail" if unexpected > 0 else "pass"
    evidence = {
        "command": " ".join(cmd),
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "exit_code": p.returncode,
        "stats": stats,
        "suite": [t.get("title") for t in _flatten_titles(report)],
        "pass_criterion": "stats.unexpected == 0 AND stats.expected > 0 from the real e2e/ Playwright suite",
    }
    summary = (
        f"npx playwright test e2e/ (exit {p.returncode}): expected={expected}, "
        f"unexpected={unexpected}, skipped={skipped}, flaky={flaky}."
    )
    call_writer(result, summary, evidence)


def _flatten_titles(report):
    titles = []
    for suite in report.get("suites", []) or []:
        for spec in suite.get("specs", []) or []:
            titles.append({"title": spec.get("title")})
    return titles


if __name__ == "__main__":
    main()
