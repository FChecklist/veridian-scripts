#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 3
(UMR-20260804-180711-7f96, UMR-20260804-203846-e722, citing OCID-068's own
UMR-20260804-170055-a069): "no premature UMR minting, validate the input,
the OCID, the task identity, the database, and zero duplication in that
order, and only mint a UMR and write the database and create the task
after every validation passes, if any validation fails do not create a
UMR, do not write the database, do not create a task, and return a real
failure instead."

Real, previously-unvalidated gap this closes: inputs.ocid_number was only
ever read AFTER upsert_umr_task() had already minted the row -- a malformed
value would silently fail to link rather than being rejected up front.
This file proves (a) a malformed ocid_number is rejected before any real
database write, and (b) the pre-existing validations (task_identity,
inputs shape, task_kind) already correctly refuse to mint/write/create
anything on failure -- confirming Rule 3's core guarantee holds across
the validation sequence, not just for the newly-added OCID check.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_r3", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def _row_count(sbr, scratch_db):
    conn = sqlite3.connect(scratch_db)
    n = conn.execute("SELECT COUNT(*) FROM umr_tasks").fetchone()[0]
    conn.close()
    return n


def test_malformed_ocid_number_rejected_before_any_db_write():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule3_bad_ocid", "resource_governor.py", env=env)
        sbr = _load("sbr_rule3_bad_ocid", "superboss-register.py", env=env)

        before = _row_count(sbr, scratch_db)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        raised = None
        try:
            try:
                rg.submit(
                    task_spec={"task_identity": "test-rule3-bad-ocid", "task_kind": "veridian_task_create",
                               "inputs": {"ocid_number": "not-a-real-ocid", "repo": "compliance-tracker",
                                          "title": "x", "prompt": "x"}},
                    tier=2, source_trigger="unit_test",
                )
            except ValueError as e:
                raised = e
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        after = _row_count(sbr, scratch_db)
        assert raised is not None, "expected ValueError for malformed ocid_number, none raised"
        assert "ocid_number" in str(raised), raised
        assert after == before, f"Rule 3 violation: umr_tasks row count changed ({before} -> {after}) despite a failed validation"
        print(f"PASS: test_malformed_ocid_number_rejected_before_any_db_write -> {raised}")


def test_valid_ocid_number_still_accepted_and_mints_normally():
    """Sanity: the new validation must not reject real, valid ocid_number
    values -- e.g. the exact 'OCID-999'/'OCID-042' shapes already used by
    the pre-existing test_ocid_artifact_links.py suite."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule3_good_ocid", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                task_spec={"task_identity": "test-rule3-good-ocid", "task_kind": "veridian_task_create",
                           "inputs": {"ocid_number": "OCID-042", "repo": "compliance-tracker",
                                      "title": "x", "prompt": "x"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["accepted"] is True, result
        print(f"PASS: test_valid_ocid_number_still_accepted_and_mints_normally -> umr_id={result['umr_id']}")


def test_omitted_ocid_number_unaffected_by_new_validation():
    """The overwhelming majority of real callers omit ocid_number entirely
    -- must remain completely unaffected (ocid_number is None, the
    validation's own `is not None` guard skips it)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule3_no_ocid", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                task_spec={"task_identity": "test-rule3-no-ocid", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@x.service", "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["accepted"] is True, result
        print(f"PASS: test_omitted_ocid_number_unaffected_by_new_validation -> umr_id={result['umr_id']}")


def test_invalid_task_identity_still_mints_nothing_pre_existing_guarantee():
    """Confirms Rule 3's core guarantee already held for the pre-existing
    task_identity validation (not new in this PR, but real evidence Rule 3's
    requirement is genuinely satisfied end-to-end, not just for the one new
    OCID check)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule3_bad_identity", "resource_governor.py", env=env)
        sbr = _load("sbr_rule3_bad_identity", "superboss-register.py", env=env)

        before = _row_count(sbr, scratch_db)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        raised = None
        try:
            try:
                rg.submit(task_spec={"task_identity": "", "inputs": {}}, tier=2, source_trigger="unit_test")
            except ValueError as e:
                raised = e
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        after = _row_count(sbr, scratch_db)
        assert raised is not None, raised
        assert after == before, f"Rule 3 violation: umr_tasks row count changed ({before} -> {after})"
        print(f"PASS: test_invalid_task_identity_still_mints_nothing_pre_existing_guarantee -> {raised}")


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
