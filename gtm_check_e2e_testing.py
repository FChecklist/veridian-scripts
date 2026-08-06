#!/usr/bin/env python3
"""gtm_check_e2e_testing.py -- real, re-runnable check for GTM certification
category_index=6 ("end to end testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 6's
evidence_json already recorded a real result (`npx playwright test e2e/
--reporter=json`, exit 0, expected=1/unexpected=0/skipped=0/flaky=0) but
cited a script_path, gtm_check_e2e_testing.py, confirmed genuinely absent
from disk. This script reproduces that exact, real command as a genuine,
committed, re-runnable file.

What it does, every time it runs:
  Runs the real, existing e2e/ Playwright suite inside the live
  compliance-tracker checkout (read-only: no git pull, no dependency
  changes) via `npx playwright test e2e/ --reporter=json`, using the same
  local-libs LD_LIBRARY_PATH reuse the rest of the GTM browser-based checks
  use, and parses the real JSON reporter summary.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> real exit code 0 AND stats.unexpected == 0 AND stats.expected > 0.
  Any real non-zero unexpected count, or a real non-zero exit code with a
  parseable report, is a genuine FAIL. "blocked" is reserved for: the e2e/
  directory or playwright.config.* confirmed absent, `npx`/`playwright`
  confirmed unusable, or the reporter output not being parseable JSON.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=6's result.

Usage:
  gtm_check_e2e_testing.py [--no-write]
"""
import argparse
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


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    if not shutil.which("npx"):
        emit(args, "blocked", "npx confirmed absent from PATH; cannot run the e2e/ Playwright suite.", {"missing_tools": ["npx"]})
        return

    e2e_dir = os.path.join(COMPLIANCE_TRACKER_DIR, "e2e")
    if not os.path.isdir(e2e_dir):
        emit(args, "blocked", f"e2e/ suite directory confirmed absent at {e2e_dir}.", {"e2e_dir": e2e_dir})
        return

    if not os.path.isdir(LOCAL_LIBS):
        emit(
            args, "blocked",
            f"Real user-space shared-library set at {LOCAL_LIBS} (required for Playwright chromium to launch on this OS) is confirmed absent.",
            {"local_libs_dir": LOCAL_LIBS, "local_libs_present": False},
        )
        return

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    cmd = ["npx", "playwright", "test", "e2e/", "--reporter=json"]
    try:
        p = subprocess.run(cmd, cwd=COMPLIANCE_TRACKER_DIR, env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        emit(args, "blocked", "npx playwright test e2e/ timed out after 900s.", {"command": " ".join(cmd)})
        return

    try:
        parsed = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        emit(
            args, "blocked",
            f"npx playwright test e2e/ ran (exit {p.returncode}) but stdout was not parseable JSON.",
            {"command": " ".join(cmd), "exit_code": p.returncode, "stdout_tail": (p.stdout or "")[-2000:], "stderr_tail": (p.stderr or "")[-2000:], "json_parse_error": str(e)},
        )
        return

    stats = parsed.get("stats", {})
    expected = stats.get("expected", 0)
    unexpected = stats.get("unexpected", 0)
    skipped = stats.get("skipped", 0)
    flaky = stats.get("flaky", 0)

    result = "pass" if (p.returncode == 0 and unexpected == 0 and expected > 0) else "fail"

    evidence = {
        "command": " ".join(cmd),
        "engine": "chromium (Playwright-bundled, launched via reused user-space local-libs LD_LIBRARY_PATH)",
        "local_libs_dir": LOCAL_LIBS,
        "exit_code": p.returncode,
        "stats": {"startTime": stats.get("startTime"), "duration": stats.get("duration"), "expected": expected, "skipped": skipped, "unexpected": unexpected, "flaky": flaky},
        "suite": parsed.get("suites", []),
        "pass_criterion": "stats.unexpected == 0 AND stats.expected > 0 from the real e2e/ Playwright suite",
    }
    summary = f"npx playwright test e2e/ (exit {p.returncode}): expected={expected}, unexpected={unexpected}, skipped={skipped}, flaky={flaky}."
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
