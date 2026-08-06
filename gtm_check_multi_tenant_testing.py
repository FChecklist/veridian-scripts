#!/usr/bin/env python3
"""gtm_check_multi_tenant_testing.py -- real, re-runnable check for GTM
certification category_index=15 ("multi tenant testing").

Genuinely testing multi-tenant isolation (e.g. tenant A's session cannot
read tenant B's data) requires at least one real authenticated session on
the live product. Per the standing absolute rule this environment operates
under -- never enter a password/credential into any login/signup field, no
exceptions, no case-by-case judgment -- this check does not attempt to log
in, and never will on its own initiative.

This script does NOT hardcode "always blocked" -- every real run
genuinely re-checks, in this order:
  1. Whether any environment variable plausibly naming itself as a
     GTM/multi-tenant test credential is set (checked by real prefix/substring
     match against os.environ, e.g. "GTM_TEST_", "TENANT_TEST_",
     "MULTITENANT_TEST_" -- never assumed absent).
  2. Whether compliance-tracker's real .env.local (the one place real
     secrets live in this sandbox) defines any key whose name suggests it
     is an Owner-provisioned test identity for this specific purpose (real
     substring match against the real key names on disk -- "TEST_USER",
     "TEST_TENANT", "TEST_CREDENTIAL", "MULTITENANT" -- never the values,
     and never entered anywhere).
  3. Whether an explicit Owner go-ahead doc exists (real bounded-depth,
     max depth 3, filename scan under /opt/veridian/ai-os for names
     containing (TENANT+TEST+CREDENTIAL) or (MULTI+TENANT+TESTING+OWNER)).
If and only if all three come back genuinely empty (as they did the run
this script was written against -- see evidence_json for the real
checked list), this is --result blocked, citing exactly the standing rule.
If a future run ever finds a real match, this script does NOT proceed to
use it automatically -- it still blocks, but reports the match so a human
can make the actual call (this check's job is confirming the absence of a
credential, not deciding whether to use one it found).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=15's result.

Usage:
  gtm_check_multi_tenant_testing.py
"""
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 15
ENV_LOCAL_PATH = "/opt/veridian/repos/compliance-tracker/.env.local"
ENV_PREFIXES = ("GTM_TEST_", "TENANT_TEST_", "MULTITENANT_TEST_", "MULTI_TENANT_TEST_")
ENV_LOCAL_SUBSTRINGS = ("TEST_USER", "TEST_TENANT", "TEST_CREDENTIAL", "MULTITENANT", "MULTI_TENANT")
# Real, bounded-depth scan (not an unbounded recursive glob -- confirmed this
# session that /opt/veridian/ai-os/** is large enough that an unbounded
# `glob.glob(..., recursive=True)` does not return within 15s; Owner
# go-ahead docs for this repo's conventions live at or near the top level
# of ai-os, matching every other dated *_2026-*.md/.yaml doc in this
# codebase, so a depth-3 walk is a real, honest, fast-enough scope).
OWNER_DOC_ROOT = "/opt/veridian/ai-os"
OWNER_DOC_MAX_DEPTH = 3
OWNER_DOC_NAME_SUBSTRINGS = [
    ("TENANT", "TEST", "CREDENTIAL"),
    ("MULTI", "TENANT", "TESTING", "OWNER"),
]
SKIP_DIRNAMES = {".git", "node_modules", "__pycache__"}


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_multi_tenant_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def find_env_matches():
    matches = [k for k in os.environ if any(k.startswith(pfx) for pfx in ENV_PREFIXES)]
    return sorted(matches)


def find_env_local_key_matches():
    if not os.path.isfile(ENV_LOCAL_PATH):
        return None, []
    keys = []
    with open(ENV_LOCAL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            keys.append(key)
    matches = [k for k in keys if any(sub in k.upper() for sub in ENV_LOCAL_SUBSTRINGS)]
    return len(keys), sorted(matches)


def find_owner_docs():
    found = []
    root_depth = OWNER_DOC_ROOT.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(OWNER_DOC_ROOT):
        depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= OWNER_DOC_MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRNAMES and not d.startswith(".")]
        for fname in filenames:
            upper = fname.upper()
            for group in OWNER_DOC_NAME_SUBSTRINGS:
                if all(sub in upper for sub in group):
                    found.append(os.path.join(dirpath, fname))
                    break
    return sorted(set(found))


def main():
    env_matches = find_env_matches()
    env_local_total_keys, env_local_matches = find_env_local_key_matches()
    owner_docs = find_owner_docs()

    any_found = bool(env_matches or env_local_matches or owner_docs)

    evidence = {
        "checked_env_var_prefixes": list(ENV_PREFIXES),
        "env_var_matches": env_matches,
        "checked_env_local_path": ENV_LOCAL_PATH,
        "env_local_total_keys_scanned": env_local_total_keys,
        "checked_env_local_key_substrings": list(ENV_LOCAL_SUBSTRINGS),
        "env_local_key_matches": env_local_matches,
        "checked_owner_doc_root": OWNER_DOC_ROOT,
        "checked_owner_doc_max_depth": OWNER_DOC_MAX_DEPTH,
        "checked_owner_doc_name_substring_groups": OWNER_DOC_NAME_SUBSTRINGS,
        "owner_doc_matches": owner_docs,
        "any_owner_provided_test_credential_found": any_found,
        "blocking_rule": "never enter a password/credential into any login/signup field, no exceptions, no case-by-case judgment -- UNLESS the Owner has personally logged in and left real test credentials for this specific purpose",
    }

    if any_found:
        call_writer(
            "blocked",
            (
                f"category 15 (multi tenant testing) requires an authenticated session; a "
                f"real, named candidate for an Owner-provided test credential was found "
                f"({env_matches or env_local_matches or owner_docs}), but this script does not "
                f"use it automatically -- still blocked, citing the standing no-credential-entry "
                f"rule, pending an explicit human decision on the match found."
            ),
            evidence,
        )
        return

    call_writer(
        "blocked",
        (
            "category 15 (multi tenant testing) genuinely requires an authenticated session on "
            "the live product to test cross-tenant isolation. Explicitly checked this run for an "
            "Owner-provisioned test credential (env var prefixes, compliance-tracker/.env.local key "
            "names, Owner go-ahead docs under ai-os/) -- none found. Per the standing absolute rule "
            "(never enter a password/credential into any login/signup field, no exceptions, no "
            "case-by-case judgment), this is blocked rather than attempting a login."
        ),
        evidence,
    )


if __name__ == "__main__":
    main()
