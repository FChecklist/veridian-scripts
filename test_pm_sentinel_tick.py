#!/usr/bin/env python3
"""
Real test for pm-sentinel-tick.sh (server-native hourly PM sentinel,
addendum to P1 UMR-20260806-171945-5767): proves one real tick run against a
seeded killed-status umr_tasks row dispatches a real RCA task through the
existing dispatch-owner-task.sh --no-relay front door, and that a second real
tick run against the same still-in-flight dispatch does NOT duplicate it.
Also proves the 2026-08-13 Owner-decision escalation-scope amendment
(addendum UMR-20260813-091633-8b6a): a seeded killed row whose real reason
text is a genuine financial matter (payment/invoice/billing language) is
escalated to the Owner via notify-owner.py instead of being auto-dispatched.

Runs the real pm-sentinel-tick.sh as a real subprocess (not an in-process
import -- it is a bash script), against a real, isolated sqlite3 COPY of the
live Superboss Register DB (sqlite3 backup API, same corruption-avoidance
convention as test_resource_governor_owner_priority_advance.py -- never a raw
file copy, never the live DB). The copy's own umr_tasks / owner_priority_sequence
tables are wiped after the backup (real DELETEs against the COPY only) so this
test's own seeded fixture is the only real umr_tasks content the tick sees --
deterministic, no real live killed/running/completed_unmerged rows leaking in
and triggering unrelated real dispatches or `gh` network calls.

SUPERBOSS_REGISTER_DB (resolve_superboss_db_path()'s own real testability
seam, read by every subprocess resource_governor.py / superboss-register.py /
task-gateway.py call pm-sentinel-tick.sh makes) points every one of those real
subprocess calls at the isolated copy -- this test never writes to the real
live database. The financial-escalation test similarly points
PM_SENTINEL_NOTIFY_OWNER_SCRIPT (pm-sentinel-tick.sh's own real testability
seam) at a real, throwaway stand-in script that records its real argv to a
file instead of calling the real Resend API -- never a real Owner email sent
by a test run.
"""
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINEL_SH = os.path.join(HERE, "pm-sentinel-tick.sh")
SBR_PATH = os.path.join(HERE, "superboss-register.py")
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

TEST_UMR_ID = "UMR-TESTFIX-20260101-000000-kill1"


class PmSentinelTickKilledRowTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_test_")
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

        # Real DELETEs against the COPY only -- wipe pre-existing real rows so
        # this test's own seeded fixture is the only umr_tasks content the
        # tick sees. owner_priority_sequence is wiped too so Check 1 (tracked-
        # chain status) has nothing real to iterate, isolating this test to
        # Check 2a (killed-status RCA) alone.
        conn = sqlite3.connect(self.copy_path)
        conn.execute("DELETE FROM umr_tasks")
        conn.execute("DELETE FROM umr_tasks_fts")
        try:
            conn.execute("DELETE FROM owner_priority_sequence")
        except sqlite3.OperationalError:
            pass  # table may not exist on an older schema copy -- harmless
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, "
            "status, source_trigger, task_kind, reason) VALUES (?,?,datetime('now'),1,"
            "'killed','test_seed','veridian_task_create',?)",
            (TEST_UMR_ID, "test-seed-killed-task-0001",
             "test-seeded: stuck-task SIGKILL, no exit after grace period"),
        )
        conn.commit()
        conn.close()

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        # Real testability seam resource_governor.py itself already documents
        # -- disables the standing stop-work-order gate (which otherwise
        # requires a real git fetch against origin/main) for this test run
        # only; production ticks never set this.
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=60,
        )

    def _umr_tasks_rows(self):
        conn = sqlite3.connect(self.copy_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT umr_id, task_identity, status, inputs_json FROM umr_tasks").fetchall()]
        conn.close()
        return rows

    def test_first_tick_dispatches_real_rca_for_seeded_killed_row(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(f"DISPATCHING for rca:{TEST_UMR_ID}", result.stdout)
        self.assertIn(f"DISPATCHED rca:{TEST_UMR_ID} ->", result.stdout)
        self.assertIn("pm-sentinel-tick done: 1/5 new dispatches this tick", result.stdout)

        rows = self._umr_tasks_rows()
        # The original seeded killed row, plus exactly one new real
        # umr_tasks row (the RCA dispatch), both in the isolated copy.
        self.assertEqual(len(rows), 2, msg=rows)
        new_rows = [r for r in rows if r["umr_id"] != TEST_UMR_ID]
        self.assertEqual(len(new_rows), 1, msg=rows)
        new_row = new_rows[0]
        self.assertEqual(new_row["status"], "queued")
        inputs = json.loads(new_row["inputs_json"])
        self.assertIn(TEST_UMR_ID, inputs.get("prompt", ""))
        self.assertIn("killed", inputs.get("prompt", ""))

        # Real in-flight state was recorded for this exact target.
        with open(self.state_file) as f:
            state = json.load(f)
        self.assertIn(f"rca:{TEST_UMR_ID}", state)
        self.assertEqual(state[f"rca:{TEST_UMR_ID}"]["dispatched_umr"], new_row["umr_id"])

    def test_second_tick_does_not_duplicate_already_in_flight_dispatch(self):
        first = self._run_tick()
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        rows_after_first = self._umr_tasks_rows()
        self.assertEqual(len(rows_after_first), 2, msg=rows_after_first)

        second = self._run_tick()
        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        self.assertIn(f"IN-FLIGHT: rca:{TEST_UMR_ID}", second.stdout)
        self.assertIn("0/5 new dispatches this tick", second.stdout)

        rows_after_second = self._umr_tasks_rows()
        # Still exactly 2 real rows -- no duplicate RCA dispatch was created.
        self.assertEqual(len(rows_after_second), 2, msg=rows_after_second)
        self.assertEqual(
            sorted(r["umr_id"] for r in rows_after_first),
            sorted(r["umr_id"] for r in rows_after_second),
        )


class PmSentinelTickFinancialEscalationTest(unittest.TestCase):
    """Real test for the 2026-08-13 Owner-decision escalation-scope
    amendment: a genuine financial-decision gap is escalated to the Owner
    (via notify-owner.py) instead of auto-dispatched."""

    FINANCIAL_UMR_ID = "UMR-TESTFIX-20260101-000000-fin01"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_fin_test_")
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

        conn = sqlite3.connect(self.copy_path)
        conn.execute("DELETE FROM umr_tasks")
        conn.execute("DELETE FROM umr_tasks_fts")
        try:
            conn.execute("DELETE FROM owner_priority_sequence")
        except sqlite3.OperationalError:
            pass
        # Real recorded reason is a genuine financial matter (payment/
        # invoice/billing language) -- this is what dispatch_gap()'s
        # is_financial_decision() check must catch.
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, "
            "status, source_trigger, task_kind, reason) VALUES (?,?,datetime('now'),1,"
            "'killed','test_seed','veridian_task_create',?)",
            (self.FINANCIAL_UMR_ID, "test-seed-killed-task-fin01",
             "test-seeded: job killed before completing a real vendor invoice "
             "payment and subscription billing reconciliation"),
        )
        conn.commit()
        conn.close()

        # Real, throwaway stand-in for notify-owner.py: records its real
        # argv to a file instead of calling the real Resend API. Proves the
        # real front door was called with the right subject/body, without
        # ever sending a real Owner email from a test run.
        self.notify_log = os.path.join(self.tmpdir, "notify-calls.json")
        self.fake_notify = os.path.join(self.tmpdir, "fake-notify-owner.py")
        with open(self.fake_notify, "w") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import json, sys, os\n"
                "args = sys.argv[1:]\n"
                "d = {}\n"
                "it = iter(args)\n"
                "for a in it:\n"
                "    if a in ('--subject', '--body', '--dedupe-key'):\n"
                "        d[a.lstrip('-').replace('-', '_')] = next(it)\n"
                "calls = []\n"
                "log_path = os.environ['FAKE_NOTIFY_LOG']\n"
                "if os.path.exists(log_path):\n"
                "    with open(log_path) as f:\n"
                "        calls = json.load(f)\n"
                "calls.append(d)\n"
                "with open(log_path, 'w') as f:\n"
                "    json.dump(calls, f)\n"
                "print('FAKE SENT')\n"
            )
        os.chmod(self.fake_notify, os.stat(self.fake_notify).st_mode | stat.S_IEXEC)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_NOTIFY_OWNER_SCRIPT"] = self.fake_notify
        self.env["FAKE_NOTIFY_LOG"] = self.notify_log
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=60,
        )

    def _umr_tasks_rows(self):
        conn = sqlite3.connect(self.copy_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT umr_id, task_identity, status FROM umr_tasks").fetchall()]
        conn.close()
        return rows

    def test_financial_gap_escalates_to_owner_instead_of_dispatching(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(
            f"NEEDS OWNER DECISION (financial): rca:{self.FINANCIAL_UMR_ID}",
            result.stdout,
        )
        # Never auto-dispatched for a genuine financial gap.
        self.assertNotIn(f"DISPATCHING for rca:{self.FINANCIAL_UMR_ID}", result.stdout)
        self.assertIn("pm-sentinel-tick done: 0/5 new dispatches this tick", result.stdout)

        # Real seeded row only -- no new umr_tasks row was created.
        rows = self._umr_tasks_rows()
        self.assertEqual(len(rows), 1, msg=rows)
        self.assertEqual(rows[0]["umr_id"], self.FINANCIAL_UMR_ID)

        # No in-flight dispatch state recorded (this was a human escalation,
        # not a dispatch).
        with open(self.state_file) as f:
            state = json.load(f)
        self.assertNotIn(f"rca:{self.FINANCIAL_UMR_ID}", state)

        # The real stand-in notify-owner.py front door was actually called,
        # citing the real UMR id and real evidence in the body.
        with open(self.notify_log) as f:
            calls = json.load(f)
        self.assertEqual(len(calls), 1, msg=calls)
        self.assertIn("NEEDS OWNER DECISION (financial)", calls[0]["subject"])
        self.assertIn(self.FINANCIAL_UMR_ID, calls[0]["body"])
        self.assertIn("invoice", calls[0]["body"])


if __name__ == "__main__":
    unittest.main()
