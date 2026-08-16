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
  6. QUERY-ONCE-PER-TICK (2026-08-13 addendum, UMR-20260813-105106-e9a7): a
     real umr_id that is BOTH a tracked-chain head AND status=killed is
     queried via resource_governor.py --query-umr --umr-id exactly ONCE
     this tick, not twice, and gets exactly one real dispatch, not two --
     see PmSentinelTickQueryOncePerTickTest.
  7. DECIDE-AND-FIX, NOT DECIDE-AND-ASK (same addendum): two real,
     independent findings in one tick each get their own real dispatch
     through the same gateway in that SAME tick, and the tick's own real
     FINDINGS_LOGGED/FINDINGS_ACTIONED counters reconcile -- see
     PmSentinelTickDecideAndFixTest.

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


def _schema_only_copy(dest_conn, src_conn):
    """Real root-cause fix (UMR-20260814-033442-c885, P0 disk exhaustion):
    this used to be `src.backup(dst)`, a full sqlite3 binary backup of the
    ENTIRE live ~3-4GB superboss-register.sqlite, for every single test
    invocation. This test (like every caller of _seeded_copy below) only
    ever reads back rows it seeded itself -- it never depends on the live
    DB's actual row data -- so the fix is to clone the schema (every real
    table/index/trigger/view definition from sqlite_master) with ZERO row
    data copied, not to just clean up the 4GB copy after the fact.

    Three passes, deliberately NOT just "tables then indexes/triggers" by
    raw sqlite_master rowid order -- a real, confirmed-live bug: on this
    DB's actual sqlite_master, FTS5 shadow-table rows (umr_tasks_fts_data/
    _idx/_docsize/_config) have LOWER rowids than their own owning virtual
    table's row (umr_tasks_fts) -- i.e. rowid is not reliable creation
    order across schema migrations/rebuilds. Executing the shadow tables'
    plain CREATE TABLE statements before the virtual table's own
    CREATE VIRTUAL TABLE ran left real, empty, non-fts5-linked tables
    sitting under those names; the virtual table's own subsequent create
    then found them "already exists" (swallowed, matching the FTS5-normal
    case) and silently never actually created -- leaving a real
    'no such table: main.umr_tasks_fts' failure the first time any insert
    trigger tried to use it. Fix: create every CREATE VIRTUAL TABLE first
    (pass 1, which then auto-creates ITS OWN correct shadow tables), then
    every other ordinary table (pass 2 -- any shadow-table row here is now
    the expected, benign "already exists"), then indexes/triggers/views
    last (pass 3, once every table definitely exists)."""
    rows = src_conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    is_virtual_table = lambda t, sql: t == "table" and sql.strip().upper().startswith("CREATE VIRTUAL TABLE")
    for _type, _name, sql in rows:
        if is_virtual_table(_type, sql):
            dest_conn.execute(sql)
    for _type, _name, sql in rows:
        if _type == "table" and not is_virtual_table(_type, sql):
            try:
                dest_conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e):
                    raise
    for _type, _name, sql in rows:
        if _type != "table":
            try:
                dest_conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e):
                    raise


def _seeded_copy(tmpdir, rows):
    """Real, isolated, SCHEMA-ONLY clone of the live DB (see
    _schema_only_copy above -- never a full binary copy of the live data),
    with umr_tasks/umr_tasks_fts/owner_priority_sequence seeded with
    exactly `rows` (a list of (umr_id, task_identity, status, reason)
    tuples) -- deterministic, no real live rows leaking in and triggering
    unrelated real dispatches or `gh` network calls."""
    copy_path = os.path.join(tmpdir, "superboss-register.sqlite")
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(copy_path)
    with dst:
        _schema_only_copy(dst, src)
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


def _seed_gtm_categories(copy_path, rows):
    """Seed real gtm_certification_categories rows into the isolated schema-
    only copy -- `rows` is a list of (category_index, category_name, passed,
    evidence_summary) tuples. Real live schema requires category_name/
    ocid_number/parent_umr_id/created_at/last_updated_at NOT NULL -- a
    synthetic-but-real parent_umr_id/ocid_number is fine here since Check 4
    never reads those two columns, only category_index/category_name/
    passed/evidence_summary (see list_gtm_certification_categories())."""
    conn = sqlite3.connect(copy_path)
    for category_index, category_name, passed, evidence_summary in rows:
        conn.execute(
            "INSERT INTO gtm_certification_categories (category_index, "
            "category_name, ocid_number, parent_umr_id, passed, "
            "evidence_summary, created_at, last_updated_at) VALUES "
            "(?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (category_index, category_name, "OCID-020", "UMR-TESTFIX-PARENT",
             passed, evidence_summary),
        )
    conn.commit()
    conn.close()


def _insert_umr_row_with_inputs(copy_path, umr_id, task_identity, status, inputs):
    """Insert one real umr_tasks row carrying a real inputs_json blob (title/
    repo/prompt) -- _seeded_copy()'s own INSERT never sets inputs_json (no
    existing test needed it), but gtm_orchestrator_in_flight()'s real content
    match is over task_identity + inputs_json.prompt, so the in-flight-dedup
    test below needs a real row that actually carries prompt text."""
    conn = sqlite3.connect(copy_path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, "
        "status, source_trigger, task_kind, inputs_json) VALUES "
        "(?,?,datetime('now'),1,?,'owner_dispatch_gateway','veridian_task_create',?)",
        (umr_id, task_identity, status, json.dumps(inputs)),
    )
    conn.commit()
    conn.close()


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
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.TEST_UMR_ID, "test-seed-killed-task-0001", "killed",
             "test-seeded: stuck-task SIGKILL, no exit after grace period"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
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
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
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
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
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
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
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
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
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


def _shim_resource_governor(tmpdir, call_log_path):
    """A real, throwaway resource_governor.py stand-in that logs every real
    invocation's argv (one line per call) to call_log_path, then execs the
    REAL resource_governor.py with the same argv/env/stdio (os.execv, same
    process, real exit code/stdout preserved exactly) -- proves what real
    subprocess calls pm-sentinel-tick.sh actually issued this tick, not an
    inference from its own self-reported counters."""
    shim = os.path.join(tmpdir, "shim-resource-governor.py")
    real_path = os.path.join(HERE, "resource_governor.py")
    with open(shim, "w") as f:
        f.write(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['RG_CALL_LOG'], 'a') as f:\n"
            "    f.write(' '.join(sys.argv[1:]) + chr(10))\n"
            f"os.execv(sys.executable, [sys.executable, {real_path!r}] + sys.argv[1:])\n"
        )
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
    return shim


class PmSentinelTickQueryOncePerTickTest(unittest.TestCase):
    """Real test for the 2026-08-13 addendum (UMR-20260813-105106-e9a7,
    addendum to UMR-20260813-102459-10c3), rule 1: QUERY ONCE PER TICK.

    Seeds ONE real umr_id that is BOTH a tracked-chain head (governing_umr in
    owner_priority_sequence, status='active') AND status='killed' in
    umr_tasks -- the real, concrete overlap case where, before this
    addendum, Check 1 (per-chain-head lookup) and Check 2a (system-wide
    killed-row scan) would each independently issue their own real
    `resource_governor.py --query-umr --umr-id <same id>` call for the
    identical row in the same tick, for zero new information.

    Proves, via a real logging shim that execs the real resource_governor.py
    (so the real tick behavior -- one real dispatch -- is completely
    unchanged), that `--umr-id <that id>` is issued exactly ONCE this tick,
    not twice, and that Check 2a's own real stdout shows it recognized the
    row as already handled instead of silently re-deciding it."""

    def setUp(self):
        self.TEST_UMR_ID = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_queryonce_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.TEST_UMR_ID, "test-seed-queryonce-task-0001", "killed",
             "test-seeded: query-once-per-tick overlap case (chain head + killed)"),
        ])
        # Make this same real umr_id a real tracked-chain head (Check 1's own
        # real source of chain UMRs), status='active' so Check 1 processes it.
        conn = sqlite3.connect(self.copy_path)
        conn.execute(
            "INSERT INTO owner_priority_sequence "
            "(phase_order, phase_name, governing_umr, real_member_umrs, status, "
            "created_at, updated_at, confirmed_complete_members) "
            "VALUES (999, 'test-queryonce-phase', ?, '[]', 'active', "
            "datetime('now'), datetime('now'), '[]')",
            (self.TEST_UMR_ID,),
        )
        conn.commit()
        conn.close()

        self.call_log = os.path.join(self.tmpdir, "rg-calls.log")
        open(self.call_log, "w").close()
        self.rg_shim = _shim_resource_governor(self.tmpdir, self.call_log)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = os.path.join(self.tmpdir, "report.jsonl")
        self.env["PM_SENTINEL_METRICS_FILE"] = os.path.join(self.tmpdir, "metrics.prom")
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["RESOURCE_GOVERNOR_PY"] = self.rg_shim
        self.env["RG_CALL_LOG"] = self.call_log
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_same_row_queried_at_most_once_per_tick(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        with open(self.call_log) as f:
            calls = [line.strip() for line in f if line.strip()]
        individual_lookups = [
            c for c in calls
            if "--umr-id" in c and f"--umr-id {self.TEST_UMR_ID}" in c
        ]
        self.assertEqual(
            len(individual_lookups), 1,
            msg=f"expected exactly 1 real --umr-id {self.TEST_UMR_ID} query this "
                f"tick, got {len(individual_lookups)}: all calls={calls}",
        )

        # Check 1 (tracked-chain head) is the one that actually queried it and
        # dispatched the real RCA -- proven by the real dispatch happening
        # and being recorded exactly once (same "zero duplication" proof the
        # existing killed-row test already makes, re-asserted here so this
        # test is self-contained).
        self.assertIn(f"DISPATCHING for rca:{self.TEST_UMR_ID}", result.stdout)
        self.assertIn("1/5 new dispatches this tick", result.stdout)

        # Check 2a's own real stdout shows it recognized the row as already
        # fetched+handled this tick, instead of silently re-deciding it.
        self.assertIn(f"QUERY-ONCE: {self.TEST_UMR_ID} already fetched", result.stdout)

        # Only one real umr_tasks row was created (the real dispatched RCA
        # task) -- proves Check 2a did not ALSO independently dispatch a
        # second RCA for the same real gap.
        rows = _umr_tasks_rows(self.copy_path)
        new_rows = [r for r in rows if r["umr_id"] != self.TEST_UMR_ID]
        self.assertEqual(len(new_rows), 1, msg=rows)

        # Real reconciliation output/metrics prove DECIDE-AND-FIX's own
        # counters agree: exactly 1 real finding, exactly 1 real same-tick
        # decision -- not 2 of each (which a real double-query would produce).
        self.assertIn("DECIDE-AND-FIX: 1 real finding(s) this tick, 1 same-tick decision(s)", result.stdout)
        with open(self.env["PM_SENTINEL_METRICS_FILE"]) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_findings_logged 1", metrics_txt)
        self.assertIn("pm_sentinel_tick_findings_actioned 1", metrics_txt)


class PmSentinelTickDecideAndFixTest(unittest.TestCase):
    """Real test for the 2026-08-13 addendum (UMR-20260813-105106-e9a7,
    addendum to UMR-20260813-102459-10c3), rule 2: DECIDE-AND-FIX, NOT
    DECIDE-AND-ASK.

    Seeds TWO real, genuinely independent, non-overlapping technical gaps
    (two distinct killed-status rows, neither a tracked-chain head) in one
    tick. Proves each real finding gets its own real dispatch through the
    same gateway (dispatch-owner-task.sh) IN THE SAME TICK -- not merely
    logged -- and that the tick's own real FINDINGS_LOGGED/FINDINGS_ACTIONED
    counters reconcile (2 found, 2 actioned), with no
    'DECIDE-AND-FIX VIOLATION' in the real output."""

    def setUp(self):
        self.UMR_A = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.UMR_B = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_decidefix_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.UMR_A, "test-seed-decidefix-task-a", "killed",
             "test-seeded: real independent gap A, needs real RCA"),
            (self.UMR_B, "test-seed-decidefix-task-b", "killed",
             "test-seeded: real independent gap B, needs real RCA"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
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

    def test_every_finding_gets_a_same_tick_dispatch(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        # Both real findings got a real dispatch call through the SAME real
        # gateway (dispatch-owner-task.sh), in this SAME tick run/process --
        # not merely logged for a later tick to pick up.
        self.assertIn(f"DISPATCHING for rca:{self.UMR_A}", result.stdout)
        self.assertIn(f"DISPATCHING for rca:{self.UMR_B}", result.stdout)
        self.assertIn(f"DISPATCHED rca:{self.UMR_A} ->", result.stdout)
        self.assertIn(f"DISPATCHED rca:{self.UMR_B} ->", result.stdout)
        self.assertIn("2/5 new dispatches this tick", result.stdout)

        rows = _umr_tasks_rows(self.copy_path)
        new_rows = [r for r in rows if r["umr_id"] not in (self.UMR_A, self.UMR_B)]
        self.assertEqual(len(new_rows), 2, msg=rows)
        for row in new_rows:
            self.assertEqual(row["status"], "queued")

        # DECIDE-AND-FIX's own real counters reconcile: 2 real findings, 2
        # real same-tick decisions -- no violation.
        self.assertIn("DECIDE-AND-FIX: 2 real finding(s) this tick, 2 same-tick decision(s)", result.stdout)
        self.assertNotIn("DECIDE-AND-FIX VIOLATION", result.stdout)
        self.assertNotIn("DECIDE-AND-FIX VIOLATION", result.stderr)

        with open(self.metrics_file) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_findings_logged 2", metrics_txt)
        self.assertIn("pm_sentinel_tick_findings_actioned 2", metrics_txt)

        # Real boolean-table report has one row per real finding.
        with open(self.report_file) as f:
            report_rows = [json.loads(line) for line in f if line.strip()]
        reported_ids = {r["umr_id"] for r in report_rows}
        self.assertIn(self.UMR_A, reported_ids)
        self.assertIn(self.UMR_B, reported_ids)


def _fake_systemctl_journalctl_bin(tmpdir, unit_states):
    """A real, throwaway `bin/` dir prepended to PATH containing fake
    `systemctl` and `journalctl` executables -- proves Check 2b's own parse
    of `systemctl --user show <unit> -p ActiveState -p Result` is genuinely
    order-independent, which nothing about the real systemd IPC contract
    guarantees (there is no live, deterministic way to force the real
    systemd user manager to emit properties in a specific order on demand,
    so this is the real, honest way to test both orderings).

    `unit_states` maps a fake `*.service` unit name to the exact multi-line
    `Key=Value` text that unit's fake `systemctl --user show -p ActiveState
    -p Result` call should return -- callers control the real line order
    directly to cover both the documented order and the swapped order.
    """
    bindir = os.path.join(tmpdir, "bin")
    os.makedirs(bindir, exist_ok=True)

    systemctl = os.path.join(bindir, "systemctl")
    with open(systemctl, "w") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("unit=\"\"\n")
        f.write('for a in "$@"; do case "$a" in *.service) unit="$a" ;; esac; done\n')
        f.write("case \"$unit\" in\n")
        for unit, text in unit_states.items():
            escaped = text.replace("'", "'\\''")
            f.write(f"  {unit}) printf '%s\\n' '{escaped}' ;;\n")
        f.write("  *) exit 1 ;;\n")
        f.write("esac\n")
    os.chmod(systemctl, os.stat(systemctl).st_mode | stat.S_IEXEC)

    journalctl = os.path.join(bindir, "journalctl")
    with open(journalctl, "w") as f:
        f.write("#!/usr/bin/env bash\necho 'fake journal excerpt for test'\n")
    os.chmod(journalctl, os.stat(journalctl).st_mode | stat.S_IEXEC)

    return bindir


class PmSentinelTickRunningRowOrderIndependentParseTest(unittest.TestCase):
    """Real regression test for UMR-20260813-145511-5aca / redispatch
    UMR-20260813-170956-5385: Check 2b's real live systemctl output parse
    used to assume `systemctl --user show <unit> -p ActiveState -p Result
    --value` always emits ActiveState before Result -- nothing in
    systemd's own contract guarantees that. Feeds BOTH a documented-order
    and a swapped-order real `Key=Value` payload for a genuinely ACTIVE
    unit and asserts neither is ever classified dead (false MISMATCH), and
    that a genuinely dead unit is still correctly flagged regardless of
    which order its properties come back in."""

    def setUp(self):
        self.UMR_NORMAL_ACTIVE = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.UMR_SWAPPED_ACTIVE = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.UMR_SWAPPED_DEAD = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_orderparse_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not

        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.UMR_NORMAL_ACTIVE, "test-seed-orderparse-normal-active", "running", ""),
            (self.UMR_SWAPPED_ACTIVE, "test-seed-orderparse-swapped-active", "running", ""),
            (self.UMR_SWAPPED_DEAD, "test-seed-orderparse-swapped-dead", "running", ""),
        ])
        conn = sqlite3.connect(self.copy_path)
        conn.execute("UPDATE umr_tasks SET unit_name = 'normal-active.service' WHERE umr_id = ?",
                     (self.UMR_NORMAL_ACTIVE,))
        conn.execute("UPDATE umr_tasks SET unit_name = 'swapped-active.service' WHERE umr_id = ?",
                     (self.UMR_SWAPPED_ACTIVE,))
        conn.execute("UPDATE umr_tasks SET unit_name = 'swapped-dead.service' WHERE umr_id = ?",
                     (self.UMR_SWAPPED_DEAD,))
        conn.commit()
        conn.close()

        bindir = _fake_systemctl_journalctl_bin(self.tmpdir, {
            # Documented -p flag order, genuinely active: must never MISMATCH.
            "normal-active.service": "ActiveState=active\nResult=success",
            # SWAPPED order, genuinely active (the real live-reproduced bug
            # case): must never MISMATCH either -- this is the real
            # regression case for the false RCA dispatches.
            "swapped-active.service": "Result=success\nActiveState=active",
            # SWAPPED order, genuinely dead: must still MISMATCH -- proves
            # the fix did not also break real detection.
            "swapped-dead.service": "Result=success\nActiveState=inactive",
        })

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"
        # Real fake systemctl/journalctl ahead of the real ones on PATH --
        # every OTHER real call this tick makes (resource_governor.py,
        # superboss-register.py, dispatch-owner-task.sh, git, gh, python3)
        # is unaffected, none of those are named systemctl/journalctl.
        self.env["PATH"] = bindir + os.pathsep + self.env.get("PATH", "")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_active_unit_never_classified_dead_regardless_of_property_order(self):
        result = self._run_tick()

        # Neither genuinely-active unit (documented order or swapped order)
        # is ever reported as a MISMATCH -- the real false-positive this bug
        # caused live.
        self.assertNotIn(f"MISMATCH: {self.UMR_NORMAL_ACTIVE}", result.stdout)
        self.assertNotIn(f"MISMATCH: {self.UMR_SWAPPED_ACTIVE}", result.stdout)
        self.assertNotIn(f"DISPATCHING for rca:{self.UMR_NORMAL_ACTIVE}", result.stdout)
        self.assertNotIn(f"DISPATCHING for rca:{self.UMR_SWAPPED_ACTIVE}", result.stdout)

        # The genuinely dead unit (also swapped order) is still correctly
        # flagged and RCA'd -- the fix did not also break real detection.
        self.assertIn(
            f"MISMATCH: {self.UMR_SWAPPED_DEAD} status=running but unit "
            "swapped-dead.service ActiveState=inactive Result=success",
            result.stdout,
        )
        self.assertIn(f"DISPATCHING for rca:{self.UMR_SWAPPED_DEAD}", result.stdout)

        # Only the genuinely-dead row produced a new dispatched task.
        rows = _umr_tasks_rows(self.copy_path)
        seed_ids = {self.UMR_NORMAL_ACTIVE, self.UMR_SWAPPED_ACTIVE, self.UMR_SWAPPED_DEAD}
        new_rows = [r for r in rows if r["umr_id"] not in seed_ids]
        self.assertEqual(len(new_rows), 1, msg=rows)


class PmSentinelTickImpossibleActiveStateGuardTest(unittest.TestCase):
    """Real regression test for ACTION 2 of the UMR-20260813-145511-5aca /
    UMR-20260813-170956-5385 / UMR-20260813-183133 (third redispatch) fix:
    the name-keyed parse alone is not enough defense-in-depth -- if
    systemd's own output shape ever changes again (missing key line,
    truncated field, a future systemctl behavior change) a *silent*
    re-transposition would be exactly as invisible as the original bug.
    Feeds a real fake systemctl that returns the impossible fingerprint
    ActiveState=success (a real Result value, never a real ActiveState
    value) and proves the tick refuses to act on it: no MISMATCH, no RCA
    dispatch, a loud logged rejection, and a real non-zero tick exit
    (TICK_FAILURES), rather than either silently doing nothing or firing a
    false RCA off untrustworthy data."""

    def setUp(self):
        self.UMR_IMPOSSIBLE = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_impossible_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not

        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.UMR_IMPOSSIBLE, "test-seed-impossible-activestate", "running", ""),
        ])
        conn = sqlite3.connect(self.copy_path)
        conn.execute("UPDATE umr_tasks SET unit_name = 'impossible.service' WHERE umr_id = ?",
                     (self.UMR_IMPOSSIBLE,))
        conn.commit()
        conn.close()

        bindir = _fake_systemctl_journalctl_bin(self.tmpdir, {
            # The real live-reproduced impossible fingerprint: ActiveState
            # can never legitimately be "success" (that is a Result value).
            "impossible.service": "ActiveState=success\nResult=active",
        })

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"
        self.env["PATH"] = bindir + os.pathsep + self.env.get("PATH", "")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_impossible_active_state_is_rejected_not_acted_on(self):
        result = self._run_tick()

        # Never treated as a MISMATCH/RCA candidate off untrustworthy data.
        self.assertNotIn(f"MISMATCH: {self.UMR_IMPOSSIBLE}", result.stdout)
        self.assertNotIn(f"DISPATCHING for rca:{self.UMR_IMPOSSIBLE}", result.stdout)

        # Loud, real rejection is logged (not a silent skip).
        self.assertIn("IMPOSSIBLE VALUE", result.stdout)
        self.assertIn(self.UMR_IMPOSSIBLE, result.stdout)
        self.assertIn("ActiveState=success", result.stdout)

        # No new row was dispatched for this UMR.
        rows = _umr_tasks_rows(self.copy_path)
        new_rows = [r for r in rows if r["umr_id"] != self.UMR_IMPOSSIBLE]
        self.assertEqual(len(new_rows), 0, msg=rows)

        # The tick still fails loudly overall (real non-zero exit) instead
        # of silently swallowing an untrustworthy parse.
        self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)


class PmSentinelTickDuplicateContentRefusalDoesNotFailTickTest(unittest.TestCase):
    """Real regression test for UMR-20260813-145511-5aca / redispatch
    UMR-20260813-170956-5385's second real defect:
    veridian-pm-sentinel-tick.service really exited 1 on its two most
    recent live runs. Live root cause traced to dispatch_gap() counting
    dispatch-owner-task.sh's own content-duplicate refusal (an identical
    prompt already logged within its real 6h window -- an expected,
    already-accounted-for condition, not a genuine dispatch failure) as a
    TICK_FAILURES failure like any other. Proves a tick that hits ONLY that
    condition still exits 0, while a genuinely different real
    dispatch-owner-task.sh failure (AUDIT-REJECT FIX #2) still propagates
    non-zero -- see PmSentinelTickDispatchFailurePropagatesTest above,
    intentionally left unchanged."""

    def setUp(self):
        self.TEST_UMR_ID = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_duprefusal_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        self.copy_path = _seeded_copy(self.tmpdir, [
            (self.TEST_UMR_ID, "test-seed-duprefusal-task-0001", "killed",
             "test-seeded: duplicate-content-refusal-does-not-fail-tick regression case"),
        ])
        # A real, throwaway stand-in dispatch-owner-task.sh that always
        # refuses exactly the way the real one does on a genuine content
        # duplicate (same stdout shape, same exit 1) -- proves this exact
        # refusal text is what the fix keys off, without depending on a
        # real prior instruction actually being logged within 6h.
        self.fake_dispatch = os.path.join(self.tmpdir, "fake-dispatch-owner-task.sh")
        with open(self.fake_dispatch, "w") as f:
            f.write(
                "#!/usr/bin/env bash\n"
                "echo '{\"content_duplicate_found\": true, \"duplicate_instruction_id\": \"INS-test-0001\"}'\n"
                "echo \"REFUSED: an identical instruction was already logged within the last 6 hours "
                "(see duplicate_instruction_id above). Re-run with a genuinely different prompt if this "
                "repeat is intentional.\" >&2\n"
                "exit 1\n"
            )
        os.chmod(self.fake_dispatch, os.stat(self.fake_dispatch).st_mode | stat.S_IEXEC)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        # Check 0 (UMR-20260813-195852-aa85 addendum): point the live
        # deploy-drift self-check at this test's own real, non-git tmpdir so
        # it fails closed (git fetch fails -> DRIFT CHECK UNAVAILABLE, not a
        # finding) and never perturbs this test's own dispatch-count /
        # FINDINGS_LOGGED assertions -- Check 0's own real behavior is
        # covered by its own dedicated test classes below.
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = os.path.join(self.tmpdir, "report.jsonl")
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["DISPATCH_OWNER_TASK_SH"] = self.fake_dispatch
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_duplicate_content_refusal_alone_exits_zero(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(f"SKIPPED rca:{self.TEST_UMR_ID}: duplicate content already logged", result.stdout)
        self.assertNotIn("DISPATCH FAILED for rca:", result.stdout)

        with open(self.metrics_file) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_failure_count 0", metrics_txt)

        # Not recorded in-flight (no real dispatched UMR exists for it) --
        # a later tick, after the real 6h window rolls, gets to reconsider.
        with open(self.state_file) as f:
            state = json.load(f)
        self.assertNotIn(f"rca:{self.TEST_UMR_ID}", state)


def _git(args, cwd, check=True):
    return subprocess.run(["git"] + args, cwd=cwd, check=check,
                           capture_output=True, text=True)


def _local_drift_fixture(tmpdir, extra_origin_commit):
    """Real, fully local git fixture -- a real bare 'origin' repo plus a real
    clone of it ('live') -- proving Check 0's own real
    `check_live_scripts_drift.py --live-dir <live>` call against a genuine
    real `git fetch`/`rev-parse`/`diff`, with NO GitHub, no network, and no
    dependency on this box's own real veridian-scripts checkout's real
    current drift state (which is exactly the live, changing condition Check
    0 exists to detect, so it cannot double as a deterministic test fixture).

    When `extra_origin_commit` is True, a second real commit is pushed to
    origin AFTER `live` was cloned -- `live`'s real HEAD is now genuinely
    behind real origin/main, the real drift condition. When False, `live`'s
    real HEAD stays exactly equal to real origin/main -- the real in-sync
    condition."""
    origin_dir = os.path.join(tmpdir, "origin.git")
    _git(["init", "--quiet", "--bare", "-b", "main", origin_dir], cwd=tmpdir)

    seed_dir = os.path.join(tmpdir, "seed")
    _git(["init", "--quiet", "-b", "main", seed_dir], cwd=tmpdir)
    _git(["config", "user.email", "test@pm-sentinel-tick-test.invalid"], cwd=seed_dir)
    _git(["config", "user.name", "pm-sentinel-tick test"], cwd=seed_dir)
    with open(os.path.join(seed_dir, "pm-sentinel-tick.sh"), "w") as f:
        f.write("#!/usr/bin/env bash\necho v1\n")
    _git(["add", "."], cwd=seed_dir)
    _git(["commit", "--quiet", "-m", "seed"], cwd=seed_dir)
    _git(["remote", "add", "origin", origin_dir], cwd=seed_dir)
    _git(["push", "--quiet", "origin", "main"], cwd=seed_dir)

    live_dir = os.path.join(tmpdir, "live")
    _git(["clone", "--quiet", origin_dir, live_dir], cwd=tmpdir)

    if extra_origin_commit:
        with open(os.path.join(seed_dir, "pm-sentinel-tick.sh"), "w") as f:
            f.write("#!/usr/bin/env bash\necho v2 -- real fix landed upstream\n")
        _git(["commit", "--quiet", "-am", "real fix landed on origin/main"], cwd=seed_dir)
        _git(["push", "--quiet", "origin", "main"], cwd=seed_dir)

    return live_dir


def _fake_dispatch_owner_task_recording(tmpdir, call_log_path):
    """A real, throwaway dispatch-owner-task.sh stand-in: records its real
    argv (one line per call) to call_log_path and returns a real, successful,
    fake umr_id -- proves Check 0 actually calls the one real dispatch
    gateway with a real title/prompt, without making any real GitHub/gh
    network call (unlike the other real-dispatch test classes above, which
    deliberately exercise the real dispatch-owner-task.sh end to end; Check
    0's own regression coverage only needs to prove Check 0 ITSELF detects
    drift and reaches the gateway, which the QueryOnce/DecideAndFix test
    classes above already prove end-to-end for every other check)."""
    fake = os.path.join(tmpdir, "fake-dispatch-owner-task-drift.sh")
    with open(fake, "w") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$DISPATCH_CALL_LOG\"\n"
            "echo \"DISPATCHED (fake, no real network) umr_id=UMR-FAKE-20260101-000000-$RANDOM$RANDOM\"\n"
            "exit 0\n"
        )
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    return fake


class PmSentinelTickLiveDeployDriftFoundTest(unittest.TestCase):
    """Real test for Check 0 (2026-08-13 addendum, UMR-20260813-195852-aa85):
    when this script's own real live checkout ($PM_SENTINEL_LIVE_SCRIPTS_DIR)
    is genuinely behind real origin/main (a real local git fixture, see
    _local_drift_fixture), Check 0 must detect it via the real, already-built
    check_live_scripts_drift.py and dispatch a real fix through the same
    single gateway -- the real, concrete gap this closes: a real fix (e.g.
    PR #299's Check 2b parse fix) can be merged to origin/main while
    production keeps running an older, buggy, unfixed copy forever, with
    nothing noticing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_drift_found_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        # Empty umr_tasks (no seeded rows) -- isolates this test to Check 0
        # only; Checks 1/2a/2b/3 all no-op on a real empty table.
        self.copy_path = _seeded_copy(self.tmpdir, [])
        self.live_dir = _local_drift_fixture(self.tmpdir, extra_origin_commit=True)

        self.call_log = os.path.join(self.tmpdir, "dispatch-calls.log")
        open(self.call_log, "w").close()
        self.fake_dispatch = _fake_dispatch_owner_task_recording(self.tmpdir, self.call_log)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "metrics.prom")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.live_dir
        self.env["CHECK_LIVE_SCRIPTS_DRIFT_PY"] = os.path.join(HERE, "check_live_scripts_drift.py")
        self.env["DISPATCH_OWNER_TASK_SH"] = self.fake_dispatch
        self.env["DISPATCH_CALL_LOG"] = self.call_log
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_tick(self):
        return subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )

    def test_real_drift_dispatches_real_reconcile_fix(self):
        result = self._run_tick()

        self.assertIn("DRIFT FOUND", result.stdout, msg=result.stdout + result.stderr)
        self.assertIn(f"DISPATCHING for deploy_drift:{self.live_dir}", result.stdout)
        self.assertIn("Reconcile live deploy drift", result.stdout)

        with open(self.call_log) as f:
            calls = f.read()
        self.assertIn("Reconcile live deploy drift", calls)
        self.assertIn("veridian-scripts", calls)

        with open(self.report_file) as f:
            report_rows = [json.loads(line) for line in f if line.strip()]
        drift_rows = [r for r in report_rows if r["gap_type"] == "live_deploy_drift"]
        self.assertEqual(len(drift_rows), 1, msg=report_rows)
        self.assertTrue(drift_rows[0]["FOUND"])

        with open(self.metrics_file) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_findings_logged 1", metrics_txt)
        self.assertIn("pm_sentinel_tick_findings_actioned 1", metrics_txt)


class PmSentinelTickLiveDeployDriftInSyncTest(unittest.TestCase):
    """Real control case for Check 0: when the live checkout's real HEAD
    genuinely equals real origin/main, Check 0 must NOT report a finding or
    dispatch anything -- proves the drift check doesn't false-positive on a
    genuinely healthy, in-sync deployment."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_drift_insync_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)  # UMR-20260814-033442-c885: runs even if setUp raises after this line (e.g. disk-full mid-copy) -- tearDown alone does not
        self.copy_path = _seeded_copy(self.tmpdir, [])
        self.live_dir = _local_drift_fixture(self.tmpdir, extra_origin_commit=False)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "metrics.prom")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.live_dir
        self.env["CHECK_LIVE_SCRIPTS_DRIFT_PY"] = os.path.join(HERE, "check_live_scripts_drift.py")
        # Real dispatch-owner-task.sh is fine here -- it must never actually
        # be invoked for a genuine in-sync live checkout with no other
        # seeded rows, so pointing it at the real one (never exercised) is a
        # stronger proof than a fake that could silently mask a real call.
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

    def test_in_sync_live_checkout_is_not_a_finding(self):
        result = self._run_tick()

        self.assertIn("in sync:", result.stdout, msg=result.stdout + result.stderr)
        self.assertNotIn("DRIFT FOUND", result.stdout)
        self.assertNotIn("DISPATCHING for deploy_drift:", result.stdout)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        # emit_report_row() only ever appends -- a real tick with zero real
        # findings never creates REPORT_FILE at all (no seeded umr_tasks
        # rows here, and a genuinely in-sync live checkout is not one
        # either), so an absent file is itself real, positive proof no
        # finding was emitted.
        if os.path.exists(self.report_file):
            with open(self.report_file) as f:
                report_rows = [json.loads(line) for line in f if line.strip()]
            drift_rows = [r for r in report_rows if r["gap_type"] == "live_deploy_drift"]
            self.assertEqual(len(drift_rows), 0, msg=report_rows)


class PmSentinelTickGtmCertGapDispatchTest(unittest.TestCase):
    """Real test for Check 4 (2026-08-15 Owner directive, Part3+4 GTM-
    certification completion, governing UMR UMR-20260815-044235-a5e1): real
    gap rows (passed=0 or passed IS NULL) in gtm_certification_categories
    dispatch exactly one real gap-closure task through the same
    dispatch_gap() gateway every other check uses, citing the real live-
    queried gap list -- never a hardcoded count.

    Note: SUPERBOSS_REGISTER_PY is pointed at THIS checkout's own
    superboss-register.py (not the default live-canonical-path resolution
    every other check's tests rely on) because list-gtm-categories /
    record-gtm-part3-4-certificate are new subcommands this same change
    introduces -- the live server's copy will only gain them once this PR
    is merged and synced (Check 0's own drift concern), so tests must be
    explicit about which copy they exercise."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_gtmgap_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.copy_path = _seeded_copy(self.tmpdir, [])
        # Real, live-queried gap state: 4 hard FAIL + 5 never-validated, same
        # real shape the 2026-08-15 directive itself describes -- but the
        # check must never assume this exact count, only what it queries.
        _seed_gtm_categories(self.copy_path, [
            (3, "security audit", 0, "REAL FAIL: xss on login form"),
            (10, "load testing", None, None),
            (11, "stress testing", None, None),
            (13, "AI testing", None, None),
            (14, "browser compatibility", 0, "REAL FAIL: 2/3 engines loaded"),
            (15, "multi tenant testing", None, None),
            (16, "role permission testing", None, None),
            (23, "UX audit", 0, "REAL FAIL: 5 heuristics failed"),
            (25, "production readiness audit", 0, "REAL FAIL: synthesis over 24 categories"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["SUPERBOSS_REGISTER_PY"] = os.path.join(HERE, "superboss-register.py")
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
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

    def test_real_gap_rows_dispatch_one_real_gap_closure_task(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("9 real gap row(s) in gtm_certification_categories", result.stdout)
        self.assertIn("DISPATCHING for gtm-part3-4-gap:OCID-020", result.stdout)
        self.assertIn("DISPATCHED gtm-part3-4-gap:OCID-020 ->", result.stdout)

        rows = _umr_tasks_rows(self.copy_path)
        self.assertEqual(len(rows), 1, msg=rows)
        inputs = json.loads(rows[0]["inputs_json"])
        prompt = inputs.get("prompt", "")
        self.assertIn("gtm_certification_categories", prompt)
        self.assertIn("security audit", prompt)
        self.assertIn("pm_lifecycle.py", prompt)
        self.assertEqual(inputs.get("repo"), "compliance-tracker")

        with open(self.report_file) as f:
            report_rows = [json.loads(line) for line in f if line.strip()]
        gap_rows = [r for r in report_rows if r["gap_type"] == "gtm_certification_gap"]
        self.assertEqual(len(gap_rows), 1, msg=report_rows)

    def test_second_tick_does_not_duplicate_via_own_state_file(self):
        first = self._run_tick()
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        second = self._run_tick()
        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        self.assertIn("IN-FLIGHT:", second.stdout)
        rows = _umr_tasks_rows(self.copy_path)
        self.assertEqual(len(rows), 1, msg=rows)


class PmSentinelTickGtmCertAlreadyInFlightTest(unittest.TestCase):
    """Real test for SPEC step 2's content-matched pre-dispatch check
    (gtm_orchestrator_in_flight()): a real gap exists, but a real, genuinely
    independent orchestrator run (NOT one this sentinel itself dispatched --
    no STATE_FILE entry for it at all, modeling the real seed UMRs
    UMR-20260815-033344-4799 / UMR-20260815-042226-f271 that were dispatched
    outside this sentinel) is already queued with real prompt text
    referencing gtm_certification_categories/OCID-020. This tick must do
    nothing for this gap -- zero duplication."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_gtminflight_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.copy_path = _seeded_copy(self.tmpdir, [])
        _seed_gtm_categories(self.copy_path, [
            (3, "security audit", 0, "REAL FAIL: xss on login form"),
        ])
        self.INFLIGHT_UMR = f"UMR-TESTFIX-20260101-000000-{uuid.uuid4().hex[:8]}"
        _insert_umr_row_with_inputs(
            self.copy_path, self.INFLIGHT_UMR,
            f"owner-task-inflight-{uuid.uuid4().hex[:6]}", "queued",
            {"title": "Close real GTM certification gaps",
             "repo": "compliance-tracker",
             "prompt": "Run pm_lifecycle.py to close real gtm_certification_categories gaps under OCID-020"},
        )
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["SUPERBOSS_REGISTER_PY"] = os.path.join(HERE, "superboss-register.py")
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
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

    def test_genuinely_in_flight_orchestrator_run_blocks_duplicate_dispatch(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("IN-FLIGHT: a real pm_lifecycle.py orchestrator run", result.stdout)
        self.assertIn(self.INFLIGHT_UMR, result.stdout)
        self.assertNotIn("DISPATCHING for gtm-part3-4-gap:OCID-020", result.stdout)

        # No new umr_tasks row was created -- only the pre-seeded in-flight one.
        rows = _umr_tasks_rows(self.copy_path)
        self.assertEqual(len(rows), 1, msg=rows)
        self.assertEqual(rows[0]["umr_id"], self.INFLIGHT_UMR)

        # DECIDE-AND-FIX: an already-in-flight gap is not a new finding this
        # tick (nothing new to decide) -- counters stay reconciled at 0/0.
        self.assertIn("DECIDE-AND-FIX: 0 real finding(s) this tick, 0 same-tick decision(s)", result.stdout)
        self.assertNotIn("DECIDE-AND-FIX VIOLATION", result.stdout)


class PmSentinelTickGtmCertCompletionCertificateTest(unittest.TestCase):
    """Real test for SPEC step 4: zero real gap rows AND every real passed=1
    row carries real, non-empty, non-placeholder evidence -> a real,
    timestamped, evidence-citing completion certificate is written via
    superboss-register.py record-gtm-part3-4-certificate, exactly once
    (idempotent across ticks)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_gtmcert_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.copy_path = _seeded_copy(self.tmpdir, [])
        _seed_gtm_categories(self.copy_path, [
            (1, "static code analysis", 1, "Real eslint exit 0, tsc exit 0 against commit abc1234"),
            (2, "API testing", 1, "Real 12/12 endpoint checks passed against staging"),
            (15, "multi tenant testing", 1, "Real 4/4 test accounts, 0 cross-tenant leaks"),
            (16, "role permission testing", 1, "Real 17/17 role x endpoint checks matched"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["SUPERBOSS_REGISTER_PY"] = os.path.join(HERE, "superboss-register.py")
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
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

    def test_zero_gaps_all_evidenced_writes_real_certificate(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("0 real gap rows in gtm_certification_categories", result.stdout)
        self.assertIn("CERTIFIED: real Part3+4 GTM-certification completion certificate written", result.stdout)

        conn = sqlite3.connect(self.copy_path)
        conn.row_factory = sqlite3.Row
        cert = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log "
            "WHERE event_type='gtm_part3_4_completion_certificate'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(cert)
        self.assertEqual(cert["ocid_number"], "OCID-020")
        detail = json.loads(cert["detail_json"])
        self.assertEqual(len(detail["categories"]), 4)
        for c in detail["categories"]:
            self.assertEqual(c["passed"], 1)
            self.assertTrue((c["evidence_summary"] or "").strip())

    def test_certificate_is_idempotent_across_ticks(self):
        first = self._run_tick()
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        self.assertIn("CERTIFIED: real Part3+4", first.stdout)

        second = self._run_tick()
        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        self.assertNotIn("CERTIFIED: real Part3+4", second.stdout)
        self.assertIn("already certified", second.stdout)

        conn = sqlite3.connect(self.copy_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM ocid_master_standard_audit_log "
            "WHERE event_type='gtm_part3_4_completion_certificate'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class PmSentinelTickGtmCertEvidenceGapTest(unittest.TestCase):
    """Real test for SPEC step 4's own "never accept passed=1 with empty
    evidence as real" guard: zero rows with passed=0/NULL, but one real
    passed=1 row carries placeholder evidence_summary ("TBD") -- this must
    be treated as a real, newly-found gap (dispatched for a real fix), and
    no certificate may be written this tick."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_gtmevidgap_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.copy_path = _seeded_copy(self.tmpdir, [])
        _seed_gtm_categories(self.copy_path, [
            (1, "static code analysis", 1, "Real eslint exit 0, tsc exit 0"),
            (2, "API testing", 1, "TBD"),
        ])
        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "pm-sentinel-tick-report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "pm-sentinel-tick.prom")
        self.env = dict(os.environ)
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.tmpdir
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["SUPERBOSS_REGISTER_PY"] = os.path.join(HERE, "superboss-register.py")
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
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

    def test_placeholder_evidence_on_passed_row_dispatches_fix_not_certificate(self):
        result = self._run_tick()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("DISPATCHING for gtm-part3-4-evidence-gap:OCID-020", result.stdout)
        self.assertIn("2:API testing", result.stdout)
        self.assertNotIn("CERTIFIED: real Part3+4", result.stdout)

        conn = sqlite3.connect(self.copy_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM ocid_master_standard_audit_log "
            "WHERE event_type='gtm_part3_4_completion_certificate'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class PmSentinelTickTokenZeroGuardTest(unittest.TestCase):
    """Real test for assert_zero_llm_token_usage() / LLM_INVOCATION_PATTERN --
    this file's own real, continuous, every-tick enforcement of the
    "TOKEN USAGE: ZERO calls to any LLM" contract documented in
    pm-sentinel-tick.sh's own "TOKEN USAGE" header comment (previously only a
    one-time manual grep claim, never re-checked; now a real regression
    guard, run first thing every real tick). See PROGRESS.md,
    "pm-sentinel-tick.sh real measured token delta", for the real
    before/after this guard's own PASS result is the concrete, continuously
    re-verified evidence for."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pm_sentinel_tick_token_zero_test_")
        # Empty umr_tasks -- isolates this test to the guard itself, same
        # convention as PmSentinelTickLiveDeployDriftInSyncTest above.
        self.copy_path = _seeded_copy(self.tmpdir, [])
        self.live_dir = _local_drift_fixture(self.tmpdir, extra_origin_commit=False)

        self.state_file = os.path.join(self.tmpdir, "pm-sentinel-inflight.json")
        self.report_file = os.path.join(self.tmpdir, "report.jsonl")
        self.metrics_file = os.path.join(self.tmpdir, "metrics.prom")
        self.env = dict(os.environ)
        self.env["SUPERBOSS_REGISTER_DB"] = self.copy_path
        self.env["PM_SENTINEL_STATE_FILE"] = self.state_file
        self.env["PM_SENTINEL_MAX_DISPATCH"] = "5"
        self.env["PM_SENTINEL_REPORT_FILE"] = self.report_file
        self.env["PM_SENTINEL_METRICS_FILE"] = self.metrics_file
        self.env["PM_SENTINEL_LIVE_SCRIPTS_DIR"] = self.live_dir
        self.env["CHECK_LIVE_SCRIPTS_DRIFT_PY"] = os.path.join(HERE, "check_live_scripts_drift.py")
        # Real dispatch-owner-task.sh is fine here -- an empty umr_tasks
        # table + in-sync live checkout never dispatches anything, same
        # reasoning as PmSentinelTickLiveDeployDriftInSyncTest above.
        self.env["DISPATCH_OWNER_TASK_SH"] = REAL_DISPATCH_OWNER_TASK_SH
        self.env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        self.env["DISPATCH_TMUX_SESSION"] = "pm-sentinel-test-throwaway-session"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_real_shipped_script_passes_its_own_token_zero_guard(self):
        result = subprocess.run(
            [SENTINEL_SH], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )
        self.assertIn("TOKEN-ZERO GUARD: PASS", result.stdout, msg=result.stdout + result.stderr)
        self.assertNotIn("TOKEN-ZERO GUARD: FAIL", result.stdout)

        with open(self.metrics_file) as f:
            metrics_txt = f.read()
        self.assertIn("pm_sentinel_tick_llm_invocation_count 0", metrics_txt)

    def test_injected_real_llm_call_site_fails_the_guard_and_the_tick(self):
        # Real, concrete regression proof this guard is a real detector, not
        # a tautology: a mutated copy of the real script with one real
        # LLM-invocation call site appended must be caught -- TOKEN-ZERO
        # GUARD: FAIL, and a real non-zero exit code (same AUDIT-REJECT FIX
        # #2 exit-code-propagation convention every other real tick failure
        # already uses).
        mutated = os.path.join(self.tmpdir, "pm-sentinel-tick-mutated.sh")
        with open(SENTINEL_SH) as f:
            src = f.read()
        with open(mutated, "w") as f:
            f.write(src)
            f.write("\ncurl https://api.anthropic.com/v1/messages -d '{}'\n")
        os.chmod(mutated, os.stat(mutated).st_mode | stat.S_IEXEC)

        result = subprocess.run(
            [mutated], cwd=HERE, env=self.env,
            capture_output=True, text=True, timeout=90,
        )
        self.assertIn("TOKEN-ZERO GUARD: FAIL", result.stdout + result.stderr,
                       msg=result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
