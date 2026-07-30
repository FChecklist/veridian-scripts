#!/usr/bin/env python3
"""
backfill_phase_self_report.py -- closes the root cause behind two real,
consecutive manual interventions this session (VERIDIAN_ARCHITECTURE_V2
phase_1 / compliance-tracker PR #559, phase_2 / PR #560): a phase's
worker was told (via auto_phase_continuation.py's own build_prompt())
to update its own phase-plan entry's status/completed_by_task/evidence
fields after merging, but real evidence from two consecutive real phases
shows workers do not reliably do this. is_phase_done() in
auto_phase_continuation.py requires that self-report to exist before it
will even attempt its real merged-PR cross-reference, so a missing
self-report permanently blocks auto-dispatch of whatever depends on it
until a human hand-edits the YAML (see commits fab4ff4, 1f9fd52).

This makes the self-report SOFTWARE's responsibility instead: given a
task id, it independently confirms a real merge via `gh pr view
--json state,mergedAt` (never trusting the task's own self-report), then
mechanically writes status/completed_by_task/evidence into the correct
phase-plan file under /opt/veridian/repos/claude-control (the master
clone every other real-state check in this pipeline already reads via
`git show master:...`) and commits+pushes directly to master -- the same
place the two manual backfills above were made.

Two call sites (both real, both root-caused this session):
  1. supervisor-entrypoint.sh, right after it confirms a tier1 merge for
     itself (`--task-id $TASK_ID`, single task, best-effort, non-fatal).
  2. auto_phase_continuation.py's own tier2 sweep (`--sweep`), since a
     tier2 PR is merged by a human out-of-band -- nothing else ever
     revisits it. --sweep also serves as the one-off retroactive-backfill
     tool for every existing phase plan in the system (SCOPE item 5).

Idempotent by construction: a phase already self-reporting done AND
carrying an extractable task id (the exact condition
auto_phase_continuation.is_phase_done() checks) is left untouched, so
re-running this after a worker DID self-report correctly is a no-op, and
re-running it on every tier1 merge / every 30-minute cron tick never
double-writes or clobbers a worker's own good evidence text.

--audit-plans (added 2026-07-26, real incident: VERIDIAN_ARCHITECTURE_V2
phase_4, commit 4611924 -- a worker task wrote status: done directly into
its own phase-plan entry, citing only a branch name, while the real PR
#562 stayed OPEN/mergeStateStatus=DIRTY. This bypassed this script
entirely: nothing about --task-id/--sweep above stops a worker's own PR
commit from writing status/completed_by_task/evidence straight into the
shared YAML, since the file itself has no protection. Caught only because
a human happened to cross-check `gh pr view` by hand -- see commit
a82ee2d, ai-os/OWNER_DIRECTIVES/MEMORY_OWNER_AI.yaml incident
2026-07-26_worker_bypassed_self_report_backfill). --audit-plans closes
this by treating every phase-plan file's already-"done" phases as
untrusted input: it independently re-confirms each one via `gh pr
list`/confirm_merge() (the exact same never-trust-the-file standard this
script already applies to NEW self-reports), and any phase that fails
re-verification is reverted -- via `git blame` on the status line to find
the responsible commit, then diffed against that commit's own parent
revision -- to its state immediately before that commit, with the
incident recorded in the revert's own commit message (this repo's
existing incident-record convention; no new incident-log file). A CI
check gating worker PRs directly (the other option for this gap) is not
available to a worker task: pushing `.github/workflows/**` is silently
rejected for lack of the `workflow` OAuth scope (SUPERBOSS_DISPATCH_PROMPT.md's
hard rule) -- this script, already the sole legitimate writer and already
deployed live via deploy-live-scripts.sh + the existing
supervisor-sweep.sh cron, is the real achievable enforcement point.

--audit-plans fix (2026-07-26, real AUDIT: REJECT on this same PR): the
first cut of confirm_merge() collapsed "gh confirmed this PR/branch is
NOT merged" and "the gh call itself failed" (auth failure, rate-limiting,
transient network error, bad target_repo) into the same False result, and
find_first_unverified_done_block() treated any False as a violation. On
an unattended cron sweep, a transient gh failure would therefore auto-
revert a genuinely-done phase and auto-commit+push a false incident
straight to master -- worse than the bug --audit-plans was meant to fix.
confirm_merge() now returns a 4th `ambiguous` flag; only a definitive
non-merge triggers a revert, an ambiguous gh failure skips-with-warning
like the existing blame_line/parent-lookup failure paths. Two more real
gaps from the same review, closed in the same pass: a phase with no
target_repo field at all (e.g. every phase in
ai-os/AUDITOR_ENGINE_PHASE_PLAN_2026-07-24.yaml) now logs a visible
warning instead of silently passing as "nothing to check"; and a blame_
line/parent-lookup failure -- including a worker fabricating an entirely
new phase block from scratch with no prior state to revert to, the most
direct reproduction of the original phase_4/PR#562 incident -- now
records a real incident instead of a silent stderr-only warning.

--audit-plans fix #2 (2026-07-26, real AUDIT: REJECT, live-reproduced): a
plan file with MORE THAN ONE phase falsely self-reporting done desynced
audit_and_correct_plan_file()'s line numbers from blame_line()'s `git
blame HEAD` lookup -- reverting the first violation can delete lines
in-place (revert_block_fields, when the parent lacked completed_by_task/
evidence), and the old while-loop re-read that mutated, shorter file to
compute the next violation's status-line number, then blamed it against
HEAD, which still pointed at the larger pre-revert commit (the revert
wasn't committed yet). The offset landed blame on the wrong line,
occasionally the repo's own root commit, which `git blame` (no --root)
prefixes with its boundary-commit '^' marker -- corrupting the "hash",
failing the `{hash}^` parent lookup, and silently leaving that violation
un-reverted while still auto-committing+pushing the first revert. Fixed by
resolving EVERY violation's blame/parent lookup in one pass against the
same unmutated `lines` snapshot (== HEAD, since REPO_ROOT was just synced)
before any revert is applied, then applying all reverts bottom-to-top in a
single pass so an earlier block's [start, end) is never invalidated by a
later block's line-count change. Also: a violation found but not
auto-corrected (blame/parent-lookup/fabricated-block failure) previously
left changed_any False for that file, so if no OTHER file in the same run
had a real revert, the incident was recorded only in that invocation's own
stdout/stderr and vanished once the process exited. commit_and_push_audit
now runs (with --allow-empty when nothing was reverted) whenever any
incident was recorded at all, so a found-but-uncorrected violation is
always persisted to master's history.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import yaml

# Overridable via env for the regression tests (tests/backfill_phase_self_report_test.py)
# -- production always uses the real defaults, never a CLI flag, so a real
# invocation can never accidentally point at a test fixture.
REPO_ROOT = os.environ.get("VERIDIAN_REPO_ROOT_OVERRIDE", "/opt/veridian/repos/claude-control")
TASKS_DIR = os.environ.get("VERIDIAN_TASKS_DIR_OVERRIDE", "/opt/veridian/ai-os/tasks")
VERIDIAN_TASK = os.environ.get("VERIDIAN_TASK_CLI_OVERRIDE", "/opt/veridian/scripts/veridian-task.py")
PLAN_GLOB_RE = re.compile(r".*_(?:PHASE_PLAN|IMPLEMENTATION_PLAN)_[0-9]{4}-[0-9]{2}-[0-9]{2}\.yaml$")
TASK_ID_RE = re.compile(r"task-20\d{6}-[a-z0-9-]+")
PHASE_REF_RE = re.compile(
    r"This is (?P<phase_id>[a-zA-Z0-9_-]+) of ai-os/(?P<plan_file>[A-Za-z0-9_.-]+\.yaml)"
)


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, timeout=60, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


# ---------------------------------------------------------------------------
# Resolving which phase-plan entry a task corresponds to
# ---------------------------------------------------------------------------

def resolve_phase_ref(task_dir):
    """Prefers the structured phase_plan.yaml sidecar (written by
    auto_phase_continuation.py's dispatch() going forward); falls back to
    regex-parsing the task's own dispatch prompt.txt, which already
    contains "This is <phase_id> of ai-os/<plan_file>" verbatim (same
    text build_prompt() has generated all along) -- needed for every task
    dispatched before this sidecar existed, including any currently
    in-flight task."""
    sidecar = os.path.join(task_dir, "phase_plan.yaml")
    if os.path.isfile(sidecar):
        try:
            with open(sidecar) as f:
                doc = yaml.safe_load(f) or {}
            if doc.get("plan_file") and doc.get("phase_id"):
                return doc["plan_file"], doc["phase_id"]
        except (OSError, yaml.YAMLError) as e:
            log(f"  WARNING: could not parse {sidecar}: {e}")

    prompt_path = os.path.join(task_dir, "prompt.txt")
    if os.path.isfile(prompt_path):
        with open(prompt_path) as f:
            text = f.read()
        m = PHASE_REF_RE.search(text)
        if m:
            return m.group("plan_file"), m.group("phase_id")
    return None, None


# ---------------------------------------------------------------------------
# Real merge confirmation (same proof standard as
# supervisor-entrypoint.sh's own MERGE-DETECTION-BLOCK: gh pr view's
# state/mergedAt fields, never a self-report, never a shell exit code)
# ---------------------------------------------------------------------------

def confirm_merge(task_id, repo, branch=None):
    """Returns (merged: bool, pr_number, merged_at, ambiguous: bool) for
    task_id's PR on FChecklist/<repo>. branch defaults to worker/<task_id>
    (this pipeline's universal convention -- task.yaml's own branch field,
    task-gateway.py, and auto_phase_continuation.py's gh_pr_merged_for_task
    all agree on it).

    ambiguous=True means `gh` itself could not answer the question (non-
    zero returncode from auth failure/rate-limiting/transient network
    error/a bad repo value, or unparseable JSON) -- this is NOT the same
    as ambiguous=False, merged=False, which means gh definitively answered
    and no MERGED PR exists for this branch. Collapsing the two into one
    False was the real gap in a prior --audit-plans revision: a transient
    gh failure would be treated identically to a confirmed non-merge and
    trigger an auto-revert + auto-push to master. Callers doing anything
    destructive (revert, commit) must check `ambiguous` and skip-with-
    warning instead, the same way this file already treats blame_line()/
    parent-revision-lookup failures."""
    branch = branch or f"worker/{task_id}"
    proc = run(["gh", "pr", "list", "--repo", f"FChecklist/{repo}", "--head", branch,
                "--state", "all", "--json", "state,number,mergedAt"])
    if proc.returncode != 0:
        return False, None, None, True
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, None, None, True
    for r in rows:
        if r.get("state") == "MERGED" and r.get("mergedAt"):
            return True, r.get("number"), r.get("mergedAt"), False
    return False, None, None, False


# ---------------------------------------------------------------------------
# Targeted text-level phase-block edit (never a full YAML re-dump -- this
# repo's own two real hand-backfills (commits fab4ff4, 1f9fd52) are pure
# line-level edits of the status/completed_by_task/evidence fields, and a
# full yaml.safe_load+dump round-trip would reformat every block-scalar
# string in the file for a one-line semantic change)
# ---------------------------------------------------------------------------

def find_phase_block(lines, phase_id):
    """Returns (start, end) line-index range [start, end) for the phase
    entry whose id/phase field matches phase_id, covering both real
    schemas this session's plan files use: `- id: <string>` and
    `- phase: <int>` (normalized the same way
    auto_phase_continuation.normalize_phase_id() does) -- and, unlike a
    fixed-column assumption, both real indent styles this session's plan
    files actually use (`- id:` at column 0 in
    VERIDIAN_ARCHITECTURE_V2_PHASE_PLAN_2026-07-25.yaml, `  - phase:` at
    column 2 in AUDITOR_ENGINE_PHASE_PLAN_2026-07-24.yaml). The end
    boundary is the next list item at the SAME indent depth, not just the
    next line starting with a dash, so a phase's own nested list fields
    (scope:, depends_on:, etc.) are never mistaken for a sibling phase."""
    start = None
    indent = None
    id_re = re.compile(r"^(\s*)- id:\s*(\S+)")
    phase_re = re.compile(r"^(\s*)- phase:\s*(\d+)\s*$")
    target_num = None
    m = re.match(r"phase-(\d+)$", phase_id)
    if m:
        target_num = m.group(1)
    for i, line in enumerate(lines):
        idm = id_re.match(line)
        if idm and idm.group(2) == phase_id:
            start, indent = i, idm.group(1)
            break
        pm = phase_re.match(line)
        if pm and target_num and pm.group(2) == target_num:
            start, indent = i, pm.group(1)
            break
    if start is None:
        return None, None
    list_item_re = re.compile(rf"^{re.escape(indent)}- (id|phase):")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if list_item_re.match(lines[j]):
            end = j
            break
    return start, end


def _find_field_indent(lines, start, end, key):
    """Returns (line_index_or_None, indent_string_or_None) for `  <key>:`
    inside [start, end), tolerant of whatever indent depth this
    particular plan file's phase entries actually use."""
    m_re = re.compile(rf"^(\s+){re.escape(key)}:\s*(.*)$")
    for i in range(start, end):
        m = m_re.match(lines[i])
        if m:
            return i, m.group(1)
    return None, None


def block_self_reports_done(lines, start, end):
    idx, _indent = _find_field_indent(lines, start, end, "status")
    status = ""
    if idx is not None:
        status = lines[idx].split(":", 1)[1].strip().strip("'\"").lower()
    if not ("done" in status or "complete" in status or status == "this_task"):
        return False
    block_text = "".join(lines[start:end])
    return bool(TASK_ID_RE.search(block_text))


def _extract_field(lines, start, end, key):
    """Raw scalar value of `<key>:` inside [start, end), or None -- same
    lookup block_self_reports_done/patch_phase_block already do for
    `status`, generalized for reading completed_by_task/target_repo too."""
    idx, _indent = _find_field_indent(lines, start, end, key)
    if idx is None:
        return None
    return lines[idx].split(":", 1)[1].strip().strip("'\"")


def list_phase_blocks(lines):
    """Enumerates every phase entry in a plan file as (phase_id, start, end)
    -- the same two real schemas find_phase_block() already tolerates
    (`- id: <string>` and `- phase: <int>`, normalized to `phase-<N>`), but
    scanning the whole file instead of stopping at one target id. Used by
    --audit-plans to independently re-check every already-"done" phase, not
    just the one phase a --task-id/--sweep call is currently resolving."""
    id_re = re.compile(r"^(\s*)- id:\s*(\S+)")
    phase_re = re.compile(r"^(\s*)- phase:\s*(\d+)\s*$")
    items = []
    for i, line in enumerate(lines):
        idm = id_re.match(line)
        if idm:
            items.append((idm.group(2), idm.group(1), i))
            continue
        pm = phase_re.match(line)
        if pm:
            items.append((f"phase-{pm.group(2)}", pm.group(1), i))
    blocks = []
    for idx, (phase_id, indent, start) in enumerate(items):
        end = len(lines)
        for j in range(idx + 1, len(items)):
            if items[j][1] == indent:
                end = items[j][2]
                break
        blocks.append((phase_id, start, end))
    return blocks


def yaml_scalar(value):
    """Renders value the same way PyYAML would render it as a mapping
    value, without dumping the surrounding structure (keeps the edit to
    exactly the lines that change)."""
    dumped = yaml.safe_dump({"v": value}, default_flow_style=False, allow_unicode=True)
    return dumped[len("v: "):].rstrip("\n")


def patch_phase_block(lines, start, end, task_id, evidence):
    """Mutates lines in place. Returns True if anything changed. Field
    indent is derived from whichever field this block already has (status,
    falling back to completed_by_task/evidence, falling back to the list
    marker's own indent + 2) -- never hardcoded, since this session's real
    plan files use more than one indent depth for phase entries."""
    changed = False
    status_idx, field_indent = _find_field_indent(lines, start, end, "status")
    completed_idx, completed_indent = _find_field_indent(lines, start, end, "completed_by_task")
    evidence_idx, evidence_indent = _find_field_indent(lines, start, end, "evidence")
    field_indent = field_indent or completed_indent or evidence_indent
    if field_indent is None:
        marker_indent_len = len(lines[start]) - len(lines[start].lstrip(" "))
        field_indent = " " * (marker_indent_len + 2)

    if status_idx is not None:
        current = lines[status_idx].split(":", 1)[1].strip().strip("'\"").lower()
        if not ("done" in current or "complete" in current):
            lines[status_idx] = f"{field_indent}status: done\n"
            changed = True

    insert_at = end
    if completed_idx is not None:
        lines[completed_idx] = f"{field_indent}completed_by_task: {task_id}\n"
    else:
        lines.insert(insert_at, f"{field_indent}completed_by_task: {task_id}\n")
        insert_at += 1
        if evidence_idx is not None and evidence_idx >= insert_at - 1:
            evidence_idx += 1
        changed = True

    evidence_line = f"{field_indent}evidence: {yaml_scalar(evidence)}\n"
    if evidence_idx is not None:
        lines[evidence_idx] = evidence_line
    else:
        lines.insert(insert_at, evidence_line)
        changed = True

    return changed


def revert_block_fields(lines, start, end, parent_lines, parent_start, parent_end):
    """Mutates lines[start:end] in place so status/completed_by_task/evidence
    match whatever parent_lines[parent_start:parent_end] had for those same
    keys (parent being the phase block's own state one commit before the
    commit that wrote its current status line -- see blame_line()). A key
    present in the parent overwrites (or, if missing from the current block,
    appends -- matching patch_phase_block's own insertion convention); a key
    absent from the parent is deleted from the current block outright. This
    is a targeted revert of exactly the 3 self-report fields, not a full
    block replacement, so any other real content a legitimate edit added to
    the same phase (scope, depends_on, etc.) is left untouched."""
    block = lines[start:end]
    parent_block = parent_lines[parent_start:parent_end]
    for key in ("status", "completed_by_task", "evidence"):
        cur_idx, _cur_indent = _find_field_indent(block, 0, len(block), key)
        par_idx, _par_indent = _find_field_indent(parent_block, 0, len(parent_block), key)
        if par_idx is not None:
            new_line = parent_block[par_idx]
            if cur_idx is not None:
                block[cur_idx] = new_line
            else:
                block.append(new_line)
        elif cur_idx is not None:
            if key == "status":
                marker_indent_len = len(block[0]) - len(block[0].lstrip(" "))
                field_indent = " " * (marker_indent_len + 2)
                block[cur_idx] = f"{field_indent}status: not_started\n"
            else:
                del block[cur_idx]
    lines[start:end] = block


# ---------------------------------------------------------------------------
# Repo sync + commit/push (direct to master, same place the two real
# hand-backfills this session landed on -- see module docstring)
# ---------------------------------------------------------------------------

def fetch_master():
    run(["git", "-C", REPO_ROOT, "fetch", "origin", "master"])


def read_commit_plan_lines(plan_file, ref):
    """Read-only: ai-os/<plan_file> as of git ref `ref` (a commit-ish, e.g.
    "origin/master" or "<sha>^"), zero working-tree mutation."""
    proc = run(["git", "-C", REPO_ROOT, "show", f"{ref}:ai-os/{plan_file}"])
    if proc.returncode != 0:
        return None
    return proc.stdout.splitlines(keepends=True)


def read_master_plan_lines(plan_file):
    """Read-only: the committed origin/master copy of ai-os/<plan_file>.
    Used as a cheap up-front check so the far more expensive/invasive
    sync_repo_root() (a hard reset of the shared REPO_ROOT checkout) only
    ever runs on the real, rare path where a phase actually needs
    backfilling -- not on every awaiting_human_approval task on every
    30-minute cron tick, which is the common case once a phase already
    self-reports correctly."""
    return read_commit_plan_lines(plan_file, "origin/master")


def blame_line(plan_file, line_no):
    """Returns the full commit hash that introduced the current content of
    1-indexed line `line_no` of ai-os/<plan_file> in REPO_ROOT's checked-out
    HEAD (the branch sync_repo_root() just reset to origin/master), or None.
    Used to find exactly which commit wrote a phase's current status line,
    regardless of that commit's own message (a worker's commit can mimic
    this script's real commit-message convention -- see module docstring --
    so the message text alone is never trusted; only independent `gh`
    re-verification, done by the caller, decides whether a revert happens)."""
    proc = run(["git", "-C", REPO_ROOT, "blame", "-l", "-L", f"{line_no},{line_no}",
                "HEAD", "--", f"ai-os/{plan_file}"])
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def sync_repo_root():
    status = run(["git", "-C", REPO_ROOT, "status", "--porcelain"])
    if status.stdout.strip():
        return False, "repo root has uncommitted local changes, refusing to touch it"
    fetch_master()
    reset = run(["git", "-C", REPO_ROOT, "reset", "--hard", "origin/master"])
    if reset.returncode != 0:
        return False, f"git reset --hard origin/master failed: {reset.stderr}"
    return True, None


def commit_and_push(plan_file, phase_id, task_id):
    msg = (f"Auto-backfill {phase_id} self-report -- confirmed merged PR for "
           f"{task_id}, software-written per INS auto-continuation self-report fix "
           f"(no worker/human edit)")
    add = run(["git", "-C", REPO_ROOT, "add", f"ai-os/{plan_file}"])
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr}"
    commit = run(["git", "-C", REPO_ROOT, "commit", "-m", msg])
    if commit.returncode != 0:
        if "nothing to commit" in (commit.stdout + commit.stderr):
            return True, "nothing to commit (already up to date)"
        return False, f"git commit failed: {commit.stderr}"
    push = run(["git", "-C", REPO_ROOT, "push", "origin", "master"])
    if push.returncode != 0:
        return False, f"git push failed: {push.stderr}"
    return True, None


# ---------------------------------------------------------------------------
# Per-task orchestration
# ---------------------------------------------------------------------------

def backfill_one(task_id, task_dir=None, repo_override=None, dry_run=False, checkpoint_on_success=False):
    task_dir = task_dir or os.path.join(TASKS_DIR, task_id)
    result = {"task_id": task_id, "changed": False, "skipped_reason": None, "error": None}

    if not os.path.isdir(task_dir):
        result["error"] = f"no such task dir: {task_dir}"
        return result

    repo = repo_override
    branch = f"worker/{task_id}"
    task_yaml_path = os.path.join(task_dir, "task.yaml")
    if not repo and os.path.isfile(task_yaml_path):
        try:
            with open(task_yaml_path) as f:
                tdoc = yaml.safe_load(f) or {}
            repo = tdoc.get("repo")
            branch = tdoc.get("branch") or branch
        except (OSError, yaml.YAMLError):
            pass
    if not repo:
        result["skipped_reason"] = "could not determine repo (no task.yaml, no --repo-override)"
        return result

    plan_file, phase_id = resolve_phase_ref(task_dir)
    if not plan_file:
        result["skipped_reason"] = "no phase reference found (no phase_plan.yaml sidecar, no match in prompt.txt)"
        return result
    result["plan_file"] = plan_file
    result["phase_id"] = phase_id

    merged, pr_number, merged_at, ambiguous = confirm_merge(task_id, repo, branch=branch)
    if not merged:
        if ambiguous:
            result["skipped_reason"] = (
                f"could not verify merge state for {branch} on FChecklist/{repo} "
                f"(gh call failed or returned unparseable output) -- treating as "
                f"not-yet-confirmed, no write attempted")
        else:
            result["skipped_reason"] = f"no confirmed MERGED PR for {branch} on FChecklist/{repo}"
        return result
    result["pr_number"] = pr_number
    result["merged_at"] = merged_at

    fetch_master()
    precheck_lines = read_master_plan_lines(plan_file)
    if precheck_lines is not None:
        pre_start, pre_end = find_phase_block(precheck_lines, phase_id)
        if pre_start is not None and block_self_reports_done(precheck_lines, pre_start, pre_end):
            result["skipped_reason"] = "already self-reports done with an extractable task id -- no change needed"
            return result

    ok, err = sync_repo_root()
    if not ok:
        result["error"] = err
        return result

    plan_path = os.path.join(REPO_ROOT, "ai-os", plan_file)
    if not os.path.isfile(plan_path):
        result["error"] = f"plan file not found at {plan_path}"
        return result
    with open(plan_path) as f:
        lines = f.readlines()

    start, end = find_phase_block(lines, phase_id)
    if start is None:
        result["error"] = f"phase id {phase_id} not found in {plan_file}"
        return result

    if block_self_reports_done(lines, start, end):
        result["skipped_reason"] = "already self-reports done with an extractable task id -- no change needed"
        return result

    evidence = f"{repo} PR #{pr_number} merged {merged_at} (auto-backfilled by backfill_phase_self_report.py)"
    changed = patch_phase_block(lines, start, end, task_id, evidence)
    if not changed:
        result["skipped_reason"] = "no textual change needed"
        return result

    if dry_run:
        result["changed"] = True
        result["dry_run"] = True
        return result

    with open(plan_path, "w") as f:
        f.writelines(lines)

    ok, err = commit_and_push(plan_file, phase_id, task_id)
    if not ok:
        result["error"] = err
        return result
    result["changed"] = True
    result["commit_note"] = err

    if checkpoint_on_success:
        run(["python3", VERIDIAN_TASK, "checkpoint", task_id, "--status", "completed",
             "--note", f"auto-backfilled phase self-report + confirmed merged PR #{pr_number} "
                       f"({repo}); checkpoint updated by backfill_phase_self_report.py --sweep"],
            timeout=30)
        result["checkpointed"] = True

    return result


def sweep(status_filter, dry_run=False, checkpoint_on_success=False):
    results = []
    if not os.path.isdir(TASKS_DIR):
        return results
    for name in sorted(os.listdir(TASKS_DIR)):
        task_dir = os.path.join(TASKS_DIR, name)
        task_yaml_path = os.path.join(task_dir, "task.yaml")
        if not os.path.isfile(task_yaml_path):
            continue
        try:
            with open(task_yaml_path) as f:
                tdoc = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        status = tdoc.get("status")
        if status_filter and status not in status_filter:
            continue
        r = backfill_one(tdoc.get("id", name), task_dir=task_dir, dry_run=dry_run,
                          checkpoint_on_success=checkpoint_on_success and status == "awaiting_human_approval")
        if r.get("changed") or r.get("error"):
            results.append(r)
            log(f"  {r['task_id']}: changed={r.get('changed')} error={r.get('error')}")
    return results


# ---------------------------------------------------------------------------
# --audit-plans: independently re-verify every already-"done" phase against
# a real gh-confirmed merged PR, and revert any that fails re-verification.
# Closes the real 2026-07-26 phase_4/PR#562 gap (see module docstring) where
# a worker's own PR commit wrote a false self-report directly into the
# shared phase-plan file, bypassing backfill_one()/sweep() above entirely.
# ---------------------------------------------------------------------------

def find_all_unverified_done_blocks(lines, plan_file, warnings=None):
    """Scans list_phase_blocks(lines) for every phase that self-reports done
    but whose completed_by_task/target_repo do NOT independently
    confirm_merge() -- i.e. a self-report the file claims but `gh` itself
    does not back up. Returns a list of (phase_id, start, end, task_id,
    repo), one entry per violation found in a single pass over `lines`.

    Deliberately returns ALL violations in one pass (not just the first):
    audit_and_correct_plan_file() needs every violation's blame lookup
    computed against this SAME, unmutated `lines` state (== HEAD, since the
    caller reads it fresh right after sync_repo_root()) before any revert
    is applied -- reverting one violation can delete lines from the file
    (see revert_block_fields), which would shift every later violation's
    line numbers out of sync with a `git blame HEAD` lookup performed
    against the not-yet-committed, still-original HEAD.

    Two cases are deliberately NOT treated as a violation, but ARE recorded
    into `warnings` (if given) so they stay visible rather than silently
    passing as "nothing to check":
      - no target_repo field at all: this phase has zero independent gh
        coverage under this audit, full stop -- flagged rather than
        silently skipped, so a plan file like AUDITOR_ENGINE_PHASE_PLAN
        (no target_repo on any phase) doesn't read as "audited clean" when
        nothing was actually checked.
      - confirm_merge() ambiguous=True: gh itself could not answer (auth/
        rate-limit/network failure, bad target_repo). A confirmed NOT-
        merged is a real violation; an ambiguous gh failure is not -- it
        must skip-with-warning like blame_line/parent-lookup failures
        below, never silently collapse into a revert."""
    violations = []
    for phase_id, start, end in list_phase_blocks(lines):
        if not block_self_reports_done(lines, start, end):
            continue
        completed_raw = _extract_field(lines, start, end, "completed_by_task") or ""
        m = TASK_ID_RE.search(completed_raw)
        if not m:
            continue
        task_id = m.group(0)
        repo = _extract_field(lines, start, end, "target_repo")
        if not repo:
            w = (f"{plan_file}::{phase_id}: self-reports done via completed_by_task={task_id} "
                 f"but has no target_repo field -- cannot independently verify, no real gh "
                 f"coverage for this phase under --audit-plans")
            log(f"  WARNING: {w}")
            if warnings is not None:
                warnings.append(w)
            continue
        merged, _pr_number, _merged_at, ambiguous = confirm_merge(task_id, repo)
        if merged:
            continue
        if ambiguous:
            w = (f"{plan_file}::{phase_id}: self-reports done via completed_by_task={task_id} "
                 f"target_repo={repo}, but `gh pr list --head worker/{task_id}` itself failed or "
                 f"returned unparseable output (transient auth/rate-limit/network failure, or a "
                 f"bad target_repo value) -- could NOT confirm this is a real bypass, skipping "
                 f"without revert; needs re-audit once gh is reachable")
            log(f"  WARNING: {w}")
            if warnings is not None:
                warnings.append(w)
            continue
        violations.append((phase_id, start, end, task_id, repo))
    return violations


def audit_and_correct_plan_file(plan_file, incidents, warnings, dry_run=False):
    """Finds every unverified done-self-report in ai-os/<plan_file> (REPO_
    ROOT must already be synced to origin/master by the caller, and this is
    the first time this run touches this particular file, so its on-disk
    content still equals HEAD) and reverts all of them. Returns True if
    anything changed (or, in dry-run mode, would change).

    All violations are found in ONE pass over the original, unmutated file
    (find_all_unverified_done_blocks), and every violation's git-blame/
    parent-revision lookup is resolved BEFORE any revert is applied to
    `lines`. This is deliberate: reverting a violation can delete lines
    from the block (revert_block_fields deletes completed_by_task/evidence
    entirely when the parent revision never had them), which shifts every
    later violation's line numbers. A prior revision of this function
    resolved+applied one violation at a time in a loop, re-reading the
    mutated on-disk file each iteration but still blaming against `git
    blame HEAD` -- HEAD hadn't advanced yet (the revert wasn't committed),
    so the second violation's blame lookup ran with a stale, too-large line
    number against the original (pre-revert) HEAD tree, occasionally
    landing on the repo's root commit and getting back a boundary-commit
    line (git blame's own '^' prefix) as the "hash" -- which then failed
    the `{hash}^` parent lookup and silently left that violation
    un-reverted. Resolving every lookup up front against a single,
    unmutated `lines` snapshot removes the desync entirely. Reverts are
    then applied bottom-to-top (highest start index first) so an
    earlier-in-the-file violation's own [start, end) range is never
    invalidated by a later-in-the-file violation's line-count change.

    A violation that is found but cannot be auto-corrected (git blame can't
    find the responsible commit, its parent revision can't be read, or --
    the most direct reproduction of the original phase_4/PR#562 incident --
    the phase block doesn't exist at all in the parent revision because it
    was fabricated from scratch with no prior state to revert to) records a
    real incident but does NOT block resolution of the other, independent
    violations in the same file."""
    plan_path = os.path.join(REPO_ROOT, "ai-os", plan_file)
    if not os.path.isfile(plan_path):
        return False
    with open(plan_path) as f:
        lines = f.readlines()

    violations = find_all_unverified_done_blocks(lines, plan_file, warnings=warnings)
    if not violations:
        return False

    resolved = []
    for phase_id, start, end, task_id, repo in violations:
        status_idx, _indent = _find_field_indent(lines, start, end, "status")
        blame_hash = blame_line(plan_file, status_idx + 1)
        if not blame_hash:
            note = (f"{plan_file}::{phase_id}: self-reported done via completed_by_task={task_id} "
                    f"target_repo={repo}, but independent `gh pr list --head worker/{task_id}` "
                    f"re-verification found no real MERGED PR on FChecklist/{repo}, AND `git blame` "
                    f"could not identify the commit that wrote the status line -- could NOT "
                    f"auto-revert. Needs manual review.")
            log(f"  INCIDENT (found, NOT auto-corrected): {note}")
            incidents.append(note)
            continue
        parent_lines = read_commit_plan_lines(plan_file, f"{blame_hash}^")
        if parent_lines is None:
            note = (f"{plan_file}::{phase_id}: self-reported done via completed_by_task={task_id} "
                    f"target_repo={repo}, but independent `gh pr list --head worker/{task_id}` "
                    f"re-verification found no real MERGED PR on FChecklist/{repo}, AND the parent "
                    f"revision {blame_hash}^ of {plan_file} could not be read -- could NOT "
                    f"auto-revert. Needs manual review.")
            log(f"  INCIDENT (found, NOT auto-corrected): {note}")
            incidents.append(note)
            continue
        parent_start, parent_end = find_phase_block(parent_lines, phase_id)
        if parent_start is None:
            note = (f"{plan_file}::{phase_id}: self-reported done via completed_by_task={task_id} "
                    f"target_repo={repo}, but independent `gh pr list --head worker/{task_id}` "
                    f"re-verification found no real MERGED PR on FChecklist/{repo}, AND {phase_id} "
                    f"does not exist at all in parent revision {blame_hash}^ of {plan_file} -- this "
                    f"phase block was fabricated from scratch with no prior state to revert to "
                    f"(the most direct reproduction of the 2026-07-26 phase_4/PR#562 incident). "
                    f"Could NOT auto-revert. Needs manual review.")
            log(f"  INCIDENT (found, NOT auto-corrected): {note}")
            incidents.append(note)
            continue

        note = (f"{plan_file}::{phase_id}: self-reported done via completed_by_task={task_id} "
                f"target_repo={repo}, but independent `gh pr list --head worker/{task_id}` "
                f"re-verification found no real MERGED PR on FChecklist/{repo}. Reverted "
                f"status/completed_by_task/evidence to their state immediately before "
                f"commit {blame_hash[:7]} (git blame on the status line).")
        log(f"  INCIDENT: {note}")
        incidents.append(note)
        resolved.append((start, end, parent_lines, parent_start, parent_end))

    if not resolved:
        return False
    if dry_run:
        return True

    for start, end, parent_lines, parent_start, parent_end in sorted(resolved, key=lambda r: r[0], reverse=True):
        revert_block_fields(lines, start, end, parent_lines, parent_start, parent_end)
    with open(plan_path, "w") as f:
        f.writelines(lines)

    return True


def commit_and_push_audit(plan_files, incidents):
    """Commits+pushes whatever audit_and_correct_plan_file() found. `incidents`
    can be non-empty even when `plan_files` is empty -- a violation that was
    found but could NOT be auto-corrected (blame/parent-lookup failure, or a
    fabricated block with no parent to revert to) never touches the working
    tree, so there is nothing to `git add`. That case still gets a real
    commit (--allow-empty) so the incident text is recorded permanently in
    master's history instead of only ever appearing in this invocation's own
    stdout/stderr and then vanishing once the process exits -- the same
    persistent-record convention already used for real reverts, just with an
    empty diff."""
    if not plan_files and not incidents:
        return True, "no violations found"
    reverted = bool(plan_files)
    title = ("Automated self-report audit: reverted unverified phase self-report(s)"
             if reverted else
             "Automated self-report audit: found unverified phase self-report(s), "
             "could NOT auto-revert (see incident(s) below)")
    msg_lines = [
        title,
        "",
        "backfill_phase_self_report.py --audit-plans independently re-confirmed every",
        "phase currently self-reporting done against a real `gh pr` MERGED state (never",
        "trusting the file -- the same standard already applied to new self-reports) and",
        f"found {len(incidents)} that do not hold up:",
        "",
    ] + [f"- {n}" for n in incidents] + [
        "",
        "Closes the gap behind the 2026-07-26 VERIDIAN_ARCHITECTURE_V2 phase_4/PR#562",
        "incident (commit 4611924): a worker's own PR commit wrote a false self-report",
        "directly into a phase-plan file, bypassing this script's real merged-PR check",
        "entirely. See ai-os/OWNER_DIRECTIVES/MEMORY_OWNER_AI.yaml incidents[] id",
        "2026-07-26_worker_bypassed_self_report_backfill.",
    ]
    msg = "\n".join(msg_lines)
    for pf in plan_files:
        add = run(["git", "-C", REPO_ROOT, "add", f"ai-os/{pf}"])
        if add.returncode != 0:
            return False, f"git add failed for {pf}: {add.stderr}"
    commit_cmd = ["git", "-C", REPO_ROOT, "commit"]
    if not reverted:
        commit_cmd.append("--allow-empty")
    commit_cmd += ["-m", msg]
    commit = run(commit_cmd)
    if commit.returncode != 0:
        if "nothing to commit" in (commit.stdout + commit.stderr):
            return True, "nothing to commit (already up to date)"
        return False, f"git commit failed: {commit.stderr}"
    push = run(["git", "-C", REPO_ROOT, "push", "origin", "master"])
    if push.returncode != 0:
        return False, f"git push failed: {push.stderr}"
    return True, None


def audit_plans(dry_run=False):
    result = {"audited": [], "incidents": [], "warnings": [], "changed": False, "error": None}
    ok, err = sync_repo_root()
    if not ok:
        result["error"] = err
        return result

    ai_os_dir = os.path.join(REPO_ROOT, "ai-os")
    if not os.path.isdir(ai_os_dir):
        return result
    plan_files = sorted(f for f in os.listdir(ai_os_dir) if PLAN_GLOB_RE.match(f))

    incidents = []
    warnings = []
    changed_files = []
    for pf in plan_files:
        result["audited"].append(pf)
        if audit_and_correct_plan_file(pf, incidents, warnings, dry_run=dry_run):
            changed_files.append(pf)
    result["incidents"] = incidents
    result["warnings"] = warnings

    if dry_run:
        if changed_files:
            result["changed"] = True
            result["dry_run"] = True
    elif changed_files or incidents:
        # incidents can be non-empty with changed_files empty: a violation
        # was found but could not be auto-corrected. That must still be
        # persisted (commit_and_push_audit --allow-empty's when there's
        # nothing to revert) rather than silently vanishing once this
        # process exits -- see commit_and_push_audit's own docstring.
        ok, err = commit_and_push_audit(changed_files, incidents)
        if not ok:
            result["error"] = err
            return result
        result["changed"] = True
        result["commit_note"] = err
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task-id")
    p.add_argument("--repo-override")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sweep", action="store_true",
                    help="Scan every task dir (completed + awaiting_human_approval) for a "
                         "confirmed merged PR whose phase-plan self-report is still missing.")
    p.add_argument("--checkpoint-on-success", action="store_true",
                    help="When --sweep finds a merged awaiting_human_approval (tier2) task, "
                         "also checkpoint it completed -- nothing else ever revisits a tier2 "
                         "task after a human merges its PR out-of-band.")
    p.add_argument("--audit-plans", action="store_true",
                    help="Independently re-verify every phase currently self-reporting done "
                         "against a real gh-confirmed merged PR, and revert (with a logged "
                         "incident) any that fails re-verification. Catches a worker's own PR "
                         "commit writing a false self-report directly into a phase-plan file, "
                         "bypassing --task-id/--sweep above entirely (real incident: "
                         "2026-07-26 VERIDIAN_ARCHITECTURE_V2 phase_4 / PR #562).")
    args = p.parse_args()

    if args.audit_plans:
        result = audit_plans(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(1 if result.get("error") else 0)

    if args.sweep:
        results = sweep({"completed", "awaiting_human_approval"}, dry_run=args.dry_run,
                         checkpoint_on_success=args.checkpoint_on_success)
        print(json.dumps({"sweep": True, "dry_run": args.dry_run, "results": results}, indent=2, default=str))
        return

    if not args.task_id:
        p.error("--task-id is required unless --sweep or --audit-plans is passed")

    result = backfill_one(args.task_id, repo_override=args.repo_override, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
