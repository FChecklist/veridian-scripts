#!/usr/bin/env python3
"""Real end-to-end tests for the OCID-068 structured OCID/UMR/PR/commit/
file-path traceability requirement (Owner real-time implementation override,
UMR-20260804-172011-b839, on the standing hard-rule-7 lock; citing OCID-068's
own UMR-20260804-164106-3fb8 and the requirement addendum
UMR-20260804-170055-a069). Covers both real wiring call sites: (1)
resource_governor.py's submit(), the real UMR-creation chokepoint; (2)
supervisor-entrypoint.sh's merge-time wiring (tested here as the exact
literal logic copied from that file's inline python3 -c block, with only the
hardcoded /opt/veridian/scripts/ path substituted for this repo checkout, so
it never touches the real live deployed copy). Every test uses a real,
isolated, temp-file SQLite database seeded with the real schema -- never the
live production database.
"""
import importlib.util
import os
import re
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    """Real finding from writing these tests: resolve_superboss_db_path()'s
    own verification (OCID-068, UMR-20260804-180210-9e2c) means
    SUPERBOSS_REGISTER_DB pointing at a not-yet-existing path no longer
    lazily bootstraps a brand-new DB -- step 2's own exists+non-zero
    pre-check correctly falls through to the real default path instead
    (safe, correct production behavior: a bad/missing override never
    silently creates a fresh unverified DB). So every test fixture here
    pre-creates a real, valid, schema'd scratch DB first, exactly like a
    real production DB always already exists."""
    spec = importlib.util.spec_from_file_location("sbr_seed", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


def _load(name, filename, env=None):
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_call_site_1_resource_governor_submit():
    """A real submit() call whose task_spec carries an ocid_number in its
    inputs must record one real ocid_artifact_links row, readable back
    exactly."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_call_site_1", "resource_governor.py", env=env)
        sbr = _load("sbr_test_call_site_1", "superboss-register.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                task_spec={
                    "task_identity": "test-ocid-links-call-site-1",
                    "task_kind": "veridian_task_create",
                    "inputs": {"ocid_number": "OCID-999", "repo": "compliance-tracker"},
                },
                tier=2, source_trigger="unit_test",
            )
            assert result["accepted"] is True, result
            umr_id = result["umr_id"]

            conn = sbr._connect()
            try:
                links = sbr.query_ocid_artifact_links(conn, ocid_number="OCID-999")
            finally:
                conn.close()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert len(links) == 1, f"expected exactly 1 real link row, got {len(links)}"
        link = links[0]
        assert link["umr_id"] == umr_id, link
        assert link["repo"] == "compliance-tracker", link
        assert link["link_kind"] == "registration", link
        assert link["pr_number"] is None, link
        print(f"PASS: test_call_site_1_resource_governor_submit -> {link}")


def test_call_site_2_supervisor_merge_wiring():
    """The exact literal merge-time logic from supervisor-entrypoint.sh's
    inline python3 -c block (only the hardcoded live script path
    substituted) must record one real ocid_artifact_links row, readable
    back exactly, keyed off a real umr_tasks row created via a real
    submit() call first (matching the real real-world ordering: a UMR must
    exist before a PR for it can be merged)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_call_site_2", "resource_governor.py", env=env)

        task_id = "test-ocid-links-call-site-2"
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                task_spec={"task_identity": task_id, "task_kind": "veridian_task_create", "inputs": {}},
                tier=2, source_trigger="unit_test",
            )
            assert result["accepted"] is True, result

            branch, repo = "fix/ocid042-something-real", "compliance-tracker"
            pr_url, merge_commit_sha = "https://github.com/FChecklist/compliance-tracker/pull/12345", "abc123def456"

            # --- exact literal logic from supervisor-entrypoint.sh's merge block ---
            _spec = importlib.util.spec_from_file_location(
                'superboss_register_supervisor_test', os.path.join(SCRIPTS_DIR, 'superboss-register.py'))
            sbr = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(sbr)
            m = re.search(r'ocid-?0*([0-9]+)', branch, re.IGNORECASE)
            assert m, "the real branch-name regex must match a real ocid-NNN branch"
            ocid_number = f'OCID-{int(m.group(1)):03d}'
            conn = sbr._connect()
            sbr._ensure_umr_table(conn)
            sbr._ensure_ocid_artifact_links_table(conn)
            rows = sbr.query_umr_tasks(conn, task_identity=task_id, limit=1)
            assert rows, "expected the real fixture umr_tasks row to be found by task_identity"
            sbr.insert_ocid_artifact_link(
                conn, ocid_number=ocid_number, umr_id=rows[0]['umr_id'], repo=repo,
                pr_number=int(pr_url.rstrip('/').rsplit('/', 1)[-1]),
                commit_sha=merge_commit_sha or None, link_kind='merge',
            )
            conn.commit()
            conn.close()
            # --- end exact literal logic ---

            conn = sbr._connect()
            try:
                links = sbr.query_ocid_artifact_links(conn, ocid_number="OCID-042")
            finally:
                conn.close()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert len(links) == 1, f"expected exactly 1 real link row, got {len(links)}"
        link = links[0]
        assert link["umr_id"] == result["umr_id"], link
        assert link["pr_number"] == 12345, link
        assert link["commit_sha"] == "abc123def456", link
        assert link["link_kind"] == "merge", link
        print(f"PASS: test_call_site_2_supervisor_merge_wiring -> {link}")


def test_insert_is_idempotent_on_repeat_call():
    """A second, identical insert_ocid_artifact_link() call for the same
    (ocid_number, umr_id, repo, pr_number, file_path) must not create a
    duplicate row -- real idempotency by explicit pre-insert check, not
    assumed from the SQL UNIQUE constraint alone (see the function's own
    docstring re: SQLite NULL semantics)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_idempotent", "superboss-register.py", env={"SUPERBOSS_REGISTER_DB": scratch_db})
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            conn = sbr._connect()
            sbr._ensure_umr_table(conn)
            sbr._ensure_ocid_artifact_links_table(conn)
            umr_id = sbr.upsert_umr_task(conn, {
                "task_identity": "idempotency-test", "tier": 2, "source_trigger": "unit_test",
            })
            conn.commit()
            id1 = sbr.insert_ocid_artifact_link(conn, ocid_number="OCID-777", umr_id=umr_id, repo="r", link_kind="registration")
            id2 = sbr.insert_ocid_artifact_link(conn, ocid_number="OCID-777", umr_id=umr_id, repo="r", link_kind="registration")
            conn.commit()
            assert id1 == id2, f"expected the same real row id on repeat insert, got {id1} vs {id2}"
            links = sbr.query_ocid_artifact_links(conn, ocid_number="OCID-777")
            conn.close()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]
        assert len(links) == 1, f"expected exactly 1 real row after 2 identical inserts, got {len(links)}"
        print(f"PASS: test_insert_is_idempotent_on_repeat_call -> id={id1}")


def test_submit_gracefully_handles_broken_db_path():
    """Real regression test for the independent review's real finding on
    PR #20: resource_governor.py's submit() must never let an uncaught
    SuperbossDbPathError (raised by superboss-register.py's own
    resolve_superboss_db_path(), OCID-068 UMR-20260804-180210-9e2c) crash
    task submission -- it must fail open with a real, clearly-labeled
    rejection instead, matching the same philosophy already applied to the
    reuse-check three lines above submit()'s own _superboss_register() call.
    Forces a real SuperbossDbPathError via SUPERBOSS_REGISTER_DB pointing at
    a real, existing, non-zero-size file that is a real SQLite database but
    is missing the umr_tasks table (so step 2's own exists+non-zero
    pre-check accepts it as the candidate, and step 4's schema check then
    genuinely fails) -- not a synthetic mock, the real function's real
    failure path."""
    with tempfile.TemporaryDirectory() as d:
        wrong_schema_db = _make_wrong_schema_db(d)
        env = {"SUPERBOSS_REGISTER_DB": wrong_schema_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_broken_db", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = wrong_schema_db
        try:
            result = rg.submit(
                task_spec={"task_identity": "test-broken-db-path", "task_kind": "veridian_task_create", "inputs": {}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["accepted"] is False, result
        assert result["umr_id"] is None, result
        assert "superboss_register_unavailable" in result["reason"], result
        assert "umr_tasks table" in result["reason"], result
        print(f"PASS: test_submit_gracefully_handles_broken_db_path -> {result['reason']}")


def _make_wrong_schema_db(d):
    """Real fixture, shared by every broken-DB regression test below: a
    real, existing, non-zero-size SQLite file lacking the umr_tasks table,
    so resolve_superboss_db_path()'s step 2 exists+non-zero pre-check
    accepts it as the real candidate and step 4's schema check then
    genuinely, really fails -- not a synthetic mock of the failure."""
    path = os.path.join(d, "wrong-schema.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert os.path.getsize(path) > 0
    return path


def test_dispatch_one_gracefully_handles_broken_db_path():
    """Real regression test for the independent review round 2's finding:
    dispatch_one()'s own docstring promises 'Never raises for a normal
    nothing-to-do/frozen outcome' -- a broken Superboss Register must be the
    same kind of real, non-raising outcome, not an uncaught crash of the
    real dispatch loop."""
    with tempfile.TemporaryDirectory() as d:
        wrong_schema_db = _make_wrong_schema_db(d)
        env = {"SUPERBOSS_REGISTER_DB": wrong_schema_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_dispatch_one_broken", "resource_governor.py", env=env)
        os.environ["SUPERBOSS_REGISTER_DB"] = wrong_schema_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]
        assert result["action"] == "superboss_unavailable", result
        assert "umr_tasks table" in result["detail"], result
        print(f"PASS: test_dispatch_one_gracefully_handles_broken_db_path -> {result['action']}")


def test_scan_stuck_tasks_gracefully_handles_broken_db_path():
    """Real regression test: scan_stuck_tasks() (the real SIGTERM/SIGKILL
    stuck-task safety net) must never crash on a broken Superboss Register
    -- it returns the same real, empty shape as the ordinary
    nothing-stuck case, real failure still surfaced via ATTENTION.md."""
    with tempfile.TemporaryDirectory() as d:
        wrong_schema_db = _make_wrong_schema_db(d)
        env = {"SUPERBOSS_REGISTER_DB": wrong_schema_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_scan_stuck_broken", "resource_governor.py", env=env)
        os.environ["SUPERBOSS_REGISTER_DB"] = wrong_schema_db
        try:
            result = rg.scan_stuck_tasks()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]
        assert result == [], result
        print(f"PASS: test_scan_stuck_tasks_gracefully_handles_broken_db_path -> {result}")


if __name__ == "__main__":
    tests = [
        test_call_site_1_resource_governor_submit,
        test_call_site_2_supervisor_merge_wiring,
        test_insert_is_idempotent_on_repeat_call,
        test_submit_gracefully_handles_broken_db_path,
        test_dispatch_one_gracefully_handles_broken_db_path,
        test_scan_stuck_tasks_gracefully_handles_broken_db_path,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
