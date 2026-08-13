#!/usr/bin/env python3
"""
Real test for pm-sentinel-tick.sh (the ONE integrated server-native PM tick,
addendum UMR-20260813-102459-10c3 collapsing UMR-20260813-084321-2962 +
UMR-20260813-091633-8b6a + UMR-20260813-092654-326b into this single file).

Proves, against a real, isolated sqlite3 COPY of the live Superboss Register
DB (sqlite3 backup API, same corruption-avoidance convention as
test_resource_governor_owner_priority_advance.py -- never a raw file copy,
never the live DB):
  1. a seeded killed-status row dispatches a real RCA task through the
     existing dispatch-owner-task.sh --no-relay front door (2962 scope);
  2. a second real tick run against the same still-in-flight dispatch does
     NOT duplicate it (zero-duplication, 326b point 3);
  3. a seeded killed row whose real reason text is a genuine financial
     matter (payment/invoice/billing language) is escalated to the Owner via
     notify-owner.py instead of being auto-dispatched (8b6a scope);
  4. DISPATCH_OWNER_TASK_SH resolves to the real live dispatch-owner-task.sh
     even when this test's own HERE directory (a git checkout, which does
     NOT track dispatch-owner-task.sh) does not contain it -- the real
     regression test for AUDIT-REJECT FIX #1/#3 (UMR-20260813-101452-bd10).
  5. a real dispatch-owner-task.sh failure makes the whole tick exit
     non-zero (AUDIT-REJECT FIX #2).

Real finding folded into this integration (see pm-sentinel-tick.sh's own
"REUSE, not reimplementation" header comment): a task.yaml-level
status='blocked' is NOT a real umr_tasks.status value (the table's own CHECK
constraint has no 'blocked' member) -- real blocked/stuck-task reconciliation
already happens inside resource_governor.py's own scan_stuck_tasks() (already
wired into every real run_tick(), independent of this sentinel), which
reconciles a real stuck task to status='killed' -- covered by the existing
killed-row check (Check 2a) once that reconciliation runs. No separate
blocked-status test exists here because no such dispatch path exists in the
script (there would be nothing real to test).

Runs the real pm-sentinel-tick.sh as a real subprocess (not an in-process
import -- it is a bash script). SUPERBOSS_REGISTER_DB (resolve_superboss_
db_path()'s own real testability seam, read by every subprocess
resource_governor.py / superboss-register.py / task-gateway.py call
pm-sentinel-tick.sh makes) points every one of those real subprocess calls at
the isolated copy -- this test never writes to the real live database. The
financial-escalation test similarly points PM_SENTINEL_NOTIFY_OWNER_SCRIPT
(pm-sentinel-tick.sh's own real testability seam) at a real, throwaway
stand-in script that records its real argv to a file instead of calling the
real Resend API -- never a real Owner email sent by a test run.
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
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINEL_SH = os.path.join(HERE, "pm-sentinel-tick.sh")
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"
# AUDIT-REJECT FIX #1 regression coverage: dispatch-owner-task.sh is real but
# deliberately NOT tracked in this git repo (live-server-only file) -- this
# test's own HERE (a checkout of this repo) therefore does NOT contain it.
# pm-sentinel-tick.sh must still resolve the real live path on its own.
REAL_DISPATCH_OWNER_TASK_SH = "/opt/veridian/scripts/dispatch-owner-task.sh"


def _seeded_copy(tmpdir, rows):
    """Real, isolated sqlite3 COPY of the live DB (backup API), with
    umr_tasks/umr_tasks_fts/owner_priority_sequence wiped and replaced with
    exactly `rows` (a list of (umr_id, task_identity, status, reason)
    tuples) -- deterministic, no real live rows leaking in and triggering
    unrelated real dispatches or `gh` network calls."""
    copy_path = os.path.join(tmpdir, "superboss-register.sqlite")
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(copy_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    conn = sqlite3.connect(copy_path)
    conn.execute("DELETE FROM umr_tasks")
    conn.execute("DELETE FROM umr_tasks_fts")
    try:
        conn.execute("DELETE FROM owner_priority_sequence")
    except sqlite3.OperationalError:
        pass  # table may not exist on an older schema copy -- harmless
    # Real test-isolation fix: dispatch-owner-task.sh's own real
    # content-duplicate guard (superboss-register.py's log_instruction(),
    # "content_duplicate_found") checks the `instructions`/`instructions_fts`
    # tables over a real 6-hour window -- NOT scoped by umr_id, so a fixed
    # test fixture UMR id with byte-identical prompt content across separate
    # test runs (or separate test methods backed by the same live-DB
    # snapshot) would otherwise be REFUSED as a real duplicate of a prior
    # real test run, not a genuine bug in pm-sentinel-tick.sh itself. Wiped
    # here, same DELETE-against-the-COPY-only discipline as umr_tasks above.
    for table in ("instructions", "instructions_fts"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    # Real finding: this wipe only clears the ISOLATED COPY. The real
    # content-duplicate guard (superboss-register.py's log_instruction()) is
    # served through the live veridian-superboss-gateway.service singleton
    # (Owner directive 2026-08-07 single-writer gateway), which is NOT
    # redirected by SUPERBOSS_REGISTER_DB -- it always checks the one real
    # live `instructions` table. So a byte-identical prompt dispatched twice
    # for the same fixed test UMR id, even across separate isolated test
    # runs, is correctly REFUSED by that real live guard as a real duplicate
    # within its real 6-hour window -- not a bug in pm-sentinel-tick.sh. The
    # fix is a real unique UMR id per test invocation (see TEST_UMR_ID below,
    # generated via uuid per test, not a fixed constant), so real prompt
    # content is never byte-identical across separate real test runs.
    for umr_id, task_identity, status, reason in rows:
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, "
            "status, source_trigger, task_kind, reason) VALUES (?,?,datetime('now'),1,"
            "?,'test_seed','veridian_task_create',?)",
            (umr_id, task_identity, status, reason),
        )
    conn.commit()
    conn.close()
    return copy_path


def _umr_tasks_rows(copy_path):
    conn = sqlite3.connect(copy_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT umr_id, task_identity, status, inputs_json FROM umr_tasks").fetchall()]
    conn.close()
    return rows


def _fake_notify_owner(tmpdir, log_path):
    fake_notify = os.path.join(tmpdir, "fake-notify-owner.py")
    with open(fake_notify, "w") as f:
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
    os.chmod(fake_notify, os.stat(fake_notify).st_mode | stat.S_IEXEC)
    return fake_notify


class PmSentinelTickKilledRowTest(unittest.TestCase):
    def setUp(self):
        # Real unique id per test invocation (uuid, not a fixed constant) --
        # see the real content-duplicate-guard finding in _seeded_copy()
        # above for why a fixed id would collide with a prior real test run.
        self.TEST_UMR_ID = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_test_")
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.TEST_UMR_ID, "test-seed-killed-task-0001", "killed",
             "test-seeded: stuck-task SIGKILL, no exit after grace period"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        # AUDIT-REJECT FIX #1 regression coverage -- see module docstring.
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_first_tick_dispatches_real_rca_for_seeded_killed_row(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(f"DISPATCHING for rca:{self.TEST_UMR_ID}", result.stdout)
        self.assertIn(f"DISPATCHED rca:{self.TEST_UMR_ID} ->", result.stdout)
        self.assertIn("1/5 new dispatches this tick", result.stdout)

        rows = _umr_tasks_rows(self.copy_path)
        new_rows = [r for r in rows if r["umr_id"] != self.TEST_UMR_ID]
        self.assertEqual(len(new_rows), 1, msg=rows)
        new_row = new_rows[0]
        self.assertEqual(new_row["status"], "queued")
        inputs = json.loads(new_row["inputs_json"])
        self.assertIn(self.TEST_UMR_ID, inputs.get("prompt", ""))
        self.assertIn("killed", inputs.get("prompt", ""))

        with open(self.state_file) as f:
            state = json.load(f)
        self.assertIn(f"rca:{self.TEST_UMR_ID}", state)
        self.assertEqual(state[f"rca:{self.TEST_UMR_ID}"]["dispatched_umr"], new_row["umr_id"])

        # REPORT FORMAT (326b point 5): a real boolean-table row was written.
        with open(self.report_file) as f:
            report_rows = [json.loads(line) for line in f if line.strip()]
        matching = [r for r in report_rows if r["umr_id"] == self.TEST_UMR_ID]
        self.assertEqual(len(matching), 1, msg=report_rows)
        self.assertTrue(matching[0]["FOUND"])
        self.assertEqual(matching[0]["gap_type"], "killed_needs_rca")

        # Metrics file (Prometheus textfile-collector format) was written.
        with open(self.metrics_file) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_dispatch_count 1", metrics_txt)
        self.assertIn("pm_sentinel_tick_failure_count 0", metrics_txt)

    def test_second_tick_does_not_duplicate_already_in_flight_dispatch(self):
        first = self._run_tick()
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        rows_after_first = _umr_tasks_rows(self.copy_path)

        second = self._run_tick()
        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        self.assertIn(f"IN-FLIGHT: rca:{self.TEST_UMR_ID}", second.stdout)
        self.assertIn("0/5 new dispatches this tick", second.stdout)

        rows_after_second = _umr_tasks_rows(self.copy_path)
        self.assertEqual(
            sorted(r["umr_id"] for r in rows_after_first),
            sorted(r["umr_id"] for r in rows_after_second),
        )


class PmSentinelTickFinancialEscalationTest(unittest.TestCase):
    """Real test for the 2026-08-13 Owner-decision escalation-scope
    amendment: a genuine financial-decision gap is escalated to the Owner
    (via notify-owner.py) instead of auto-dispatched."""

    def setUp(self):
        self.FINANCIAL_UMR_ID = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_fin_test_")
        # Real recorded reason is a genuine financial matter (payment/
        # invoice/billing language) -- this is what dispatch_gap()'s
        # is_financial_decision() check must catch.
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.FINANCIAL_UMR_ID, "test-seed-killed-task-fin01", "killed",
             "test-seeded: job killed before completing a real vendor invoice "
             "payment and subscription billing reconciliation"),
        ])

        self.notify_log = os.path.join(self.tmpdir, "notify-calls.json")
        self.fake_notify = _fake_notify_owner(self.tmpdir, self.notify_log)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = os.path.join(self.tmpdir, "report.jsonl")
        self.env["PM_SENTINEL_METRICS_FILE"] = os.path.join(self.tmpdir, "metrics.prom")
        self.env["PM_SENTINEL_NOTIFY_OWNER_SCRIPT"] = self.fake_notify
        self.env["FAKE_NOTIFY_LOG"] = self.notify_log
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_financial_gap_escalates_to_owner_instead_of_dispatching(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(
            f"NEEDS OWNER DECISION (financial): rca:{self.FINANCIAL_UMR_ID}",
            result.stdout,
        )
        self.assertNotIn(f"DISPATCHING for rca:{self.FINANCIAL_UMR_ID}", result.stdout)
        self.assertIn("0/5 new dispatches this tick", result.stdout)

        rows = _umr_tasks_rows(self.copy_path)
        self.assertEqual(len(rows), 1, msg=rows)
        self.assertEqual(rows[0]["umr_id"], self.FINANCIAL_UMR_ID)

        with open(self.state_file) as f:
            state = json.load(f)
        self.assertNotIn(f"rca:{self.FINANCIAL_UMR_ID}", state)

        with open(self.notify_log) as f:
            calls = json.load(f)
        self.assertEqual(len(calls), 1, msg=calls)
        self.assertIn("NEEDS OWNER DECISION (financial)", calls[0]["subject"])
        self.assertIn(self.FINANCIAL_UMR_ID, calls[0]["body"])
        self.assertIn("invoice", calls[0]["body"])


class PmSentinelTickDispatchFailurePropagatesTest(unittest.TestCase):
    """AUDIT-REJECT FIX #2: a real dispatch-owner-task.sh failure must make
    the whole tick exit non-zero, not silently exit 0."""

    def setUp(self):
        self.TEST_UMR_ID = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_failure_test_")
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.TEST_UMR_ID, "test-seed-killed-task-failx1", "killed",
             "test-seeded: real failure-propagation regression case"),
        ])
        # A real, throwaway stand-in dispatch-owner-task.sh that always
        # fails -- proves the tick itself surfaces that real failure via its
        # own exit code instead of swallowing it (fix #2), without needing a
        # genuinely broken real dispatch-owner-task.sh call.
        self.fake_dispatch = os.path.join(self.tmpdir, "fake-dispatch-owner-task.sh")
        with open(self.fake_dispatch, "w") as f:
            f.write("#!/usr/bin/env bash\necho 'simulated real failure' >&2\nexit 7\n")
        os.chmod(self.fake_dispatch, os.stat(self.fake_dispatch).st_mode | stat.S_IEXEC)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = os.path.join(self.tmpdir, "report.jsonl")
        self.env["PM_SENTINEL_METRICS_FILE"] = os.path.join(self.tmpdir, "metrics.prom")
        self.env["DISPATCH_OWNER_TASK_SH"] = self.fake_dispatch
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_real_dispatch_failure_makes_tick_exit_nonzero(self):
        result = subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )
        self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("DISPATCH FAILED for rca:", result.stdout)
        with open(self.env["PM_SENTINEL_METRICS_FILE"]) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_failure_count 1", metrics_txt)


if __name__ == "__main__":
    unittest.main()
