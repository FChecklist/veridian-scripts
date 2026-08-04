#!/usr/bin/env python3
"""
VERIDIAN-DEV AI-OS task/worker manager.
Server-side, self-contained state management for async AI workers that
survive client disconnects, server reboots, and resume from checkpoints
on interruption. See /opt/veridian/README-SERVER.md.
"""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import yaml

AI_OS = "/opt/veridian/ai-os"
CONTROLLER = f"{AI_OS}/CONTROLLER.yaml"
CONTROLLER_LOCK = f"{AI_OS}/.controller.lock"
REPOS = "/opt/veridian/repos"


OPS_APP_SYNC_URL = "https://veridian-aios.com/api/internal/ops-task-sync"


def _sync_to_app(task, extra_note=""):
    """Bridge write (2026-07-20, TASK 1.1): mirrors this task's current
    state into the app's own database (platform.ops_dev_tasks via
    POST /api/internal/ops-task-sync) so a coding task dispatched, run,
    and merged entirely on this server is visible from the app/Supabase
    side too -- closes the "two machines, zero bridge" gap. Same
    fail-open discipline as _auto_log_task_event below: never raises,
    never blocks real task lifecycle management, a network hiccup here
    must never be the reason a checkpoint fails. Short timeout (5s) for
    the same reason -- this is best-effort telemetry, not a dependency.
    """
    try:
        secret = os.environ.get("OPS_SYNC_SECRET")
        if not secret:
            env_path = "/opt/veridian/shared/.env"
            with open(env_path) as f:
                for line in f:
                    if line.startswith("OPS_SYNC_SECRET="):
                        secret = line.strip().split("=", 1)[1]
                        break
        if not secret:
            return  # can't sync without the secret -- fail open, silent

        last_note = extra_note or ""
        checkpoints = task.get("checkpoints") or []
        if checkpoints and not last_note:
            last_note = checkpoints[-1].get("note", "") or ""

        payload = {
            "ops_task_id": task["id"],
            "title": task.get("title", task["id"]),
            "repo": task.get("repo", "unknown"),
            "branch": task.get("branch"),
            "status": task.get("status", "unknown"),
            "software_task_id": task.get("software_task_id"),
            "ai_task_id": task.get("ai_task_id"),
            "execution_seconds": task.get("execution_seconds"),
            "restart_count": task.get("restart_count"),
            "last_checkpoint_note": (last_note or "")[:2000],
        }
        req = urllib.request.Request(
            OPS_APP_SYNC_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {secret}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _auto_log_task_event(kind, task, extra_note=""):
    """Automatic server-side logging to the Superboss Register -- Owner
    directive 2026-07-20: the laptop-side Claude Code hooks
    (.claude/hooks/*.ps1) only cover the interactive session; this is the
    other half -- every AI worker/supervisor/doc-worker task dispatched
    on THIS server, regardless of what created it (queue-dispatcher.py,
    module-queue-dispatcher.py, master-decompose.py, a manual CLI call).
    veridian-task.py is the single choke point every one of those paths
    already goes through for create/checkpoint, so instrumenting it here
    once covers all of them -- same principle as the interactive hooks,
    applied to the dispatch layer instead of the chat layer.

    Reuses superboss-register.py's own tested CLI exactly (log-work /
    log-action) rather than a second, parallel write path. Runs entirely
    on this server, so there is no network-latency concern the laptop
    hooks had to design around.

    MUST NEVER break real task lifecycle management, which is this
    script's actual job -- a logging failure here is swallowed, never
    raised, never blocks task create/checkpoint.

    Subprocess timeout (2026-07-23 corruption RCA fix): must be >= the
    30s busy_timeout superboss-register.py's own _connect() uses, and
    superboss-register.py now additionally serializes all its writes with
    an OS file lock (see its _write_lock()) -- a caller-side timeout
    shorter than the callee's own wait window is exactly what SIGKILLed a
    writer while it still held that lock, corrupting the db (full RCA in
    superboss-register.py's _write_lock() docstring). 35s gives a margin
    above the 30s busy_timeout without hanging a checkpoint loop forever.
    """
    try:
        if kind == "create":
            subprocess.run(
                ["python3", "/opt/veridian/scripts/superboss-register.py", "log-work",
                 "--ai-task-id", task["id"], "--source", "software", "--medium", "veridian_task_cli",
                 "--campaign", "auto-worker-task-log", "--content", f"task_create:{task['title'][:60]}",
                 "--term", "auto_log,worker_task,create,software",
                 "--status", task["status"]],
                capture_output=True, timeout=35,
            )
        elif kind == "checkpoint":
            subprocess.run(
                ["python3", "/opt/veridian/scripts/superboss-register.py", "log-action",
                 "--source", "ai_agent", "--medium", "veridian_task_cli",
                 "--campaign", "auto-worker-task-log",
                 "--content", f"task_checkpoint:{task['id']} status={task['status']}",
                 "--term", "auto_log,worker_task,checkpoint",
                 "--result", (extra_note or "")[:500]],
                capture_output=True, timeout=35,
            )
        elif kind == "record_usage":
            subprocess.run(
                ["python3", "/opt/veridian/scripts/superboss-register.py", "log-action",
                 "--source", "ai_agent", "--medium", "veridian_task_cli",
                 "--campaign", "auto-worker-task-log",
                 "--content", f"task_usage:{task['id']}",
                 "--term", "auto_log,worker_task,record_usage,cost",
                 "--result", (extra_note or "")[:500]],
                capture_output=True, timeout=35,
            )
    except Exception:
        pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextlib.contextmanager
def controller_lock():
    """Exclusive OS-level lock so concurrent workers/supervisors can't
    interleave read-modify-write cycles on CONTROLLER.yaml (root cause of
    the 2026-07-18 corruption -- two processes both read, both modified,
    both wrote, second write silently clobbered/interleaved with the first).
    """
    with open(CONTROLLER_LOCK, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def load_controller():
    with open(CONTROLLER) as f:
        return yaml.safe_load(f) or {"server": "VERIDIAN-DEV", "tasks": []}


def save_controller(ctrl):
    ctrl["updated_at"] = now()
    ctrl["task_count"] = len(ctrl["tasks"])
    # Atomic write (temp file + rename) so a reader can never observe a
    # partially-written file, even without holding the lock itself.
    tmp_path = f"{CONTROLLER}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        yaml.safe_dump(ctrl, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp_path, CONTROLLER)


def load_task(task_id):
    path = f"{AI_OS}/tasks/{task_id}/task.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def save_task(task_id, task):
    path = f"{AI_OS}/tasks/{task_id}/task.yaml"
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        yaml.safe_dump(task, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp_path, path)


@contextlib.contextmanager
def task_lock(task_id):
    lock_path = f"{AI_OS}/tasks/{task_id}/.task.lock"
    with open(lock_path, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def sync_controller_entry(task):
    with controller_lock():
        ctrl = load_controller()
        entry = {
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "repo": task["repo"],
            "branch": task["branch"],
            "created_at": task["created_at"],
            "last_checkpoint_at": task.get("last_checkpoint_at"),
            "service": task["service"],
            "task_dir": task["task_dir"],
            "execution_seconds": task.get("execution_seconds", 0),
            "restart_count": task.get("restart_count", 0),
        }
        ctrl["tasks"] = [t for t in ctrl["tasks"] if t["id"] != task["id"]] + [entry]
        save_controller(ctrl)


def parse_progress_md(workspace):
    """Parses a PROGRESS.md with '## Completed' / '## Remaining' checklist sections."""
    path = os.path.join(workspace, "PROGRESS.md")
    if not os.path.isfile(path):
        return None, None
    text = open(path).read()
    sections = re.split(r"^##\s+", text, flags=re.MULTILINE)
    completed, remaining = [], []
    for sec in sections:
        if sec.lower().startswith("completed"):
            completed = re.findall(r"^\s*-\s*\[[xX ]\]\s*(.+)$", sec, re.MULTILINE)
        elif sec.lower().startswith("remaining"):
            remaining = re.findall(r"^\s*-\s*\[[xX ]\]\s*(.+)$", sec, re.MULTILINE)
    return completed, remaining


# ---------------------------------------------------------------------------
# OCID-063 (PM directive UMR-20260804-060832-9fdf, real implementation
# authorized by UMR-20260804-061827-e3c6, governed by the Mandatory
# Governance Directive UMR-20260804-051521-7099): the mechanical handoff
# envelope. Real gap this closes -- confirmed via OCID-063's own discovery
# doc (ai-os/VERIDIAN_OCID_063_MECHANICAL_HANDOFF_ENVELOPE_DISCOVERY_2026-08-04.md
# in the compliance-tracker repo) -- no existing mechanism on this platform
# (task.yaml's narrated completed_steps/remaining_steps, ACTIVE-CLAIMS.yaml's
# claim registration, resource_governor.py's reuse_check_result,
# credit-accountant.py's deterministic verdict, or the AUDIT: PASS/FAIL
# comment convention) is a mechanical, per-tool-invocation call log with
# real status codes. Per that same discovery's own design proposal (and the
# Mandatory Governance Directive's explicit "never build new when existing
# can be enhanced" rule): this extends the EXISTING checkpoint write path
# (cmd_checkpoint below) with one new optional field, rather than a new
# schema/table/file. Entirely additive and optional -- a checkpoint that
# never supplies a handoff envelope behaves exactly as before this change.
#
# Status-category taxonomy matches the proposal's own three named
# categories (client error, server error, timeout) plus a real "success"
# and "unknown" fallback for any status this taxonomy doesn't recognize --
# never silently drops or misclassifies a real status value.
STATUS_CATEGORY_TIMEOUT = "timeout"
STATUS_CATEGORY_CLIENT_ERROR = "client_error"
STATUS_CATEGORY_SERVER_ERROR = "server_error"
STATUS_CATEGORY_SUCCESS = "success"
STATUS_CATEGORY_UNKNOWN = "unknown"

# "Capped" per the proposal's own framing ("a capped unknowns list"). No
# existing precedent sets this number -- chosen to match
# ai-os/CONSTITUTION.yaml's other small human-reviewable list caps (e.g.
# a PR's own findings/issues lists in this session's real audit comments
# rarely exceed single digits); revisit if real usage shows it's wrong.
MAX_UNKNOWNS = 10


def classify_call_status(status):
    """Mechanically classifies one real tool-call status value into the
    proposal's own three rejection categories, plus success/unknown.
    Deterministic, no narration: an integer status is bucketed purely by
    numeric range (matching real HTTP status-code semantics, the same
    convention every real status code seen this session already uses --
    e.g. credit-accountant.py's own subprocess exit-code handling and the
    real 401/403/429/500 codes this session's own live E2E testing hit
    against projexa-ai.com); the literal string "timeout" is its own
    category since a timeout has no real numeric status to classify."""
    if status == STATUS_CATEGORY_TIMEOUT:
        return STATUS_CATEGORY_TIMEOUT
    if isinstance(status, bool):
        # bool is a subclass of int in Python -- exclude explicitly so a
        # stray True/False status never silently classifies as a 2xx/4xx.
        return STATUS_CATEGORY_UNKNOWN
    if isinstance(status, int):
        if 200 <= status < 400:
            return STATUS_CATEGORY_SUCCESS
        if 400 <= status < 500:
            return STATUS_CATEGORY_CLIENT_ERROR
        if 500 <= status < 600:
            return STATUS_CATEGORY_SERVER_ERROR
        return STATUS_CATEGORY_UNKNOWN
    return STATUS_CATEGORY_UNKNOWN


def compute_rejected_paths(call_log):
    """The proposal's own "rejected paths list," derived MECHANICALLY (not
    narrated) by filtering call_log for entries whose real status falls
    into the client-error, server-error, or timeout category. call_log is
    a list of dicts, each expected to carry a "status" key (int or the
    literal string "timeout") -- entries missing "status" entirely are
    treated as unknown, never silently rejected or silently accepted.
    Pure function, no I/O, no mutation of call_log."""
    return [
        entry for entry in call_log
        if classify_call_status(entry.get("status"))
        in (STATUS_CATEGORY_CLIENT_ERROR, STATUS_CATEGORY_SERVER_ERROR, STATUS_CATEGORY_TIMEOUT)
    ]


def validate_handoff_envelope(call_log, conclusion, unknowns, max_unknowns=MAX_UNKNOWNS):
    """The proposal's own three strict-validation rules, exactly as
    specified (PM directive UMR-20260804-060832-9fdf): reject if the call
    log is empty; reject if rejected paths exist but unknowns is empty;
    reject if the conclusion is not exactly one sentence. Plus the
    "capped" enforcement PM directive UMR-20260804-061827-e3c6 explicitly
    named as its own real check: reject if unknowns exceeds max_unknowns.

    Returns (valid: bool, errors: list[str], rejected_paths: list[dict]) --
    rejected_paths is always returned (even when valid) since a caller
    that stores the envelope wants the real, already-computed list, not a
    second call to compute_rejected_paths.

    Pure function: never raises on malformed-but-well-typed input (empty
    list/string are valid inputs to check, not errors in the Python
    sense) -- the same fail-closed-on-content, fail-open-on-plumbing
    philosophy this platform's other deterministic checks already use
    (e.g. plan_generator.py's check_reuse_before_dispatch never raises,
    only returns a real recommendation)."""
    errors = []
    rejected_paths = compute_rejected_paths(call_log)

    if not call_log:
        errors.append("call_log must not be empty")

    if rejected_paths and not unknowns:
        errors.append(
            f"rejected_paths is non-empty ({len(rejected_paths)} entr"
            f"{'y' if len(rejected_paths) == 1 else 'ies'}) but unknowns is empty"
        )

    if len(unknowns) > max_unknowns:
        errors.append(f"unknowns exceeds cap of {max_unknowns} (found {len(unknowns)})")

    sentence_endings = re.findall(r"[.!?]+(?:\s|$)", (conclusion or "").strip())
    sentence_count = len(sentence_endings)
    if sentence_count != 1:
        errors.append(
            f"conclusion must be exactly one sentence (found {sentence_count} "
            f"sentence-ending mark{'s' if sentence_count != 1 else ''})"
        )

    return (len(errors) == 0, errors, rejected_paths)


# OCID-068 seven-rule guardrails addendum, Rule 7 (UMR-20260804-180711-7f96,
# UMR-20260804-205741-cf3f, citing UMR-20260804-170055-a069): "implementation
# completion, an implementation is not complete until real code, a real
# database change, a real test, a real artifact, a real pull request, and
# real evidence all match, never declare complete from narration alone. On
# completion, return the real evidence, the real files modified, the real
# database changes, the UMR, the OCID, the PR, the commit, the real test
# results, any open items, any blockers, and the real next action, no
# assumptions, no narration, no estimates."
#
# Real, existing partial enforcement this extends: cmd_checkpoint()'s own
# state-machine guard (below) already refuses "completed" unless a real
# "pending_review" checkpoint already exists in this task's own history --
# so "completed" can never be self-reported directly, only reached via the
# real pending_review -> supervisor-review path. That guard checks the
# STATE SEQUENCE is real; it does not check that the specific fields Rule 7
# names are genuinely present. This is the complement: an optional, strictly
# validated --evidence-json (same real, already-reviewed pattern OCID-063's
# --handoff-envelope established -- validated and rejected BEFORE the task
# lock is taken/anything is loaded or saved, so a malformed evidence file
# never partially writes a checkpoint; omitting it entirely behaves exactly
# as before this change, same backward-compatibility contract
# --handoff-envelope already set).
COMPLETION_EVIDENCE_REQUIRED_STRING_FIELDS = ("pr_url", "commit_sha", "test_results", "umr_id", "next_action")
COMPLETION_EVIDENCE_REQUIRED_LIST_FIELDS = ("open_items", "blockers")
COMPLETION_EVIDENCE_NARRATION_PLACEHOLDERS = {
    "n/a", "na", "none", "tbd", "todo", "unknown", "-", "pending", "later", "see above",
}
_COMPLETION_PR_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")
_COMPLETION_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_COMPLETION_UMR_ID_RE = re.compile(r"^UMR-\d{8}-\d{6}-[0-9a-f]{4}$")


def validate_completion_evidence(evidence):
    """Rule 7's real, structural completion-evidence check. Pure function,
    never raises: returns (valid: bool, errors: list[str]).

    A "narration, not evidence" placeholder (empty string, or one of the
    real generic strings in COMPLETION_EVIDENCE_NARRATION_PLACEHOLDERS,
    case-insensitive) in any required string field is rejected -- the whole
    point of this check is refusing exactly the kind of vague "done"/"N/A"
    self-report Rule 7's own text names ("never declare complete from
    narration alone"). pr_url/commit_sha/umr_id are further checked against
    their real, known formats (a real GitHub PR URL, a real hex commit SHA,
    a real UMR-<date>-<time>-<hex> id) -- not proof the PR is actually
    merged or the commit actually exists (this function does no I/O, by
    design, same as validate_handoff_envelope() above), but a real,
    mechanical format check that catches an obviously-fabricated or
    copy-pasted-wrong value, which a purely narrated "done" could never be
    checked against at all.

    open_items/blockers are real, required LIST fields -- an empty list is
    a real, valid "genuinely nothing open" state (not itself an error), but
    the key must be present and must actually be a list, never omitted or a
    narrated string standing in for a list ("no blockers" as free text is
    exactly the narration this check exists to refuse).

    db_changes is intentionally NOT in the required-fields list: many real
    completions genuinely touch no database at all (Rules 1-6's own PRs
    included plenty of code-only changes) -- requiring a non-empty
    db_changes value on every completion would itself be a fabrication
    demand. If present, it is validated the same narration-placeholder way
    as the required string fields; if absent, that is itself real, honest
    information (no claim made either way), not an error."""
    errors = []
    if not isinstance(evidence, dict):
        return False, [f"evidence must be a JSON object, got {type(evidence).__name__}"]

    def _is_narration_placeholder(value):
        return not isinstance(value, str) or not value.strip() or value.strip().lower() in COMPLETION_EVIDENCE_NARRATION_PLACEHOLDERS

    for field in COMPLETION_EVIDENCE_REQUIRED_STRING_FIELDS:
        value = evidence.get(field)
        if _is_narration_placeholder(value):
            errors.append(f"{field!r} is missing or a narration placeholder ({value!r}), real evidence required")
    db_changes = evidence.get("db_changes")
    db_changes_normalized = db_changes.strip().lower() if isinstance(db_changes, str) else None
    if "db_changes" in evidence and _is_narration_placeholder(db_changes) and db_changes_normalized not in ("none", "no schema or data changes"):
        # "none"/"no schema or data changes" (case-insensitive) are real,
        # explicit, honest
        # claims of absence, not narration placeholders -- deliberately
        # exempted from the generic placeholder list above (which exists
        # to catch vague non-answers, not honest "nothing to report").
        errors.append(f"'db_changes' is present but a narration placeholder ({evidence['db_changes']!r}) -- state 'none' explicitly if there truly were no database changes, or provide the real change")

    for field in COMPLETION_EVIDENCE_REQUIRED_LIST_FIELDS:
        if field not in evidence:
            errors.append(f"{field!r} is required (an empty list is valid; the key must be present)")
        elif not isinstance(evidence[field], list):
            errors.append(f"{field!r} must be a real list, got {type(evidence[field]).__name__} ({evidence[field]!r})")

    pr_url = evidence.get("pr_url")
    if isinstance(pr_url, str) and pr_url.strip() and not _COMPLETION_PR_URL_RE.match(pr_url.strip()):
        errors.append(f"'pr_url' does not match a real GitHub PR URL shape: {pr_url!r}")
    commit_sha = evidence.get("commit_sha")
    if isinstance(commit_sha, str) and commit_sha.strip() and not _COMPLETION_COMMIT_SHA_RE.match(commit_sha.strip()):
        errors.append(f"'commit_sha' does not match a real hex commit SHA shape: {commit_sha!r}")
    umr_id = evidence.get("umr_id")
    if isinstance(umr_id, str) and umr_id.strip() and not _COMPLETION_UMR_ID_RE.match(umr_id.strip()):
        errors.append(f"'umr_id' does not match the real UMR-<date>-<time>-<hex> shape: {umr_id!r}")

    return (len(errors) == 0, errors)


def cmd_create(args):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in args.title.lower())[:40].strip("-")
    task_id = f"task-{ts}-{slug}"
    task_dir = f"{AI_OS}/tasks/{task_id}"
    workspace = f"{task_dir}/workspace"
    branch = f"worker/{task_id}"
    repo_path = f"{REPOS}/{args.repo}"

    if not os.path.isdir(repo_path):
        print(f"ERROR: repo not found at {repo_path}")
        sys.exit(1)

    os.makedirs(task_dir, exist_ok=True)

    subprocess.run(["git", "-C", repo_path, "fetch", "origin"], check=True)
    default_ref = subprocess.run(
        ["git", "-C", repo_path, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    default_branch = default_ref.rsplit("/", 1)[-1]
    subprocess.run(
        ["git", "-C", repo_path, "worktree", "add", "-b", branch, workspace, f"origin/{default_branch}"],
        check=True,
    )

    # Reset PROGRESS.md: it is committed to main by each merged task, so a
    # fresh worktree otherwise inherits the PREVIOUS task's "complete"
    # content, which the checkpoint/resume-context flow would misreport as
    # this task's own status. Give every task a clean, task-scoped stub.
    progress_path = os.path.join(workspace, "PROGRESS.md")
    with open(progress_path, "w") as pf:
        pf.write(
            "# PROGRESS -- " + task_id + "\n\n"
            "## Completed\n\n"
            "## Remaining\n"
            "- [ ] Not started\n"
        )

    with open(f"{task_dir}/prompt.txt", "w") as f:
        f.write(args.prompt)

    task = {
        "id": task_id,
        "title": args.title,
        "status": "pending",
        "repo": args.repo,
        "branch": branch,
        "workspace": workspace,
        "task_dir": task_dir,
        "service": f"veridian-worker@{task_id}.service",
        "created_at": now(),
        "last_checkpoint_at": None,
        "completed_steps": [],
        "remaining_steps": [],
        "files_modified": [],
        "checkpoints": [],
        "execution_seconds": 0,
        "restart_count": 0,
        "token_usage": None,
        # Real, machine-readable hold-for-signoff (2026-07-26, root-caused
        # against the PR563 incident): set from task-gateway.py's cmd_start(),
        # which threads through tight_task_validation.py's real extraction of
        # a HOLD_FOR_OWNER_SIGNOFF: true marker in the dispatch prompt's
        # EXPECTED_OUTPUT/CONSTRAINTS. supervisor-entrypoint.sh's merge-decision
        # block reads this field FIRST, before tier/verdict are even considered.
        "hold_for_owner_signoff": args.hold_for_owner_signoff,
    }
    save_task(task_id, task)
    sync_controller_entry(task)
    _auto_log_task_event("create", task, extra_note=f"repo={args.repo}")
    _sync_to_app(task, extra_note=f"repo={args.repo}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    # 2026-08-01 RCA fix (24-unit OOM-kill incident): this used to also call
    # `systemctl --user enable`, on the theory that a `WantedBy=default.target`
    # unit "surviving a reboot" was a good thing -- it is, for the queued/
    # in-progress WORK, but not for how systemd carries that out. `enable`
    # doesn't give a task a gated, one-at-a-time restart; it hands systemd a
    # standing instruction to start EVERY enabled unit, in parallel, the
    # instant the box boots -- completely bypassing dispatch_core.py's shared
    # lock/CONCURRENCY_CAP that gates every other spawn path on this box. On
    # 2026-08-01, 24 accumulated worker units did exactly that in parallel
    # against an 11GB cap and OOM-killed the box. A worker unit must now only
    # ever be started explicitly (this `start` call here, or a later
    # `systemctl --user start` from a dispatch_core-gated tick script) --
    # never via systemd's own boot activation. Surviving a reboot mid-task is
    # still handled, just the right way: see
    # dispatch-tick.py:resume_interrupted_workers_tick(), which notices an
    # interrupted task.yaml after a reboot and re-submits it through
    # resource_governor.py's submit()/umr_tasks queue -- the SAME cap/lock as
    # any other new task, so N interrupted tasks trickle back in at the
    # existing cap instead of all firing at once. (See also this unit's own
    # template, veridian-worker@.service, which no longer declares
    # `[Install] WantedBy=default.target` at all -- `enable` would be a no-op
    # against it now even if some other call site tried.)
    subprocess.run(["systemctl", "--user", "start", task["service"]], check=True)

    print(f"CREATED: {task_id}")
    print(f"service: {task['service']} (started; NOT boot-enabled -- see resume_interrupted_workers_tick for reboot recovery)")
    print(f"workspace: {workspace}")


def cmd_adopt(args):
    """Registers a real, existing branch/PR that was created OUTSIDE the
    normal task-gateway.py -> cmd_create dispatch flow (e.g. a manual
    recovery/safety-net action) as a real task_dir/task.yaml entry -- the
    only thing supervisor-sweep.sh's discovery loop (a glob over
    /opt/veridian/ai-os/tasks/*/task.yaml) can find. Without this, such work
    is permanently invisible to sweep/supervisor and never gets a real audit
    (real incident: claude-control PR #84, the recovered-lifecycle-fix-e6c7049
    branch-recovery PR, had zero task_dir entry until this command existed).

    Unlike cmd_create, this does NOT spawn a fresh worker branch/service --
    the real work already exists on args.branch. It wires that existing
    branch into the same task.yaml shape cmd_create produces, with
    status=pending_review and no review.json, so supervisor-sweep.sh picks
    it up on its next run exactly like a worker task whose immediate
    post-checkpoint supervisor trigger was missed.
    """
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in args.title.lower())[:40].strip("-")
    task_id = args.task_id or f"task-{ts}-adopted-{slug}"
    task_dir = f"{AI_OS}/tasks/{task_id}"
    workspace = f"{task_dir}/workspace"
    repo_path = f"{REPOS}/{args.repo}"

    if not os.path.isdir(repo_path):
        print(f"ERROR: repo not found at {repo_path}")
        sys.exit(1)
    if os.path.isdir(task_dir):
        print(f"ERROR: task_dir already exists at {task_dir} -- refusing to overwrite")
        sys.exit(1)

    subprocess.run(["git", "-C", repo_path, "fetch", "origin"], check=True)
    verify = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--verify", f"origin/{args.branch}"],
        capture_output=True, text=True,
    )
    if verify.returncode != 0:
        print(f"ERROR: origin/{args.branch} does not exist -- nothing to adopt")
        sys.exit(1)

    os.makedirs(task_dir, exist_ok=True)

    # A branch can only be checked out as a named ref in ONE worktree at a
    # time; adopting a branch some other task's workspace already has
    # checked out (as happened with PR #84's own recovery/verification task)
    # must not fail here -- fall back to a detached-HEAD checkout of the same
    # commit, which git always permits regardless of other worktrees.
    result = subprocess.run(
        ["git", "-C", repo_path, "worktree", "add", workspace, args.branch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "-C", repo_path, "worktree", "add", "--detach", workspace, f"origin/{args.branch}"],
            check=True,
        )

    prompt_text = args.prompt or (
        f"Adopted, pre-existing branch '{args.branch}'"
        + (f" (PR: {args.pr_url})" if args.pr_url else "")
        + " -- not a fresh worker dispatch. Real work already exists on this "
        "branch; this task entry exists solely so supervisor-sweep.sh's "
        "discovery loop can find it and give it a real audit."
    )
    with open(f"{task_dir}/prompt.txt", "w") as f:
        f.write(prompt_text)

    adopt_note = f"adopted existing branch '{args.branch}'" + (f" ({args.pr_url})" if args.pr_url else "") + " -- registered for real audit, not a fresh worker dispatch"
    created_at = now()
    task = {
        "id": task_id,
        "title": args.title,
        "status": "pending_review",
        "repo": args.repo,
        "branch": args.branch,
        "workspace": workspace,
        "task_dir": task_dir,
        "service": f"veridian-supervisor@{task_id}.service",
        "created_at": created_at,
        "last_checkpoint_at": created_at,
        "completed_steps": [],
        "remaining_steps": [],
        "files_modified": [],
        "checkpoints": [{
            "at": created_at,
            "status": "pending_review",
            "files_modified": [],
            "completed_steps": [],
            "remaining_steps": [],
            "recent_commits": [],
            "note": adopt_note,
        }],
        "execution_seconds": 0,
        "restart_count": 0,
        "token_usage": None,
        "hold_for_owner_signoff": False,
        "adopted": True,
        "adopted_pr_url": args.pr_url,
    }
    save_task(task_id, task)
    sync_controller_entry(task)
    _auto_log_task_event("create", task, extra_note=adopt_note)
    _sync_to_app(task, extra_note=adopt_note)

    print(f"ADOPTED: {task_id}")
    print(f"branch: {args.branch}  workspace: {workspace}")
    print("status: pending_review (no review.json) -- supervisor-sweep.sh will pick this up on its next run")


def cmd_checkpoint(args):
    # OCID-063 (UMR-20260804-060832-9fdf / UMR-20260804-061827-e3c6): optional
    # mechanical handoff envelope. Validated and rejected BEFORE the task
    # lock is taken / anything is loaded or saved -- same "reject loudly,
    # save nothing" posture the existing completed-status guard below
    # already uses, so a malformed envelope never partially writes a
    # checkpoint. A checkpoint that omits --handoff-envelope entirely
    # behaves exactly as before this change.
    envelope = None
    rejected_paths = []
    if args.handoff_envelope:
        try:
            with open(args.handoff_envelope) as f:
                envelope = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: could not read --handoff-envelope {args.handoff_envelope!r}: {e}")
            sys.exit(1)
        # Real defect found by independent review (PR #19, round 1): syntactically
        # valid JSON whose top-level value isn't an object (null, a bare string,
        # a number, a bool, or an array) parsed fine here and then crashed with an
        # uncaught AttributeError on the very next line's .get() call -- json.load()
        # only guarantees valid JSON, never that the result is a dict. Checked
        # explicitly, same "reject loudly before anything is mutated" posture as
        # the OSError/JSONDecodeError case immediately above.
        if not isinstance(envelope, dict):
            print(
                f"ERROR: --handoff-envelope {args.handoff_envelope!r} must contain a "
                f"JSON object at its top level, got {type(envelope).__name__}"
            )
            sys.exit(1)
        call_log = envelope.get("call_log", [])
        conclusion = envelope.get("conclusion", "")
        unknowns = envelope.get("unknowns", [])
        valid, errors, rejected_paths = validate_handoff_envelope(call_log, conclusion, unknowns)
        if not valid:
            print(f"ERROR: --handoff-envelope failed strict validation for {args.task_id}:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)

    # OCID-068 seven-rule guardrails addendum, Rule 7 (UMR-20260804-180711-7f96,
    # UMR-20260804-205741-cf3f): optional --evidence-json, same real,
    # already-reviewed pre-lock-validation pattern as --handoff-envelope
    # above. Only enforced when --status completed is also used (Rule 7
    # is specifically about the completion declaration); a checkpoint at any
    # other status, or a completed checkpoint from a caller that predates
    # this change and omits --evidence-json, behaves exactly as before.
    completion_evidence = None
    if args.evidence_json:
        try:
            with open(args.evidence_json) as f:
                completion_evidence = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: could not read --evidence-json {args.evidence_json!r}: {e}")
            sys.exit(1)
        if args.status == "completed":
            valid, errors = validate_completion_evidence(completion_evidence)
            if not valid:
                print(f"ERROR: --evidence-json failed Rule 7 strict validation for {args.task_id}:")
                for e in errors:
                    print(f"  - {e}")
                sys.exit(1)

    with task_lock(args.task_id):
        task = load_task(args.task_id)
        if args.status == "completed":
            # Real fix (2026-07-24, task-20260724-041754 gap-close): root-caused
            # against task-20260724-033446, whose checkpoint history went
            # in_progress -> completed directly at 03:56:47, skipping pending_review
            # entirely -- traced to worker-entrypoint.sh's "clean working tree"
            # shortcut (the agent had already self-committed real changes, so the
            # tree was clean by the time the script checked, and the script wrongly
            # read that as "no work happened"). That call site is fixed separately,
            # but nothing here stopped it, or any future caller, from doing the same
            # thing -- this is the actual state-machine guard: completed is only
            # reachable after this task's own checkpoint history already contains a
            # pending_review entry (which is what triggers the supervisor review via
            # `systemctl --user start veridian-supervisor@<id>.service`), never
            # self-reported directly by a worker.
            prior_statuses = {cp.get("status") for cp in task.get("checkpoints", [])}
            if "pending_review" not in prior_statuses:
                print(
                    f"ERROR: refusing to checkpoint {args.task_id} as 'completed' -- "
                    "no prior 'pending_review' checkpoint exists in its own history. "
                    "completed must be reached via pending_review + supervisor review, "
                    "never self-reported directly."
                )
                sys.exit(1)
        task["last_checkpoint_at"] = now()
        if args.status:
            if args.status == "in_progress" and task["status"] != "pending":
                task["restart_count"] = task.get("restart_count", 0) + 1
            task["status"] = args.status
        # Real fix (independent review, PR #35 round 1): this used to fire
        # whenever completion_evidence was not None, regardless of
        # args.status -- so a non-'completed' checkpoint with
        # --evidence-json bypassed validate_completion_evidence() entirely
        # (it only runs above when args.status == "completed") yet still
        # got persisted, meaning a LATER non-completed checkpoint could
        # silently overwrite a previously-validated completed evidence
        # record with unvalidated data. Gated on the same condition
        # validation itself uses, so persistence and validation can never
        # drift apart again.
        if completion_evidence is not None and args.status == "completed":
            # Real, structured Rule 7 record -- persisted verbatim on the
            # task itself so it survives alongside the rest of this task's
            # own real history, same convention checkpoints[]/
            # files_modified already use.
            task["completion_evidence"] = completion_evidence

        workspace = task["workspace"]
        log_out = ""
        if os.path.isdir(workspace):
            try:
                status_out = subprocess.run(
                    ["git", "-C", workspace, "status", "--porcelain"],
                    capture_output=True, text=True, check=True,
                ).stdout
                files = [line[3:] for line in status_out.splitlines() if line.strip()]
                task["files_modified"] = files

                # Real-branch resolution (2026-07-26, root-caused against the
                # PR561/PR562/PR78 corrective-fix incidents): task["branch"]
                # is set once at cmd_create time (always the worker's own
                # freshly-created branch) and never otherwise updated. When a
                # corrective task's own dispatch prompt instructs the worker
                # to check out and push its real commits to a different,
                # pre-existing branch instead, supervisor-entrypoint.sh kept
                # reading the stale creation-time value for `gh pr create
                # --head`/`gh pr list --head`, so it could never find a PR to
                # comment on or merge -- a human had to intervene every time.
                # This is the actual handoff point (the checkpoint that moves
                # a task toward supervisor review), so read the workspace's
                # REAL current branch here instead of trusting the recorded
                # one -- no new field, the existing 'branch' value just
                # becomes trustworthy. Best-effort: a detached HEAD or a
                # workspace mid-git-operation must never break checkpointing.
                #
                # Follow-up fix (2026-07-26, root-caused against
                # task-20260726-105110's stuck 'pr80-work' incident): the
                # local checked-out branch name (`rev-parse --abbrev-ref
                # HEAD`) is not necessarily the branch that was actually
                # pushed -- a worker can `git checkout -b <local-alias>
                # <remote-ref>` and commit/push there via the tracking
                # relationship alone, leaving the local name never matching
                # anything on the remote. supervisor-entrypoint.sh's `gh pr
                # create --head`/`gh pr list --head` need the real remote
                # branch name, so prefer the upstream tracking branch
                # (`@{upstream}`, with its leading "<remote>/" prefix
                # stripped) when one is set, and only fall back to the local
                # HEAD branch name when there is no upstream -- e.g. a
                # genuinely new branch with nothing to track yet.
                #
                # Follow-up fix (2026-07-27, root-caused live against 2 stuck
                # rca- tasks whose task.yaml ended up with branch: master,
                # which then made supervisor-entrypoint.sh fail with
                # "supervisor could not resolve a real PR for branch
                # 'master'"): git's own branch.autoSetupMerge default means a
                # BRAND NEW branch created via `checkout -b <branch>
                # origin/<default>` -- exactly what cmd_create's `git
                # worktree add -b` does -- has `@{upstream}` pointing at
                # `origin/<default>` from the INSTANT it is created, before
                # any commit or push (confirmed directly: a fresh worktree's
                # `@{upstream}` resolves to "origin/master" immediately after
                # creation, with nothing pushed). Any checkpoint that runs
                # before the first real `git push -u origin <branch>` -- the
                # very first in_progress checkpoint, or any checkpoint on a
                # task that never gets past pre-flight -- was reading this
                # default fork-point tracking ref as if it were proof of a
                # real push, overwriting task["branch"] with the repo's own
                # default branch name. That default branch is never a
                # legitimate real target branch for a worker's own PR in
                # this workflow, so it is excluded here: @{upstream} is only
                # trusted once it has actually been retargeted by a real
                # push away from the default branch.
                default_ref = subprocess.run(
                    ["git", "-C", workspace, "symbolic-ref", "refs/remotes/origin/HEAD"],
                    capture_output=True, text=True,
                )
                default_branch = (
                    default_ref.stdout.strip().rsplit("/", 1)[-1]
                    if default_ref.returncode == 0 and default_ref.stdout.strip() else None
                )

                upstream = subprocess.run(
                    ["git", "-C", workspace, "rev-parse", "--abbrev-ref",
                     "--symbolic-full-name", "@{upstream}"],
                    capture_output=True, text=True,
                )
                real_branch = None
                if upstream.returncode == 0 and upstream.stdout.strip():
                    candidate = upstream.stdout.strip()
                    if "/" in candidate:
                        candidate = candidate.split("/", 1)[1]
                    if candidate != default_branch:
                        real_branch = candidate
                if not real_branch:
                    real_branch = subprocess.run(
                        ["git", "-C", workspace, "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()
                if real_branch and real_branch != "HEAD":
                    task["branch"] = real_branch

                log_out = subprocess.run(
                    ["git", "-C", workspace, "log", "--oneline", "-10"],
                    capture_output=True, text=True, check=True,
                ).stdout
                completed, remaining = parse_progress_md(workspace)
                if completed is not None:
                    task["completed_steps"] = completed
                if remaining is not None:
                    task["remaining_steps"] = remaining
            except subprocess.CalledProcessError:
                pass

        checkpoint = {
            "at": task["last_checkpoint_at"],
            "status": task["status"],
            "files_modified": task["files_modified"],
            "completed_steps": task.get("completed_steps", []),
            "remaining_steps": task.get("remaining_steps", []),
            "recent_commits": log_out.strip().splitlines(),
            "note": args.note or "",
        }
        # OCID-063: optional, additive -- only present when a caller
        # actually supplies --handoff-envelope (already validated above,
        # before the task lock was even taken). rejected_paths is the
        # real, mechanically-computed list, not re-derived from the
        # stored call_log by a future reader.
        if envelope is not None:
            checkpoint["tool_call_log"] = envelope.get("call_log", [])
            checkpoint["conclusion"] = envelope.get("conclusion", "")
            checkpoint["unknowns"] = envelope.get("unknowns", [])
            checkpoint["rejected_paths"] = rejected_paths
        task.setdefault("checkpoints", []).append(checkpoint)
        save_task(args.task_id, task)
    sync_controller_entry(task)
    _auto_log_task_event("checkpoint", task, extra_note=args.note or f"files_modified={len(task.get('files_modified', []))}")
    _sync_to_app(task, extra_note=args.note or "")
    print(f"CHECKPOINT saved for {args.task_id}: status={task['status']}")


def cmd_resume_context(args):
    task = load_task(args.task_id)
    checkpoints = task.get("checkpoints", [])
    if not checkpoints:
        print("(no prior checkpoint — this is a fresh start)")
        return
    last = checkpoints[-1]
    print(f"Last checkpoint at {last['at']} (status was: {last['status']})")
    if last.get("note"):
        print(f"Note: {last['note']}")
    if last.get("completed_steps"):
        print("Completed so far:")
        for s in last["completed_steps"]:
            print(f"  - {s}")
    if last.get("remaining_steps"):
        print("Remaining (per last known plan):")
        for s in last["remaining_steps"]:
            print(f"  - {s}")
    if last.get("files_modified"):
        print(f"Files with uncommitted changes at last checkpoint: {', '.join(last['files_modified'])}")
    if last.get("recent_commits"):
        print("Recent commits:")
        for c in last["recent_commits"]:
            print(f"  {c}")


def cmd_record_usage(args):
    with task_lock(args.task_id):
        task = load_task(args.task_id)
        task["execution_seconds"] = task.get("execution_seconds", 0) + args.elapsed
        result_path = f"{task['task_dir']}/result.json"
        if os.path.isfile(result_path):
            try:
                with open(result_path) as f:
                    result = json.load(f)
                usage = result.get("usage") or result.get("total_cost_usd")
                if usage:
                    task["token_usage"] = usage
            except (json.JSONDecodeError, ValueError):
                pass
        save_task(args.task_id, task)
    sync_controller_entry(task)
    _auto_log_task_event("record_usage", task, extra_note=f"+{args.elapsed}s total={task['execution_seconds']}s usage={task.get('token_usage')}")
    print(f"USAGE recorded for {args.task_id}: +{args.elapsed}s (total {task['execution_seconds']}s)")


def cmd_status(args):
    ctrl = load_controller()
    tasks = ctrl.get("tasks", [])
    if not tasks:
        print("No tasks recorded.")
        return
    by_status = {}
    for t in tasks:
        by_status.setdefault(t["status"], []).append(t)
    order = ["in_progress", "pending", "pending_review", "awaiting_human_approval", "blocked", "failed", "completed"]
    for status in order:
        items = by_status.pop(status, [])
        if not items:
            continue
        print(f"\n=== {status.upper()} ({len(items)}) ===")
        for t in items:
            print(f"  {t['id']}  [{t['repo']}:{t['branch']}]  {t['title']}")
            print(f"    last checkpoint: {t.get('last_checkpoint_at')}  restarts: {t.get('restart_count', 0)}  exec_seconds: {t.get('execution_seconds', 0)}")
    for status, items in by_status.items():
        print(f"\n=== {status.upper()} ({len(items)}) ===")
        for t in items:
            print(f"  {t['id']}  {t['title']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--prompt", required=True)
    c.add_argument("--hold-for-owner-signoff", action="store_true", dest="hold_for_owner_signoff")
    c.set_defaults(func=cmd_create)

    ad = sub.add_parser("adopt")
    ad.add_argument("--title", required=True)
    ad.add_argument("--repo", required=True)
    ad.add_argument("--branch", required=True)
    ad.add_argument("--pr-url", default=None, dest="pr_url")
    ad.add_argument("--task-id", default=None, dest="task_id")
    ad.add_argument("--prompt", default=None)
    ad.set_defaults(func=cmd_adopt)

    ck = sub.add_parser("checkpoint")
    ck.add_argument("task_id")
    ck.add_argument("--status", default=None)
    ck.add_argument("--note", default=None)
    ck.add_argument("--auto", action="store_true")
    ck.add_argument(
        "--handoff-envelope", default=None, dest="handoff_envelope",
        help="OCID-063: path to a JSON file with {\"call_log\": [...], "
             "\"conclusion\": \"...\", \"unknowns\": [...]}. Optional -- "
             "omitting it behaves exactly as before this flag existed. "
             "Strictly validated before the checkpoint is written; a "
             "failing envelope rejects the whole checkpoint call.",
    )
    ck.add_argument(
        "--evidence-json", default=None, dest="evidence_json",
        help="OCID-068 Rule 7: path to a JSON file with real completion "
             "evidence (pr_url, commit_sha, test_results, umr_id, "
             "next_action, open_items, blockers, optionally db_changes). "
             "Optional -- omitting it behaves exactly as before this flag "
             "existed. Strictly validated before a --status completed "
             "checkpoint is written; a failing evidence file rejects the "
             "whole checkpoint call. Ignored for any other --status.",
    )
    ck.set_defaults(func=cmd_checkpoint)

    rc = sub.add_parser("resume-context")
    rc.add_argument("task_id")
    rc.set_defaults(func=cmd_resume_context)

    ru = sub.add_parser("record-usage")
    ru.add_argument("task_id")
    ru.add_argument("--elapsed", type=int, required=True)
    ru.set_defaults(func=cmd_record_usage)

    st = sub.add_parser("status")
    st.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)
