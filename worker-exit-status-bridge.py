#!/usr/bin/env python3
"""
ExecStopPost hook for veridian-worker@.service (STEP 2 fix, UMR-20260807-020911-7f31 /
task-20260807-052027-platform-integrity--worker-units-exit-0).

Root cause this closes: worker-entrypoint.sh's every exit path calls
`veridian-task.py checkpoint`, which writes task.yaml/CONTROLLER.yaml and fires an
audit-log row via `superboss-register.py log-action` (veridian-task.py's own
_auto_log_task_event) -- but NEVER touches umr_tasks.status. umr_tasks.status is set to
'running' exactly once, at dispatch time (resource_governor.py's dispatch_one()), and
nothing in the worker's own lifecycle ever writes it again. Every "give up, no more
retries" path in worker-entrypoint.sh (budget cap, preflight hard stop, quality-gate
exhausted after 2 auto-fix attempts) disables the unit and exits 0 -- ExecMainStatus=0,
Result=success, systemd never restarts it -- while umr_tasks silently keeps saying
'running' forever, with no automatic path back to a terminal state. Confirmed live: 32
real rows in exactly this shape, ages 0.9h-5.3h. Full write-up:
this task's own PROGRESS.md.

Wired in as `ExecStopPost=/opt/veridian/scripts/worker-exit-status-bridge.py %i` in
systemd/veridian-worker@.service specifically because ExecStopPost runs on EVERY stop --
clean exit, non-zero exit, TimeoutStopSec, SIGKILL, OOM-kill -- unlike the bash
`trap ... EXIT` already inside worker-entrypoint.sh, which cannot run at all once the
shell itself is SIGKILLed (bash traps do not fire for SIGKILL, only for signals a
process can catch/ignore) -- exactly the "including timeout and kill" case this task's
own SPEC requires and the entrypoint's own trap structurally cannot cover.

Supervisor-side reuse (UMR-20260813-090037-9a34, addendum to UMR-20260806-171945-5767,
closing the real AUDIT:FAIL finding "(b) NO SUPERVISOR-SIDE BRIDGE" against this PR):
veridian-supervisor@.service's own success/failure paths (supervisor-entrypoint.sh) have
the exact same shape as worker-entrypoint.sh's -- every exit path calls
`veridian-task.py checkpoint` (status completed/blocked) and never `mark-umr-terminal` --
so this same script, same decision rule, is wired a second time via
`ExecStopPost=/opt/veridian/scripts/worker-exit-status-bridge.py %i supervisor` in
systemd/veridian-supervisor@.service, rather than forking a near-duplicate script (this
codebase's own zero-duplication convention). `unit_kind` (default "worker", the
pre-existing, back-compatible behavior) selects the real unit-name template
(`veridian-worker@<id>.service` / `veridian-supervisor@<id>.service`) and the real
per-task log file (`worker.log` / `supervisor.log`, matching each entrypoint's own
`StandardOutput=append:.../<name>.log` unit directive) -- everything else (the umr_tasks
lookup, the self-reported-negative-only decision rule, the mark-umr-terminal call) is
identical for both, since supervisor-entrypoint.sh's own vocabulary
(`completed`/`blocked`) is already a strict subset of SELF_REPORTED_NEGATIVE_STATUSES
below.

UMR-20260813-215742-db64 addendum (real fix for the supervisor's own hard-fail-every-
no-op-branch defect, task-20260813-215756): supervisor-entrypoint.sh's real NO-OP-BRANCH-
GUARD-BLOCK now writes task.yaml checkpoint status='completed_no_change' -- never
'completed' -- for the one, narrow, real case a worker/supervisor branch has ZERO commits
ahead of its own base branch (`git rev-list --count base..branch` == 0): the real
deliverable was already merged by a prior task, this is a genuine, evidenced completion,
not a plumbing failure, and must not fall through to the failed/killed path a supervisor
hard-fail (exit 1) would otherwise reach. That checkpoint is always paired with a real,
structured `no_op.json` marker (base_sha/branch_sha/base_branch/branch/reason) written to
the SAME task dir, in the SAME commit of work, by the SAME script, immediately before
exit 0 -- so, unlike point 1 below, this hook trusts that marker's branch_sha as a real
completion candidate and bridges it to umr_tasks.status='completed' via the exact same
`mark-umr-terminal --status completed --commit-sha <branch_sha>` real-evidence gate
(validate_umr_terminal_completion_evidence) every other real 'completed' writer already
goes through -- it is independently, live re-verified there (a real `git merge-base
--is-ancestor` check against the real local repo checkout), never blindly trusted from
the marker file alone. See `_bridge_no_op_completion()` below.

Deliberately conservative in both directions:
  1. NEVER writes status=completed/completed_unmerged from a bare exit code alone. This
     hook only ever has a process exit code to go on for every OTHER status, and this
     task's own SPEC is explicit: a clean exit code is NOT evidence of substantive
     completion. Only reconcile_stale_running_workers.py's own real-artifact check (a
     real commit, verified server-side by validate_umr_terminal_completion_evidence --
     real git ancestry / real file existence, never our own guess) -- or the real,
     structured no_op.json marker described above -- is allowed to write those two
     statuses.
  2. Only writes umr_tasks.status=failed for a DEFINITIVE, already-self-reported negative
     outcome: task.yaml's own last checkpoint status is one of
     failed/blocked/cancelled/rejected_duplicate/superseded/not_needed (same vocabulary
     resource_governor.py's own _forward_progress_decision() already treats as "no
     override, default failed retained", reused verbatim here so the two never drift)
     -- i.e. the worker itself already decided, deliberately, durably (written to
     task.yaml BEFORE this process exited) that it is giving up. That is exactly the
     class of row this task's SPEC found stuck: self-disabled, ExecMainStatus=0,
     Result=success, task.yaml says failed/blocked, umr_tasks still says running.
  3. Never touches a row while task.yaml's last status is pending_review (a deliberate
     hand-off to veridian-supervisor@<id>.service, which owns the umr's next real
     decision -- the umr is still genuinely 'running' at the dispatch layer) or
     in_progress/pending (this stop might be one of systemd's own Restart=on-failure
     retries about to relaunch the SAME unit RestartSec=30s from now -- writing a
     terminal status here would both be a real guess with no evidence, AND could let some
     other caller's find_active_umr_by_identity() dedup check see this task_identity as
     no-longer-active while a retry is actually still in flight, a real duplicate-dispatch
     risk). Rows that end up genuinely abandoned (retries exhausted, no further exit ever
     comes -- e.g. StartLimitBurst hit, or an OOM-kill loop that outlives this hook) are
     exactly what reconcile_stale_running_workers.py's own ActiveState=inactive sweep
     picks up next -- deferred to real evidence, never dropped.
  4. Every DB write goes through the existing `superboss-register.py mark-umr-terminal`
     CLI (never a raw SQL UPDATE), same convention as every other real writer in this
     codebase.
  5. Fully best-effort / fail-open, and always exits 0 itself: a non-zero ExecStopPost
     exit status would make systemd mark the UNIT's own Result as failed regardless of
     what the main process actually did, corrupting the exact ExecMainStatus/Result
     signal this whole fix depends on. Any exception here is caught, logged to the
     task's own worker.log, and swallowed.
"""
import json
import os
import subprocess
import sys

import yaml

AI_OS = "/opt/veridian/ai-os"
SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

# Real, canonical DB-path resolution (OCID-068, see superboss-register.py's own
# resolve_superboss_db_path() docstring) -- same lazy-import-cached convention
# resource_governor.py's own _superboss_register() already established, reused here
# instead of a hardcoded path so this module (a) never drifts from the one real
# SUPERBOSS_REGISTER_DB override every other real caller already honors, and (b) is
# genuinely, hermetically testable against a real scratch DB (see
# tests/test_worker_exit_status_bridge.py) the same way tests/
# test_mark_umr_terminal_structured_evidence.py already tests the CLI itself.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_exit_bridge", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

# Same real vocabulary resource_governor.py's own _forward_progress_decision() already
# treats as "no override, default failed retained" for a task.yaml that has already
# self-reported a genuinely-terminal-negative outcome -- reused here verbatim so the two
# never drift apart.
SELF_REPORTED_NEGATIVE_STATUSES = (
    "failed", "blocked", "cancelled", "rejected_duplicate", "superseded", "not_needed",
)

# UMR-20260813-215742-db64: the one real self-reported POSITIVE-but-distinct status this
# hook recognizes -- see the module docstring's own addendum section above for the full
# real chain (supervisor-entrypoint.sh's NO-OP-BRANCH-GUARD-BLOCK writes this, paired with
# a real no_op.json evidence marker, only when a branch genuinely has zero commits ahead
# of its base). Kept as its own named constant (not folded into
# SELF_REPORTED_NEGATIVE_STATUSES, which it structurally is not) so the two vocabularies
# can never silently drift into meaning the same thing.
SELF_REPORTED_NO_OP_STATUS = "completed_no_change"


def _load_task_yaml(task_id):
    path = f"{AI_OS}/tasks/{task_id}/task.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None


def _load_no_op_marker(task_id):
    """UMR-20260813-215742-db64: real, structured hand-off written by
    supervisor-entrypoint.sh's own NO-OP-BRANCH-GUARD-BLOCK immediately before it
    checkpoints task.yaml status='completed_no_change' and exits 0. Read here, not
    re-derived -- same 'trust the caller's own already-computed real evidence, which the
    real mark-umr-terminal gate independently re-verifies rather than blindly accepts'
    posture the --evidence-json/--handoff-envelope flags elsewhere in this codebase
    already established. Missing/unparseable is a real, safe fail-open (leaves the row at
    'running' for a human or the STEP 3 reconciler), never a guess."""
    path = f"{AI_OS}/tasks/{task_id}/no_op.json"
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _find_umr_row_for_unit(unit_name):
    """Real, read-only sqlite3 lookup -- same 'python3's own sqlite3 module, never the
    CLI binary' convention worker-entrypoint.sh's own deterministic-briefing lookup
    already uses. Returns the most recently submitted umr_tasks row bound to this
    unit_name, or None if no such row exists."""
    import sqlite3
    conn = None
    try:
        conn = sqlite3.connect(_resolve_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT umr_id, status FROM umr_tasks WHERE unit_name=? ORDER BY ts_submitted DESC LIMIT 1",
            (unit_name,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# Real unit-name templates + per-task log file this bridge is wired against -- see the
# "Supervisor-side reuse" docstring section above. Adding a 3rd real unit kind here is
# the one, single place that would need a new entry; everything else in this module is
# already generic over unit_kind.
UNIT_KIND_CONFIG = {
    "worker": {"unit_template": "veridian-worker@{task_id}.service", "log_name": "worker.log"},
    "supervisor": {"unit_template": "veridian-supervisor@{task_id}.service", "log_name": "supervisor.log"},
}


def _log(task_id, unit_kind, message):
    log_name = UNIT_KIND_CONFIG[unit_kind]["log_name"]
    try:
        with open(f"{AI_OS}/tasks/{task_id}/{log_name}", "a") as f:
            f.write(f"[worker-exit-status-bridge:{unit_kind}] {message}\n")
    except OSError:
        pass


def run(task_id, unit_kind="worker"):
    unit_name = UNIT_KIND_CONFIG[unit_kind]["unit_template"].format(task_id=task_id)

    row = _find_umr_row_for_unit(unit_name)
    if row is None:
        # No umr_tasks row was ever bound to this unit (a manually-started unit, a test
        # unit, or a task dispatched by a path that never wrote unit_name) -- nothing to
        # bridge, real fail-open, not an error.
        return
    if row["status"] != "running":
        # Already terminal/queued/whatever -- another writer (this same script on a
        # prior stop of this same unit, the STEP 3 reconciler, a human) already handled
        # it. Idempotent, deliberate no-op.
        return

    task = _load_task_yaml(task_id)
    if not task:
        # No task.yaml at all -- cannot determine a real self-reported outcome. Leave the
        # row at 'running'; reconcile_stale_running_workers.py's own no-task.yaml branch
        # is the real, evidence-gated place this gets resolved (requeue), not a guess here.
        _log(task_id, unit_kind, f"no task.yaml for {task_id}, umr {row['umr_id']} left at running for STEP 3 reconciler")
        return

    checkpoints = task.get("checkpoints") or []
    last_status = checkpoints[-1]["status"] if checkpoints else task.get("status")

    if last_status == SELF_REPORTED_NO_OP_STATUS:
        _bridge_no_op_completion(task_id, unit_kind, unit_name, row, task)
        return

    if last_status not in SELF_REPORTED_NEGATIVE_STATUSES:
        # pending_review (supervisor handoff) / completed (already gated elsewhere) /
        # in_progress / pending (ambiguous mid-work stop, maybe a systemd restart is
        # coming) -- none of these are a hasty, safe terminal write from an exit hook.
        # Leave alone.
        _log(task_id, unit_kind, f"last task.yaml status={last_status!r} for umr {row['umr_id']} -- "
                                  f"not a self-reported negative outcome, leaving at running")
        return

    # task-20260815-230158-propagate-the-real-preflight-denial-reas: for the one real,
    # common case that matters most operationally -- a preflight hard stop
    # (worker-entrypoint.sh's PREFLIGHT-GUARD-BLOCK, e.g. tight_task_schema_violation,
    # credit_accountant_rejected, circuit_breaker_tripped, ...) -- task.yaml's own last
    # checkpoint note already carries the real, specific, self-describing rejection
    # reason (e.g. "PRE-FLIGHT HARD STOP (tight_task_schema_violation): Complexity tier
    # moderate is not recognized. Please use one of: mechanical, integrative,
    # judgment."). Previously this hook discarded that note entirely and wrote only its
    # own generic "worker-exit-status-bridge (ExecStopPost, ...)" boilerplate as the
    # register reason -- which names the COMPONENT THAT REPORTED the exit, not the
    # cause, forcing every reader of a failed register row to SSH in and read task.yaml
    # by hand to learn why. When that real note exists, use IT as the register reason
    # (so tight_task_schema_violation-shaped rejections are diagnosable straight from
    # the register), with a short suffix identifying this bridge as the writer -- never
    # leading with the bridge name. Every other self-reported-negative status (no note,
    # or a non-'blocked' status such as plain 'failed'/'cancelled'/etc., which this
    # hook's own generic boilerplate has always covered) is completely unaffected.
    checkpoint_note = None
    if last_status == "blocked":
        checkpoint_note = checkpoints[-1].get("note") if checkpoints else task.get("note")

    if checkpoint_note:
        reason = f"{checkpoint_note} [via worker-exit-status-bridge]"
    else:
        reason = (
            f"worker-exit-status-bridge (ExecStopPost, STEP 2 fix task-20260807-052027-platform-"
            f"integrity--worker-units-exit-0{'  + supervisor-side extension UMR-20260813-090037-9a34' if unit_kind == 'supervisor' else ''}): "
            f"unit {unit_name} stopped with task.yaml's own last "
            f"checkpoint status={last_status!r} (a self-reported, no-more-automatic-progress "
            f"outcome) -- bridging to umr_tasks so the row does not stay at 'running' forever with "
            f"no further exit ever coming."
        )
    try:
        result = subprocess.run(
            ["python3", SUPERBOSS_REGISTER, "mark-umr-terminal",
             "--umr-id", row["umr_id"], "--status", "failed", "--reason", reason],
            capture_output=True, text=True, timeout=35,
        )
        _log(task_id, unit_kind, f"mark-umr-terminal umr={row['umr_id']} status=failed rc={result.returncode} "
                                  f"stdout={result.stdout.strip()[:300]} stderr={result.stderr.strip()[:300]}")
    except Exception as e:
        _log(task_id, unit_kind, f"mark-umr-terminal call raised (non-fatal): {e}")


def _bridge_no_op_completion(task_id, unit_kind, unit_name, row, task):
    """UMR-20260813-215742-db64: real, distinct handling for
    status='completed_no_change' -- see the module docstring's own addendum for the full
    real chain this closes (previously: an unrecognized checkpoint status fell through to
    the generic 'leave at running' branch, which pm-sentinel-tick.sh's own status='running'
    + dead-unit cross-check (Check 2b) would then independently catch and dispatch a fresh,
    unnecessary RCA for -- the exact unbounded paid-AI re-dispatch loop this UMR exists to
    close).

    Requires a real no_op.json marker with a real branch_sha -- written by
    supervisor-entrypoint.sh's own NO-OP-BRANCH-GUARD-BLOCK in the same run that wrote this
    checkpoint status, never guessed here. That branch_sha is exactly the real evidence
    validate_umr_terminal_completion_evidence() already requires for status='completed' (a
    real commit that IS an ancestor of the base branch -- which is structurally guaranteed
    true here: supervisor-entrypoint.sh only ever writes 'completed_no_change' when `git
    rev-list --count base..branch` was genuinely 0) -- reusing the existing 'completed'
    status rather than widening the umr_tasks CHECK-constraint enum for a case that is,
    evidentially, already exactly that. --repo is threaded from this task's own real
    task.yaml repo field so the real ancestor check runs against the real local checkout
    this branch actually lives in, not a hardcoded default."""
    no_op = _load_no_op_marker(task_id)
    if not no_op or not no_op.get("branch_sha"):
        _log(task_id, unit_kind, f"last task.yaml status='{SELF_REPORTED_NO_OP_STATUS}' for umr {row['umr_id']} "
                                  f"but no real no_op.json marker (with a real branch_sha) was found -- leaving "
                                  f"at running rather than guessing")
        return

    reason = (
        f"worker-exit-status-bridge (ExecStopPost, UMR-20260813-215742-db64 no-op-branch "
        f"completion guard): unit {unit_name} stopped with task.yaml's own last checkpoint "
        f"status='{SELF_REPORTED_NO_OP_STATUS}' -- real no_op.json evidence: {no_op.get('reason', '')}"
    )
    cmd = [
        "python3", SUPERBOSS_REGISTER, "mark-umr-terminal",
        "--umr-id", row["umr_id"], "--status", "completed",
        "--commit-sha", no_op["branch_sha"], "--reason", reason,
    ]
    repo = task.get("repo")
    if repo:
        cmd += ["--repo", repo]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        _log(task_id, unit_kind, f"mark-umr-terminal umr={row['umr_id']} status=completed(no-op) rc={result.returncode} "
                                  f"stdout={result.stdout.strip()[:300]} stderr={result.stderr.strip()[:300]}")
    except Exception as e:
        _log(task_id, unit_kind, f"mark-umr-terminal call raised (non-fatal): {e}")


def main():
    if len(sys.argv) < 2 or not sys.argv[1]:
        return 0
    task_id = sys.argv[1]
    unit_kind = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else "worker"
    if unit_kind not in UNIT_KIND_CONFIG:
        # Unknown unit kind (a typo'd ExecStopPost, or a future unit this script hasn't
        # been taught about yet) -- real fail-open, never guess, never crash the unit's
        # own Result by exiting non-zero (see docstring point 5 above).
        _log(task_id, "worker", f"unknown unit_kind {unit_kind!r} passed as argv[2] -- no-op")
        return 0
    try:
        run(task_id, unit_kind)
    except Exception as e:
        _log(task_id, unit_kind, f"worker-exit-status-bridge raised at top level (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
