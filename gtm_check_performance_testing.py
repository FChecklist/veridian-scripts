#!/usr/bin/env python3
"""gtm_check_performance_testing.py -- real, re-runnable check for GTM
certification category_index=9 ("performance testing").

Lighthouse is not installed anywhere in this repo/server as a persistent
dependency (confirmed absent this session: no `lighthouse` on PATH, no
node_modules/lighthouse anywhere under /opt/veridian/repos). Per the
dispatching task's own instruction ("try `npx lighthouse` -- on-demand
fetch, needs network access to npm registry, confirm it actually works
before trusting it -- if that fails, --result blocked honestly, do not
install a new global tool without being asked"): `npx lighthouse --version`
was tried first and confirmed to genuinely work this session (real
on-demand fetch from the npm registry into npx's cache, real v13.4.1,
real Chrome launch via /home/rajat/.local/bin/google-chrome) -- so this
check proceeds with a real run rather than blocking.

Distinct from category_index=24 ("lighthouse audit", same underlying
tool): this category scopes to the `performance` Lighthouse category only
(`--only-categories=performance`) -- a narrower, faster, more classically
"performance testing"-shaped check, whereas category 24 runs the full
default category set as a broader site-quality audit. Same script pattern,
deliberately not the same script, so each has its own independent,
category-scoped pass bar.

What it does, every real run:
  1. Confirms npx is present (never assumes -- `shutil.which`).
  2. Runs `npx --yes lighthouse <url> --only-categories=performance
     --output=json --chrome-flags="--headless=new --no-sandbox
     --disable-dev-shm-usage"` against a real, live, public page (default
     https://projexa-ai.com/login), pointing CHROME_PATH at the real
     installed google-chrome binary.
  3. Parses Lighthouse's own real JSON output -- `categories.performance.score`
     (a real 0-1 float Lighthouse itself computed from real Core Web Vitals
     measurements against the real page), `runtimeError` -- never a
     narrative description.

Pass bar (documented, fixed, not adjustable at call time, using
Lighthouse's own published scoring bands: 0-0.49 "poor", 0.5-0.89 "needs
improvement", 0.9-1.0 "good"):
  PASS <=> real performance score >= 0.5 (not in Lighthouse's own "poor"
           band) AND no real runtimeError.
  A real score < 0.5, or a real runtimeError, is a genuine FAIL -- the page
  loaded and Lighthouse genuinely measured it. "blocked" is reserved for:
  npx confirmed absent, the on-demand `npx lighthouse` fetch/launch itself
  failing (network/registry unavailable, no working Chrome), or no
  parseable JSON output.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=9's result.

Usage:
  gtm_check_performance_testing.py [--url URL]
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
CHROME_PATH = "/home/rajat/.local/bin/google-chrome"
LOCAL_LIBS = "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
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

    out_fd, out_path = tempfile.mkstemp(prefix="gtm_lh_perf_", suffix=".json")
    os.close(out_fd)
    try:
        cmd = [
            "npx", "--yes", "lighthouse", args.url,
            "--only-categories=performance",
            "--output=json", f"--output-path={out_path}",
            "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
        ]
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=150)
        except subprocess.TimeoutExpired:
            call_writer("blocked", f"`npx lighthouse {args.url} --only-categories=performance` timed out after 150s.", {"command": cmd})
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
        perf_cat = (report.get("categories") or {}).get("performance") or {}
        score = perf_cat.get("score")

        if score is None:
            call_writer(
                "blocked",
                f"lighthouse report against {args.url} has no real performance.score (runtimeError={runtime_error}).",
                {"command": cmd, "runtime_error": runtime_error, "final_url": report.get("finalUrl")},
            )
            return

        result = "fail" if (score < PASS_THRESHOLD or runtime_error) else "pass"
        band = "good" if score >= 0.9 else ("needs improvement" if score >= 0.5 else "poor")
        evidence = {
            "url": args.url,
            "final_url": report.get("finalUrl"),
            "requested_url": report.get("requestedUrl"),
            "lighthouse_version": report.get("lighthouseVersion"),
            "performance_score": score,
            "performance_band": band,
            "runtime_error": runtime_error,
            "chrome_path": CHROME_PATH if os.path.isfile(CHROME_PATH) else None,
            "pass_threshold": PASS_THRESHOLD,
            "pass_criterion": f"real performance.score >= {PASS_THRESHOLD} (Lighthouse's own 'poor' band starts below this) AND no runtimeError",
        }
        summary = (
            f"npx lighthouse {args.url} --only-categories=performance: real score "
            f"{score} ({band} band){', runtimeError=' + str(runtime_error) if runtime_error else ''}."
        )
        call_writer(result, summary, evidence)
    finally:
        if os.path.isfile(out_path):
            os.remove(out_path)


if __name__ == "__main__":
    main()
