#!/usr/bin/env python3
"""
auto_phase_continuation.py -- INS-20260724-113032-8032 mechanism 2 (the
automatic phase-continuation script).

Root cause this closes: 5 real initiatives this session (self-sustaining-
system-engine, 20-Engines/10-Gateways, Auditor Engine, Testing Engine,
Terminology Standardization) each got a real, multi-phase
*_PHASE_PLAN_2026-07-24.yaml, shipped a real Phase 1, and then nothing ever
looked at "Phase 1 merged, what's next" and dispatched Phase 2. Phased
dispatch stalled at Phase 1 for all 5 not because Phase 2+ wasn't planned
(it was, in detail, in each plan file's own phases[] block) but because
nothing was wired to continue it automatically.

What this script does, every time it runs (cron, every 30 min, via
run-logged.sh -- see OWNER_DECISIONS_NEEDED_2026-07-23.yaml id
auto-phase-continuation-crontab-addition):

  1. Scans for every *_PHASE_PLAN_2026-07-24.yaml file under BOTH
     /opt/veridian/ai-os/ and /opt/veridian/repos/claude-control/ai-os/.
     Both locations are read and merged, never just one -- this session
     has confirmed live drift between them (TESTING_ENGINE_PHASE_PLAN's
     live copy has a completed-Phase-1 status_detail block the repo copy
     lacks; TERMINOLOGY_STANDARDIZATION_PHASE_PLAN_2026-07-24.yaml exists
     only in the repo copy, not live) -- reading only one directory would
     silently miss real information the other has.
  2. Normalizes phases[] across this session's two real schemas: a string
     `id:` field (20-Engines/Testing/Terminology) and a bare integer
     `phase:` field (Auditor Engine) -- both become "phase-<N>" internally.
  3. For each phase claiming a done-ish status, mechanically extracts the
     task id that produced it (produced_by_task / completed_by_task /
     evidence.task / status_detail.produced_by_task / a regex fallback)
     and confirms it via `gh pr list --head worker/<task_id> --state
     merged` -- the phase file's own self-reported status string is a
     hint, never the proof (see is_phase_done()).
  4. Walks phases in file order; the next candidate is the first phase
     that is not done, whose depends_on are all confirmed done, and whose
     external_depends_on (if any) are also confirmed done by the SAME
     merged-PR mechanism against whichever other phase plan they name --
     a reference this script cannot mechanically resolve is treated as
     UNMET, never assumed satisfied (see external_deps_satisfied()).
  5. If that candidate does not already have a dispatched task (checked
     via already_dispatched()'s phase-number + keyword heuristic against
     `veridian-task.py status` + `gh pr list` output), generates a real
     OBJECTIVE/SCOPE/KNOWN_CONTEXT/SUCCESS_CRITERIA/EXPECTED_OUTPUT/
     CONSTRAINTS/COMPLEXITY_TIER prompt -- content drawn directly from
     that phase's own objective/scope/name fields, not invented -- and
     calls task-gateway.py submit then start for it for real.

--dry-run prints what would be dispatched for every initiative without
calling submit/start. Real (non-dry-run) runs dispatch every ready,
not-yet-dispatched phase found -- one per initiative per run, since each
plan's own depends_on graph only ever has one phase ready at a time today.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import yaml

import backfill_phase_self_report

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
TASK_GATEWAY = f"{SCRIPTS}/task-gateway.py"
VERIDIAN_TASK = f"{SCRIPTS}/veridian-task.py"
DEFAULT_REPO = "claude-control"
GH_REPO = "FChecklist/claude-control"

PLAN_DIRS = [
    "/opt/veridian/ai-os",
    "/opt/veridian/repos/claude-control/ai-os",
]
PLAN_GLOBS = ["*_PHASE_PLAN_*.yaml", "*_IMPLEMENTATION_PLAN_*.yaml"]  # PLAN_GLOB was hardcoded to
# 2026-07-24 only; widened 2026-07-25 (Wiring Engine Phase 0, task-20260725-032718) so a newly-dated
# plan (e.g. WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml) is discovered without a further hardcoded-date
# edit every time a new initiative starts a day later. Widened again same day (Security Audit
# Evaluation, task-20260725-041130) from a single PLAN_GLOB string to a PLAN_GLOBS list so a plan
# named *_IMPLEMENTATION_PLAN_*.yaml (e.g. SECURITY_AUDIT_IMPLEMENTATION_PLAN_2026-07-25.yaml -- name
# fixed by that task's own spec, not renameable to fit the narrower pattern) is discovered too. Still
# matches every existing *_PHASE_PLAN_2026-07-24.yaml file unchanged.

TASK_ID_RE = re.compile(r"task-20\d{6}-[a-z0-9-]+")


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Plan discovery + normalization
# ---------------------------------------------------------------------------

def discover_plan_paths():
    by_name = {}
    for d in PLAN_DIRS:
        if not os.path.isdir(d):
            continue
        for plan_glob in PLAN_GLOBS:
            for path in glob.glob(os.path.join(d, plan_glob)):
                by_name.setdefault(os.path.basename(path), []).append(path)
    return by_name


def normalize_phase_id(raw):
    if isinstance(raw, dict) and "id" in raw:
        return str(raw["id"])
    if isinstance(raw, dict) and "phase" in raw:
        return f"phase-{raw['phase']}"
    return None


def normalize_dep_ref(dep):
    if isinstance(dep, int):
        return f"phase-{dep}"
    return str(dep)


def load_phases_from_doc(doc):
    """Returns {phase_id: phase_raw_dict} for one parsed plan document."""
    phases = doc.get("phases")
    if not isinstance(phases, list):
        return {}
    out = {}
    for p in phases:
        if not isinstance(p, dict):
            continue
        pid = normalize_phase_id(p)
        if not pid:
            continue
        out[pid] = p
    return out


def merge_phase_versions(name, paths):
    """Merges 1-2 on-disk copies of the same plan filename. Per phase_id,
    prefers whichever copy's raw dict looks more complete: if either copy's
    own status string is done-ish and the other isn't, the done-ish one
    wins (closing exactly the live-vs-repo drift this session already
    found -- a phase genuinely completed live must not be re-attempted
    just because the repo copy hasn't caught up, and vice versa)."""
    docs = []
    for path in paths:
        try:
            with open(path) as f:
                docs.append((path, yaml.safe_load(f) or {}))
        except (OSError, yaml.YAMLError) as e:
            log(f"WARNING: could not parse {path}: {e}")
    if not docs:
        return None, {}

    merged_meta = docs[0][1].get("meta", {})
    per_doc_phases = [load_phases_from_doc(doc) for _, doc in docs]
    all_ids = []
    for pd in per_doc_phases:
        for pid in pd:
            if pid not in all_ids:
                all_ids.append(pid)

    merged = {}
    for pid in all_ids:
        candidates = [pd[pid] for pd in per_doc_phases if pid in pd]
        if len(candidates) == 1:
            merged[pid] = candidates[0]
            continue
        done_candidates = [c for c in candidates if _self_reports_done(c)]
        merged[pid] = done_candidates[0] if done_candidates else candidates[-1]

    return merged_meta, merged


def _self_reports_done(phase_raw):
    status = str(phase_raw.get("status") or "").lower()
    return "done" in status or "complete" in status or status == "this_task"


# ---------------------------------------------------------------------------
# Real merged-PR cross-reference (never trust self-reported status alone)
# ---------------------------------------------------------------------------

def extract_produced_task_id(phase_raw, fallback_task_id=None):
    for key in ("produced_by_task", "completed_by_task"):
        v = phase_raw.get(key)
        if isinstance(v, str):
            m = TASK_ID_RE.search(v)
            if m:
                return m.group(0)
    for key in ("status_detail", "evidence"):
        v = phase_raw.get(key)
        if isinstance(v, dict):
            for subkey in ("produced_by_task", "task", "completed_by_task"):
                sv = v.get(subkey)
                if isinstance(sv, str):
                    m = TASK_ID_RE.search(sv)
                    if m:
                        return m.group(0)
    try:
        dumped = yaml.safe_dump(phase_raw)
    except Exception:
        dumped = str(phase_raw)
    m = TASK_ID_RE.search(dumped)
    if m:
        return m.group(0)
    return fallback_task_id


_PR_MERGE_CACHE = {}


def gh_pr_merged_for_task(task_id, repo=GH_REPO):
    if task_id in _PR_MERGE_CACHE:
        return _PR_MERGE_CACHE[task_id]
    proc = run(["gh", "pr", "list", "--repo", repo, "--head", f"worker/{task_id}",
                "--state", "all", "--json", "state,number"])
    result = (False, None)
    if proc.returncode == 0:
        try:
            rows = json.loads(proc.stdout)
            for r in rows:
                if r.get("state") == "MERGED":
                    result = (True, r.get("number"))
                    break
        except json.JSONDecodeError:
            pass
    _PR_MERGE_CACHE[task_id] = result
    return result


_MASTER_PLAN_CACHE = {}


def master_shows_phase_done(plan_filename, phase_id, repo_path="/opt/veridian/repos/claude-control"):
    """Fallback proof-of-merge, independent of any one PR's own state:
    reads ai-os/<plan_filename> as actually committed on the master branch
    (not the possibly-uncommitted live/task-workspace copy this script
    also reads) and checks whether THAT copy shows the phase done. Needed
    because a phase's originating PR can itself show CLOSED (not MERGED)
    while its content still reached master through a separate recovery
    commit -- confirmed real, live case: Auditor Engine's Phase 0 PR #12
    is CLOSED/never merged, but commit 6797aae ("Session audit: recover
    Auditor Engine phase-0 files missing from master (PR #12 gap)")
    landed the same content on master separately. Checking master's own
    ref content directly is a strictly stronger real-merge proof than any
    single PR's state, since it is the actual thing "confirmed merged"
    is meant to establish."""
    if plan_filename in _MASTER_PLAN_CACHE:
        doc = _MASTER_PLAN_CACHE[plan_filename]
    else:
        proc = run(["git", "-C", repo_path, "show", f"master:ai-os/{plan_filename}"])
        doc = None
        if proc.returncode == 0:
            try:
                doc = yaml.safe_load(proc.stdout) or {}
            except yaml.YAMLError:
                doc = None
        _MASTER_PLAN_CACHE[plan_filename] = doc
    if not doc:
        return False
    phases = load_phases_from_doc(doc)
    praw = phases.get(phase_id)
    return bool(praw and _self_reports_done(praw))


def is_phase_done(phase_raw, doc_meta_task_id=None, plan_filename=None):
    """Real done-check: self-reported status is a necessary but NOT
    sufficient signal. Primary cross-reference is a real MERGED PR on
    GitHub for the linked task id; if that lookup fails (e.g. the PR was
    closed unmerged but the content landed via a separate recovery
    commit -- see master_shows_phase_done()), fall back to checking
    master's own committed copy of the plan file directly. Only if BOTH
    checks fail is the phase treated as not actually done. Returns
    (done: bool, task_id, detail)."""
    if not _self_reports_done(phase_raw):
        return False, None, "phase does not self-report done/complete"
    task_id = extract_produced_task_id(phase_raw, fallback_task_id=doc_meta_task_id)
    if not task_id:
        return False, None, "self-reports done but no linked task id could be extracted"
    merged, pr_number = gh_pr_merged_for_task(task_id)
    if merged:
        return True, task_id, f"confirmed via merged PR #{pr_number}"
    if plan_filename:
        # phase_id is looked up by the caller via load_phases_from_doc's
        # keys, but this function only has the raw phase dict -- callers
        # pass plan_filename and this re-derives phase_id from phase_raw.
        phase_id = normalize_phase_id(phase_raw)
        if phase_id and master_shows_phase_done(plan_filename, phase_id):
            return True, task_id, ("linked task's PR is not MERGED, but master's committed plan-file "
                                    "copy independently shows this phase done (recovery-commit fallback)")
    return False, task_id, f"linked task {task_id} has no MERGED PR on {GH_REPO}, and no master fallback confirmed it"


# ---------------------------------------------------------------------------
# Next-phase selection
# ---------------------------------------------------------------------------

def build_done_map(phases, doc_meta_task_id=None, plan_filename=None):
    done_map = {}
    for pid, praw in phases.items():
        done, task_id, detail = is_phase_done(praw, doc_meta_task_id=doc_meta_task_id, plan_filename=plan_filename)
        done_map[pid] = {"done": done, "task_id": task_id, "detail": detail}
    return done_map


def external_deps_satisfied(phase_raw, all_done_maps):
    ext = phase_raw.get("external_depends_on")
    if not ext:
        return True, None
    file_re = re.compile(r"([A-Z0-9_]+_(?:PHASE_PLAN|IMPLEMENTATION_PLAN)_[0-9]{4}-[0-9]{2}-[0-9]{2}\.yaml)")
    num_re = re.compile(r"[Pp]hase[ _]?(\d+)")
    for entry in ext:
        text = entry if isinstance(entry, str) else str(entry)
        file_m = file_re.search(text)
        num_m = num_re.search(text)
        if not file_m or not num_m:
            return False, f"external dependency not mechanically resolvable: {text[:120]!r}"
        ext_file, ext_num = file_m.group(1), num_m.group(1)
        target_done_map = all_done_maps.get(ext_file)
        if target_done_map is None:
            return False, f"referenced plan {ext_file} not found among scanned plans"
        candidates = {f"phase-{ext_num}", f"phase_{ext_num}"}
        matched = [pid for pid in target_done_map if pid in candidates or pid.startswith(f"phase_{ext_num}_")]
        if not matched or not any(target_done_map[pid]["done"] for pid in matched):
            return False, f"{ext_file} phase {ext_num} not yet confirmed done"
    return True, None


def phase_number_token(phase_id):
    m = re.search(r"(\d+)", phase_id)
    return f"phase{m.group(1)}" if m else None


def phase_keyword(phase_id, name):
    words = re.findall(r"[a-z]+", (name or phase_id).lower())
    stop = {"phase", "the", "and", "for", "with", "this"}
    sig = [w for w in words if w not in stop and len(w) > 4]
    return sig[0] if sig else None


_KNOWN_TASK_ITEMS_CACHE = None


def known_task_items():
    """Returns a list of independent strings, ONE PER TASK/PR -- each
    item is that single task's id + title + branch, lowercased. Kept as
    separate items (never concatenated into one blob) specifically so
    already_dispatched()'s token+keyword check only ever matches within a
    single real task's own identity, not across two unrelated tasks that
    happen to each contribute one of the two required words."""
    global _KNOWN_TASK_ITEMS_CACHE
    if _KNOWN_TASK_ITEMS_CACHE is not None:
        return _KNOWN_TASK_ITEMS_CACHE
    items = []
    status_proc = run(["python3", VERIDIAN_TASK, "status"], timeout=30)
    if status_proc.returncode == 0:
        current = None
        for line in status_proc.stdout.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("==="):
                continue
            if stripped.startswith("last checkpoint:"):
                continue
            # a task's own line: "  <task_id>  [<repo>:<branch>]  <title>"
            current = stripped.lower()
            items.append(current)
    gh_proc = run(["gh", "pr", "list", "--repo", GH_REPO, "--state", "all",
                   "--limit", "500", "--json", "headRefName,title"], timeout=60)
    if gh_proc.returncode == 0:
        try:
            for row in json.loads(gh_proc.stdout):
                items.append(f"{row.get('headRefName', '')} {row.get('title', '')}".lower())
        except json.JSONDecodeError:
            pass
    _KNOWN_TASK_ITEMS_CACHE = items
    return items


def already_dispatched(phase_id, name):
    """Heuristic (documented, not exact): a phase counts as already
    dispatched if some SINGLE existing task/PR's own id+title+branch
    contains BOTH its numeric token ("phase2") AND its first significant
    (>4 char) keyword from its own name/id. Requiring both, on the SAME
    item, avoids the false positive of a DIFFERENT initiative's
    same-numbered phase (e.g. self-sustaining-system-engine's own
    "phase2" tasks do not also contain 20-Engines phase_2's keyword
    "policy", so they don't match) and avoids matching two unrelated
    tasks that each independently contribute one of the two words."""
    token = phase_number_token(phase_id)
    if not token:
        return False
    keyword = phase_keyword(phase_id, name)
    for item in known_task_items():
        if token in item and (not keyword or keyword in item):
            return True
    return False


def find_next_phase(meta, phases, done_map, all_done_maps, order):
    for pid in order:
        praw = phases[pid]
        if praw.get("external_plan"):
            continue  # pointer to another plan, not a real dispatchable phase
        if done_map[pid]["done"]:
            continue
        deps = [normalize_dep_ref(d) for d in (praw.get("depends_on") or [])]
        if not all(done_map.get(d, {}).get("done") for d in deps):
            continue
        ext_ok, ext_reason = external_deps_satisfied(praw, all_done_maps)
        if not ext_ok:
            log(f"  skipping {pid}: {ext_reason}")
            continue
        return pid, praw
    return None, None


# ---------------------------------------------------------------------------
# Prompt generation (content drawn from the phase itself, not invented)
# ---------------------------------------------------------------------------

def slugify(text, max_words=5):
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    stop = {"and", "the", "for", "with", "phase", "of", "a", "an"}
    words = [w for w in words if w not in stop][:max_words]
    return "-".join(words) if words else "phase"


def scope_text(praw):
    scope = praw.get("scope")
    if isinstance(scope, list):
        return "\n".join(f"- {s}" for s in scope)
    if isinstance(scope, str) and scope.strip():
        return scope.strip()
    return praw.get("objective") or praw.get("name") or "See the phase plan file's own phases[] entry."


def build_prompt(initiative_name, plan_filename, phase_id, praw, dep_task_ids):
    objective = (praw.get("objective") or praw.get("name") or "").strip()
    if not objective:
        objective = f"Execute {phase_id} of {plan_filename} for the {initiative_name} initiative."
    name = praw.get("name") or objective
    scope = scope_text(praw)
    deps_note = (", ".join(dep_task_ids) if dep_task_ids else "none (entry phase)")

    phase_marker = f'"id: {phase_id}"' if not phase_id.startswith("phase-") else f'"phase: {phase_id.split("-")[1]}"'

    prompt = f"""## OBJECTIVE
{objective}

## SCOPE
{scope}

## KNOWN_CONTEXT
This is {phase_id} of ai-os/{plan_filename}, part of the {initiative_name} initiative. Read that file in
full before starting -- this OBJECTIVE/SCOPE text is copied verbatim from its own phases[] entry for
{phase_id}, not invented here. Its depends_on phase(s) are confirmed done via real merged PR(s) for task(s):
{deps_note}. This task was auto-dispatched by scripts/auto_phase_continuation.py per instruction
INS-20260724-113032-8032, which exists specifically because this same initiative's phased dispatch had
previously stalled after an earlier phase with nothing to continue it automatically -- do not stop after
this phase either without updating the plan file per EXPECTED_OUTPUT below, so the next cron tick can find
real next-phase work if one exists.

## SUCCESS_CRITERIA
- `python3 scripts/superboss-register.py query-knowledge "{phase_id}" --tag domain:{slugify(initiative_name)}` returns at least one registered row for this phase's real deliverable once you register it (same convention this plan file's own `registration:` block or prior phases already use).
- `grep -A3 {phase_marker} ai-os/{plan_filename}` shows this phase's own status updated away from its current not-done value (e.g. to `status: done`), committed as part of this task's real diff -- not hand-edited prose, produced by whatever script/process you use to close this phase out.

## EXPECTED_OUTPUT
Real, committed and pushed code (PR on claude-control) implementing this phase's scope above. Update
ai-os/{plan_filename}'s own phases[] entry for {phase_id} in place (same status/status_detail or
evidence convention the plan file's already-done phases use) to record what shipped and real evidence, so
a future run of auto_phase_continuation.py can correctly detect this phase as done and move on to whatever
depends on it.

## CONSTRAINTS
Do not re-build or re-litigate anything already marked done in an earlier phase of this same plan file.
Do not duplicate an existing engine/mechanism -- search first, per this session's standing no-duplication
rule (see {plan_filename}'s own inventory/precedent notes if present). Any crontab change needs an approved
entry in ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml plus a matching ai-os/CRONTAB_APPROVED_SNAPSHOT.txt
update, per the Owner's existing blanket approval -- do not add cron entries without that citation.

## COMPLEXITY_TIER
judgment
"""
    _m = re.search(r'(\d+)', phase_id)
    _num = _m.group(1) if _m else 'x'
    title = f"phase{_num}-{slugify(name, max_words=6)}"
    return prompt, title


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def write_phase_plan_sidecar(task_id, plan_filename, phase_id, initiative_name):
    """Structured pointer from a dispatched task back to the exact
    phase-plan entry it was generated from -- mirrors the existing
    module_scope.yaml sidecar convention (task_dir/module_scope.yaml,
    read by supervisor-entrypoint.sh's own scope-check). Lets
    backfill_phase_self_report.py resolve plan_file+phase_id reliably
    without regex-parsing prompt.txt (kept as a fallback for tasks
    dispatched before this sidecar existed)."""
    task_dir = os.path.join("/opt/veridian/ai-os/tasks", task_id)
    try:
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "phase_plan.yaml"), "w") as f:
            yaml.safe_dump({"plan_file": plan_filename, "phase_id": phase_id,
                             "initiative": initiative_name}, f)
    except OSError as e:
        log(f"  WARNING: could not write phase_plan.yaml sidecar for {task_id}: {e}")


def dispatch(prompt_text, title, repo=DEFAULT_REPO, plan_filename=None, phase_id=None, initiative_name=None):
    prompt_path = f"/tmp/auto_phase_continuation_{title}.txt"
    with open(prompt_path, "w") as f:
        f.write(prompt_text)

    submit_proc = run(["python3", TASK_GATEWAY, "submit", "--text", prompt_text,
                        "--source", "ai_agent", "--session-id", "auto_phase_continuation"],
                       timeout=90)
    if submit_proc.returncode != 0:
        return {"dispatched": False, "step": "submit", "stdout": submit_proc.stdout, "stderr": submit_proc.stderr}
    try:
        submit_result = json.loads(submit_proc.stdout)
    except json.JSONDecodeError:
        return {"dispatched": False, "step": "submit", "stdout": submit_proc.stdout, "stderr": submit_proc.stderr}
    instruction_id = submit_result.get("instruction_id")

    start_proc = run(["python3", TASK_GATEWAY, "start", "--prompt-file", prompt_path,
                       "--title", title, "--repo", repo, "--instruction-id", str(instruction_id)],
                      timeout=120)
    if start_proc.returncode != 0:
        failure = {"dispatched": False, "step": "start", "instruction_id": instruction_id,
                   "stdout": start_proc.stdout, "stderr": start_proc.stderr}
        # already_dispatched() only recognizes real tasks/PRs, and a validation
        # rejection here creates neither -- so this exact phase will be
        # regenerated and re-rejected identically on every future cron tick
        # with no other signal (the JSON below is otherwise only visible by
        # reading this run's full report). Surface it plainly so a human
        # scanning the next status check (or stderr/cron log) sees it fast.
        try:
            start_error = json.loads(start_proc.stdout)
        except json.JSONDecodeError:
            start_error = {}
        if "tight_task_validation.py rejected" in str(start_error.get("error", "")):
            failure["note"] = (
                f"REJECTED BY tight_task_validation.py: {start_error.get('reason')} -- this phase's prompt "
                "is regenerated deterministically from its own phase-plan content, so it will keep failing "
                "the SAME way on every future cron tick until a human edits the phase-plan entry or the "
                "validator; it is not silently marked done or skipped."
            )
            log(f"  REJECTED by tight_task_validation.py for {title}: {start_error.get('reason')}")
        return failure
    try:
        start_result = json.loads(start_proc.stdout)
    except json.JSONDecodeError:
        return {"dispatched": False, "step": "start", "stdout": start_proc.stdout, "stderr": start_proc.stderr}

    dispatched_task_id = start_result.get("task_id")
    if dispatched_task_id and plan_filename and phase_id:
        write_phase_plan_sidecar(dispatched_task_id, plan_filename, phase_id, initiative_name)

    return {"dispatched": True, "instruction_id": instruction_id, "task_id": dispatched_task_id,
            "systemd_active": start_result.get("systemd_active")}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=None,
                         help="Cap the number of real dispatches this run performs (dry-run entries are "
                              "never capped). Omit for the recurring cron behavior (dispatch every ready, "
                              "not-yet-dispatched phase found); pass a small number for a controlled manual "
                              "run that leaves the remainder for the next cron tick.")
    args = parser.parse_args()
    dispatched_count = 0

    # Root cause 1, tier2 half (real incident: VERIDIAN_ARCHITECTURE_V2 phase_1,
    # compliance-tracker PR #559 -- tier2, merged by a human out-of-band, but
    # nothing ever revisited that task afterward: its own checkpoint stayed
    # `awaiting_human_approval` and its phase-plan self-report stayed missing
    # forever). supervisor-entrypoint.sh only ever backfills its OWN tier1
    # auto-merges since it's the one confirming those merges in real time; a
    # tier2 PR's merge happens after this script's own dispatch call already
    # returned, so this cron tick (every 30 min) is what re-checks every
    # outstanding tier2 task for a real merge that's since landed.
    sweep_result = backfill_phase_self_report.sweep(
        {"awaiting_human_approval"}, dry_run=args.dry_run, checkpoint_on_success=True)
    if sweep_result:
        log(f"tier2 self-report sweep: backfilled {len(sweep_result)} task(s)")

    by_name = discover_plan_paths()
    if not by_name:
        print(json.dumps({"error": "no plan files matching PLAN_GLOBS found in any scanned dir",
                           "scanned_dirs": PLAN_DIRS}))
        sys.exit(1)

    all_docs = {}
    for name, paths in sorted(by_name.items()):
        meta, phases = merge_phase_versions(name, paths)
        all_docs[name] = {"meta": meta or {}, "phases": phases, "paths": paths}

    all_done_maps = {}
    for name, doc in all_docs.items():
        meta_task_id = None
        m = TASK_ID_RE.search(str((doc["meta"] or {}).get("produced_by_task") or ""))
        if m:
            meta_task_id = m.group(0)
        all_done_maps[name] = build_done_map(doc["phases"], doc_meta_task_id=meta_task_id, plan_filename=name)

    report = []
    for name, doc in all_docs.items():
        phases = doc["phases"]
        done_map = all_done_maps[name]
        order = list(phases.keys())
        initiative_name = (doc["meta"] or {}).get("title", name.replace("_PHASE_PLAN_2026-07-24.yaml", ""))

        next_id, next_praw = find_next_phase(doc["meta"], phases, done_map, all_done_maps, order)
        entry = {
            "plan_file": name,
            "found_in": doc["paths"],
            "initiative": initiative_name,
            "done_phases": [pid for pid, d in done_map.items() if d["done"]],
        }
        if next_id is None:
            entry["next_phase"] = None
            entry["note"] = "no dispatchable next phase (all done, or all remaining blocked/external)"
            report.append(entry)
            continue

        entry["next_phase"] = next_id
        dep_task_ids = [done_map[normalize_dep_ref(d)]["task_id"]
                        for d in (next_praw.get("depends_on") or [])
                        if normalize_dep_ref(d) in done_map and done_map[normalize_dep_ref(d)]["task_id"]]

        if already_dispatched(next_id, next_praw.get("name")):
            entry["already_dispatched"] = True
            report.append(entry)
            continue
        entry["already_dispatched"] = False

        prompt_text, title = build_prompt(initiative_name, name, next_id, next_praw, dep_task_ids)
        entry["generated_title"] = title

        if args.dry_run:
            entry["would_dispatch"] = True
        elif args.limit is not None and dispatched_count >= args.limit:
            entry["deferred_reason"] = f"--limit {args.limit} reached this run; left for the next cron tick"
        else:
            phase_repo = next_praw.get("target_repo") or args.repo
            result = dispatch(prompt_text, title, repo=phase_repo,
                               plan_filename=name, phase_id=next_id, initiative_name=initiative_name)
            entry["dispatch_result"] = result
            if result.get("dispatched"):
                dispatched_count += 1
            elif result.get("note"):
                # Promoted out of the nested dispatch_result blob so a human
                # (or a future automated status check) sees a rejected
                # auto-dispatch without having to dig into stdout/stderr --
                # this phase will otherwise re-fail identically every tick.
                entry["note"] = result["note"]

        report.append(entry)

    print(json.dumps({"dry_run": args.dry_run, "tier2_self_report_sweep": sweep_result,
                       "initiatives": report}, indent=2, default=str))


if __name__ == "__main__":
    main()
