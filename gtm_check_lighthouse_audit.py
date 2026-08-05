#!/usr/bin/env python3
"""gtm_check_lighthouse_audit.py -- real, re-runnable check for GTM
certification category_index=24 ("lighthouse audit").

Same real tooling situation as category_index=9 ("performance testing"):
lighthouse is not installed anywhere in this repo/server as a persistent
dependency, but `npx lighthouse` was confirmed this session to genuinely
work (real on-demand npm-registry fetch, real Chrome launch). Per the
dispatching task's own instruction ("same tooling situation as category 9
-- likely the same script/finding"), this check reuses that same
confirmed-working mechanism, but is deliberately a distinct, broader check
rather than a duplicate of category 9: it runs Lighthouse's full DEFAULT
category set (performance, accessibility, best-practices, seo) -- the
conventional meaning of "a lighthouse audit" as opposed to "performance
testing" specifically -- and its pass bar looks at all four category
scores together, not performance alone.

What it does, every real run:
  1. Confirms npx is present (never assumes).
  2. Runs `npx --yes lighthouse <url> --output=json --chrome-flags=...`
     (no --only-categories flag -- Lighthouse's real default category set)
     against a real, live, public page (default
     https://projexa-ai.com/login), pointing CHROME_PATH at the real
     installed google-chrome binary.
  3. Parses Lighthouse's own real JSON output -- every category's real
     `score`, plus `runtimeError` -- never a narrative description.

Pass bar (documented, fixed, not adjustable at call time, same Lighthouse
scoring bands as category 9 -- 0-0.49 "poor", 0.5-0.89 "needs
improvement", 0.9-1.0 "good"):
  PASS <=> every one of the real default categories Lighthouse actually
           returned a score for has score >= 0.5 (none in the "poor"
           band) AND no real runtimeError.
  Any real category genuinely scoring below 0.5, or a real runtimeError,
  is a genuine FAIL. "blocked" is reserved for: npx confirmed absent, the
  on-demand `npx lighthouse` fetch/launch itself failing, or no parseable
  JSON output.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=24's result.

Usage:
  gtm_check_lighthouse_audit.py [--url URL]
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
CHROME_PATH = "/home/rajat/.local/bin/google-chrome"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    npx = shutil.which("npx")
    if not npx:
        call_writer("blocked", "npx confirmed absent from PATH; lighthouse is not installed anywhere in this repo, and the on-demand npx fetch path is unavailable.", {"missing_tools": ["npx"]})
        return

    env = dict(os.environ)
    if os.path.isfile(CHROME_PATH):
        env["CHROME_PATH"] = CHROME_PATH
    if os.path.isdir(LOCAL_LIBS):
        env["LD_LIBRARY_PATH"] = LOCAL_LIBS + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    out_fd, out_path = tempfile.mkstemp(prefix="gtm_lh_audit_", suffix=".json")
    os.close(out_fd)
    try:
        cmd = [
            "npx", "--yes", "lighthouse", args.url,
            "--output=json", f"--output-path={out_path}",
            "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
        ]
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            call_writer("blocked", f"`npx lighthouse {args.url}` (full default categories) timed out after 180s.", {"command": cmd})
            return
        except FileNotFoundError as e:
            call_writer("blocked", f"command not found running lighthouse: {e}", {"command": cmd})
            return

        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            call_writer(
                "blocked",
                f"`npx lighthouse` (exit {p.returncode}) produced no output JSON file -- real on-demand fetch/launch likely failed.",
                {"command": cmd, "exit_code": p.returncode, "stdout_tail": (p.stdout or "")[-2000:], "stderr_tail": (p.stderr or "")[-2000:]},
            )
            return

        try:
            with open(out_path) as f:
                report = json.load(f)
        except json.JSONDecodeError as e:
            call_writer(
                "blocked",
                f"lighthouse output at {out_path} was not parseable JSON: {e}",
                {"command": cmd, "exit_code": p.returncode},
            )
            return

        runtime_error = report.get("runtimeError")
        categories = report.get("categories") or {}
        scores = {k: v.get("score") for k, v in categories.items()}
        real_scores = {k: v for k, v in scores.items() if v is not None}

        if not real_scores:
            call_writer(
                "blocked",
                f"lighthouse report against {args.url} returned no real category scores (runtimeError={runtime_error}).",
                {"command": cmd, "runtime_error": runtime_error, "final_url": report.get("finalUrl")},
            )
            return

        failing = {k: v for k, v in real_scores.items() if v < PASS_THRESHOLD}
        result = "fail" if (failing or runtime_error) else "pass"
        bands = {k: ("good" if v >= 0.9 else ("needs improvement" if v >= 0.5 else "poor")) for k, v in real_scores.items()}
        evidence = {
            "url": args.url,
            "final_url": report.get("finalUrl"),
            "requested_url": report.get("requestedUrl"),
            "lighthouse_version": report.get("lighthouseVersion"),
            "category_scores": real_scores,
            "category_bands": bands,
            "failing_categories": failing,
            "runtime_error": runtime_error,
            "chrome_path": CHROME_PATH if os.path.isfile(CHROME_PATH) else None,
            "pass_threshold": PASS_THRESHOLD,
            "pass_criterion": f"every real Lighthouse default-category score >= {PASS_THRESHOLD} AND no runtimeError",
        }
        summary = (
            f"npx lighthouse {args.url} (full default categories): "
            + ", ".join(f"{k}={v}" for k, v in real_scores.items())
            + (f"; FAILING: {list(failing.keys())}" if failing else "")
            + (f"; runtimeError={runtime_error}" if runtime_error else "")
        )
        call_writer(result, summary, evidence)
    finally:
        if os.path.isfile(out_path):
            os.remove(out_path)


if __name__ == "__main__":
    main()
