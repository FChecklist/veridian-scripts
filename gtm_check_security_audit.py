#!/usr/bin/env python3
"""gtm_check_security_audit.py -- real, re-runnable check for GTM
certification category_index=3 ("security audit").

What it does, every time it runs:
  1. Clones a FRESH copy of https://github.com/FChecklist/compliance-tracker.git
     into a new temp directory (default), OR reuses an already-fresh clone
     passed via --clone-dir (e.g. the same clone gtm_check_static_code_analysis.py
     just made in the same session, to avoid re-cloning/re-downloading twice --
     this script never mutates or assumes prior state beyond "this directory is
     a checkout of compliance-tracker").
  2. Checks `gitleaks` and `trivy` are both present in PATH FIRST. If either is
     confirmed absent, calls the writer with --result blocked for the WHOLE
     category (never a partial/fabricated result for the missing tool).
  3. Runs `gitleaks detect --source . --no-git --report-format json --report-path <tmp>
     --gitleaks-ignore-path gtm_security_audit_gitleaksignore.txt`.
     --no-git is used deliberately: this check scans the real files on disk as
     they exist in the checkout (working tree secrets), not git history, and
     that choice is fixed and documented here, not decided ad hoc per run.
     The ignore-path excludes exactly 16 fingerprints, individually
     investigated and confirmed real false positives (UMR-20260805-155908-b101,
     child of UMR-20260802-165606-4413/OCID-020, per UMR-20260805-155838-6270)
     -- see gtm_security_audit_gitleaksignore.txt's own header for the real
     per-group classification. Fingerprint-scoped, not a pattern/rule
     relaxation -- a new real secret anywhere else, including a new instance
     of the SAME rule on a different line, still fails this check.
  4. Runs `trivy fs . --format json -o <tmp> --skip-version-check` (filesystem
     scan for known vulnerabilities in dependencies, e.g. bun.lock/requirements.txt).
  5. Captures REAL finding counts by severity from both tools' structured JSON
     output (never scraped/estimated from human-readable text).

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> gitleaks reports ZERO findings
           AND trivy reports ZERO findings at severity CRITICAL or HIGH.
  trivy MEDIUM/LOW findings are recorded in evidence_json but do NOT fail the
  check on their own.
  If gitleaks/trivy ran successfully and found real findings above the bar,
  that is a genuine FAIL (the tools work; they found real problems) -- never
  "blocked". "blocked" is reserved for gitleaks/trivy (or git/bun, needed to
  produce the clone) being confirmed absent, or the clone itself failing.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=3's result.

Usage:
  gtm_check_security_audit.py [--clone-dir DIR] [--keep-clone]
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
GITLEAKSIGNORE_PATH = os.path.join(SCRIPTS_DIR, "gtm_security_audit_gitleaksignore.txt")
REPO_URL = "https://github.com/FChecklist/compliance-tracker.git"
CATEGORY_INDEX = 3
FAILING_TRIVY_SEVERITIES = ("CRITICAL", "HIGH")


def sh(cmd, cwd=None, timeout=600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
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
        "--script-path", "gtm_check_security_audit.py",
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
    ap.add_argument("--clone-dir", default=None)
    ap.add_argument("--keep-clone", action="store_true")
    args = ap.parse_args()

    missing = [t for t in ("git", "bun", "gitleaks", "trivy") if not which(t)]
    if missing:
        call_writer(
            "blocked",
            f"Required tool(s) confirmed absent from PATH: {', '.join(missing)}. Cannot genuinely run the security audit.",
            {"missing_tools": missing},
        )
        return

    made_own_clone = False
    clone_dir = args.clone_dir
    try:
        if clone_dir is None:
            clone_dir = tempfile.mkdtemp(prefix="compliance-tracker-secaudit-")
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

        # --- gitleaks (working-tree scan, --no-git, JSON report) ---
        gitleaks_report = os.path.join(tempfile.mkdtemp(prefix="gitleaks-report-"), "report.json")
        gitleaks_cmd = ["gitleaks", "detect", "--source", ".", "--no-git",
                        "--report-format", "json", "--report-path", gitleaks_report]
        used_ignore_path = os.path.isfile(GITLEAKSIGNORE_PATH)
        if used_ignore_path:
            gitleaks_cmd += ["--gitleaks-ignore-path", GITLEAKSIGNORE_PATH]
        rc_gl, out_gl, err_gl = sh(gitleaks_cmd, cwd=clone_dir, timeout=300)
        gitleaks_findings = []
        if os.path.isfile(gitleaks_report):
            try:
                with open(gitleaks_report) as f:
                    content = f.read().strip()
                    gitleaks_findings = json.loads(content) if content else []
            except json.JSONDecodeError:
                gitleaks_findings = None  # honestly unknown, not zero
        gitleaks_count = len(gitleaks_findings) if isinstance(gitleaks_findings, list) else None
        gitleaks_rule_ids = (
            sorted(set(f.get("RuleID", "unknown") for f in gitleaks_findings))
            if isinstance(gitleaks_findings, list) else None
        )

        # --- trivy (filesystem scan, JSON) ---
        # Real bug found and fixed 2026-08-05 (UMR-20260805-155838-6270's own
        # investigation): trivy's bun/npm dependency-vulnerability detection
        # needs a real resolved node_modules to see actual installed package
        # versions -- without it, trivy silently reports a false ZERO (an
        # empty/near-empty Results list, not an error), which a prior manual
        # re-run of this exact check produced and would have been trusted as
        # a genuine pass had it not been independently cross-checked against
        # the original run. `bun install` MUST run before trivy, every time.
        rc_bi, out_bi, err_bi = sh(["bun", "install"], cwd=clone_dir, timeout=300)
        if rc_bi != 0:
            call_writer(
                "blocked",
                f"bun install failed (exit {rc_bi}) -- trivy's dependency scan would be a false negative without it, refusing to run trivy blind.",
                {"commit_sha": commit_sha, "bun_install_exit_code": rc_bi, "bun_install_stderr_tail": (err_bi or "")[-2000:]},
            )
            return

        trivy_report = os.path.join(tempfile.mkdtemp(prefix="trivy-report-"), "report.json")
        rc_tv, out_tv, err_tv = sh(
            ["trivy", "fs", ".", "--format", "json", "--skip-version-check", "-o", trivy_report],
            cwd=clone_dir, timeout=480,
        )
        trivy_severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        trivy_parse_ok = False
        if os.path.isfile(trivy_report):
            try:
                with open(trivy_report) as f:
                    trivy_data = json.load(f)
                trivy_parse_ok = True
                for res in (trivy_data.get("Results") or []):
                    for v in (res.get("Vulnerabilities") or []):
                        sev = v.get("Severity", "UNKNOWN")
                        trivy_severity_counts[sev] = trivy_severity_counts.get(sev, 0) + 1
            except (json.JSONDecodeError, OSError):
                trivy_parse_ok = False

        # --- decide result ---
        if gitleaks_count is None or not trivy_parse_ok:
            call_writer(
                "blocked",
                "gitleaks and/or trivy ran but produced unparseable/missing JSON report output; "
                "cannot honestly determine finding counts, refusing to guess pass/fail.",
                {
                    "commit_sha": commit_sha,
                    "gitleaks_exit_code": rc_gl,
                    "trivy_exit_code": rc_tv,
                    "gitleaks_report_parsed": gitleaks_count is not None,
                    "trivy_report_parsed": trivy_parse_ok,
                },
            )
            return

        trivy_failing = sum(trivy_severity_counts[s] for s in FAILING_TRIVY_SEVERITIES)
        result = "pass" if (gitleaks_count == 0 and trivy_failing == 0) else "fail"

        evidence = {
            "commit_sha": commit_sha,
            "repo_url": REPO_URL,
            "commands_run": [
                " ".join(gitleaks_cmd),
                "trivy fs . --format json --skip-version-check -o <tmp>",
            ],
            "gitleaks_source_mode": "--no-git (working-tree file scan, not git history)",
            "gitleaks_ignore_path_used": used_ignore_path,
            "gitleaks_ignore_fingerprints_excluded": 16 if used_ignore_path else 0,
            "gitleaks_ignore_investigation_umr": "UMR-20260805-155908-b101" if used_ignore_path else None,
            "gitleaks_exit_code": rc_gl,
            "gitleaks_finding_count": gitleaks_count,
            "gitleaks_rule_ids": gitleaks_rule_ids,
            "trivy_findings_status": "NOT investigated/excluded this round -- any trivy CRITICAL/HIGH count above is real and unaddressed, only the 16 gitleaks fingerprints were reviewed",
            "trivy_exit_code": rc_tv,
            "trivy_severity_counts": trivy_severity_counts,
            "trivy_failing_severities": list(FAILING_TRIVY_SEVERITIES),
            "trivy_failing_count": trivy_failing,
            "pass_bar": (
                "PASS <=> gitleaks_finding_count == 0 AND "
                "trivy CRITICAL+HIGH finding count == 0. "
                "trivy MEDIUM/LOW are recorded but do not fail the check."
            ),
        }
        summary = (
            f"gitleaks (--no-git): {gitleaks_count} finding(s) (exit {rc_gl}). "
            f"trivy fs: {trivy_severity_counts} (exit {rc_tv}); "
            f"{trivy_failing} at CRITICAL/HIGH. Against fresh compliance-tracker clone HEAD {commit_sha}."
        )
        call_writer(result, summary, evidence)
    finally:
        if made_own_clone and not args.keep_clone and clone_dir and os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
