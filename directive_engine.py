#!/usr/bin/env python3
"""
DIRECTIVE (merged, generic). Single engine replacing the earlier separate
DIRECTIVE-001/DIRECTIVE-002 scripts. Reads /opt/veridian/ai-os/DIRECTIVE.yaml
live every pass -- both its own config and its priority_queue -- so new tasks
can be appended at any time without a restart, and priority ordering lives in
the file, not in script logic.

Run via: screen -dmS directive_execution bash /opt/veridian/scripts/directive_engine.sh
"""
import re
import sqlite3
import subprocess
import sys
import time
import json
import os
import uuid

try:
    import yaml
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pyyaml"], check=False)
    import yaml

# Real fix (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7):
# env-overridable, same convention as every other real path constant in this
# codebase (e.g. superboss-register.py's SUPERBOSS_REGISTER_DB) -- previously
# hardcoded, which meant a real test could only ever point at the live
# production DB/files, or not exercise this module's file-touching functions
# at all. Defaults are unchanged, so production behavior is 100% identical.
SUPERBOSS_DB = os.environ.get(
    "VERIDIAN_DIRECTIVE_SUPERBOSS_DB", "/opt/veridian/ai-os/memory/superboss-register.sqlite")
GOVERNOR = os.environ.get("VERIDIAN_DIRECTIVE_GOVERNOR_SCRIPT", "/opt/veridian/scripts/resource_governor.py")
TASK_GATEWAY = os.environ.get("VERIDIAN_DIRECTIVE_TASK_GATEWAY_SCRIPT", "/opt/veridian/scripts/task-gateway.py")
DIRECTIVE_FILE = os.environ.get("VERIDIAN_DIRECTIVE_FILE", "/opt/veridian/ai-os/DIRECTIVE.yaml")
LOG_PATH = os.environ.get("VERIDIAN_DIRECTIVE_LOG_PATH", "/opt/veridian/ai-os/tasks/directive_status.log")
PENDING_REVIEW_FILE = os.environ.get(
    "VERIDIAN_DIRECTIVE_PENDING_REVIEW_FILE", "/opt/veridian/ai-os/PENDING_OWNER_REVIEW.md")
SPEC_TMP_DIR = os.environ.get("VERIDIAN_DIRECTIVE_SPEC_TMP_DIR", "/tmp/directive_specs")
TERMINAL_STATES = {"completed", "failed", "rejected_duplicate", "killed"}

os.makedirs(SPEC_TMP_DIR, exist_ok=True)


def log_status(task_identity, status_words):
    line = f"[{task_identity}]: {status_words}\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)
    print(line, end="", flush=True)


def resource_snapshot():
    try:
        with open("/proc/loadavg") as f:
            load = f.read().split()[:3]
        mem = subprocess.run(["free", "-h"], capture_output=True, text=True).stdout
        mem_line = [l for l in mem.splitlines() if l.startswith("Mem:")]
        return f"load={','.join(load)} {mem_line[0] if mem_line else ''}"
    except Exception as e:
        return f"snapshot_failed: {e}"


def load_directive():
    with open(DIRECTIVE_FILE) as f:
        return yaml.safe_load(f) or {}


def umr_status_for_identity(task_identity):
    conn = sqlite3.connect(SUPERBOSS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT status, umr_id FROM umr_tasks WHERE task_identity = ? "
        "ORDER BY ts_submitted DESC LIMIT 1",
        (task_identity,),
    )
    r = cur.fetchone()
    conn.close()
    return (r[0], r[1]) if r else (None, None)


def umr_status_and_reason_for_identity(task_identity):
    """Real fix (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7,
    parent UMR-20260806-071025-1d28): same real query as umr_status_for_identity()
    above, plus the row's own persisted `reason` column -- the durable signal
    process_one()'s retry-once gate below now reads instead of an in-memory flag
    that never survived a tick (see that call site's own comment for the full real
    incident this closes)."""
    conn = sqlite3.connect(SUPERBOSS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT status, umr_id, reason FROM umr_tasks WHERE task_identity = ? "
        "ORDER BY ts_submitted DESC LIMIT 1",
        (task_identity,),
    )
    r = cur.fetchone()
    conn.close()
    return (r[0], r[1], r[2]) if r else (None, None, None)


NON_TERMINAL_STATES = ("queued", "dispatched", "running")

# Real fix (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7):
# resource_governor.py's submit() (Rule 1, UMR-20260804-180711-7f96) reuses the
# prior umr_id when a task_identity is resubmitted, and records this EXACT
# reason prefix on that row (see submit()'s own f-string:
# f"resubmitted (reused umr_id, prior status was {prior['status']!r})"). This is
# the one real, durable signal that "this task_identity has already had its one
# real retry" -- unlike an in-memory flag on a dict that gets discarded and
# recreated from a fresh DIRECTIVE.yaml parse every single tick (see
# process_one()'s own comment below for the real incident this replaces).
_RESUBMITTED_REASON_PREFIX = "resubmitted (reused umr_id"

# Real bug found 2026-07-28: this file's own predecessors (DIRECTIVE-001/DIRECTIVE-002,
# superseded, see this module's docstring) re-prefixed the SAME real target with a
# different task_identity each time a new DIRECTIVE.yaml version was authored
# (DIRECTIVE-001-PR617-REVIEW, DIRECTIVE-002-PR617-REVIEW, PR617-REVIEW all point at the
# real, same PR #617) -- umr_status_for_identity()'s exact-match query cannot catch this,
# so the same real work got redispatched repeatedly (PR #617: 6 separate real attempts
# same day). This strips any leading DIRECTIVE-<N>- prefix so the real underlying target
# identity is comparable across DIRECTIVE.yaml file versions.
_DIRECTIVE_PREFIX_RE = re.compile(r"^DIRECTIVE-\d+-", re.IGNORECASE)


def normalize_task_identity(task_identity):
    # 2026-07-29 adversarial-test fix (real bug, live-reproduced): re.sub()
    # with this ^-anchored pattern can only ever match once per call, no
    # matter how many times it's logically "global" -- a doubly-re-prefixed
    # identity like "DIRECTIVE-002-DIRECTIVE-001-PR617-REVIEW" (the exact
    # shape this function's own docstring cites as the real PR #617
    # incident, re-authored across DIRECTIVE.yaml file versions) used to
    # normalize to "DIRECTIVE-001-PR617-REVIEW" instead of "PR617-REVIEW",
    # silently defeating find_in_flight_duplicate()'s exact-match compare.
    # Strip repeatedly until nothing more matches, so any number of stacked
    # prefixes is handled the same as one.
    result = task_identity or ""
    while True:
        stripped = _DIRECTIVE_PREFIX_RE.sub("", result)
        if stripped == result:
            return stripped
        result = stripped


def find_in_flight_duplicate(task_identity):
    """Real, additive duplicate guard (2026-07-28): before submitting an entry whose
    EXACT task_identity has no in-flight row, also check whether any OTHER task_identity
    with the same normalized (prefix-stripped) target is currently non-terminal in
    umr_tasks. Catches the DIRECTIVE-001-/DIRECTIVE-002-/unprefixed re-authoring case
    above -- resource_governor.py's own per-identity dedup only sees exact string
    matches, so it cannot catch this on its own. Returns (other_task_identity, status)
    or (None, None) if no in-flight duplicate found. Never raises -- a failed check
    here must never block a real, legitimate submission."""
    target = normalize_task_identity(task_identity)
    if not target:
        return None, None
    try:
        conn = sqlite3.connect(SUPERBOSS_DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT task_identity, status FROM umr_tasks WHERE status IN (?,?,?) "
            "ORDER BY ts_submitted DESC",
            NON_TERMINAL_STATES,
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None, None  # fail open -- never block real dispatch on a broken check
    for other_identity, status in rows:
        if other_identity != task_identity and normalize_task_identity(other_identity) == target:
            return other_identity, status
    return None, None


def run_check_duplicate_battery(task_identity, title, prompt):
    """Real, confirmed gap closed (Stage 5, 2026-07-29): this module used to go
    straight from find_in_flight_duplicate() above to resource_governor.py
    --submit, bypassing task-gateway.py submit's own check-duplicate/search/
    query-knowledge/lookup-capability battery (cmd_submit in task-gateway.py)
    entirely -- every OTHER real trigger into the task lifecycle (see
    prompt_gateway/gateway.py's dispatch_to_task_lifecycle(), action "start")
    goes through that battery first, and this file's own docstring history
    already documents why DIRECTIVE.yaml-driven submissions deserve the same
    guards as any other trigger, not a separate weaker path.

    This calls task-gateway.py's real "submit" subcommand via subprocess --
    it does NOT reimplement check-duplicate/search/query-knowledge/lookup-
    capability here, only reuses the already-built command exactly as
    gateway.py's dispatch_to_task_lifecycle() already does for its own
    "start" action. --source is always ai_agent (software calling software,
    not a raw Owner-text gate -- DIRECTIVE.yaml entries are Owner-authored
    config, not live chat text) and --owner-chat-id carries task_identity
    through for audit, the same cross-reference convention cmd_submit's own
    docstring establishes for --source ai_agent callers.

    Returns the parsed JSON dict from task-gateway.py submit, or None on any
    failure -- fails open, same philosophy as find_in_flight_duplicate()
    above: a broken check here must never silently block a real, legitimate
    DIRECTIVE.yaml-driven dispatch."""
    text = f"{title}\n\n{prompt}".strip() if prompt else (title or task_identity)
    try:
        result = subprocess.run(
            ["python3", TASK_GATEWAY, "submit",
             "--text", text, "--source", "ai_agent",
             "--session-id", f"directive-engine-{task_identity}",
             "--owner-chat-id", task_identity],
            capture_output=True, text=True, timeout=90,
        )
        return json.loads(result.stdout.strip())
    except Exception as e:
        log_status(task_identity, f"check-duplicate battery call failed, fail-open, proceeding: {e}")
        return None


def submit_task(task_identity, tier, title, prompt, repo):
    spec = {
        "task_identity": task_identity,
        "task_kind": "veridian_task_create",
        "inputs": {"repo": repo, "title": title, "prompt": prompt},
    }
    # 2026-07-29 adversarial-test fix: task_identity used to be embedded
    # directly into the spec filename. A task_identity containing "../"
    # sequences would escape SPEC_TMP_DIR when the file is later opened
    # (real path-traversal shape; flagged, not confirmed live-exploited --
    # DIRECTIVE.yaml entries are Owner-authored today, not attacker text --
    # fixed anyway as defense-in-depth). The real task_identity value is
    # still stored verbatim in the JSON content above; only the on-disk
    # filename is sanitized.
    safe_slug = re.sub(r"[^A-Za-z0-9_-]", "_", task_identity)[:80]
    spec_path = os.path.join(SPEC_TMP_DIR, f"{safe_slug}-{uuid.uuid4().hex[:8]}.json")
    with open(spec_path, "w") as f:
        json.dump(spec, f)
    result = subprocess.run(
        ["python3", GOVERNOR, "--submit", "--spec-file", spec_path,
         "--tier", str(tier), "--source-trigger", "DIRECTIVE"],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        return {"accepted": False, "reason": f"unparseable governor output: {result.stdout[:200]} {result.stderr[:200]}"}


def _already_flagged_for_review(task_identity):
    """Real idempotency guard (dispatch-queue-starvation investigation,
    UMR-20260806-090229-f2a7): PENDING_OWNER_REVIEW.md is append-only and this
    engine ticks forever (every poll_seconds, default 60s) -- without this
    check, a task_identity whose retry is exhausted would get a fresh line
    appended on literally every subsequent tick, silently growing this file
    unbounded. This project has already hit exactly this shape of bug once for
    a different log (see resource_governor_tick_loop.sh's own header comment:
    "argparse ... got silently appended to LOG 7946 times before anyone
    noticed"). Real, simple check against the actual on-disk file -- no new
    state file, no new schema, no new database write."""
    if not os.path.exists(PENDING_REVIEW_FILE):
        return False
    marker = f"- {task_identity}:"
    with open(PENDING_REVIEW_FILE) as f:
        return any(line.startswith(marker) for line in f)


def note_needs_review(task_identity, reason):
    if _already_flagged_for_review(task_identity):
        return
    with open(PENDING_REVIEW_FILE, "a") as f:
        f.write(f"- {task_identity}: {reason} (logged {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})\n")
    log_status(task_identity, f"queued for Owner review: {reason[:60]}")


def dependencies_satisfied(entry):
    """Generic dependency gate: a task with depends_on: [other_task_identity, ...]
    is not submitted until every listed task_identity has real status 'completed'.
    Not phase-specific -- any queue entry can declare this."""
    for dep in entry.get("depends_on", []) or []:
        dep_status, _ = umr_status_for_identity(dep)
        if dep_status != "completed":
            return False, dep
    return True, None


def process_one(entry):
    task_identity = entry["task_identity"]
    status, umr_id = umr_status_for_identity(task_identity)

    if status == "completed":
        return "completed"
    if status in ("queued", "dispatched", "running"):
        return status
    if status in ("failed", "rejected_duplicate", "killed"):
        # Real fix (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7,
        # parent UMR-20260806-071025-1d28): this branch used to gate its real
        # retry-once policy on entry.get("_retried") -- an in-memory flag set on a
        # dict that main()'s own `directive = load_directive()` recreates fresh
        # from DIRECTIVE.yaml every single outer-loop tick (every poll_seconds,
        # default 60s). That flag NEVER survived to the next tick, so this branch
        # resubmitted the SAME task_identity via submit_task() below on literally
        # every real tick, forever, instead of the one real retry the code always
        # intended. Confirmed live via task_identity PHASE-3-BUILD-CALC /
        # PHASE-4-BUILD-WORKFLOW (DIRECTIVE.yaml priority_queue): resubmitted 20+
        # times since 2026-07-29, each later resubmission reusing the SAME umr_id
        # (resource_governor.py submit()'s Rule-1 reuse-on-resubmit path) whose
        # ts_submitted is, by that function's own documented design, NEVER
        # refreshed on reuse -- so that one row aged to TIER_MIN=0 within minutes
        # and then permanently won next_queued_task()'s ascending-ts_submitted
        # tiebreak against every other real queued row, including 30 genuinely
        # distinct tier-1 rows dated 2026-08-04 (confirmed ~2 real days stuck at
        # the time this was found) that could never even be ATTEMPTED --
        # dispatch_one() only ever evaluates the single top-ranked row per call,
        # and run_tick() stops the whole tick the moment that one row's own
        # dispatch attempt isn't "dispatched" (e.g. deferred for a free
        # concurrency/resource slot). A real, durable head-of-line-blocking
        # poison pill.
        #
        # Real fix: read the row's own persisted `reason` column instead of the
        # ephemeral in-memory flag. resource_governor.py's submit() already
        # writes reason="resubmitted (reused umr_id, prior status was ...)" on
        # exactly the one real resubmission this code path is meant to allow --
        # that is real, durable state living in umr_tasks itself, unaffected by
        # this process restarting or DIRECTIVE.yaml being reloaded.
        _, _, reason = umr_status_and_reason_for_identity(task_identity)
        if reason and reason.startswith(_RESUBMITTED_REASON_PREFIX):
            note_needs_review(
                task_identity,
                f"ended {status} after a real resubmission retry also ended {status} "
                f"(durable retry-exhaustion via the row's own persisted reason, not an "
                f"in-memory flag) -- needs human judgment",
            )
            return status
        # else: this is the task_identity's first real terminal outcome seen so
        # far -- fall through and allow exactly the one real resubmission the
        # code has always intended. submit_task() below reuses the same umr_id
        # via resource_governor.py's own Rule-1 path and records that fact in
        # `reason`, so the check above durably catches it if THIS retry also
        # ends terminal, on the very next tick that reaches this task_identity.

    ok, blocking_dep = dependencies_satisfied(entry)
    if not ok:
        return "waiting_on_dependency"  # not logged every pass -- avoid log spam; heartbeat covers it

    # Real duplicate guard, 2026-07-28 (see find_in_flight_duplicate's own docstring for
    # the real incident this closes -- PR #617 redispatched 6x same day via re-prefixed
    # task_identity strings across DIRECTIVE.yaml file versions).
    dup_identity, dup_status = find_in_flight_duplicate(task_identity)
    if dup_identity:
        log_status(task_identity, f"skipped -- real in-flight duplicate found: "
                                   f"{dup_identity} is {dup_status}")
        return f"duplicate_of_{dup_identity}"

    # Real gap closed, Stage 5 (2026-07-29): run the same check-duplicate/search/
    # query-knowledge/lookup-capability battery task-gateway.py submit runs for
    # every other real trigger, before ever constructing resource_governor.py's
    # --spec-file below. Mirrors prompt_gateway/gateway.py's
    # dispatch_to_task_lifecycle() "start" branch: call submit's battery first,
    # and if it reports duplicate_found, skip/flag instead of dispatching past
    # it -- exactly how that real call site already handles the same result.
    battery = run_check_duplicate_battery(
        task_identity, entry.get("title", task_identity), entry.get("prompt", ""),
    )
    if battery and battery.get("duplicate_found"):
        note_needs_review(
            task_identity,
            f"task-gateway.py submit's check-duplicate/search/query-knowledge battery "
            f"reports duplicate_found=true: {battery.get('duplicate_evidence', [])}",
        )
        log_status(task_identity, "skipped -- task-gateway.py submit battery flagged "
                                   "duplicate_found=true, queued for Owner review")
        return "duplicate_flagged_by_gateway_battery"

    result = submit_task(
        task_identity, entry.get("tier", 2), entry.get("title", task_identity),
        entry.get("prompt", ""), entry.get("repo", "compliance-tracker"),
    )
    if not result.get("accepted"):
        if result.get("reason") != "duplicate":
            note_needs_review(task_identity, f"submit rejected: {result.get('reason')}")
        return "queued"
    log_status(task_identity, f"submitted, umr_id={result.get('umr_id')}")
    return "queued"


def main():
    directive = load_directive()
    heartbeat_seconds = directive.get("execution_policy", {}).get("heartbeat_seconds", 300)
    poll_seconds = directive.get("execution_policy", {}).get("poll_queue_file_seconds", 60)

    log_status("DIRECTIVE", "engine resumed/started, reading live priority_queue every pass")
    last_heartbeat = time.time()

    while True:
        directive = load_directive()
        queue = sorted(
            directive.get("priority_queue", []),
            key=lambda t: t.get("priority", 999),
        )

        for entry in queue:
            task_identity = entry.get("task_identity")
            if not task_identity:
                continue
            process_one(entry)

        if time.time() - last_heartbeat >= heartbeat_seconds:
            statuses = [umr_status_for_identity(t["task_identity"])[0] for t in queue if t.get("task_identity")]
            done = statuses.count("completed")
            total = len(statuses)
            log_status("DIRECTIVE", f"[{done}/{total} completed] {resource_snapshot()}")
            last_heartbeat = time.time()

        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
