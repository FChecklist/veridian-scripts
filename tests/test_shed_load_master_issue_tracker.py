#!/usr/bin/env python3
"""Real test for UMR171945-0018 (master_issue_tracker automatic-write
coverage audit, governing chain UMR-20260806-171945-5767): proves
resource_governor.py's Stage 2 load-shedding cascade (_shed_load()) now
writes a real master_issue_tracker row automatically, the same real
coverage Stage 3's hard-stop cascade (_write_emergency_stop()) already had
-- a real, previously-confirmed gap (grep of every
_record_master_issue_if_new( call site before this fix found none inside
_shed_load()).

SAFETY: a first draft of this test set SUPERBOSS_REGISTER_DB only for the
duration of importing resource_governor.py, then restored the environment
in a `finally` block before ever calling _shed_load(). That was a real,
live incident during development -- resource_governor.py's superboss-
register.py handle (_sbr) is a lazily-initialized singleton, only created
on the FIRST call inside _shed_load() itself, by which point the env var
had already been reverted, so the "isolated" test actually connected to
the real, live production DB and wrote/mutated real rows (caught,
diagnosed, and reverted by hand afterward -- see UMR171945-0018 close-issue
notes for the full incident record). Fixed here two ways: (1) the env var
is held for the ENTIRE test body, only restored at the very end, and
(2) a hard assertion checks rg._superboss_register().DB_PATH against the
scratch path BEFORE ever calling _shed_load(), so any future regression of
this kind fails loudly instead of silently touching production.

_run() (the real systemctl wrapper) is also monkeypatched to a no-op
recorder so this test never sends a real SIGTERM to any real process,
independent of the DB isolation above.
"""
import importlib.util
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_shedload_seed", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, unit_name, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,?,4,'running','unit_test','veridian_task_create',?,'{}','{}','{}')",
        ("UMR-TEST-SHEDLOAD-VICTIM", "shedload-victim-identity",
         datetime.now(timezone.utc).isoformat(), "veridian-worker@test-shedload-victim.service"),
    )
    conn.commit()
    conn.close()


class _IsolatedGovernor:
    """Holds SUPERBOSS_REGISTER_DB for the whole `with` scope (not just
    module import), and hard-asserts the governor's own lazily-created sbr
    handle really points at the scratch DB before returning control to the
    caller -- see module docstring for why this is load-bearing, not
    defensive-programming theater."""

    def __init__(self, scratch_db, modname):
        self.scratch_db = scratch_db
        self.modname = modname
        self._old_env = None

    def __enter__(self):
        self._old_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = self.scratch_db
        spec = importlib.util.spec_from_file_location(
            self.modname, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        rg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rg)
        # Force the lazy singleton to initialize NOW, while the env var is
        # still definitely set, and hard-verify it resolved to the scratch
        # DB -- never trust this silently.
        sbr = rg._superboss_register()
        assert sbr.DB_PATH == self.scratch_db, (
            f"REFUSING to proceed: resource_governor.py's real DB handle resolved to "
            f"{sbr.DB_PATH!r}, not the scratch DB {self.scratch_db!r}. Calling _shed_load() "
            f"here would risk mutating the real production database again."
        )
        self.rg = rg
        return rg

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._old_env is None:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        else:
            os.environ["SUPERBOSS_REGISTER_DB"] = self._old_env
        return False


def test_shed_load_writes_a_real_master_issue_tracker_row_automatically():
    """The real boolean test UMR171945-0018 itself specifies: trigger one
    real, safe, synthetic failure condition covered by this mechanism, and
    confirm a real new master_issue_tracker row appears automatically, no
    manual add-issue call."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        with _IsolatedGovernor(scratch_db, "rg_shedload_test") as rg:
            conn = sqlite3.connect(scratch_db)
            before = conn.execute(
                "SELECT COUNT(*) FROM master_issue_tracker WHERE issue_id=?",
                ("RG-EMERGENCY-STOP-SHEDLOAD",),
            ).fetchone()[0]
            conn.close()
            assert before == 0, "test setup invariant violated -- row already existed pre-trigger"

            real_kill_calls = []

            def fake_run(cmd, **kw):
                real_kill_calls.append(cmd)

                class _Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return _Result()

            rg._run = fake_run  # never send a real SIGTERM to any real process

            victim_unit = rg._shed_load(state={"cpu": 3}, metrics={"cpu": 99.5})

            assert victim_unit == "veridian-worker@test-shedload-victim.service", victim_unit
            assert len(real_kill_calls) == 1, real_kill_calls
            assert real_kill_calls[0][:4] == ["systemctl", "--user", "kill", "-s"], real_kill_calls[0]

            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM master_issue_tracker WHERE issue_id=?",
                ("RG-EMERGENCY-STOP-SHEDLOAD",),
            ).fetchone()
            conn.close()
            assert row is not None, "no real master_issue_tracker row was written automatically"
            assert row["linked_umr_id"] == "UMR-20260808-074726-d105", dict(row)
            assert "_shed_load" in (row["linked_source"] or "") or \
                "load-shedding" in (row["issue_identified"] or ""), dict(row)

        # Belt-and-braces: confirm the real production DB path was never
        # even referenced by this test process's env in a way that could
        # leak past the `with` block.
        assert os.environ.get("SUPERBOSS_REGISTER_DB") != scratch_db
        print("PASS: test_shed_load_writes_a_real_master_issue_tracker_row_automatically")


def test_shed_load_second_trigger_is_a_real_dedup_no_op():
    """A second, later Stage-2 shed does NOT insert a second row -- matches
    _record_master_issue_if_new()'s own documented dedup contract (a
    recurring trip of an already-known issue class must never spam a fresh
    row per occurrence)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        conn = sqlite3.connect(scratch_db)
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
            "source_trigger, task_kind, unit_name, inputs_json, outputs_json, metadata_json) "
            "VALUES (?,?,?,4,'running','unit_test','veridian_task_create',?,'{}','{}','{}')",
            ("UMR-TEST-SHEDLOAD-VICTIM-2", "shedload-victim-identity-2",
             datetime.now(timezone.utc).isoformat(), "veridian-worker@test-shedload-victim-2.service"),
        )
        conn.commit()
        conn.close()

        with _IsolatedGovernor(scratch_db, "rg_shedload_dedup_test") as rg:
            rg._run = lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            rg._shed_load(state={"cpu": 3}, metrics={"cpu": 99.5})
            rg._shed_load(state={"cpu": 4}, metrics={"cpu": 99.6})

            conn = sqlite3.connect(scratch_db)
            count = conn.execute(
                "SELECT COUNT(*) FROM master_issue_tracker WHERE issue_id=?",
                ("RG-EMERGENCY-STOP-SHEDLOAD",),
            ).fetchone()[0]
            conn.close()
            assert count == 1, f"expected exactly 1 deduplicated row after 2 real triggers, found {count}"
        print("PASS: test_shed_load_second_trigger_is_a_real_dedup_no_op")


if __name__ == "__main__":
    test_shed_load_writes_a_real_master_issue_tracker_row_automatically()
    test_shed_load_second_trigger_is_a_real_dedup_no_op()
    print("ALL PASS")
