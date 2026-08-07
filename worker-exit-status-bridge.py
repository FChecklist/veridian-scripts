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

Deliberately conservative in both directions:
  1. NEVER writes status=completed/completed_unmerged from here. This hook only ever has
     a process exit code to go on, and this task's own SPEC is explicit: a clean exit
     code is NOT evidence of substantive completion. Only
     reconcile_stale_running_workers.py's own real-artifact check (a real commit,
     verified server-side by validate_umr_terminal_completion_evidence -- real git
     ancestry / real file existence, never our own guess) is allowed to write those two
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
import os
import subprocess
import sys

import yaml

AI_OS = "/opt/veridian/ai-os"
SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")
SUPERBOSS_DB = os.path.join(AI_OS, "memory", "superboss-register.sqlite")

# Same real vocabulary resource_governor.py's own _forward_progress_decision() already
# treats as "no override, default failed retained" for a task.yaml that has already
# self-reported a genuinely-terminal-negative outcome -- reused here verbatim so the two
# never drift apart.
SELF_REPORTED_NEGATIVE_STATUSES = (
    "failed", "blocked", "cancelled", "rejected_duplicate", "superseded", "not_needed",
)


def _load_task_yaml(task_id):
    path = f"{AI_OS}/tasks/{task_id}/task.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None


def _find_umr_row_for_unit(unit_name):
    """Real, read-only sqlite3 lookup -- same 'python3's own sqlite3 module, never the
    CLI binary' convention worker-entrypoint.sh's own deterministic-briefing lookup
    already uses. Returns the most recently submitted umr_tasks row bound to this
    unit_name, or None if no such row exists."""
    import sqlite3
    conn = None
    try:
        conn = sqlite3.connect(SUPERBOSS_DB)
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


def _log(task_id, message):
    try:
        with open(f"{AI_OS}/tasks/{task_id}/worker.log", "a") as f:
            f.write(f"[worker-exit-status-bridge] {message}\n")
    except OSError:
        pass


def run(task_id):
    unit_name = f"veridian-worker@{task_id}.service"

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
        _log(task_id, f"no task.yaml for {task_id}, umr {row['umr_id']} left at running for STEP 3 reconciler")
        return

    checkpoints = task.get("checkpoints") or []
    last_status = checkpoints[-1]["status"] if checkpoints else task.get("status")

    if last_status not in SELF_REPORTED_NEGATIVE_STATUSES:
        # pending_review (supervisor handoff) / completed (already gated elsewhere) /
        # in_progress / pending (ambiguous mid-work stop, maybe a systemd restart is
        # coming) -- none of these are a hasty, safe terminal write from an exit hook.
        # Leave alone.
        _log(task_id, f"last task.yaml status={last_status!r} for umr {row['umr_id']} -- "
                       f"not a self-reported negative outcome, leaving at running")
        return

    reason = (
        f"worker-exit-status-bridge (ExecStopPost, STEP 2 fix task-20260807-052027-platform-"
        f"integrity--worker-units-exit-0): unit {unit_name} stopped with task.yaml's own last "
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
        _log(task_id, f"mark-umr-terminal umr={row['umr_id']} status=failed rc={result.returncode} "
                       f"stdout={result.stdout.strip()[:300]} stderr={result.stderr.strip()[:300]}")
    except Exception as e:
        _log(task_id, f"mark-umr-terminal call raised (non-fatal): {e}")


def main():
    if len(sys.argv) < 2 or not sys.argv[1]:
        return 0
    task_id = sys.argv[1]
    try:
        run(task_id)
    except Exception as e:
        _log(task_id, f"worker-exit-status-bridge raised at top level (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
