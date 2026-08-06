#!/usr/bin/env python3
"""gtm_check_lighthouse_audit.py -- real, re-runnable check for GTM
certification category_index=24 ("lighthouse audit").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 24's
evidence_json already recorded a real result (`npx lighthouse
https://projexa-ai.com/login`, full default categories, real scores
performance=0.97 / accessibility=1 / best-practices=1 / seo=1 /
agentic-browsing=1) but cited a script_path, gtm_check_lighthouse_audit.py,
confirmed genuinely absent from disk. This script reproduces that exact,
real methodology as a genuine, committed, re-runnable file.

Distinct from category_index=9 (performance testing, scoped to the single
`performance` category with a low release-blocking bar): this is the
broader P2 quality/polish gate across every real Lighthouse default
category.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> every real Lighthouse default-category score >= 0.5 AND no
           runtimeError.
  Any real category scoring below 0.5 is a genuine FAIL. "blocked" is
  reserved for: `lighthouse`/`npx` or a working Chrome binary confirmed
  absent, or Lighthouse's own JSON report not being parseable.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=24's result.

Usage:
  gtm_check_lighthouse_audit.py [--url URL] [--no-write]
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
CATEGORY_INDEX = 24
DEFAULT_URL = "https://projexa-ai.com/login"
DEFAULT_CHROME_PATH = "/home/rajat/.local/bin/google-chrome"
PASS_THRESHOLD = 0.5


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_lighthouse_audit.py",
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
    categories = report.get("categories", {})
    category_scores = {k: v.get("score") for k, v in categories.items()}
    category_bands = {k: ("good" if (v is not None and v >= PASS_THRESHOLD) else "poor") for k, v in category_scores.items()}
    failing_categories = {k: v for k, v in category_scores.items() if v is None or v < PASS_THRESHOLD}

    result = "pass" if (not failing_categories and not runtime_error) else "fail"

    evidence = {
        "url": args.url,
        "final_url": report.get("finalUrl"),
        "requested_url": report.get("requestedUrl"),
        "lighthouse_version": report.get("lighthouseVersion"),
        "category_scores": category_scores,
        "category_bands": category_bands,
        "failing_categories": failing_categories,
        "runtime_error": runtime_error,
        "chrome_path": args.chrome_path,
        "pass_threshold": PASS_THRESHOLD,
        "pass_criterion": "every real Lighthouse default-category score >= 0.5 AND no runtimeError",
    }
    scores_desc = ", ".join(f"{k}={v}" for k, v in category_scores.items())
    summary = f"npx lighthouse {args.url} (full default categories): {scores_desc}"
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
