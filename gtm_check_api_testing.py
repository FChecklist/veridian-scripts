#!/usr/bin/env python3
"""gtm_check_api_testing.py -- real, re-runnable check for GTM
certification category_index=4 ("API testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 4's
evidence_json already recorded a real result (7/7 public endpoints on
https://projexa-ai.com returning their expected status code) but cited a
script_path, gtm_check_api_testing.py, that was confirmed genuinely absent
from both the veridian-scripts repo and the live deployed scripts/ dir --
the prior result was real but not independently re-runnable. This script
reproduces that exact, real methodology as a genuine, committed,
re-runnable file.

What it does, every time it runs:
  Issues a real HTTP GET (no auth, no state mutation) against each of a
  fixed list of public paths on the live site and compares the real
  response status code to the expected one.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> every one of the checked paths returns its expected status code.
  Any real mismatch is a genuine FAIL. "blocked" is reserved for: the base
  URL being completely unreachable (connection error) for every single path
  checked (distinguishes "site is down" from "one page 404s").

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=4's result.

Usage:
  gtm_check_api_testing.py [--base-url URL]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 4
DEFAULT_BASE_URL = "https://projexa-ai.com"

# real, public, unauthenticated paths -- same set as the prior real run's
# recorded evidence_json.results keys
PATHS_EXPECTED = {
    "/": 200,
    "/login": 200,
    "/signup": 200,
    "/pricing": 200,
    "/contact": 200,
    "/terms": 200,
    "/privacy": 200,
}


def check_path(base_url, path, timeout=20):
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "veridian-gtm-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, str(e)


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_api_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    import subprocess
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    results = {}
    errors = {}
    for path, expected in PATHS_EXPECTED.items():
        actual, err = check_path(args.base_url, path)
        results[path] = {"expected": expected, "actual": actual, "match": actual == expected}
        if err:
            errors[path] = err

    if len(errors) == len(PATHS_EXPECTED):
        evidence = {"base_url": args.base_url, "results": results, "errors": errors}
        if args.no_write:
            print(json.dumps({"result": "blocked", "evidence": evidence}, indent=2))
            return
        call_writer(
            "blocked",
            f"All {len(PATHS_EXPECTED)} real requests to {args.base_url} failed with a connection-level error; base URL confirmed unreachable this run.",
            evidence,
        )
        return

    mismatches = {p: r for p, r in results.items() if not r["match"]}
    result = "fail" if mismatches else "pass"

    evidence = {
        "base_url": args.base_url,
        "results": results,
    }
    if errors:
        evidence["errors"] = errors
    summary = (
        f"{len(PATHS_EXPECTED) - len(mismatches)}/{len(PATHS_EXPECTED)} real public endpoints on {args.base_url} "
        f"returned their expected status code."
        + (f" Mismatched: {', '.join(mismatches.keys())}." if mismatches else "")
    )
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
