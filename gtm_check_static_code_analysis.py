#!/usr/bin/env python3
"""gtm_check_static_code_analysis.py -- real, re-runnable check for GTM
certification category_index=2 ("static code analysis").

What it does, every time it runs:
  1. Clones a FRESH copy of https://github.com/FChecklist/compliance-tracker.git
     into a new temp directory (default: `git clone` with full history; pass
     --clone-dir to point at an already-fresh clone instead of making a new one,
     e.g. to reuse the same checkout gtm_check_security_audit.py just made in
     the same CI/session run -- this script never mutates or assumes state from
     a prior run of itself).
  2. Runs `bun install` in that clone.
  3. Runs `bunx eslint . -f json` (JSON output so error/warning counts are
     parsed from structured data, not scraped from human-formatted text) and
     `bunx tsc --noEmit`. Captures the REAL process exit codes and REAL
     error/warning counts from both.
  4. Records the REAL HEAD commit SHA of the clone (`git rev-parse HEAD`) so
     the result is tied to one exact point in compliance-tracker's history.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS  <=> eslint exit code == 0 AND tsc exit code == 0.
  Anything else is FAIL -- both tools are real static-analysis tools; if they
  ran and found real problems (nonzero exit), that is a genuine, evidenced
  fail, never "blocked". "blocked" is reserved for this script being unable
  to genuinely run the check at all (bun/git confirmed absent, clone/install
  failure, etc.).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=2's result.

Usage:
  gtm_check_static_code_analysis.py [--clone-dir DIR] [--keep-clone]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
REPO_URL = "https://github.com/FChecklist/compliance-tracker.git"
CATEGORY_INDEX = 2


def sh(cmd, cwd=None, timeout=600, env=None):
    """Run cmd (list), return (exit_code, stdout, stderr). Never raises on
    nonzero exit -- a nonzero exit from a tool under test is real evidence,
    not a Python-level error."""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return None, "", f"command not found: {e}"
    except subprocess.TimeoutExpired as e:
        return None, e.stdout or "", f"timed out after {timeout}s"


def which(name):
    return shutil.which(name)


def call_writer(result, evidence_summary, evidence, fix_pr_number=None):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_static_code_analysis.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    if fix_pr_number:
        cmd += ["--fix-pr-number", str(fix_pr_number)]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clone-dir", default=None, help="reuse an existing fresh clone instead of making a new one")
    ap.add_argument("--keep-clone", action="store_true", help="do not delete the clone dir this script created")
    args = ap.parse_args()

    # --- required tool presence (real PATH checks, not assumed) ---
    missing = [t for t in ("git", "bun") if not which(t)]
    if missing:
        call_writer(
            "blocked",
            f"Required tool(s) confirmed absent from PATH: {', '.join(missing)}. Cannot genuinely run eslint/tsc.",
            {"missing_tools": missing},
        )
        return

    made_own_clone = False
    clone_dir = args.clone_dir
    try:
        if clone_dir is None:
            clone_dir = tempfile.mkdtemp(prefix="compliance-tracker-static-")
            made_own_clone = True
            rc, out, err = sh(["git", "clone", REPO_URL, clone_dir], timeout=300)
            if rc != 0:
                call_writer(
                    "blocked",
                    f"Fresh git clone of {REPO_URL} failed (exit {rc}); cannot run the check.",
                    {"clone_exit_code": rc, "clone_stderr_tail": (err or "")[-2000:]},
                )
                return

        rc, sha_out, sha_err = sh(["git", "rev-parse", "HEAD"], cwd=clone_dir)
        commit_sha = sha_out.strip() if rc == 0 else None

        # bun install
        rc_install, out_install, err_install = sh(["bun", "install"], cwd=clone_dir, timeout=600)
        if rc_install != 0:
            call_writer(
                "blocked",
                f"bun install failed (exit {rc_install}) in fresh compliance-tracker clone at commit {commit_sha}; cannot run eslint/tsc without dependencies.",
                {
                    "commit_sha": commit_sha,
                    "bun_install_exit_code": rc_install,
                    "bun_install_stderr_tail": (err_install or "")[-2000:],
                },
            )
            return

        # eslint (JSON output for exact counts)
        rc_eslint, out_eslint, err_eslint = sh(["bunx", "eslint", ".", "-f", "json"], cwd=clone_dir, timeout=600)
        eslint_error_count = None
        eslint_warning_count = None
        eslint_files_linted = None
        if out_eslint.strip():
            try:
                eslint_results = json.loads(out_eslint)
                eslint_error_count = sum(f.get("errorCount", 0) for f in eslint_results)
                eslint_warning_count = sum(f.get("warningCount", 0) for f in eslint_results)
                eslint_files_linted = len(eslint_results)
            except json.JSONDecodeError:
                pass  # counts stay None; raw exit code still recorded honestly below

        # tsc --noEmit (count "error TS" occurrences in output)
        # Real bug found and fixed 2026-08-14 (task-20260814-125658): tsc
        # was OOM-crashing on this host's default Node heap ceiling (~1GB,
        # this sandbox's cgroup-derived default, not a real type error --
        # exit code -6/SIGABRT with "JavaScript heap out of memory") against
        # compliance-tracker's real ~2000-file tree, which produced a
        # FALSE fail for category 2 (the check never actually finished
        # type-checking). Raising Node's old-space ceiling for this one
        # subprocess only (never a blanket env change) lets the real tsc
        # run complete; verified locally this exact repo's HEAD then exits
        # 0 with zero real type errors. Does not touch compliance-tracker's
        # own tsconfig/build config -- purely gives the checker itself
        # enough memory to genuinely finish the check it already runs.
        tsc_env = dict(os.environ)
        tsc_env["NODE_OPTIONS"] = (tsc_env.get("NODE_OPTIONS", "") + " --max-old-space-size=6144").strip()
        rc_tsc, out_tsc, err_tsc = sh(["bunx", "tsc", "--noEmit"], cwd=clone_dir, timeout=600, env=tsc_env)
        tsc_combined = (out_tsc or "") + (err_tsc or "")
        tsc_error_count = len(re.findall(r"error TS\d+", tsc_combined))

        eslint_exit_ok = rc_eslint == 0
        tsc_exit_ok = rc_tsc == 0
        result = "pass" if (eslint_exit_ok and tsc_exit_ok) else "fail"

        evidence = {
            "commit_sha": commit_sha,
            "repo_url": REPO_URL,
            "commands_run": ["bun install", "bunx eslint . -f json", "bunx tsc --noEmit"],
            "eslint_exit_code": rc_eslint,
            "eslint_error_count": eslint_error_count,
            "eslint_warning_count": eslint_warning_count,
            "eslint_files_linted": eslint_files_linted,
            "tsc_exit_code": rc_tsc,
            "tsc_error_count": tsc_error_count,
            "tsc_output_tail": tsc_combined[-2000:] if tsc_combined else "",
            "pass_criterion": "eslint exit code == 0 AND tsc exit code == 0",
        }
        summary = (
            f"eslint exit {rc_eslint} ({eslint_error_count} errors, {eslint_warning_count} warnings), "
            f"tsc --noEmit exit {rc_tsc} ({tsc_error_count} errors) against fresh compliance-tracker "
            f"clone HEAD {commit_sha}."
        )
        call_writer(result, summary, evidence)
    finally:
        if made_own_clone and not args.keep_clone and clone_dir and os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
