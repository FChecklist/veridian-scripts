#!/usr/bin/env python3
"""
Real, deterministic check for GTM category 4, "API testing".

Scope, documented here explicitly: calls the real, live, PUBLIC (no
authentication) pages/endpoints on https://projexa-ai.com only -- the exact
set already independently confirmed this session to be real, existing,
unauthenticated routes (root, login, signup, pricing, contact, terms,
privacy). Does NOT touch any authenticated route or /api/* path (robots.txt
disallows /api/ and none were confirmed public this session) -- this check
never logs in or submits credentials, consistent with the standing rule.

Pass criterion: every expected path returns its expected real HTTP status
code (curl -o /dev/null -w '%{http_code}', following redirects with
--max-redirs, since '/' is a real 307 -> /login by design, not a failure).
Any unexpected code (including a fresh domain-misattachment regression, the
same class as the /signup brand bug found earlier this session) is a real
fail, not blocked -- the endpoint is reachable, the response just isn't
what's expected.
"""
import json
import os
import subprocess
import sys

WRITER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtm_write_category_result.py")
CATEGORY_INDEX = 4
BASE = "https://projexa-ai.com"

EXPECTED = {
    "/": 200,       # 307 -> /login, curl -L follows to final 200
    "/login": 200,
    "/signup": 200,
    "/pricing": 200,
    "/contact": 200,
    "/terms": 200,
    "/privacy": 200,
}


def run_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_api_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    print(out.stdout.strip())
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)


def check_one(path):
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-L", "--max-redirs", "5", f"{BASE}{path}"],
        capture_output=True, text=True, timeout=20,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def main():
    results = {}
    for path, expected in EXPECTED.items():
        actual = check_one(path)
        results[path] = {"expected": expected, "actual": actual, "match": actual == expected}

    mismatches = {p: r for p, r in results.items() if not r["match"]}
    evidence = {"base_url": BASE, "results": results}

    if mismatches:
        run_writer(
            "fail",
            f"{len(mismatches)}/{len(EXPECTED)} real public endpoints returned an unexpected status code",
            evidence,
        )
    else:
        run_writer(
            "pass",
            f"all {len(EXPECTED)} real public endpoints on {BASE} returned their expected status code",
            evidence,
        )


if __name__ == "__main__":
    main()
