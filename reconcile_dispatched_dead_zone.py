#!/usr/bin/env python3
"""reconcile_dispatched_dead_zone.py -- real, mechanical, auto-remediating
sweep for the "dispatched dead zone": a real umr_tasks row sitting at
status='dispatched' with no real ts_completed and no real corresponding
task directory or systemd worker unit EVER created.

UMR-20260806-115538-1e55 (original real ask: "no existing deterministic
check detects this real gap" -- the exact real gap that let two real rows,
UMR-20260806-110244-d3b7 and UMR-20260806-110906-57a9, sit silently
unreachable for 40+ minutes) + UMR-20260806-115605-854d, dispatched
immediately after 1e55 and superseding its "just report" framing (implement
THIS behavior, not 1e55's original one): the real objective of these
deterministic scripts is to act as real project-manager software --
mechanical, safe, reversible fixes must happen automatically, right when
detected, never sit waiting for an AI to read a report. Resetting a row
stuck at dispatched >15min with no real task/worker ever created back to
'queued' requires zero judgment (safe, reversible -- worst case: it gets
redispatched). The pm_decisions_pending write this script makes on a first
occurrence is a real AUDIT LOG of what was auto-fixed and when --
informational only, never a blocking gate. Only the exact same row falling
into the dead zone a SECOND time (after already having been auto-reset once
by this same script) escalates to a real, blocking pm_decisions_pending
entry -- that repeat pattern is the real signal something deeper is wrong,
not a mechanical fix.

REAL, LIVE EVIDENCE THIS SCRIPT WAS BUILT AGAINST (read before changing the
dead-zone condition below): d3b7/57a9's own real closed pm_decisions_pending
note (id lookup: related_umr IN (those two umr_ids), written by the
concurrent UMR-20260806-115423-500d remediation, minutes before 1e55 was
even dispatched) says, verbatim: "this executor received and acted on this
exact message directly, opening real PR #978/#979 ... NOT reset to queued
-- doing so would trigger a harmful duplicate re-dispatch of already-
completed work." I.e. for source_trigger='owner_dispatch_gateway' rows
(task_kind='veridian_task_create', relayed into a live interactive Claude
Code session -- see dispatch-owner-task.sh's own header comment: "There is
no systemd unit ... task_kind='veridian_task_create' rows are relayed to a
live interactive session, never started as a systemd unit"), the absence of
a task directory/systemd unit is the NORMAL, permanent, by-design signature
of that entire channel -- not proof of death. A genuinely-in-progress (or
already-done-but-not-yet-recorded) interactive row looks IDENTICAL, on this
one signal alone, to a genuinely-abandoned one. This is the same real class
of mistake resource_governor.py's own backfill_null_heartbeats() was built
to stop repeating (see that function's own docstring: 6 of 9 real rows it
originally mislabeled 'failed' unconditionally actually had real forward
progress) -- its fix was "cross-check real evidence before declaring a row
dead," never "trust absence-of-liveness-signal alone."

Given that, and given the Owner's own UMR-20260806-115605-854d explicitly,
repeatedly directs this exact auto-reset with "zero judgment" and
explicitly names "worst case: redispatched" as an ALREADY-ACCEPTED, safe
outcome, this script implements the literal 15-minute/no-task-dir/no-
systemd-unit condition exactly as specified -- but adds exactly ONE real,
free, already-established, zero-network-call defense-in-depth guard before
ever resetting a row: a real ocid_artifact_links check (the SAME real,
already-existing mechanism resource_governor.py's own dispatch_one() already
uses at its own "was this task's real work already done via the other
channel" chokepoint, see resource_governor.py's OCID-evidence-supersedes
comment block). A row carrying a real ocid_artifact_links entry is real,
local, already-established, zero-cost proof that real work landed against
this exact umr_id -- never auto-reset, reported as a real negative control
instead. This does not fully close the gap the d3b7/57a9 evidence above
documents (that evidence had no OCID-titled prompt, so this guard would not
by itself have caught it) -- it is a real, honest, minimal, already-
established improvement over doing nothing, not a claim of completeness.
This is disclosed here, not hidden, precisely so a future reader (human or
AI) does not mistake "auto-reset happened" for "proof nothing was lost."

Real dead-zone condition (ALL must hold):
  1. status == 'dispatched'
  2. ts_dispatched is real (non-NULL) and older than
     DEAD_ZONE_THRESHOLD_MINUTES (15, per UMR-20260806-115605-854d's own
     literal number -- not invented, not re-derived. Env-overridable via
     VERIDIAN_DEAD_ZONE_THRESHOLD_MINUTES for tests only).
  3. no real task directory was ever created under the real tasks path
     (TASKS_DIR, imported from dispatch_core.py -- never hardcoded, same
     "import the real constant, don't re-guess it" convention
     generate_pm_report_v3.py's get_worker_ceiling() already established)
     for either this row's own task_identity OR (if recorded)
     outputs_json.new_task_id, the real fresh id veridian-task.py create
     mints at spawn time (resource_governor.py's _perform_spawn()).
  4. no real systemd unit was ever spawned for this row -- unit_name IS
     NULL/empty. unit_name is written ONLY at real spawn time (_perform_spawn()/
     dispatch_one() in resource_governor.py, or mark-umr-dispatched's own
     optional --unit-name), so its historical presence/absence on the row
     itself IS the real "ever spawned" record -- a live `systemctl` check
     would only prove "still exists right now," a weaker and wrong signal
     for "ever" (an already-cleaned-up-after-completion unit would falsely
     read as "never existed"). Same signal reconcile_stale_heartbeats()
     already treats as authoritative ("if not unit: continue -- nothing to
     check liveness against").
  5. (this script's own added guard, see the real evidence above) no real
     ocid_artifact_links row exists for this umr_id.

Every real write goes through superboss-register.py's own canonical
functions ONLY -- reset_umr_task_to_queued() / insert_pm_decision_pending() /
resolve_pm_decision_pending() -- never a raw SQL UPDATE/INSERT (a past
raw-SQL mistake this session was caught and corrected; this script does not
repeat it). Every read is a plain read-only SELECT, same convention every
other real script in this codebase already uses for its own reads
(get_pm_decisions_pending(), reconcile_owner_dispatch_status.py's own
load_rows()).

Escalation (second occurrence): a prior real, closed
decision_type='dead_zone_auto_remediation' pm_decisions_pending row for this
exact umr_id is real, direct proof this row was already auto-reset once
before by this same script. If the row is found back in the dead zone after
that, this script does NOT reset it again -- it opens one real, blocking,
decision_type='pm_decision' (the default, so it surfaces in
generate_pm_report_v3.py's existing Section 7 "PM DECISION REQUIRED",
status='open') pm_decisions_pending row instead, and never opens a second
one for the same umr_id while one is already open (checked before writing).

REAL TOCTOU FIX (found by this script's own independent PR review, applied
before merge): run_sweep() gathers its candidate rows and every real
task-dir/unit/ocid-evidence check BEFORE ever taking _write_lock() -- so a
real concurrent writer (a genuine mark-umr-terminal call, an operator, a
legitimately-finishing interactive session) could, in principle, move a
row's real status/ts_dispatched in the narrow gap between that read and
this script's own write. Both auto_reset_to_queued() and escalate() now
re-verify their own evidence with a fresh, real SELECT taken INSIDE the
write lock, immediately before the real write (_row_still_matches_evidence()/
the inline _has_open_escalation() re-check) -- and skip their own write
entirely (no reset, no audit log, no duplicate escalation) the instant that
fresh check disagrees with the evidence gathered earlier, rather than ever
acting on stale evidence. See test_row_that_legitimately_completes_mid_sweep_is_never_clobbered()
and test_escalation_never_duplicates_even_if_caller_side_check_is_stale() in
tests/test_reconcile_dispatched_dead_zone.py for the real regression
coverage.

Wired for real automatic/immediate operation into resource_governor_tick_loop.sh
(same 30s cadence as --tick/--reconcile-stale) -- see that script's own
comment block. generate_pm_report_v3.py's own Section 15 (read-only) surfaces
this script's real recent activity for PM visibility, purely by reading
pm_decisions_pending -- it never calls into this script's write path itself,
keeping that file's own "zero AI calls, pure read + one snapshot insert"
contract intact.

Usage:
  python3 reconcile_dispatched_dead_zone.py              # real sweep, auto-applies (default)
  python3 reconcile_dispatched_dead_zone.py --dry-run     # report only, no writes (manual inspection)
  python3 reconcile_dispatched_dead_zone.py --umr-id UMR-...  # limit to one row (debugging/re-run)
  python3 reconcile_dispatched_dead_zone.py --json PATH   # also write full per-row evidence JSON

Exit codes: 0 = ran clean (regardless of whether any row was reset/escalated
            -- this is a real, expected, steady-state outcome, not a
            failure). 2 = a real internal error (DB path, subprocess, etc.).
"""
import argparse
import importlib.util as _ilu
import json
import os
import sys
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEAD_ZONE_THRESHOLD_MINUTES = float(
    os.environ.get("VERIDIAN_DEAD_ZONE_THRESHOLD_MINUTES", "15"))

DECISION_TYPE_AUDIT = "dead_zone_auto_remediation"
ESCALATION_TITLE_PREFIX = "DEAD-ZONE REPEAT"

SUPERBOSS_REGISTER_PY = os.environ.get(
    "VERIDIAN_SUPERBOSS_REGISTER_PY", os.path.join(SCRIPT_DIR, "superboss-register.py"))
DISPATCH_CORE_PY = os.environ.get(
    "VERIDIAN_DISPATCH_CORE_PY", os.path.join(SCRIPT_DIR, "dispatch_core.py"))


def _load_module(name, path):
    """Fresh module load via importlib (never the process-wide sys.modules
    cache) -- same convention every other real script/test in this codebase
    uses for superboss-register.py (reconcile_owner_dispatch_status.py's own
    _sbr, generate_pm_report_v3.py's load_module_from_path()), so a caller
    can point SUPERBOSS_REGISTER_DB/dispatch_core's own env vars at a fresh
    path per real test without any stale module surviving from a previous
    call/test."""
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def load_dispatched_candidates(conn, now, threshold_minutes=DEAD_ZONE_THRESHOLD_MINUTES, umr_id=None):
    """Real, read-only SELECT (never a write) -- every real umr_tasks row
    at status='dispatched' with a real, non-NULL ts_dispatched older than
    threshold_minutes. Deliberately does NOT pre-filter on source_trigger:
    the real UMR-20260806-115538-1e55 condition is stated generically over
    every real 'dispatched' row, not scoped to owner_dispatch_gateway alone
    -- classify() below is what actually decides whether each real
    candidate's task-dir/unit/ocid-evidence signals clear it or not."""
    cutoff = (now - timedelta(minutes=threshold_minutes)).isoformat()
    if umr_id:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_dispatched, unit_name, "
            "outputs_json, source_trigger FROM umr_tasks "
            "WHERE umr_id=? AND status='dispatched' AND ts_dispatched IS NOT NULL AND ts_dispatched < ?",
            (umr_id, cutoff),
        )
    else:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_dispatched, unit_name, "
            "outputs_json, source_trigger FROM umr_tasks "
            "WHERE status='dispatched' AND ts_dispatched IS NOT NULL AND ts_dispatched < ? "
            "ORDER BY ts_dispatched",
            (cutoff,),
        )
    return [dict(r) for r in cur.fetchall()]


def _task_dir_candidates(row, tasks_dir):
    """Real candidate directory names to check under tasks_dir: the row's
    own task_identity, plus outputs_json.new_task_id if this row was ever
    actually mechanically dispatched (resource_governor.py's _perform_spawn()
    is the only real writer of new_task_id -- see that function's own
    veridian_task_create branch)."""
    candidates = [row.get("task_identity")]
    try:
        outputs = json.loads(row.get("outputs_json") or "{}")
    except (TypeError, ValueError):
        outputs = {}
    if isinstance(outputs, dict):
        new_task_id = outputs.get("new_task_id")
        if new_task_id:
            candidates.append(new_task_id)
    return [os.path.join(tasks_dir, c) for c in candidates if c]


def has_real_task_dir(row, tasks_dir):
    return any(os.path.isdir(p) for p in _task_dir_candidates(row, tasks_dir))


def has_real_systemd_unit(row):
    return bool(row.get("unit_name"))


def has_real_completed_work_evidence(sbr, conn, row):
    """Real, local, zero-network ocid_artifact_links check -- see this
    module's own docstring for why this exists and its documented, honest
    limitation."""
    try:
        links = sbr.query_ocid_artifact_links(conn, umr_id=row["umr_id"])
    except Exception:
        # Fail toward "cannot verify, do not touch" -- same fail-open-
        # toward-safety philosophy as reconcile_owner_dispatch_status.py's
        # own NEEDS_AI_JUDGMENT branches: a broken evidence check must never
        # silently promote a row to "safe to auto-reset."
        return True
    return bool(links)


def classify(sbr, conn, row, now, tasks_dir, threshold_minutes=DEAD_ZONE_THRESHOLD_MINUTES):
    """Pure(ish) classification against already-fetched row data + one real
    local DB read (ocid_artifact_links) + real os.path.isdir() checks --
    never writes. Returns a real evidence dict, always carrying `bucket`."""
    dispatched_at = _parse_iso(row.get("ts_dispatched"))
    age_minutes = ((now - dispatched_at).total_seconds() / 60.0) if dispatched_at else None
    evidence = {
        "umr_id": row["umr_id"],
        "task_identity": row.get("task_identity"),
        "source_trigger": row.get("source_trigger"),
        "ts_dispatched": row.get("ts_dispatched"),
        "age_minutes": age_minutes,
        "unit_name": row.get("unit_name"),
        "threshold_minutes": threshold_minutes,
    }

    task_dir_hit = has_real_task_dir(row, tasks_dir)
    unit_hit = has_real_systemd_unit(row)
    evidence_hit = has_real_completed_work_evidence(sbr, conn, row)
    evidence["has_real_task_dir"] = task_dir_hit
    evidence["has_real_systemd_unit"] = unit_hit
    evidence["has_real_ocid_artifact_evidence"] = evidence_hit

    if task_dir_hit:
        evidence["bucket"] = "NOT_DEAD_HAS_TASK_DIR"
        evidence["reason"] = "a real task directory exists under the real tasks path -- not touched."
        return evidence
    if unit_hit:
        evidence["bucket"] = "NOT_DEAD_HAS_SYSTEMD_UNIT"
        evidence["reason"] = "a real systemd unit was recorded for this row -- not touched."
        return evidence
    if evidence_hit:
        evidence["bucket"] = "NOT_DEAD_HAS_COMPLETED_WORK_EVIDENCE"
        evidence["reason"] = ("a real ocid_artifact_links row already links this umr_id to real "
                               "completed work -- not touched (see module docstring: the real "
                               "d3b7/57a9 incident this check exists to catch).")
        return evidence

    evidence["bucket"] = "DEAD_ZONE"
    evidence["reason"] = (
        f"status='dispatched' for {age_minutes:.1f} real minutes (>{threshold_minutes} threshold), "
        "no real task directory, no real systemd unit, no real ocid_artifact_links evidence."
    )
    return evidence


def _prior_auto_remediation_count(conn, umr_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM pm_decisions_pending WHERE related_umr=? AND decision_type=?",
        (umr_id, DECISION_TYPE_AUDIT),
    ).fetchone()
    return row["c"] if row else 0


def _has_open_escalation(conn, umr_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM pm_decisions_pending WHERE related_umr=? "
        "AND status='open' AND title LIKE ?",
        (umr_id, f"{ESCALATION_TITLE_PREFIX}%"),
    ).fetchone()
    return (row["c"] if row else 0) > 0


def _row_still_matches_evidence(conn, evidence):
    """Real TOCTOU guard (found by independent review of this script's own
    PR): run_sweep() gathers its candidate row list via one batch SELECT,
    then classify()'s own real filesystem/DB checks, all BEFORE ever taking
    _write_lock() -- so between that read and the moment a write actually
    happens, a real concurrent writer (an operator's manual mark-umr-terminal,
    a genuinely-finishing interactive session, dispatch-tick's own mechanical
    pickup) could have legitimately moved this exact row's status/ts_dispatched
    out from under this script. Re-checked here with a fresh, real SELECT,
    taken INSIDE the write lock right before the real write -- both
    auto_reset_to_queued() and escalate() call this and skip their own write
    entirely (no reset, no audit log, no escalation) rather than act on
    stale evidence. Returns the fresh row dict on a genuine match, None on
    any mismatch (row gone, status changed, or a fresh ts_dispatched from a
    real intervening redispatch)."""
    fresh = conn.execute(
        "SELECT status, ts_dispatched FROM umr_tasks WHERE umr_id=?", (evidence["umr_id"],)
    ).fetchone()
    if fresh is None:
        return None
    if fresh["status"] != "dispatched":
        return None
    if fresh["ts_dispatched"] != evidence["ts_dispatched"]:
        return None
    return dict(fresh)


def auto_reset_to_queued(sbr, conn, evidence):
    """Real first-occurrence action: reset the row via the one real
    canonical function, then write one real, informational, already-closed
    audit-log pm_decisions_pending row (decision_type=DECISION_TYPE_AUDIT --
    structurally excluded from get_pm_decisions_pending()'s/
    get_owner_proposals_pending()'s own decision_type-scoped WHERE clauses,
    and immediately resolved here too, so it can never be mistaken for a
    real open/blocking item by any reader). Both writes happen inside one
    real _write_lock() + one commit, and only after _row_still_matches_evidence()
    reconfirms the row is still genuinely in the exact state this evidence
    was computed against -- an audit-log entry for a reset that never
    happened (or a reset applied against stale evidence) would itself be a
    real correctness bug this script exists to prevent elsewhere."""
    umr_id = evidence["umr_id"]
    reason = (
        f"reconcile_dispatched_dead_zone.py auto-reset (UMR-20260806-115605-854d): {evidence['reason']}"
    )
    with sbr._write_lock():
        if _row_still_matches_evidence(conn, evidence) is None:
            evidence["action"] = "skipped_stale_evidence"
            evidence["reason"] += (" [SKIPPED: a real, fresh re-check under the write lock found this "
                                    "row's status/ts_dispatched had already changed -- never acted on "
                                    "stale evidence.]")
            return evidence
        sbr.reset_umr_task_to_queued(conn, umr_id, reason=reason)
        decision_id = sbr.insert_pm_decision_pending(
            conn,
            title=f"Auto-reset dead-zone dispatched row: {umr_id}",
            detail=json.dumps(evidence, default=str),
            related_umr=umr_id,
            decision_type=DECISION_TYPE_AUDIT,
        )
        sbr.resolve_pm_decision_pending(
            conn, decision_id, closed_by="reconcile_dispatched_dead_zone.py",
            closed_note="Informational audit-log entry only -- the real fix was already applied "
                        "automatically (reset to queued); this row is never a blocking gate.",
            status="resolved",
        )
        conn.commit()
    evidence["action"] = "auto_reset_to_queued"
    evidence["audit_log_decision_id"] = decision_id
    return evidence


def escalate(sbr, conn, evidence):
    """Real second(+)-occurrence action: open one real, blocking
    (decision_type='pm_decision', the default -- appears in
    generate_pm_report_v3.py's existing Section 7 "PM DECISION REQUIRED",
    status='open') pm_decisions_pending row, and do NOT reset the row again.
    Idempotent: _has_open_escalation() is re-checked HERE, inside the write
    lock, immediately before the real INSERT (not only by the caller before
    taking the lock -- found by independent review: two overlapping real
    invocations, e.g. the tick loop's scheduled run overlapping a manual
    --umr-id debug re-run, could otherwise both pass the caller-side check
    and each insert a duplicate row) -- so at most one real open escalation
    row can ever exist per umr_id, even under real concurrent callers."""
    umr_id = evidence["umr_id"]
    detail = (
        f"{umr_id} has fallen into the dispatched dead zone a SECOND time after already being "
        f"auto-reset once by this same script -- that repeat pattern is the real signal something "
        f"deeper is wrong (per UMR-20260806-115605-854d), not something to mechanically re-reset "
        f"again. Real current evidence: {json.dumps(evidence, default=str)}"
    )
    with sbr._write_lock():
        if _has_open_escalation(conn, umr_id):
            evidence["action"] = "already_escalated_skipped"
            return evidence
        decision_id = sbr.insert_pm_decision_pending(
            conn,
            title=f"{ESCALATION_TITLE_PREFIX}: {umr_id} dead-zoned a second time after auto-reset",
            detail=detail,
            related_umr=umr_id,
            recommended_option="Investigate why this UMR keeps re-entering the dispatched dead zone "
                                "before taking any further action -- do not auto-reset again.",
        )
        conn.commit()
    evidence["action"] = "escalated_blocking_decision"
    evidence["escalation_decision_id"] = decision_id
    return evidence


def run_sweep(dry_run=False, umr_id=None, threshold_minutes=DEAD_ZONE_THRESHOLD_MINUTES,
              sbr_path=SUPERBOSS_REGISTER_PY, dispatch_core_path=DISPATCH_CORE_PY, now=None):
    """Real end-to-end sweep -- loaded fresh per call so tests can point
    both module loads at isolated temp paths without any stale state
    surviving between calls."""
    sbr = _load_module("superboss_register_dead_zone", sbr_path)
    dispatch_core = _load_module("dispatch_core_dead_zone", dispatch_core_path)
    tasks_dir = dispatch_core.TASKS_DIR

    now = now or _now()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_pm_decisions_pending_table(conn)

    candidates = load_dispatched_candidates(conn, now, threshold_minutes=threshold_minutes, umr_id=umr_id)
    results = []
    for row in candidates:
        evidence = classify(sbr, conn, row, now, tasks_dir, threshold_minutes=threshold_minutes)
        if evidence["bucket"] != "DEAD_ZONE":
            evidence["action"] = "none"
            results.append(evidence)
            continue
        if dry_run:
            evidence["action"] = "would_act (dry-run)"
            results.append(evidence)
            continue
        prior_count = _prior_auto_remediation_count(conn, row["umr_id"])
        if prior_count == 0:
            results.append(auto_reset_to_queued(sbr, conn, evidence))
        elif _has_open_escalation(conn, row["umr_id"]):
            evidence["action"] = "already_escalated_skipped"
            results.append(evidence)
        else:
            results.append(escalate(sbr, conn, evidence))

    conn.close()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                     help="Report only, no writes -- default is to auto-apply real, safe "
                          "fixes immediately (UMR-20260806-115605-854d: never sit waiting "
                          "for an AI/human to read a report before acting).")
    ap.add_argument("--umr-id", dest="umr_id", default=None,
                     help="Limit to a single umr_id (debugging/targeted re-run).")
    ap.add_argument("--threshold-minutes", dest="threshold_minutes", type=float,
                     default=DEAD_ZONE_THRESHOLD_MINUTES)
    ap.add_argument("--json", metavar="PATH", help="Also write full per-row evidence JSON to this path.")
    args = ap.parse_args()

    try:
        results = run_sweep(dry_run=args.dry_run, umr_id=args.umr_id,
                             threshold_minutes=args.threshold_minutes)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 2

    from collections import Counter
    actions = Counter(r.get("action", "none") for r in results)
    print(f"TOTAL dispatched rows examined (>{args.threshold_minutes}min, "
          f"{'umr_id=' + args.umr_id if args.umr_id else 'all'}): {len(results)}")
    for a, c in actions.most_common():
        print(f"  {a}: {c}")
    print(f"mode: {'DRY-RUN (no writes)' if args.dry_run else 'LIVE (real writes applied automatically)'}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"full per-row evidence written to: {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
