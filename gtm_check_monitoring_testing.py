#!/usr/bin/env python3
"""gtm_check_monitoring_testing.py -- real, re-runnable check for GTM
certification category_index=20 ("monitoring testing").

What it does, every time it runs:
  Runs real `systemctl --user is-active` and `systemctl --user is-enabled`
  against the 3 real systemd units already confirmed this session as this
  server's live monitoring/dispatch backbone:
    - veridian-directive-engine.service   (directive engine dispatch loop)
    - veridian-governor-tick.service      (resource governor tick loop)
    - veridian-cron-health-check-15min.timer (15-min health check cadence)
  These are real, mechanical process-state checks (real exit codes and
  real stdout from `systemctl`), never parsed from a description or
  narrated from memory.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> `systemctl --user is-active` reports "active" AND
           `systemctl --user is-enabled` reports "enabled" for ALL 3 units.
  Any unit reporting a different real state (inactive, failed, disabled,
  static, etc.) is a genuine FAIL -- systemctl ran and returned the real
  state, this is real evidence, never "blocked".
  "blocked" is reserved for `systemctl` itself being confirmed absent from
  PATH, or the `--user` bus being unreachable (e.g. no DBUS_SESSION_BUS
  for this user -- checked via a real systemctl invocation's own exit
  behavior, not assumed).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=20's result.

Usage:
  gtm_check_monitoring_testing.py
"""
import json
import os
import shutil
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 20

EXPECTED_UNITS = [
    "veridian-directive-engine.service",
    "veridian-governor-tick.service",
    "veridian-cron-health-check-15min.timer",
]


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_monitoring_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def systemctl(*args):
    try:
        p = subprocess.run(
            ["systemctl", "--user"] + list(args),
            capture_output=True, text=True, timeout=20,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return None, "", "systemctl not found"
    except subprocess.TimeoutExpired:
        return None, "", "systemctl timed out"


def main():
    if not shutil.which("systemctl"):
        call_writer("blocked", "systemctl confirmed absent from PATH; cannot check real unit state.", {"missing_tools": ["systemctl"]})
        return

    # Real reachability probe of the --user bus itself before trusting any
    # per-unit result.
    rc_probe, out_probe, err_probe = systemctl("is-system-running")
    if rc_probe is None:
        call_writer(
            "blocked",
            f"systemctl --user bus unreachable for this user (probe failed): {err_probe}",
            {"probe_command": "systemctl --user is-system-running", "probe_stderr": err_probe},
        )
        return

    results = {}
    for unit in EXPECTED_UNITS:
        rc_active, active_out, active_err = systemctl("is-active", unit)
        rc_enabled, enabled_out, enabled_err = systemctl("is-enabled", unit)
        results[unit] = {
            "is_active_exit_code": rc_active,
            "is_active_state": active_out,
            "is_enabled_exit_code": rc_enabled,
            "is_enabled_state": enabled_out,
            "meets_pass_bar": active_out == "active" and enabled_out == "enabled",
        }

    failing_units = {u: r for u, r in results.items() if not r["meets_pass_bar"]}
    result = "fail" if failing_units else "pass"

    evidence = {
        "expected_units": EXPECTED_UNITS,
        "results_per_unit": results,
        "failing_units": list(failing_units.keys()),
        "pass_criterion": "systemctl --user is-active reports 'active' AND is-enabled reports 'enabled' for all 3 expected units",
        "commands_run": ["systemctl --user is-active <unit>", "systemctl --user is-enabled <unit>"],
        "bus_reachability_probe": {"command": "systemctl --user is-system-running", "exit_code": rc_probe, "state": out_probe},
    }
    summary = (
        f"{len(EXPECTED_UNITS) - len(failing_units)}/{len(EXPECTED_UNITS)} expected monitoring units "
        f"active+enabled via real systemctl --user checks."
        + (f" Failing: {', '.join(failing_units.keys())}." if failing_units else "")
    )
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
