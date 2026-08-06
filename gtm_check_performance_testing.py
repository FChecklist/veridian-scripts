#!/usr/bin/env python3
"""gtm_check_performance_testing.py -- real, re-runnable check for GTM
certification category_index=9 ("performance testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 9's
evidence_json already recorded a real result (`npx lighthouse
https://projexa-ai.com/login --only-categories=performance`, real score
0.91, "good" band) but cited a script_path, gtm_check_performance_testing.py,
confirmed genuinely absent from disk. This script reproduces that exact,
real methodology as a genuine, committed, re-runnable file.

Distinct from category_index=24 (lighthouse audit, which checks every
default Lighthouse category): this check is scoped to the single
`performance` category only, with a low pass bar (Lighthouse's own "poor"
band starts below 0.5) -- release-blocking performance regression
detection, not a polish/quality gate.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> real performance.score >= 0.5 AND no runtimeError.
  A real score below 0.5, or a real runtimeError, is a genuine FAIL.
  "blocked" is reserved for: `lighthouse`/`npx` or a working Chrome binary
  confirmed absent, or Lighthouse's own JSON report not being parseable.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=9's result.

Usage:
  gtm_check_performance_testing.py [--url URL] [--no-write]
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
CATEGORY_INDEX = 9
DEFAULT_URL = "https://projexa-ai.com/login"
DEFAULT_CHROME_PATH = "/home/rajat/.local/bin/google-chrome"
PASS_THRESHOLD = 0.5


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_performance_testing.py",
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
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--chrome-path", default=DEFAULT_CHROME_PATH)
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    if not shutil.which("npx"):
        emit(args, "blocked", "npx confirmed absent from PATH; cannot run lighthouse.", {"missing_tools": ["npx"]})
        return
    if not os.path.isfile(args.chrome_path):
        emit(args, "blocked", f"Chrome binary confirmed absent at {args.chrome_path}.", {"chrome_path": args.chrome_path})
        return

    with tempfile.TemporaryDirectory() as td:
        out_path = os.path.join(td, "lh_report.json")
        cmd = [
            "npx", "lighthouse", args.url,
            "--output=json", f"--output-path={out_path}",
            "--only-categories=performance",
            f"--chrome-path={args.chrome_path}",
            "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
            "--quiet",
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            emit(args, "blocked", "lighthouse run timed out after 180s.", {"command": " ".join(cmd)})
            return

        if not os.path.isfile(out_path):
            emit(
                args, "blocked",
                f"lighthouse exited {p.returncode} but produced no report file.",
                {"command": " ".join(cmd), "exit_code": p.returncode, "stderr_tail": (p.stderr or "")[-2000:]},
            )
            return

        try:
            with open(out_path) as f:
                report = json.load(f)
        except json.JSONDecodeError as e:
            emit(args, "blocked", f"lighthouse report was not parseable JSON: {e}", {"command": " ".join(cmd)})
            return

    runtime_error = report.get("runtimeError")
    perf = report.get("categories", {}).get("performance", {})
    perf_score = perf.get("score")

    if perf_score is None:
        emit(
            args, "blocked",
            "lighthouse ran but produced no real performance.score in its report.",
            {"url": args.url, "final_url": report.get("finalUrl"), "runtime_error": runtime_error, "raw_categories": report.get("categories")},
        )
        return

    band = "good" if perf_score >= PASS_THRESHOLD else "poor"
    result = "pass" if (perf_score >= PASS_THRESHOLD and not runtime_error) else "fail"

    evidence = {
        "url": args.url,
        "final_url": report.get("finalUrl"),
        "requested_url": report.get("requestedUrl"),
        "lighthouse_version": report.get("lighthouseVersion"),
        "performance_score": perf_score,
        "performance_band": band,
        "runtime_error": runtime_error,
        "chrome_path": args.chrome_path,
        "pass_threshold": PASS_THRESHOLD,
        "pass_criterion": "real performance.score >= 0.5 (Lighthouse's own 'poor' band starts below this) AND no runtimeError",
    }
    summary = f"npx lighthouse {args.url} --only-categories=performance: real score {perf_score} ({band} band)."
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
