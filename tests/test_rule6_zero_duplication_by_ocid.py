#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 6
(UMR-20260804-180711-7f96, UMR-20260804-205741-cf3f, citing OCID-068's own
UMR-20260804-170055-a069): "zero duplication, before creating any new UMR
verify the OCID, the task identity, the umr_tasks table, active tasks, and
canonical registries, and if a match is found return the existing UMR
instead of creating a duplicate."

Covers find_active_umr_by_ocid() and its wiring into submit() -- the
OCID-dimension complement to find_active_umr_by_identity()'s pre-existing
task_identity check. Every test uses a real, isolated, temp-file SQLite
database seeded with the real schema -- never the live production
database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_r6", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def test_second_submission_for_same_ocid_while_first_still_active_is_rejected():
    """The real, narrow case Rule 6 targets: a genuinely CONCURRENT second
    UMR for an OCID that already has one actively in flight."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule6_dup", "resource_governor.py", env=env)
        # Real issue #980's standing stop-work-order gate is out of scope for
        # this file (Rule 6 dedup, not governance) -- disabled the same way
        # tests/test_flag_stale_queued_tasks.py disables EMERGENCY_STOP_PATH
        # for tests unrelated to that gate: a direct module-attribute
        # override, not a real exemption.
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-rule6-dup-1", "task_kind": "veridian_task_create",
                           "inputs": {"ocid_number": "OCID-777", "repo": "compliance-tracker",
                                      "title": "x", "prompt": "x"}},
                tier=2, source_trigger="unit_test",
            )
            assert first["accepted"] is True, first

            second = rg.submit(
                task_spec={"task_identity": "test-rule6-dup-2", "task_kind": "veridian_task_create",
                           "inputs": {"ocid_number": "OCID-777", "repo": "compliance-tracker",
                                      "title": "y", "prompt": "y"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert second["accepted"] is False, second
        assert "OCID-777" in second["reason"] and first["umr_id"] in second["reason"], second["reason"]
        print(f"PASS: test_second_submission_for_same_ocid_while_first_still_active_is_rejected -> {second['reason']}")


def test_second_submission_for_same_ocid_after_first_goes_terminal_is_allowed():
    """Rule 6's real, deliberate scope limit: once the first UMR for an OCID
    goes terminal, a genuinely NEW UMR for the same OCID must be allowed --
    this session's own real history has many legitimate sequential UMRs per
    OCID (e.g. OCID-068 itself, ~15 real UMRs across distinct PM
    directives). Blocking this would be a real, wrong over-application."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule6_sequential", "resource_governor.py", env=env)
        sbr = _load("sbr_rule6_sequential", "superboss-register.py", env=env)
        # Real issue #980's standing stop-work-order gate is out of scope for
        # this file (Rule 6 dedup, not governance) -- see the sibling test
        # above for why this override is the right fix, not a real exemption.
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-rule6-seq-1", "task_kind": "veridian_task_create",
                           "inputs": {"ocid_number": "OCID-778", "repo": "compliance-tracker",
                                      "title": "x", "prompt": "x"}},
                tier=2, source_trigger="unit_test",
            )
            assert first["accepted"] is True, first

            conn = sbr._connect()
            try:
                sbr.update_umr_task(conn, first["umr_id"], status="completed", reason="test: simulated real completion")
                conn.commit()
            finally:
                conn.close()

            second = rg.submit(
                task_spec={"task_identity": "test-rule6-seq-2", "task_kind": "veridian_task_create",
                           "inputs": {"ocid_number": "OCID-778", "repo": "compliance-tracker",
                                      "title": "y", "prompt": "y"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert second["accepted"] is True, second
        assert second["umr_id"] != first["umr_id"], "a genuinely new, distinct UMR is expected here, not a reuse"
        print(f"PASS: test_second_submission_for_same_ocid_after_first_goes_terminal_is_allowed -> new umr_id={second['umr_id']}")


def test_omitted_ocid_number_unaffected_by_rule6():
    """The overwhelming majority of real callers omit ocid_number entirely
    -- two submissions with different task_identity and no ocid_number must
    never collide via this check."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule6_no_ocid", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-rule6-no-ocid-1", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@x.service", "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
            second = rg.submit(
                task_spec={"task_identity": "test-rule6-no-ocid-2", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@y.service", "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert first["accepted"] is True and second["accepted"] is True, (first, second)
        print("PASS: test_omitted_ocid_number_unaffected_by_rule6")


def test_find_active_umr_by_ocid_direct_no_match_returns_none():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        sbr = _load("sbr_rule6_direct_none", "superboss-register.py", env=env)
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        result = sbr.find_active_umr_by_ocid(conn, "OCID-999999-never-used")
        conn.close()
        assert result is None, result
        print("PASS: test_find_active_umr_by_ocid_direct_no_match_returns_none")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
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
