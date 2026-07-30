#!/usr/bin/env python3
"""
system-sync.py -- single unified sync/audit mechanism for "one server, one
system." Built 2026-07-20 per Owner directive covering TASKS 2-6 of the
6-part 2026-07-20 systems-design mandate:
  TASK 2: CONSTITUTION.yaml staleness detection
  TASK 3/4: integration -- files/registries getting missed, drift between
            live server state and git-tracked mirrors
  TASK 5: a script referenced FROM MASTER_INDEX.yaml that syncs the whole
          system on demand (this file, invoked directly)
  TASK 6: the same running on a schedule (wired into cron via run-logged.sh)

Design principle: every check here is MECHANICAL (file existence, mtime/git-
log comparison, path diffing) -- zero AI judgment embedded in the detection
logic itself. Judgment calls (what should an updated CONSTITUTION.yaml
sentence say, is a newly-found file actually important) are left to whoever
reads the findings -- this script's job is to make sure nothing gets missed
BY OMISSION, not to auto-write documentation prose.

Three checks, each independent and individually skippable:
  1. mirror_drift_check()      -- live vs git-tracked-repo-copy drift for
                                   scripts + MASTER_INDEX.yaml. SAFE to
                                   auto-fix (pure file copy, staged not
                                   committed -- a human/AI still verifies
                                   and commits, same discipline used
                                   manually all session).
  2. constitution_staleness_check() -- extracts every backtick-quoted
                                   src/lib/*.ts path from CONSTITUTION.yaml's
                                   `mechanism:` fields (same claim-shape
                                   claim-verification.ts already checks for
                                   AI output, reused here for the doc
                                   itself), compares each file's last git-
                                   commit date against CONSTITUTION.yaml's
                                   own last-commit date. REPORT ONLY -- never
                                   auto-edits the doc.
  3. unindexed_files_check()   -- walks canonical_dirs.ops_scripts and
                                   .ai_os_governance_repo_copy (same
                                   exclusion_rules MASTER_INDEX.yaml already
                                   declares), diffs against
                                   file_inventory's declared lists. REPORT
                                   ONLY.
  4. documentation_generation_check() -- added 2026-07-23 (governance item 60,
                                   "automatic documentation generation for
                                   every script/service/log"): invokes the
                                   three real, working generator scripts
                                   (generate_task_checklist.py,
                                   generate-system-diagram.py,
                                   generate_quick_reference.py) as subprocess
                                   calls on system-sync.py's own existing
                                   6-hourly cron cadence, so their outputs
                                   can no longer silently go stale between
                                   manual runs. Reports each generator's
                                   success/failure and whether its output
                                   file's mtime actually advanced.
  5. resume_balance_blocked_check() -- added during this file's own audit
                                   round 2, after verifying (not assuming)
                                   that "blocked" tasks do NOT all
                                   auto-resume once OpenRouter credits are
                                   restored (queue-dispatcher.py only
                                   revisits gap_queue.yaml items still in
                                   'dispatched' status; most of the 46
                                   recovered units were either already
                                   terminal there or never tracked there
                                   at all). Queries the real balance; if
                                   restored, finds every task whose LAST
                                   checkpoint note is the
                                   openrouter_balance_exhausted hard stop
                                   and restarts its systemd unit directly
                                   -- operates on CONTROLLER.yaml/systemd,
                                   not gap_queue.yaml, so it covers every
                                   balance-blocked task regardless of
                                   dispatch lineage.

Findings from checks 2 and 3 are appended to the existing live alert surface
(/opt/veridian/ai-os/logs/ATTENTION.md, registries.attention_alerts) -- not
a new channel, reusing what health-check-15min.py and cost-usage-60min.py
already write to.

Usage: system-sync.py [--dry-run] [--check mirror|constitution|unindexed|all]
Exit 0 always (this is a detector, not a guardrail -- never blocks
anything). Exit 1 only on a genuine script-level error (can't read a file
it needs).
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

import dispatch_core  # 2026-07-29 cron-consolidation-phase6: shared concurrency gate

REPO = "/opt/veridian/repos/compliance-tracker"
ALL_REPOS = [
    "/opt/veridian/repos/compliance-tracker",
    "/opt/veridian/repos/projexa",
    "/opt/veridian/repos/veda-advisors",
]
LIVE_SCRIPTS_DIR = "/opt/veridian/scripts"
MIRROR_SCRIPTS_DIR = f"{REPO}/ai-os/scripts"
LIVE_MASTER_INDEX = "/opt/veridian/ai-os/MASTER_INDEX.yaml"
MIRROR_MASTER_INDEX = f"{REPO}/ai-os/MASTER_INDEX.yaml"
CONSTITUTION = f"{REPO}/ai-os/CONSTITUTION.yaml"
ATTENTION_MD = "/opt/veridian/ai-os/logs/ATTENTION.md"

DOC_GENERATED_DIR = "/opt/veridian/ai-os/generated"
SYSTEM_DIAGRAM_MD = "/opt/veridian/ai-os/SYSTEM_DIAGRAM.md"
DOC_GENERATORS = [
    {
        "name": "generate_task_checklist.py",
        "cmd": [sys.executable, f"{LIVE_SCRIPTS_DIR}/generate_task_checklist.py", LIVE_MASTER_INDEX],
        "output_file": f"{DOC_GENERATED_DIR}/generate_task_checklist-latest.yaml",
    },
    {
        "name": "generate_quick_reference.py",
        "cmd": [sys.executable, f"{LIVE_SCRIPTS_DIR}/generate_quick_reference.py", LIVE_MASTER_INDEX],
        "output_file": f"{DOC_GENERATED_DIR}/generate_quick_reference-latest.yaml",
    },
    {
        "name": "generate-system-diagram.py",
        "cmd": [sys.executable, f"{LIVE_SCRIPTS_DIR}/generate-system-diagram.py", SYSTEM_DIAGRAM_MD],
        "output_file": SYSTEM_DIAGRAM_MD,
    },
]

EXCLUDE_DIR_NAMES = {"node_modules", ".git", "worktree", "worktrees"}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def git_last_commit_date(repo_dir, rel_path):
    """Last commit touching rel_path, or None if never committed (new/untracked)."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", "-1", "--format=%aI", "--", rel_path],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def append_attention(section, lines):
    if not lines:
        return
    os.makedirs(os.path.dirname(ATTENTION_MD), exist_ok=True)
    with open(ATTENTION_MD, "a") as f:
        f.write(f"\n## system-sync.py -- {section} -- {now_iso()}\n")
        for line in lines:
            f.write(f"- {line}\n")


# -----------------------------------------------------------------------
# Check 1: mirror drift (live scripts + MASTER_INDEX.yaml vs repo copies)
# -----------------------------------------------------------------------
def mirror_drift_check(dry_run):
    findings = []
    fixed = []

    os.makedirs(MIRROR_SCRIPTS_DIR, exist_ok=True)
    live_files = sorted(
        f for f in os.listdir(LIVE_SCRIPTS_DIR)
        if f.endswith((".py", ".sh")) and os.path.isfile(os.path.join(LIVE_SCRIPTS_DIR, f))
    )
    for fname in live_files:
        live_path = os.path.join(LIVE_SCRIPTS_DIR, fname)
        mirror_path = os.path.join(MIRROR_SCRIPTS_DIR, fname)
        if not os.path.exists(mirror_path):
            findings.append(f"NEW (no mirror at all): {fname}")
            if not dry_run:
                shutil.copy2(live_path, mirror_path)
                fixed.append(fname)
            continue
        with open(live_path, "rb") as a, open(mirror_path, "rb") as b:
            if a.read() != b.read():
                findings.append(f"DRIFTED (live != mirror content): {fname}")
                if not dry_run:
                    shutil.copy2(live_path, mirror_path)
                    fixed.append(fname)

    # MASTER_INDEX.yaml pair
    if os.path.exists(LIVE_MASTER_INDEX) and os.path.exists(MIRROR_MASTER_INDEX):
        with open(LIVE_MASTER_INDEX, "rb") as a, open(MIRROR_MASTER_INDEX, "rb") as b:
            if a.read() != b.read():
                findings.append("DRIFTED: MASTER_INDEX.yaml (live != repo mirror)")
                if not dry_run:
                    shutil.copy2(LIVE_MASTER_INDEX, MIRROR_MASTER_INDEX)
                    fixed.append("MASTER_INDEX.yaml")

    return findings, fixed


# -----------------------------------------------------------------------
# Check 2: CONSTITUTION.yaml staleness vs the files it names
# -----------------------------------------------------------------------
FILE_REF_RE = re.compile(r"\b(src/[A-Za-z0-9_\-./]+\.tsx?)\b")


def constitution_staleness_check():
    findings = []
    if not os.path.exists(CONSTITUTION):
        return findings
    with open(CONSTITUTION, encoding="utf-8") as f:
        text = f.read()

    constitution_rel = os.path.relpath(CONSTITUTION, REPO)
    constitution_date = git_last_commit_date(REPO, constitution_rel)
    if constitution_date is None:
        return findings  # uncommitted -- nothing to compare against yet

    referenced = sorted(set(FILE_REF_RE.findall(text)))
    for rel_path in referenced:
        # CONSTITUTION.yaml legitimately describes OTHER repos' files too
        # (e.g. control_model.products evidence lines) -- only flag as
        # broken if the path exists in NONE of the known repos, and only
        # check staleness against the repo it actually lives in.
        owning_repo = None
        for candidate_repo in ALL_REPOS:
            if os.path.exists(os.path.join(candidate_repo, rel_path)):
                owning_repo = candidate_repo
                break
        if owning_repo is None:
            findings.append(
                f"BROKEN REFERENCE: CONSTITUTION.yaml names '{rel_path}' -- "
                f"file does not exist in any known repo ({', '.join(os.path.basename(r) for r in ALL_REPOS)})"
            )
            continue
        if owning_repo != REPO:
            continue  # cross-repo reference, staleness compared against a different repo's history -- out of scope here, existence already confirmed
        file_date = git_last_commit_date(REPO, rel_path)
        if file_date and file_date > constitution_date:
            findings.append(
                f"POSSIBLY STALE: '{rel_path}' last changed {file_date[:10]}, "
                f"CONSTITUTION.yaml last changed {constitution_date[:10]} -- "
                f"the mechanism this file implements may have changed since "
                f"the doc was last verified against it"
            )
    return findings


# -----------------------------------------------------------------------
# Check 3: files on disk under canonical_dirs not yet in file_inventory
# -----------------------------------------------------------------------
def unindexed_files_check():
    findings = []

    # ops_scripts: every .py/.sh under /opt/veridian/scripts/ should appear
    # in MASTER_INDEX.yaml's file_inventory somewhere (tagged or not_yet_tagged).
    if not os.path.exists(LIVE_MASTER_INDEX):
        return findings
    with open(LIVE_MASTER_INDEX, encoding="utf-8") as f:
        master_index_text = f.read()

    live_files = sorted(
        f for f in os.listdir(LIVE_SCRIPTS_DIR)
        if f.endswith((".py", ".sh")) and os.path.isfile(os.path.join(LIVE_SCRIPTS_DIR, f))
    )
    for fname in live_files:
        if fname not in master_index_text:
            findings.append(
                f"UNINDEXED: {fname} exists in {LIVE_SCRIPTS_DIR}/ but is not "
                f"mentioned anywhere in MASTER_INDEX.yaml (not tagged, not "
                f"not_yet_tagged -- genuinely invisible to the index)"
            )

    # ai-os governance docs: every .md/.yaml at repo ai-os/ root should be
    # at least mentioned somewhere in MASTER_INDEX.yaml.
    ai_os_root = f"{REPO}/ai-os"
    if os.path.isdir(ai_os_root):
        for fname in sorted(os.listdir(ai_os_root)):
            full = os.path.join(ai_os_root, fname)
            if not os.path.isfile(full):
                continue
            if not fname.endswith((".md", ".yaml", ".yml")):
                continue
            if fname not in master_index_text:
                findings.append(
                    f"UNINDEXED: ai-os/{fname} exists but is not mentioned "
                    f"anywhere in MASTER_INDEX.yaml"
                )

    return findings


# -----------------------------------------------------------------------
# Check 4: run the three real doc generators on this file's own cron
# cadence, so MASTER_INDEX.yaml-derived docs can't silently go stale
# between manual runs (governance item 60).
# -----------------------------------------------------------------------
def documentation_generation_check(dry_run):
    findings = []
    ran = []
    for gen in DOC_GENERATORS:
        name = gen["name"]
        output_file = gen["output_file"]
        if dry_run:
            findings.append(f"DRY-RUN (not invoked): {name}")
            continue
        before_mtime = os.path.getmtime(output_file) if os.path.exists(output_file) else None
        try:
            result = subprocess.run(gen["cmd"], capture_output=True, text=True, timeout=60)
        except Exception as e:
            findings.append(f"FAILED: {name} could not be invoked -- {e}")
            continue
        if result.returncode != 0:
            findings.append(f"FAILED: {name} exited {result.returncode} -- {result.stderr.strip()[:300]}")
            continue
        if not os.path.exists(output_file):
            findings.append(f"FAILED: {name} ran (exit 0) but did not produce {output_file}")
            continue
        after_mtime = os.path.getmtime(output_file)
        if before_mtime is not None and after_mtime <= before_mtime:
            findings.append(f"FAILED: {name} ran but {output_file} mtime did not advance (stale output)")
            continue
        findings.append(f"OK: {name} regenerated {output_file}")
        ran.append(name)
    return findings, ran


# -----------------------------------------------------------------------
# Check 5: resume tasks blocked purely on OpenRouter balance, once the
# balance is actually restored. Added 2026-07-20 Audit Round 2 -- the
# original worker_fleet_rca_fix_2026_07_20 registry entry claimed
# recovered units "will resume automatically once credits are added,"
# which turned out to be FALSE on verification: queue-dispatcher.py's
# retry logic only re-touches gap_queue.yaml items still in 'dispatched'
# status (21 of the 46 had already been marked stuck_needs_human before
# recovery, a terminal state queue-dispatcher never revisits), and the
# other 25 were never tracked in gap_queue.yaml at all (dispatched by a
# different, one-off mechanism). This check closes that gap directly by
# operating on CONTROLLER.yaml/systemd, not on gap_queue.yaml's narrower
# tracking -- so it covers every blocked-on-balance task uniformly
# regardless of which dispatch lineage originally created it.
# -----------------------------------------------------------------------
def get_openrouter_remaining(min_remaining_usd=0.10):
    import json
    import urllib.request

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env_path = "/opt/veridian/shared/.env"
        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY="):
                        key = line.strip().split("=", 1)[1]
                        break
        except FileNotFoundError:
            pass
    if not key:
        return None
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    d = data.get("data", {})
    total_credits = d.get("total_credits")
    total_usage = d.get("total_usage")
    if total_credits is None or total_usage is None:
        return None
    return total_credits - total_usage


TASKS_DIR = "/opt/veridian/ai-os/tasks"
GAP_QUEUE_YAML = "/opt/veridian/ai-os/gap_queue.yaml"


def get_held_task_ids():
    """Task IDs this system must never auto-touch (resume/dispatch) even
    when their underlying blocking condition clears, because a human
    explicitly said "stop these jobs, analyze only." Currently sourced
    from gap_queue.yaml's held_task_ids (set 2026-07-20 per Owner
    directive on the 185-gap Review-Framework V2 plan) -- generalized as
    its own function so a future second hold source doesn't require
    touching resume_balance_blocked_check() itself, only this lookup."""
    held = set()
    if os.path.isfile(GAP_QUEUE_YAML):
        try:
            import yaml as _yaml
            with open(GAP_QUEUE_YAML, encoding="utf-8") as f:
                doc = _yaml.safe_load(f)
            if isinstance(doc, dict) and doc.get("dispatch_paused"):
                held.update(doc.get("held_task_ids") or [])
        except Exception:
            pass
    return held


def resume_balance_blocked_check(dry_run, min_remaining_usd=0.10):
    findings = []
    remaining = get_openrouter_remaining(min_remaining_usd)
    if remaining is None:
        findings.append("SKIPPED: could not read OpenRouter balance (network/key issue) -- fail open, no action taken")
        return findings, []
    if remaining < min_remaining_usd:
        findings.append(f"BALANCE STILL EXHAUSTED (${remaining:.4f} remaining) -- balance-blocked tasks left untouched")
        return findings, []

    import yaml as _yaml

    held_task_ids = get_held_task_ids()
    resumed = []
    if not os.path.isdir(TASKS_DIR):
        return findings, resumed
    for task_id in sorted(os.listdir(TASKS_DIR)):
        if task_id in held_task_ids:
            findings.append(f"HELD (Owner pause, not resumed): {task_id}")
            continue
        task_yaml_path = os.path.join(TASKS_DIR, task_id, "task.yaml")
        if not os.path.isfile(task_yaml_path):
            continue
        try:
            with open(task_yaml_path, encoding="utf-8") as f:
                task_doc = _yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(task_doc, dict) or task_doc.get("status") != "blocked":
            continue
        checkpoints = task_doc.get("checkpoints") or []
        if not checkpoints:
            continue
        # Only act on the LAST checkpoint being the balance block -- a task
        # that has since progressed past it (new checkpoint appended) must
        # not be touched.
        last_note = checkpoints[-1].get("note", "") or ""
        if "openrouter_balance_exhausted" not in last_note:
            continue
        unit = f"veridian-worker@{task_id}.service"
        is_active = subprocess.run(
            ["systemctl", "--user", "is-active", unit], capture_output=True, text=True
        ).stdout.strip()
        if is_active == "active":
            continue  # already running again for some other reason -- do not interfere
        findings.append(f"RESUMABLE (balance restored): {task_id}")
        if not dry_run:
            # 2026-07-29 cron-consolidation-phase6: this is a real, direct
            # `systemctl --user start` spawn call site -- the same shape of
            # unprotected worker-spawn path (no shared cap, no shared lock)
            # that caused the 2026-07-26 OOM-kill incident via
            # supervisor-sweep.sh/queue-dispatcher.py/module-queue-dispatcher.py,
            # just not one of the 3 scripts that consolidation covered. Gated
            # here the same way dispatch-tick.py gates its own 3 spawn call
            # sites: lock held across BOTH the free-slot check AND the actual
            # spawn, so the newly-started unit is guaranteed visible to the
            # next running_worker_count() call before the lock is released to
            # any other caller (closes the same TOCTOU race dispatch_core.py's
            # own docstring describes).
            with dispatch_core.acquire_dispatch_lock():
                if not dispatch_core.has_free_slot():
                    findings.append(f"SKIP resume (cap reached): {task_id}")
                    continue
                subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
                subprocess.run(["systemctl", "--user", "start", unit], capture_output=True)
            resumed.append(task_id)
    return findings, resumed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", choices=["mirror", "constitution", "unindexed", "documentation", "resume-balance", "all"], default="all")
    args = parser.parse_args()

    total_findings = 0

    if args.check in ("mirror", "all"):
        findings, fixed = mirror_drift_check(args.dry_run)
        print(f"[mirror_drift_check] {len(findings)} finding(s), {len(fixed)} auto-fixed (staged, not committed)")
        for f in findings:
            print(f"  - {f}")
        total_findings += len(findings)

    if args.check in ("constitution", "all"):
        findings = constitution_staleness_check()
        print(f"[constitution_staleness_check] {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        append_attention("constitution_staleness_check", findings)
        total_findings += len(findings)

    if args.check in ("unindexed", "all"):
        findings = unindexed_files_check()
        print(f"[unindexed_files_check] {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        append_attention("unindexed_files_check", findings)
        total_findings += len(findings)

    if args.check in ("documentation", "all"):
        findings, ran = documentation_generation_check(args.dry_run)
        print(f"[documentation_generation_check] {len(findings)} finding(s), {len(ran)} generator(s) ran successfully")
        for f in findings:
            print(f"  - {f}")
        append_attention("documentation_generation_check", findings)
        total_findings += len(findings)

    if args.check in ("resume-balance", "all"):
        findings, resumed = resume_balance_blocked_check(args.dry_run)
        print(f"[resume_balance_blocked_check] {len(findings)} finding(s), {len(resumed)} resumed")
        for f in findings:
            print(f"  - {f}")
        append_attention("resume_balance_blocked_check", findings)
        total_findings += len(findings)

    print(f"\nTotal findings across all checks run: {total_findings}")
    sys.exit(0)


if __name__ == "__main__":
    main()
