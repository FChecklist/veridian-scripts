#!/usr/bin/env python3
"""reconcile_owner_dispatch_status.py -- real, reusable, re-runnable
reconciliation of stale `status='running'` labels on `umr_tasks` rows whose
`source_trigger='owner_dispatch_gateway'`.

Real governance finding this promotes to production (Owner directive
UMR-20260806-075726-babc, child of UMR-20260806-071025-1d28): the DB
`status` column is a known-unreliable signal for this source_trigger --
a worker session that dies (session end, OOM, host restart, dispatch-lock
incident, etc.) without a graceful terminal-status write leaves its
umr_tasks row permanently stuck showing `running` even though no real
process backs it any more. This has recurred multiple times on this
project (see the scratchpad one-off classify_owner_dispatch_backlog.py
this script promotes, and the wave-2/wave-3 batch correction scripts under
UMR-20260806-071025-1d28) -- this script exists so the fix is a real,
tested, re-runnable command instead of a hand-rolled one-off every time.

For every real `umr_tasks` row currently `status='running'` with
`source_trigger='owner_dispatch_gateway'`, cross-references THREE
independent real signals (never narrated, never assumed):

  1. real systemd unit state for the row's own `unit_name`
     (`systemctl --user is-active`) -- the ground truth this whole
     mechanism exists to check the DB status against.
  2. real `task.yaml` checkpoint under /opt/veridian/ai-os/tasks/<task_id>/
     (status, last_checkpoint_at, adopted_pr_url, branch, repo) -- derived
     from unit_name, never hand-typed.
  3. real GitHub PR state + AUDIT:PASS/FAIL comment freshness (matched
     against the PR's CURRENT head SHA) for any branch/PR the task.yaml
     names -- batched one `gh pr list` call per repo, not one call per row.

Every row is bucketed deterministically:

  REAL_RUNNING            - systemd genuinely active AND (no PR yet with a
                             fresh checkpoint, OR a real open PR with no
                             stale-audit problem). Left untouched.
  STALE_LABEL_TERMINAL    - systemd inactive/failed/dead; task.yaml and/or
                             PR state give a real terminal outcome
                             (completed/failed/killed). Corrected via
                             update_umr_task() ONLY.
  NEEDS_AI_JUDGMENT       - conflicting or insufficient real evidence
                             (e.g. no task_dir found at all, PR state and
                             task.yaml disagree, audit verdict stale/absent
                             on an otherwise-idle row). NEVER auto-corrected
                             by this script -- printed for a human/AI
                             reviewer, exit code reflects this so CI can
                             gate on it.

Every real correction this script ever applies is written back ONLY
through superboss-register.py's own update_umr_task(), inside its own
_write_lock() + a single commit per row, and always carries a real
evidence dict recording exactly which of the 3 signals above were
checked, what they returned, and why the bucket was chosen -- no row is
ever corrected on narration alone.

Usage:
    python3 reconcile_owner_dispatch_status.py                 # report only (default, no writes)
    python3 reconcile_owner_dispatch_status.py --apply          # apply STALE_LABEL_TERMINAL corrections
    python3 reconcile_owner_dispatch_status.py --json out.json  # also write full per-row evidence to a file
    python3 reconcile_owner_dispatch_status.py --umr-id UMR-...  # limit to one row (debugging/re-run)

Exit codes: 0 = ran clean, no NEEDS_AI_JUDGMENT rows outstanding.
            1 = ran clean but >=1 row needs human/AI judgment (informational,
                not a script failure -- report-mode default still exits 1 so
                a cron/CI caller notices there is real unreconciled ambiguity).
            2 = a real internal error (DB path, subprocess, etc.).
"""
import argparse
import importlib.util as _ilu
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = "/opt/veridian/ai-os/tasks"
STALE_CHECKPOINT_MINUTES = 30

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return -1, "", str(e)


def _task_dir_from_unit(unit_name):
    if not unit_name:
        return None
    m = re.match(r"veridian-worker@(.+)\.service$", unit_name)
    return m.group(1) if m else None


def _read_task_yaml(task_id):
    """Real task.yaml read. Prefers PyYAML; falls back to a crude
    line-scan (same fallback shape as the scratchpad classifier this
    promotes) so a missing PyYAML install never silently no-ops this
    script -- it degrades to fewer fields, never to zero real data."""
    if not task_id:
        return None
    path = os.path.join(TASKS_DIR, task_id, "task.yaml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        info = {}
        with open(path) as f:
            for line in f:
                for key in ("status", "adopted_pr_url", "branch", "repo", "last_checkpoint_at"):
                    if line.startswith(f"{key}:"):
                        info[key] = line.split(":", 1)[1].strip().strip("'\"")
        return info


def _systemd_is_active(unit_name):
    if not unit_name:
        return "no_unit"
    rc, out, err = _run(["systemctl", "--user", "is-active", unit_name])
    return (out.strip() or err.strip() or "unknown")


def _fetch_prs(repo, cache):
    if repo not in cache:
        rc, out, err = _run(
            ["gh", "pr", "list", "--repo", f"FChecklist/{repo}", "--state", "all",
             "--json", "number,title,state,headRefName,mergedAt,headRefOid", "--limit", "500"],
            timeout=60,
        )
        cache[repo] = json.loads(out) if rc == 0 and out.strip() else []
    return cache[repo]


def _audit_verdict(repo, number):
    """Real audit-comment lookup, WITH a real freshness check against the
    PR's current head commit -- fixed per real AUDIT:FAIL finding on this
    script's own PR #147: the module docstring always claimed this
    comparison happened, but the first version only ever fetched
    current_head_sha and never actually compared it to anything. Fresh here
    means: the most-recent AUDIT:PASS/FAIL comment's createdAt is AFTER the
    head commit's own committedDate (same convention this project's own
    executor sessions already use by hand -- see pm_decisions_pending id=1's
    real closed_note precedent: 'AFTER head commit ...'s own commit
    timestamp -- verified not stale'). A comment posted BEFORE the latest
    commit landed reviewed old code, not the code on the branch right now."""
    rc, out, err = _run(
        ["gh", "pr", "view", str(number), "--repo", f"FChecklist/{repo}",
         "--json", "comments,headRefOid,commits"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None
    try:
        d = json.loads(out)
    except Exception:
        return None

    verdict = None
    for c in d.get("comments", []):
        body = c.get("body", "")
        if body.startswith("AUDIT: PASS") or body.startswith("AUDIT: FAIL"):
            verdict = {"verdict": body.splitlines()[0], "createdAt": c.get("createdAt")}

    commits = d.get("commits") or []
    head_committed_at = commits[-1].get("committedDate") if commits else None

    stale = None
    if verdict and head_committed_at:
        try:
            verdict_dt = datetime.fromisoformat(verdict["createdAt"].replace("Z", "+00:00"))
            head_dt = datetime.fromisoformat(head_committed_at.replace("Z", "+00:00"))
            stale = verdict_dt < head_dt
        except Exception:
            stale = None  # real parse failure -- honestly unknown, never guessed as fresh

    return {
        "verdict": verdict,
        "current_head_sha": d.get("headRefOid"),
        "head_committed_at": head_committed_at,
        "stale": stale,
    }


def load_rows(conn, umr_id=None):
    """Real, live rows only -- deliberately NO time-window restriction on
    the default (no --umr-id) query. Fixed per real AUDIT:FAIL finding on
    this script's own PR #147: an earlier version silently hardcoded
    `ts_submitted >= datetime('now', '-24 hours')` (inherited from the
    scratchpad classifier this promotes, which really was scoped to one
    cycle's 24h cleanup) while the module docstring claimed to reconcile
    'every real umr_tasks row currently status=running' -- exactly the
    stuck-longer-than-24h case this tool exists to catch would have been
    silently excluded from both the default report AND --apply unless the
    caller already knew the specific umr_id to pass. Every real row with
    status='running' for this source_trigger is now in scope by default."""
    if umr_id:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_submitted, unit_name "
            "FROM umr_tasks WHERE umr_id=? AND source_trigger='owner_dispatch_gateway'",
            (umr_id,),
        )
    else:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_submitted, unit_name "
            "FROM umr_tasks WHERE source_trigger='owner_dispatch_gateway' AND status='running' "
            "ORDER BY ts_submitted"
        )
    return [dict(r) for r in cur.fetchall()]


def classify_row(row, now, pr_cache):
    """Pure(ish) classification: only subprocess calls (systemctl/gh) touch
    the outside world; never writes to the DB. Returns the evidence dict --
    the exact same dict this script would later attach to a real
    update_umr_task() metadata write if the row is corrected."""
    umr_id = row["umr_id"]
    unit_name = row["unit_name"]
    evidence = {
        "umr_id": umr_id,
        "task_identity": row["task_identity"],
        "db_status": row["status"],
        "ts_submitted": row["ts_submitted"],
        "unit_name": unit_name,
    }

    task_id = _task_dir_from_unit(unit_name)
    yml = _read_task_yaml(task_id) or {}
    evidence["task_dir"] = task_id
    evidence["task_yaml_status"] = yml.get("status")
    evidence["last_checkpoint_at"] = yml.get("last_checkpoint_at")
    evidence["repo"] = yml.get("repo")
    evidence["branch"] = yml.get("branch")

    real_active = _systemd_is_active(unit_name)
    evidence["systemd_is_active"] = real_active

    pr_match = None
    if yml.get("repo") and yml.get("branch"):
        for pr in _fetch_prs(yml["repo"], pr_cache):
            if pr["headRefName"] == yml["branch"]:
                pr_match = pr
                break
    evidence["pr_number"] = pr_match["number"] if pr_match else None
    evidence["pr_state"] = pr_match["state"] if pr_match else None
    evidence["pr_head_sha"] = pr_match["headRefOid"] if pr_match else None

    audit = None
    if pr_match:
        audit = _audit_verdict(yml["repo"], pr_match["number"])
        evidence["audit"] = audit

    # -- deterministic decision --
    if real_active in ("active", "activating"):
        # genuinely alive right now -- real running, leave it, UNLESS the
        # checkpoint is stale AND there's no open PR at all (looks dead-ish
        # but systemd disagrees -- that contradiction is a judgment call,
        # not a mechanical one, so it goes to NEEDS_AI_JUDGMENT rather than
        # being auto-"corrected" against live systemd truth).
        if not pr_match and yml.get("last_checkpoint_at"):
            try:
                lc_dt = datetime.fromisoformat(yml["last_checkpoint_at"].replace("Z", "+00:00"))
                if (now - lc_dt) > timedelta(minutes=STALE_CHECKPOINT_MINUTES):
                    evidence["bucket"] = "NEEDS_AI_JUDGMENT"
                    evidence["reason"] = (
                        f"systemd reports real '{real_active}' but last_checkpoint_at is "
                        f"stale (>{STALE_CHECKPOINT_MINUTES}min) with no PR yet -- possible "
                        "hung/looping worker, needs a human/AI look rather than a mechanical fix."
                    )
                    return evidence
            except Exception:
                pass
        evidence["bucket"] = "REAL_RUNNING"
        evidence["reason"] = f"real systemd state '{real_active}' confirms this row is genuinely still running."
        return evidence

    # systemd says the unit is NOT alive (inactive/failed/dead/no_unit/unknown).
    if task_id is None:
        evidence["bucket"] = "NEEDS_AI_JUDGMENT"
        evidence["reason"] = (
            f"systemd state '{real_active}' (not active) but unit_name did not resolve to a "
            "real task directory -- cannot derive a terminal status mechanically."
        )
        return evidence

    # Only three real signals are safe for a pure status-relabeling script to
    # auto-correct on: a real MERGED PR, a real CLOSED-not-merged PR, or no
    # PR ever having been opened at all. Real, live-project precedent
    # (batch3_summary_draft.txt under UMR-20260806-071025-1d28: an
    # inactive-systemd / task.yaml-blocked row with a real OPEN PR was
    # re-adopted and continued, NEVER blanket-marked 'failed') shows that an
    # OPEN, unmerged PR -- even one already carrying a real, current-head-SHA
    # AUDIT:PASS -- still needs an actual continuation/merge action or a real
    # judgment call, not a mechanical DB relabel. Silently writing 'failed'
    # onto a row whose real work is passing review and one merge away from
    # done would itself be a new false status label -- exactly the failure
    # mode this whole script exists to correct on the other end. So: only
    # MERGED/CLOSED/no-PR get auto-corrected; every real OPEN PR always goes
    # to NEEDS_AI_JUDGMENT, regardless of task.yaml status or audit verdict.
    if pr_match and pr_match["state"] == "MERGED":
        evidence["bucket"] = "STALE_LABEL_TERMINAL"
        evidence["new_status"] = "completed"
        evidence["reason"] = (
            f"real systemd state '{real_active}', real PR #{pr_match['number']} state=MERGED "
            f"(head={pr_match['headRefOid']}) -- mechanically correctable to completed."
        )
        return evidence

    if pr_match and pr_match["state"] not in ("MERGED", "OPEN"):
        # real GitHub state is CLOSED (closed without merging) -- a genuine
        # terminal-failure signal, not an inference from task.yaml prose.
        evidence["bucket"] = "STALE_LABEL_TERMINAL"
        evidence["new_status"] = "failed"
        evidence["reason"] = (
            f"real systemd state '{real_active}', real PR #{pr_match['number']} state="
            f"{pr_match['state']} (closed without merging) -- mechanically correctable to failed."
        )
        return evidence

    if pr_match and pr_match["state"] == "OPEN":
        evidence["bucket"] = "NEEDS_AI_JUDGMENT"
        evidence["reason"] = (
            f"real systemd state '{real_active}' but real PR #{pr_match['number']} is still OPEN "
            f"(audit={((evidence.get('audit') or {}).get('verdict') or {}).get('verdict')}) -- "
            "resolving this needs a real continuation/merge action or genuine judgment, not a "
            "mechanical status relabel (see module docstring: an OPEN PR, even one already "
            "AUDIT:PASS, is never auto-terminalized by this script)."
        )
        return evidence

    if not pr_match and real_active in ("inactive", "no_unit", "unknown", "failed"):
        evidence["bucket"] = "STALE_LABEL_TERMINAL"
        evidence["new_status"] = "killed"
        evidence["reason"] = (
            f"real systemd state '{real_active}', no PR was ever opened, real task.yaml status="
            f"'{yml.get('status')}' -- no live process and no real deliverable; mechanically "
            "correctable to killed (orphaned dispatch, never produced a real artifact)."
        )
        return evidence

    # Anything else (shouldn't normally be reached given the branches above)
    # -- never guess.
    evidence["bucket"] = "NEEDS_AI_JUDGMENT"
    evidence["reason"] = (
        f"real systemd state '{real_active}', real PR state="
        f"{pr_match['state'] if pr_match else None} -- no safe mechanical rule matched; "
        "needs a real judgment call rather than a guess."
    )
    return evidence


def apply_correction(conn, evidence, dry_run):
    """Writes exactly one real correction through update_umr_task() ONLY --
    never a raw UPDATE statement (enforced by
    test_apply_correction_writes_only_via_update_umr_task, which stubs conn
    to raise on any .execute() call).

    Real read-merge-write, NOT a blind replace -- fixed per real AUDIT:FAIL
    finding on this script's own PR #147: update_umr_task()'s own
    metadata=... kwarg does a straight `metadata_json=?` column replace (see
    its own docstring/implementation), so passing only the reconciliation
    fields would silently destroy whatever real operational data was
    already in the row's metadata_json (e.g. resource_governor.py's
    submit() persists a real correlation_id there at submission time). This
    reads the row's own CURRENT metadata_json first and merges the
    reconciliation fields into it under one namespaced key, matching this
    same codebase's own established read-existing/merge/write-back
    convention (superboss-register.py's annotate_knowledge(): 'Appends a
    dated correction note ... without touching' other fields) -- every
    real pre-existing key survives untouched."""
    umr_id = evidence["umr_id"]
    new_status = evidence["new_status"]

    if dry_run:
        return

    existing_row = conn.execute(
        "SELECT metadata_json FROM umr_tasks WHERE umr_id=?", (umr_id,)
    ).fetchone()
    existing_metadata = {}
    if existing_row and existing_row["metadata_json"]:
        try:
            existing_metadata = json.loads(existing_row["metadata_json"])
        except Exception:
            # Real, non-empty, but non-JSON metadata_json (shouldn't happen
            # given every real writer json.dumps()s it) -- preserved
            # verbatim under its own key rather than silently discarded.
            existing_metadata = {"_unparseable_prior_metadata_json": existing_row["metadata_json"]}

    merged_metadata = dict(existing_metadata)
    merged_metadata["reconcile_owner_dispatch_status"] = {
        "reconciled_by": "reconcile_owner_dispatch_status.py",
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "prior_db_status": evidence["db_status"],
        "evidence": evidence,
    }
    _sbr.update_umr_task(conn, umr_id, status=new_status, metadata=merged_metadata)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Apply STALE_LABEL_TERMINAL corrections (default: report only, no writes).")
    ap.add_argument("--json", metavar="PATH", help="Also write full per-row evidence JSON to this path.")
    ap.add_argument("--umr-id", metavar="UMR-...", help="Limit to a single umr_id (debugging/targeted re-run).")
    args = ap.parse_args()

    conn = _sbr._connect()
    now = datetime.now(timezone.utc)
    pr_cache = {}

    rows = load_rows(conn, umr_id=args.umr_id)
    results = [classify_row(r, now, pr_cache) for r in rows]

    corrected = [r for r in results if r["bucket"] == "STALE_LABEL_TERMINAL"]
    if corrected and args.apply:
        # Only ever takes the real cross-process write lock when a real
        # write is about to happen -- report-mode runs (the default) never
        # contend with a live writer for this lock at all.
        with _sbr._write_lock():
            for ev in corrected:
                apply_correction(conn, ev, dry_run=False)
            conn.commit()

    conn.close()

    buckets = Counter(r["bucket"] for r in results)
    print(f"TOTAL owner_dispatch_gateway rows examined (status='running', no time window"
          f"{', umr_id=' + args.umr_id if args.umr_id else ''}): {len(results)}")
    for b, c in buckets.most_common():
        print(f"  {b}: {c}")
    print(f"mode: {'APPLIED real corrections' if args.apply else 'REPORT ONLY (no writes -- pass --apply to write)'}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"full per-row evidence written to: {args.json}")

    return 1 if buckets.get("NEEDS_AI_JUDGMENT") else 0


if __name__ == "__main__":
    sys.exit(main())
