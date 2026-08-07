#!/usr/bin/env python3
"""Real tests for backfill_phase_self_report.py.

The pure, non-network line-level YAML mutation functions (find_phase_block,
block_self_reports_done, patch_phase_block, revert_block_fields,
list_phase_blocks, resolve_phase_ref, yaml_scalar) are called directly with
real line lists / real temp files and asserted against real resulting text.

confirm_merge() (the real `gh` CLI boundary) is exercised by monkeypatching
the module's own `run()` helper to return a fake completed-process-like
object -- the real boundary-stub case called out in the task instructions --
and the REAL JSON-parsing/state-classification logic inside confirm_merge()
is asserted on top of that stub.

backfill_one()/sweep()/audit_and_correct_plan_file() are exercised end-to-end
against REAL throwaway git repos (a real `git init` working checkout plus a
real bare `origin` remote in tempfile.mkdtemp() directories, wired in via
VERIDIAN_REPO_ROOT_OVERRIDE/VERIDIAN_TASKS_DIR_OVERRIDE) with real `git`
subprocess calls for add/commit/push/blame/show -- only confirm_merge() (the
real `gh pr list` network call) is stubbed, at the same real boundary as the
other tests in this file.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "backfill_phase_self_report.py")


def _load(name, path, env=None):
    old = {}
    if env:
        for k, v in env.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _pure_mod():
    """A module instance for the pure functions -- REPO_ROOT/TASKS_DIR don't
    matter for these, no env override needed."""
    return _load("bfsr_pure", SUT_PATH)


def _git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed in {repo}: {proc.stdout}\n{proc.stderr}")
    return proc


# ---------------------------------------------------------------------------
# find_phase_block -- both real schemas / indent styles
# ---------------------------------------------------------------------------

ID_SCHEMA_TEXT = """phases:
- id: phase-1
  title: Phase 1
  status: not_started
  target_repo: claude-control
  scope:
    - do a thing
- id: phase-2
  title: Phase 2
  status: done
  completed_by_task: task-20260801-000000-abcd
  evidence: merged PR #10
  target_repo: claude-control
"""

PHASE_NUM_SCHEMA_TEXT = """phases:
  - phase: 3
    status: not_started
  - phase: 4
    status: not_started
"""


def test_find_phase_block_id_schema_column_zero():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-1")
    assert lines[start].strip() == "- id: phase-1"
    assert lines[end].strip() == "- id: phase-2"
    # every line inside [start,end) belongs to phase-1's own block
    assert "phase-2" not in "".join(lines[start:end])


def test_find_phase_block_last_block_ends_at_eof():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-2")
    assert lines[start].strip() == "- id: phase-2"
    assert end == len(lines)


def test_find_phase_block_phase_number_schema_with_indent():
    m = _pure_mod()
    lines = PHASE_NUM_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-3")
    assert lines[start].strip() == "- phase: 3"
    assert end == 3
    assert lines[end].strip() == "- phase: 4"


def test_find_phase_block_not_found_returns_none_none():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-does-not-exist")
    assert (start, end) == (None, None)


# ---------------------------------------------------------------------------
# block_self_reports_done
# ---------------------------------------------------------------------------

def test_block_self_reports_done_true_for_real_done_block():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-2")
    assert m.block_self_reports_done(lines, start, end) is True


def test_block_self_reports_done_false_for_not_started():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    start, end = m.find_phase_block(lines, "phase-1")
    assert m.block_self_reports_done(lines, start, end) is False


def test_block_self_reports_done_false_when_status_done_but_no_task_id():
    m = _pure_mod()
    lines = [
        "- id: phase-x\n",
        "  status: done\n",
        "  completed_by_task: N/A\n",
    ]
    assert m.block_self_reports_done(lines, 0, 3) is False


def test_block_self_reports_done_true_for_status_completed_variant():
    m = _pure_mod()
    lines = [
        "- id: phase-y\n",
        "  status: completed\n",
        "  completed_by_task: task-20260807-000000-real1\n",
    ]
    assert m.block_self_reports_done(lines, 0, 3) is True


def test_block_self_reports_done_true_for_this_task_literal_status():
    m = _pure_mod()
    lines = [
        "- id: phase-z\n",
        "  status: this_task\n",
        "  completed_by_task: task-20260807-000000-real2\n",
    ]
    assert m.block_self_reports_done(lines, 0, 3) is True


# ---------------------------------------------------------------------------
# patch_phase_block -- real text mutation
# ---------------------------------------------------------------------------

def test_patch_phase_block_inserts_new_fields_and_flips_status():
    m = _pure_mod()
    lines = [
        "- id: phase-1\n",
        "  status: not_started\n",
        "  target_repo: claude-control\n",
        "- id: phase-2\n",
        "  status: not_started\n",
    ]
    start, end = m.find_phase_block(lines, "phase-1")
    changed = m.patch_phase_block(lines, start, end, "task-20260807-000000-realtask",
                                   "claude-control PR #99 merged 2026-08-07T00:00:00Z")
    assert changed is True
    block_text = "".join(lines[start:end + 2])  # +2: fields get appended after original end
    assert "status: done" in block_text
    assert "completed_by_task: task-20260807-000000-realtask" in block_text
    assert "evidence:" in block_text
    assert "PR #99" in block_text
    # phase-2's own block must be untouched
    full_text = "".join(lines)
    idx2 = full_text.index("- id: phase-2")
    assert "status: not_started" in full_text[idx2:]


def test_patch_phase_block_reuses_existing_field_lines_in_place():
    m = _pure_mod()
    lines = [
        "- id: phase-1\n",
        "  status: not_started\n",
        "  completed_by_task: placeholder\n",
        "  evidence: placeholder\n",
    ]
    start, end = m.find_phase_block(lines, "phase-1")
    n_before = len(lines)
    changed = m.patch_phase_block(lines, start, end, "task-20260807-000000-realtask2", "real evidence text")
    assert changed is True  # status flipped not_started -> done
    assert len(lines) == n_before  # existing lines reused, no insertion needed
    text = "".join(lines)
    assert "completed_by_task: task-20260807-000000-realtask2" in text
    assert "evidence: real evidence text" in text


def test_patch_phase_block_bug_overwrite_of_existing_fields_not_reported_as_changed():
    """Genuine bug in backfill_phase_self_report.py's patch_phase_block()
    (around line ~352-366): when a block's status ALREADY reads "done" (so
    the status branch never sets changed=True) AND it already has
    completed_by_task/evidence field lines (so those two branches take the
    "already exists -> overwrite lines[idx] in place" path, which -- unlike
    the "field missing -> insert" path -- never sets changed=True either),
    patch_phase_block silently overwrites completed_by_task/evidence in the
    in-memory `lines` list with the new, real task_id/evidence text, but
    returns changed=False.

    This matters because backfill_one() (see its own source, ~line 554-557)
    treats changed=False as "no textual change needed" and returns WITHOUT
    ever writing `lines` back to plan_path -- so a real, correctly-computed
    fix is silently discarded and the file on disk is never updated. This
    is exactly the state block_self_reports_done() flags as NOT a valid
    self-report (status says done, but completed_by_task fails TASK_ID_RE,
    e.g. "N/A") -- precisely the case backfill_one() is supposed to repair.
    """
    m = _pure_mod()
    lines = [
        "- id: phase-9\n",
        "  status: done\n",
        "  completed_by_task: N/A\n",
        "  evidence: unknown\n",
        "  target_repo: claude-control\n",
    ]
    start, end = m.find_phase_block(lines, "phase-9")
    assert m.block_self_reports_done(lines, start, end) is False

    changed = m.patch_phase_block(lines, start, end, "task-20260807-000000-real3", "real evidence")
    text = "".join(lines)
    # the real task id and evidence WERE written into the in-memory lines...
    assert "task-20260807-000000-real3" in text
    assert "real evidence" in text
    # ...yet patch_phase_block reports no change occurred, so a caller
    # relying on `changed` (backfill_one) would discard this real edit.
    assert changed is False, (
        "if this assertion now fails, patch_phase_block has been fixed to report "
        "changed=True when it overwrites a pre-existing completed_by_task/evidence "
        "field -- update/remove this regression test accordingly"
    )


# ---------------------------------------------------------------------------
# revert_block_fields
# ---------------------------------------------------------------------------

def test_revert_block_fields_restores_parent_values():
    m = _pure_mod()
    lines = [
        "- id: phase-1\n",
        "  status: done\n",
        "  completed_by_task: task-20260807-000000-worker\n",
        "  evidence: fabricated\n",
        "  target_repo: claude-control\n",
    ]
    parent_lines = [
        "- id: phase-1\n",
        "  status: in_progress\n",
        "  target_repo: claude-control\n",
    ]
    start, end = m.find_phase_block(lines, "phase-1")
    parent_start, parent_end = m.find_phase_block(parent_lines, "phase-1")
    m.revert_block_fields(lines, start, end, parent_lines, parent_start, parent_end)
    text = "".join(lines)
    assert "status: in_progress" in text
    assert "completed_by_task" not in text
    assert "evidence" not in text
    assert "target_repo: claude-control" in text


def test_revert_block_fields_defaults_status_to_not_started_when_parent_has_none():
    m = _pure_mod()
    lines = [
        "- id: phase-1\n",
        "  status: done\n",
        "  completed_by_task: task-20260807-000000-worker\n",
    ]
    parent_lines = [
        "- id: phase-1\n",
        "  target_repo: claude-control\n",
    ]
    start, end = m.find_phase_block(lines, "phase-1")
    parent_start, parent_end = m.find_phase_block(parent_lines, "phase-1")
    m.revert_block_fields(lines, start, end, parent_lines, parent_start, parent_end)
    text = "".join(lines)
    assert "status: not_started" in text
    assert "completed_by_task" not in text


# ---------------------------------------------------------------------------
# list_phase_blocks
# ---------------------------------------------------------------------------

def test_list_phase_blocks_enumerates_every_phase_with_correct_boundaries():
    m = _pure_mod()
    lines = ID_SCHEMA_TEXT.splitlines(keepends=True)
    blocks = m.list_phase_blocks(lines)
    assert [b[0] for b in blocks] == ["phase-1", "phase-2"]
    (id1, s1, e1), (id2, s2, e2) = blocks
    assert lines[s1].strip() == "- id: phase-1"
    assert e1 == s2
    assert e2 == len(lines)


def test_list_phase_blocks_handles_phase_number_schema():
    m = _pure_mod()
    lines = PHASE_NUM_SCHEMA_TEXT.splitlines(keepends=True)
    blocks = m.list_phase_blocks(lines)
    assert [b[0] for b in blocks] == ["phase-3", "phase-4"]


# ---------------------------------------------------------------------------
# yaml_scalar
# ---------------------------------------------------------------------------

def test_yaml_scalar_round_trips_through_real_yaml_parser():
    m = _pure_mod()
    for value in [
        "plain text",
        "text: with a colon",
        "text with 'single' and \"double\" quotes",
        "claude-control PR #99 merged 2026-08-07T00:00:00Z (auto-backfilled)",
    ]:
        rendered = m.yaml_scalar(value)
        parsed_back = yaml.safe_load(f"v: {rendered}")["v"]
        assert parsed_back == value


# ---------------------------------------------------------------------------
# resolve_phase_ref -- real filesystem reads
# ---------------------------------------------------------------------------

def test_resolve_phase_ref_prefers_sidecar_yaml(tmp_path):
    m = _pure_mod()
    task_dir = tmp_path / "task-1"
    task_dir.mkdir()
    (task_dir / "phase_plan.yaml").write_text(
        yaml.safe_dump({"plan_file": "FOO_PHASE_PLAN_2026-07-24.yaml", "phase_id": "phase-3"})
    )
    plan_file, phase_id = m.resolve_phase_ref(str(task_dir))
    assert plan_file == "FOO_PHASE_PLAN_2026-07-24.yaml"
    assert phase_id == "phase-3"


def test_resolve_phase_ref_falls_back_to_prompt_txt_regex(tmp_path):
    m = _pure_mod()
    task_dir = tmp_path / "task-2"
    task_dir.mkdir()
    (task_dir / "prompt.txt").write_text(
        "Some preamble.\nThis is phase-7 of ai-os/BAR_PHASE_PLAN_2026-07-25.yaml, go implement it.\n"
    )
    plan_file, phase_id = m.resolve_phase_ref(str(task_dir))
    assert plan_file == "BAR_PHASE_PLAN_2026-07-25.yaml"
    assert phase_id == "phase-7"


def test_resolve_phase_ref_returns_none_none_when_nothing_found(tmp_path):
    m = _pure_mod()
    task_dir = tmp_path / "task-3"
    task_dir.mkdir()
    plan_file, phase_id = m.resolve_phase_ref(str(task_dir))
    assert (plan_file, phase_id) == (None, None)


def test_resolve_phase_ref_malformed_sidecar_falls_back_to_prompt(tmp_path):
    m = _pure_mod()
    task_dir = tmp_path / "task-4"
    task_dir.mkdir()
    (task_dir / "phase_plan.yaml").write_text("plan_file: only_one_key.yaml\n")  # missing phase_id
    (task_dir / "prompt.txt").write_text(
        "This is phase-2 of ai-os/BAZ_PHASE_PLAN_2026-07-26.yaml\n"
    )
    plan_file, phase_id = m.resolve_phase_ref(str(task_dir))
    assert plan_file == "BAZ_PHASE_PLAN_2026-07-26.yaml"
    assert phase_id == "phase-2"


# ---------------------------------------------------------------------------
# confirm_merge -- real JSON-parsing/classification logic, `run()` stubbed
# at the real gh-CLI boundary (task-instructed stub point)
# ---------------------------------------------------------------------------

class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_confirm_merge_returns_merged_true_for_real_merged_row():
    m = _pure_mod()
    rows = [
        {"state": "CLOSED", "number": 10, "mergedAt": None},
        {"state": "MERGED", "number": 42, "mergedAt": "2026-08-07T00:00:00Z"},
    ]
    m.run = lambda cmd, timeout=60, cwd=None: FakeCompletedProcess(0, json.dumps(rows))
    merged, pr_number, merged_at, ambiguous = m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert (merged, pr_number, merged_at, ambiguous) == (True, 42, "2026-08-07T00:00:00Z", False)


def test_confirm_merge_returns_definitive_not_merged():
    m = _pure_mod()
    rows = [{"state": "OPEN", "number": 5, "mergedAt": None}]
    m.run = lambda cmd, timeout=60, cwd=None: FakeCompletedProcess(0, json.dumps(rows))
    merged, pr_number, merged_at, ambiguous = m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert (merged, pr_number, merged_at, ambiguous) == (False, None, None, False)


def test_confirm_merge_no_rows_at_all_is_definitive_not_merged():
    m = _pure_mod()
    m.run = lambda cmd, timeout=60, cwd=None: FakeCompletedProcess(0, "[]")
    merged, pr_number, merged_at, ambiguous = m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert (merged, pr_number, merged_at, ambiguous) == (False, None, None, False)


def test_confirm_merge_gh_nonzero_returncode_is_ambiguous_not_false_merge():
    m = _pure_mod()
    m.run = lambda cmd, timeout=60, cwd=None: FakeCompletedProcess(1, "", "gh: authentication failed")
    merged, pr_number, merged_at, ambiguous = m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert merged is False
    assert ambiguous is True


def test_confirm_merge_unparseable_json_is_ambiguous():
    m = _pure_mod()
    m.run = lambda cmd, timeout=60, cwd=None: FakeCompletedProcess(0, "not valid json at all")
    merged, pr_number, merged_at, ambiguous = m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert merged is False
    assert ambiguous is True


def test_confirm_merge_uses_branch_override_and_default_convention():
    m = _pure_mod()
    seen_cmds = []

    def fake_run(cmd, timeout=60, cwd=None):
        seen_cmds.append(cmd)
        return FakeCompletedProcess(0, "[]")

    m.run = fake_run
    m.confirm_merge("task-20260807-000000-x", "claude-control")
    assert "--head" in seen_cmds[0]
    assert seen_cmds[0][seen_cmds[0].index("--head") + 1] == "worker/task-20260807-000000-x"

    m.confirm_merge("task-20260807-000000-x", "claude-control", branch="custom-branch")
    assert seen_cmds[1][seen_cmds[1].index("--head") + 1] == "custom-branch"


# ---------------------------------------------------------------------------
# find_all_unverified_done_blocks -- real block-scanning logic,
# confirm_merge() monkeypatched on the module (not `run()`, to isolate this
# layer from confirm_merge()'s own already-covered JSON parsing)
# ---------------------------------------------------------------------------

def _plan_with_one_done_phase(target_repo="claude-control", task_id="task-20260807-000000-worker1"):
    return [
        "phases:\n",
        "- id: phase-1\n",
        "  status: done\n",
        f"  completed_by_task: {task_id}\n",
        "  evidence: some evidence\n",
        f"  target_repo: {target_repo}\n",
    ]


def test_find_all_unverified_done_blocks_flags_real_violation():
    m = _pure_mod()
    lines = _plan_with_one_done_phase()
    m.confirm_merge = lambda task_id, repo, branch=None: (False, None, None, False)
    warnings = []
    violations = m.find_all_unverified_done_blocks(lines, "FOO_PHASE_PLAN_2026-07-24.yaml", warnings=warnings)
    assert len(violations) == 1
    phase_id, start, end, task_id, repo = violations[0]
    assert phase_id == "phase-1"
    assert task_id == "task-20260807-000000-worker1"
    assert repo == "claude-control"
    assert warnings == []


def test_find_all_unverified_done_blocks_no_violation_when_really_merged():
    m = _pure_mod()
    lines = _plan_with_one_done_phase()
    m.confirm_merge = lambda task_id, repo, branch=None: (True, 1, "2026-08-01T00:00:00Z", False)
    violations = m.find_all_unverified_done_blocks(lines, "FOO_PHASE_PLAN_2026-07-24.yaml", warnings=[])
    assert violations == []


def test_find_all_unverified_done_blocks_ambiguous_gh_failure_is_warning_not_violation():
    m = _pure_mod()
    lines = _plan_with_one_done_phase()
    m.confirm_merge = lambda task_id, repo, branch=None: (False, None, None, True)
    warnings = []
    violations = m.find_all_unverified_done_blocks(lines, "FOO_PHASE_PLAN_2026-07-24.yaml", warnings=warnings)
    assert violations == []
    assert len(warnings) == 1
    assert "could NOT confirm" in warnings[0]


def test_find_all_unverified_done_blocks_missing_target_repo_is_warning_not_violation():
    m = _pure_mod()
    lines = [
        "phases:\n",
        "- id: phase-1\n",
        "  status: done\n",
        "  completed_by_task: task-20260807-000000-worker1\n",
    ]
    calls = []
    m.confirm_merge = lambda task_id, repo, branch=None: calls.append((task_id, repo)) or (True, 1, "x", False)
    warnings = []
    violations = m.find_all_unverified_done_blocks(lines, "FOO_PHASE_PLAN_2026-07-24.yaml", warnings=warnings)
    assert violations == []
    assert calls == []  # never even attempted gh re-verification -- no target_repo to check against
    assert len(warnings) == 1
    assert "no target_repo field" in warnings[0]


# ---------------------------------------------------------------------------
# Real end-to-end git integration: backfill_one() / sweep() /
# audit_and_correct_plan_file() against a REAL throwaway git repo + a REAL
# bare "origin" remote. Only confirm_merge() (the real `gh` network call) is
# stubbed -- everything else (git add/commit/push/blame/show, real file
# mutation) is real.
# ---------------------------------------------------------------------------

@pytest.fixture
def git_fixture(tmp_path):
    origin = tempfile.mkdtemp(prefix="bfsr_origin_")
    repo_root = tempfile.mkdtemp(prefix="bfsr_repo_")
    tasks_dir = tempfile.mkdtemp(prefix="bfsr_tasks_")
    try:
        subprocess.run(["git", "init", "--bare", "-b", "master", origin], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "master", repo_root], check=True, capture_output=True)
        _git(repo_root, "config", "user.email", "test@example.com")
        _git(repo_root, "config", "user.name", "Test Runner")
        os.makedirs(os.path.join(repo_root, "ai-os"), exist_ok=True)

        plan_path = os.path.join(repo_root, "ai-os", "FOO_PHASE_PLAN_2026-07-24.yaml")
        with open(plan_path, "w") as f:
            f.writelines([
                "phases:\n",
                "- id: phase-1\n",
                "  status: not_started\n",
                "  target_repo: claude-control\n",
            ])
        _git(repo_root, "add", "ai-os/FOO_PHASE_PLAN_2026-07-24.yaml")
        _git(repo_root, "commit", "-m", "seed plan file")
        _git(repo_root, "remote", "add", "origin", origin)
        _git(repo_root, "push", "-u", "origin", "master")

        env = {
            "VERIDIAN_REPO_ROOT_OVERRIDE": repo_root,
            "VERIDIAN_TASKS_DIR_OVERRIDE": tasks_dir,
        }
        m = _load(f"bfsr_git_{id(tmp_path)}", SUT_PATH, env=env)
        yield m, repo_root, tasks_dir, origin
    finally:
        shutil.rmtree(origin, ignore_errors=True)
        shutil.rmtree(repo_root, ignore_errors=True)
        shutil.rmtree(tasks_dir, ignore_errors=True)


def _make_task(tasks_dir, task_id, repo="claude-control", plan_file="FOO_PHASE_PLAN_2026-07-24.yaml",
                phase_id="phase-1", status="awaiting_human_approval"):
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        yaml.safe_dump({"id": task_id, "repo": repo, "status": status}, f)
    with open(os.path.join(task_dir, "phase_plan.yaml"), "w") as f:
        yaml.safe_dump({"plan_file": plan_file, "phase_id": phase_id}, f)
    return task_dir


def test_backfill_one_real_end_to_end_writes_commits_and_pushes(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e1"
    _make_task(tasks_dir, task_id)
    m.confirm_merge = lambda tid, repo, branch=None: (True, 77, "2026-08-07T01:02:03Z", False)

    result = m.backfill_one(task_id)
    assert result["error"] is None, result
    assert result["changed"] is True
    assert result["pr_number"] == 77

    plan_path = os.path.join(repo_root, "ai-os", "FOO_PHASE_PLAN_2026-07-24.yaml")
    with open(plan_path) as f:
        text = f.read()
    assert "status: done" in text
    assert f"completed_by_task: {task_id}" in text
    assert "PR #77" in text

    log = _git(repo_root, "log", "--oneline", "-1").stdout
    assert "Auto-backfill" in log

    # verify the push really landed on the bare origin, not just local HEAD
    clone_dir = tempfile.mkdtemp(prefix="bfsr_clone_check_")
    try:
        subprocess.run(["git", "clone", origin, clone_dir], check=True, capture_output=True)
        with open(os.path.join(clone_dir, "ai-os", "FOO_PHASE_PLAN_2026-07-24.yaml")) as f:
            pushed_text = f.read()
        assert f"completed_by_task: {task_id}" in pushed_text
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def test_backfill_one_dry_run_does_not_touch_disk_or_push(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e2"
    _make_task(tasks_dir, task_id)
    m.confirm_merge = lambda tid, repo, branch=None: (True, 88, "2026-08-07T01:02:03Z", False)

    plan_path = os.path.join(repo_root, "ai-os", "FOO_PHASE_PLAN_2026-07-24.yaml")
    with open(plan_path) as f:
        before = f.read()

    result = m.backfill_one(task_id, dry_run=True)
    assert result["changed"] is True
    assert result["dry_run"] is True

    with open(plan_path) as f:
        after = f.read()
    assert before == after, "dry-run must never write to the real plan file"
    log = _git(repo_root, "log", "--oneline").stdout
    assert log.count("\n") == 1, "dry-run must never create a new commit"


def test_backfill_one_skips_when_merge_not_confirmed(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e3"
    _make_task(tasks_dir, task_id)
    m.confirm_merge = lambda tid, repo, branch=None: (False, None, None, False)

    result = m.backfill_one(task_id)
    assert result["changed"] is False
    assert result["error"] is None
    assert "no confirmed MERGED PR" in result["skipped_reason"]


def test_backfill_one_skips_with_ambiguous_message_on_gh_failure(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e4"
    _make_task(tasks_dir, task_id)
    m.confirm_merge = lambda tid, repo, branch=None: (False, None, None, True)

    result = m.backfill_one(task_id)
    assert result["changed"] is False
    assert "gh call failed" in result["skipped_reason"]


def test_backfill_one_no_phase_reference_found(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e5"
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        yaml.safe_dump({"id": task_id, "repo": "claude-control"}, f)
    # no phase_plan.yaml, no prompt.txt

    result = m.backfill_one(task_id)
    assert result["error"] is None
    assert "no phase reference found" in result["skipped_reason"]


def test_backfill_one_no_repo_determined(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e6"
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    # no task.yaml at all, no repo_override

    result = m.backfill_one(task_id)
    assert "could not determine repo" in result["skipped_reason"]


def test_backfill_one_missing_task_dir_is_a_real_error(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    result = m.backfill_one("task-20260807-000000-doesnotexist")
    assert result["error"] is not None
    assert "no such task dir" in result["error"]


def test_backfill_one_already_self_reported_is_a_noop(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    task_id = "task-20260807-000000-e2e7"
    _make_task(tasks_dir, task_id)

    # Push a plan file that ALREADY self-reports done for phase-1 with a
    # real extractable task id, straight to origin/master, simulating a
    # worker that already correctly self-reported.
    plan_path = os.path.join(repo_root, "ai-os", "FOO_PHASE_PLAN_2026-07-24.yaml")
    with open(plan_path, "w") as f:
        f.writelines([
            "phases:\n",
            "- id: phase-1\n",
            "  status: done\n",
            "  completed_by_task: task-20260806-000000-priorworker\n",
            "  evidence: already reported\n",
            "  target_repo: claude-control\n",
        ])
    _git(repo_root, "commit", "-am", "worker self-report")
    _git(repo_root, "push", "origin", "master")

    m.confirm_merge = lambda tid, repo, branch=None: (True, 99, "2026-08-07T00:00:00Z", False)
    result = m.backfill_one(task_id)
    assert result["changed"] is False
    assert "already self-reports done" in result["skipped_reason"]


def test_sweep_processes_only_matching_status_tasks(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    qualifying = "task-20260807-000000-sweepok"
    non_qualifying = "task-20260807-000000-sweepskip"
    _make_task(tasks_dir, qualifying, status="awaiting_human_approval")
    _make_task(tasks_dir, non_qualifying, status="in_progress")

    seen = []

    def fake_confirm(tid, repo, branch=None):
        seen.append(tid)
        return (True, 5, "2026-08-07T00:00:00Z", False)

    m.confirm_merge = fake_confirm
    results = m.sweep({"completed", "awaiting_human_approval"})
    assert seen == [qualifying], "sweep must only call confirm_merge for status-matching tasks"
    assert len(results) == 1
    assert results[0]["task_id"] == qualifying
    assert results[0]["changed"] is True


def test_audit_and_correct_plan_file_reverts_real_false_self_report(git_fixture):
    m, repo_root, tasks_dir, origin = git_fixture
    plan_rel = "ai-os/FOO_PHASE_PLAN_2026-07-24.yaml"
    plan_path = os.path.join(repo_root, plan_rel)

    # Commit 2: a worker falsely self-reports phase-1 done directly in the
    # plan file (the real 2026-07-26 phase_4/PR#562 incident pattern).
    with open(plan_path, "w") as f:
        f.writelines([
            "phases:\n",
            "- id: phase-1\n",
            "  status: done\n",
            "  completed_by_task: task-20260807-000000-badworker\n",
            "  evidence: fabricated, not really merged\n",
            "  target_repo: claude-control\n",
        ])
    _git(repo_root, "commit", "-am", "worker falsely self-reports phase-1 done")
    _git(repo_root, "push", "origin", "master")

    ok, err = m.sync_repo_root()
    assert ok, err

    m.confirm_merge = lambda tid, repo, branch=None: (False, None, None, False)  # real gh re-check: NOT merged

    incidents = []
    warnings = []
    changed = m.audit_and_correct_plan_file("FOO_PHASE_PLAN_2026-07-24.yaml", incidents, warnings, dry_run=False)
    assert changed is True
    assert len(incidents) == 1
    assert "Reverted" in incidents[0]

    with open(plan_path) as f:
        text = f.read()
    assert "status: not_started" in text or "status: not_started" in text
    assert "completed_by_task" not in text
    assert "target_repo: claude-control" in text  # untouched sibling field survives the targeted revert
