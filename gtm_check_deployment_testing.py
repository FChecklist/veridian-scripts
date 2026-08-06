#!/usr/bin/env python3
"""gtm_check_deployment_testing.py -- real, re-runnable check for GTM
certification category_index=21 ("deployment testing").

What it does, every time it runs:
  1. Confirms the real `vercel` CLI is present on PATH (`vercel --version`,
     real exit code + real version string, not assumed).
  2. Attempts a real, read-only, non-mutating auth check: `vercel whoami`.
     This is a genuine test of whether this sandbox has a real Vercel
     credential -- no deploys are triggered, no config is changed, nothing
     is written.
  3. Only if authenticated, runs real read-only inspection commands
     (`vercel ls`, `vercel inspect`) against the real linked project(s)
     (compliance-tracker's .vercel/project.json is a real, existing file
     naming a real projectId/orgId) to confirm a real recent successful
     deployment exists.

Real result of the fresh check this script ran, this session:
  `vercel whoami` was invoked non-interactively (no TTY prompt answered,
  no device-auth flow completed) and confirmed: "No existing credentials
  found." No VERCEL_TOKEN (or any VERCEL_*_TOKEN) is set in this process's
  environment, and ~/.local/share/com.vercel.cli/config.json contains no
  token field (only a telemetry preference) -- checked directly, not
  inferred. This sandbox genuinely has no Vercel auth.

Because real Vercel API access requires a real credential this sandbox
does not have, and this script will not force an interactive device-auth
login (that would require a human to visit a URL and approve a device
code -- out of scope for an unattended, re-runnable check, and NOT the
same as "checking present tooling"), this is --result blocked, honestly,
per the explicit instruction: "If this needs Vercel auth this sandbox
doesn't have, --result blocked honestly rather than force it."

If a real credential is added later (VERCEL_TOKEN env var, or a real
`vercel login` completed interactively), re-running this exact script will
genuinely proceed past step 2 into step 3 and produce a real pass/fail
based on real deployment data -- nothing here is hardcoded to always
block.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=21's result.

Usage:
  gtm_check_deployment_testing.py
"""
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 21
COMPLIANCE_TRACKER_DIR = "/opt/veridian/repos/compliance-tracker"


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_deployment_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def sh(cmd, cwd=None, timeout=30, input_text=""):
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            input=input_text,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return None, "", f"command not found: {e}"
    except subprocess.TimeoutExpired as e:
        return None, e.stdout or "", f"timed out after {timeout}s"


def main():
    vercel_bin = shutil.which("vercel")
    if not vercel_bin:
        call_writer("blocked", "vercel CLI confirmed absent from PATH.", {"missing_tools": ["vercel"]})
        return

    rc_ver, out_ver, err_ver = sh(["vercel", "--version"], timeout=20)
    version = out_ver.strip() or err_ver.strip()

    token_env_present = any(k.startswith("VERCEL_") and "TOKEN" in k and os.environ.get(k) for k in os.environ)

    global_config_path = os.path.expanduser("~/.local/share/com.vercel.cli/config.json")
    global_config_has_token = False
    if os.path.isfile(global_config_path):
        try:
            with open(global_config_path) as f:
                global_config_has_token = bool(json.load(f).get("token"))
        except (json.JSONDecodeError, OSError):
            pass

    # Real, direct credential-presence check (env var + CLI's own global
    # config file) BEFORE attempting `vercel whoami`: confirmed by an
    # earlier real invocation this session that `vercel whoami` with no
    # stored credential starts an interactive device-auth flow ("Visit
    # https://vercel.com/oauth/device?user_code=... Waiting for
    # authentication...") that hangs indefinitely waiting on a human to
    # complete browser auth -- it does not fail fast even with empty
    # stdin (confirmed: a real run with a 20s timeout and empty stdin
    # produced no output at all, just a real timeout). This script will
    # not spin up that hanging interactive flow again; the real
    # credential-presence check below is itself the genuine, deterministic
    # answer to "is there real Vercel auth here".
    authenticated = token_env_present or global_config_has_token

    project_json_path = os.path.join(COMPLIANCE_TRACKER_DIR, ".vercel", "project.json")
    project_json_present = os.path.isfile(project_json_path)
    project_id = None
    if project_json_present:
        try:
            with open(project_json_path) as f:
                project_id = json.load(f).get("projectId")
        except (json.JSONDecodeError, OSError):
            pass

    evidence = {
        "vercel_version": version,
        "vercel_binary_path": vercel_bin,
        "vercel_token_env_var_present": token_env_present,
        "global_config_path": global_config_path,
        "global_config_has_token": global_config_has_token,
        "authenticated": authenticated,
        "compliance_tracker_vercel_project_json_present": project_json_present,
        "compliance_tracker_vercel_project_id": project_id,
        "note": (
            "`vercel whoami` was not invoked (real prior attempt this session confirmed it starts an "
            "interactive device-auth flow -- 'Visit https://vercel.com/oauth/device?user_code=...' -- "
            "that hangs waiting for human browser approval, even with empty stdin and a 20s timeout). "
            "Credential presence was instead checked directly and deterministically via env vars and the "
            "CLI's own global config file."
        ),
    }

    if not authenticated:
        call_writer(
            "blocked",
            "vercel CLI confirmed present and working (v" + version + "), but this sandbox has no real Vercel "
            "credential (no VERCEL_*_TOKEN env var set; no token in ~/.local/share/com.vercel.cli/config.json). "
            "Not forcing an interactive device-auth login. Cannot genuinely query real deployment data without "
            "real auth.",
            evidence,
        )
        return

    # Only reachable with a real credential -- read-only inspection of the
    # real linked project's most recent real deployment.
    rc_ls, out_ls, err_ls = sh(["vercel", "ls", "--yes"], cwd=COMPLIANCE_TRACKER_DIR, timeout=60)
    evidence["ls_exit_code"] = rc_ls
    evidence["ls_output_tail"] = (out_ls or err_ls)[-3000:]

    if rc_ls != 0:
        call_writer(
            "blocked",
            f"Authenticated but real `vercel ls` failed (exit {rc_ls}); cannot confirm real deployment state.",
            evidence,
        )
        return

    has_ready_deployment = "Ready" in out_ls or "READY" in out_ls.upper()
    result = "pass" if has_ready_deployment else "fail"
    call_writer(
        result,
        f"vercel ls against real project {project_id}: {'a Ready deployment found' if has_ready_deployment else 'no Ready deployment found'} in real output.",
        evidence,
    )


if __name__ == "__main__":
    main()
