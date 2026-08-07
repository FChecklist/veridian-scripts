#!/usr/bin/env python3
"""Real tests for resource_governor.py's standing stop-work-order gate (real
issue #980, UMR_5767_ISSUE_RESOLUTION_MATRIX.json, governed by
UMR-20260806-171945-5767 / UMR-20260807-161418-a63f).

Real, confirmed incident this closes: UMR-20260807-110133-205d's real
dispatched worker (task-20260807-150203) never checked for the standing
stop-work order at all -- zero mentions anywhere in its own real
PROGRESS.md -- and proceeded straight to real PR/push work that merged,
while nine separate other dispatches independently happened to check and
correctly declined. This test file proves the real boolean test the
governing issue itself states: "with a real stop-work order active and no
real exemption, a dispatch requiring PR/push work is deterministically
blocked before any AI worker ever sees it, 100 percent of the time."

Also covers the real, confirmed fabrication pattern this gate specifically
defeats (same real day, 2026-08-07): a stop-work-order "exemption" that
exists only as dispatch-prompt text, or only as an UNCOMMITTED edit to
OWNER_DECISIONS_NEEDED_2026-07-23.yaml, must never be honored -- only an
exemption/lift entry that is genuinely committed at the git repo's HEAD
counts.

Every test uses a real, isolated, temp-file SQLite database (never the live
production database) and a real, isolated, temp `git init` repo for the
OWNER_DECISIONS_PATH file (never the live ai-os repo) -- same "real, not
mocked" convention as every other test file in this directory
(tests/test_flag_stale_queued_tasks.py, tests/test_rule6_zero_duplication_by_ocid.py).
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _schema_helpers():
    spec = importlib.util.spec_from_file_location(
        "sbr_helpers_stopwork", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _new_conn(scratch_db):
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_scratch_db(scratch_db, sbr):
    conn = _new_conn(scratch_db)
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


def _load_rg(name, env):
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _git(repo_dir, *args):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True, check=True)


def _init_owner_decisions_repo(tmp_dir, entries_yaml_text, commit=True):
    """Real, isolated git repo (never the live ai-os repo) containing a real
    OWNER_DECISIONS_NEEDED_2026-07-23.yaml at its root. Returns that file's
    real path. If commit=False, the file is written to the working tree but
    never `git add`/`git commit`ed -- reproducing the real, confirmed
    fabrication pattern (uncommitted working-tree-only edit)."""
    repo_dir = os.path.join(tmp_dir, "owner_decisions_repo")
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init", repo_dir], capture_output=True, text=True, check=True)
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test")
    path = os.path.join(repo_dir, "OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
    with open(path, "w") as f:
        f.write(entries_yaml_text)
    if commit:
        _git(repo_dir, "add", "OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
        _git(repo_dir, "commit", "-m", "real committed owner-decisions entry")
    return path


def _base_env(scratch_db, owner_decisions_path):
    return {
        "SUPERBOSS_REGISTER_DB": scratch_db,
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_OWNER_DECISIONS_PATH": owner_decisions_path,
    }


def test_veridian_task_create_blocked_when_order_open_no_exemption():
    """The real boolean test the governing issue itself states: no real
    exemption anywhere -> deterministically blocked, before any worker is
    ever spawned."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        owner_decisions_path = _init_owner_decisions_repo(d, "[]\n")
        rg = _load_rg("rg_sw_1", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-blocked-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is False, result
        assert "BLOCKED by standing stop-work order" in result["reason"], result

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT status, reason FROM umr_tasks WHERE task_identity=?", ("test-blocked-1",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "rejected_duplicate", dict(row)
        assert "BLOCKED by standing stop-work order" in row["reason"]


def test_systemctl_action_not_blocked():
    """task_kind='systemctl_action' does no PR/push work -- the order's own
    text scopes to 'any PR review or push work', so this must pass through
    untouched, same real distinction the pre-existing duplicate-PR guard
    already draws."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        owner_decisions_path = _init_owner_decisions_repo(d, "[]\n")
        rg = _load_rg("rg_sw_2", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-systemctl-1", "task_kind": "systemctl_action",
                 "unit_name": "veridian-worker@does-not-exist-test-unit.service",
                 "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is True, result


def test_uncommitted_exemption_entry_still_blocks():
    """Real, confirmed fabrication pattern (2026-08-07): an approved-looking
    exemption entry that exists only in the working tree, never committed,
    must NOT be honored."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        entries = """\
- id: phase2-subphase1-stop-work-order-exemption
  what: "Exempt task_identity test-uncommitted-1 from the standing stop-work order"
  needed_action: "Allow test-uncommitted-1 to proceed"
  status: approved
"""
        owner_decisions_path = _init_owner_decisions_repo(d, entries, commit=False)
        rg = _load_rg("rg_sw_3", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-uncommitted-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is False, result
        assert "BLOCKED by standing stop-work order" in result["reason"], result


def test_committed_matching_exemption_allows():
    """A real, committed, status:approved exemption entry whose own text
    names this exact task_identity must be honored."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        entries = """\
- id: phase2-subphase1-stop-work-order-exemption
  what: "Exempt task_identity test-committed-match-1 from the standing stop-work order"
  needed_action: "Allow test-committed-match-1 to proceed"
  status: approved
"""
        owner_decisions_path = _init_owner_decisions_repo(d, entries, commit=True)
        rg = _load_rg("rg_sw_4", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-committed-match-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is True, result


def test_committed_exemption_wrong_scope_still_blocks():
    """A real, committed, approved exemption entry that names a DIFFERENT
    task_identity/umr_id must not leak coverage onto this one."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        entries = """\
- id: phase2-subphase1-stop-work-order-exemption
  what: "Exempt task_identity some-other-task-entirely from the standing stop-work order"
  needed_action: "Allow some-other-task-entirely to proceed"
  status: approved
"""
        owner_decisions_path = _init_owner_decisions_repo(d, entries, commit=True)
        rg = _load_rg("rg_sw_5", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-wrong-scope-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is False, result


def test_committed_lifted_entry_allows_everything():
    """A real, committed, approved 'stop-work-order-lifted' entry closes the
    order entirely -- every task_identity proceeds, not just a named one."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        entries = """\
- id: owner-absolute-stop-work-order-lifted
  what: "The standing stop-work order is genuinely, fully complete."
  status: approved
"""
        owner_decisions_path = _init_owner_decisions_repo(d, entries, commit=True)
        rg = _load_rg("rg_sw_6", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-lifted-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is True, result


def test_committed_exemption_wrong_status_still_blocks():
    """A committed entry that is NOT status: approved (e.g. still pending)
    must not be honored."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        entries = """\
- id: phase2-subphase1-stop-work-order-exemption
  what: "Exempt task_identity test-pending-1 from the standing stop-work order"
  needed_action: "Allow test-pending-1 to proceed"
  status: pending
"""
        owner_decisions_path = _init_owner_decisions_repo(d, entries, commit=True)
        rg = _load_rg("rg_sw_7", _base_env(scratch_db, owner_decisions_path))

        os.environ.update(_base_env(scratch_db, owner_decisions_path))
        try:
            result = rg.submit(
                {"task_identity": "test-pending-1", "task_kind": "veridian_task_create",
                 "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            for k in _base_env(scratch_db, owner_decisions_path):
                os.environ.pop(k, None)

        assert result["accepted"] is False, result


def test_dispatch_one_defense_in_depth_blocks_preexisting_queued_row():
    """A row that reaches status='queued' by a route OTHER than submit()
    (e.g. one queued before this gate existed) must still be blocked at the
    real mechanical pickup point, dispatch_one() -- defense in depth, not
    just an admission-time check."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        owner_decisions_path = _init_owner_decisions_repo(d, "[]\n")
        env = _base_env(scratch_db, owner_decisions_path)
        rg = _load_rg("rg_sw_8", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-preexisting-queued-1", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "compliance-tracker", "title": "t", "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ.update(env)
        try:
            result = rg.dispatch_one()
        finally:
            for k in env:
                os.environ.pop(k, None)

        assert result["action"] == "blocked_stop_work_order", result
        assert result["outcome"] == "rejected", result

        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status, reason FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "rejected_duplicate", dict(row)
        assert "BLOCKED by standing stop-work order" in row["reason"]


def test_git_committed_file_text_ignores_working_tree_only_edit():
    """Direct unit test of the real primitive this whole gate depends on:
    an uncommitted working-tree edit must never be visible through
    _git_committed_file_text(), only real committed HEAD content."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        owner_decisions_path = _init_owner_decisions_repo(d, "committed: true\n", commit=True)
        rg = _load_rg("rg_sw_9", _base_env(scratch_db, owner_decisions_path))

        committed_text = rg._git_committed_file_text(owner_decisions_path)
        assert committed_text is not None and "committed: true" in committed_text

        with open(owner_decisions_path, "a") as f:
            f.write("uncommitted_addition: true\n")
        still_committed_text = rg._git_committed_file_text(owner_decisions_path)
        assert "uncommitted_addition" not in still_committed_text
        assert "committed: true" in still_committed_text


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
