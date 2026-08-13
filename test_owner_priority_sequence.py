#!/usr/bin/env python3
"""
Real, self-contained test for the owner_priority_sequence / advance
mechanism built under this task (amendment to UMR-20260807-070110-5ea7,
governed by UMR-20260806-124055-bc80).

Runs against a real COPY of the live superboss-register.sqlite (shutil.copy
of the actual file, opened via its own sqlite3 connection) -- never the
live table itself, matching the SPEC's own explicit requirement ("tested
against a real copy not the live table"). The copy is a real, byte-for-byte
snapshot of the live umr_tasks/ocid_canonical_registry data at test time,
so phase seeding runs the real discovery queries against real (not
synthetic/fabricated) rows; only the completion-evidence mutations in
test_advances_phase_1_to_phase_2 below are synthetic (needed to prove the
advance transition deterministically, without waiting on real, still-live
work to finish).

Same dynamic-import convention as this repo's other test_*.py files for a
hyphenated module (see test_ai_agent_registry.py).
"""
import importlib.util as _ilu
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SBR_PATH = os.path.join(HERE, "superboss-register.py")
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

_spec = _ilu.spec_from_file_location("owner_priority_sequence_test_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_spec)
sys.modules["owner_priority_sequence_test_mod"] = sbr
_spec.loader.exec_module(sbr)


class OwnerPrioritySequenceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="owner_priority_seq_test_")
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        self.assertNotEqual(os.path.abspath(self.copy_path), os.path.abspath(LIVE_DB))
        # Real copy of the real live file, taken via sqlite3's own backup
        # API rather than a raw shutil.copy2 -- this DB is WAL-mode and
        # actively written to by real live concurrent workers/dispatch
        # ticks, so a plain byte-level file copy can race a WAL checkpoint
        # mid-copy and land a genuinely corrupt ("database disk image is
        # malformed") snapshot (confirmed live: shutil.copy2 hit exactly
        # this on one real run of this test). conn.backup() is SQLite's own
        # documented mechanism for a consistent live snapshot without
        # blocking the source.
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        self.conn = sqlite3.connect(self.copy_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_seeds_all_4_phases_with_real_discovered_umr_ids(self):
        result = sbr.seed_owner_priority_sequence(self.conn)
        self.assertTrue(result["seeded"])
        phases = result["phases"]
        self.assertEqual([p["phase_order"] for p in phases], [1, 2, 3, 4])

        # Phase 1/2: exact explicit SPEC-named members.
        self.assertEqual(sorted(phases[0]["real_member_umrs"]),
                          sorted(sbr.OWNER_PRIORITY_PHASE1_MEMBERS))
        self.assertEqual(sorted(phases[1]["real_member_umrs"]),
                          sorted(sbr.OWNER_PRIORITY_PHASE2_MEMBERS))

        # Phase 3/4: real governing_umr from ocid_canonical_registry, real
        # non-empty discovered member set, governing UMR itself included.
        ocid_020_umr = sbr._lookup_ocid_governing_umr(self.conn, "OCID-020")
        ocid_021_umr = sbr._lookup_ocid_governing_umr(self.conn, "OCID-021")
        self.assertIsNotNone(ocid_020_umr)
        self.assertIsNotNone(ocid_021_umr)
        self.assertEqual(phases[2]["governing_umr"], ocid_020_umr)
        self.assertEqual(phases[3]["governing_umr"], ocid_021_umr)
        self.assertIn(ocid_020_umr, phases[2]["real_member_umrs"])
        self.assertIn(ocid_021_umr, phases[3]["real_member_umrs"])
        self.assertGreater(len(phases[2]["real_member_umrs"]), 1,
                            "expected real discovered children beyond the governing UMR itself")
        self.assertGreater(len(phases[3]["real_member_umrs"]), 1,
                            "expected real discovered children beyond the governing UMR itself")

        # Every single seeded id is a real row in umr_tasks (bounded to
        # real explicit ids, never a wildcard/fabrication).
        for phase in phases:
            for umr_id in phase["real_member_umrs"]:
                row = self.conn.execute(
                    "SELECT 1 FROM umr_tasks WHERE umr_id = ?", (umr_id,)
                ).fetchone()
                self.assertIsNotNone(row, f"{umr_id} (phase {phase['phase_order']}) is not a real umr_tasks row")

        # Re-running seed is a real no-op (idempotent).
        result2 = sbr.seed_owner_priority_sequence(self.conn)
        self.assertFalse(result2["seeded"])

    def test_starts_with_only_phase_1_active(self):
        sbr.seed_owner_priority_sequence(self.conn)
        rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order, status FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
        self.assertEqual(rows, [
            {"phase_order": 1, "status": "active"},
            {"phase_order": 2, "status": "pending"},
            {"phase_order": 3, "status": "pending"},
            {"phase_order": 4, "status": "pending"},
        ])

        override_umrs = sorted(dict(r)["umr_id"] for r in self.conn.execute(
            "SELECT umr_id FROM owner_priority_override").fetchall())
        self.assertEqual(override_umrs, sorted(sbr.OWNER_PRIORITY_PHASE1_MEMBERS))

    def test_advances_phase_1_to_phase_2_once_members_genuinely_completed(self):
        sbr.seed_owner_priority_sequence(self.conn)

        # Sanity: a tick BEFORE members are genuinely completed makes no
        # transition (live state has ae93 'running', f432
        # 'completed_unmerged' -- neither is genuinely complete).
        pre = sbr.advance_owner_priority_phases(self.conn)
        self.assertFalse(pre["transitioned"])
        active = dict(self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchone())
        self.assertEqual(active["phase_order"], 1)

        # Real evidence, written into the COPY only: an absolute file_path
        # that genuinely exists on disk -- the same real artifact
        # validate_umr_terminal_completion_evidence()'s own status=completed
        # gate requires, not a bare status-label flip.
        real_evidence_file = SBR_PATH
        self.assertTrue(os.path.isfile(real_evidence_file))
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            self.conn.execute(
                "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
                (sbr._now_iso(), json.dumps({"file_path": real_evidence_file, "repo": "veridian-scripts"}), umr_id),
            )
        self.conn.commit()

        # Re-verify the evidence check itself, standalone, before trusting
        # the full advance call.
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            ok, reason = sbr._umr_genuinely_completed(self.conn, umr_id)
            self.assertTrue(ok, reason)

        result = sbr.advance_owner_priority_phases(self.conn)
        self.assertTrue(result["transitioned"], result)
        self.assertEqual(result["new_active_phase"], 2)

        rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order, status FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
        self.assertEqual(rows, [
            {"phase_order": 1, "status": "complete"},
            {"phase_order": 2, "status": "active"},
            {"phase_order": 3, "status": "pending"},
            {"phase_order": 4, "status": "pending"},
        ])

        # owner_priority_override now holds ONLY phase 2's real members --
        # phase 1's prior entries were removed, never left stale.
        override_umrs = sorted(dict(r)["umr_id"] for r in self.conn.execute(
            "SELECT umr_id FROM owner_priority_override").fetchall())
        self.assertEqual(override_umrs, sorted(sbr.OWNER_PRIORITY_PHASE2_MEMBERS))

        # A second tick with nothing newly completed makes no further
        # transition (phase 2's members are all still real, live,
        # incomplete rows) -- never activates more than one phase.
        result2 = sbr.advance_owner_priority_phases(self.conn)
        self.assertFalse(result2["transitioned"])
        active_rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchall()]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["phase_order"], 2)

    def test_never_more_than_one_active_phase_after_full_run(self):
        sbr.seed_owner_priority_sequence(self.conn)
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            self.conn.execute(
                "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
                (sbr._now_iso(), json.dumps({"file_path": SBR_PATH, "repo": "veridian-scripts"}), umr_id),
            )
        self.conn.commit()
        sbr.advance_owner_priority_phases(self.conn)
        active_rows = self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchall()
        self.assertEqual(len(active_rows), 1)

    def test_advance_memoizes_confirmed_members_and_bounds_per_tick_evaluations(self):
        """Real review finding (PR #256 review.json): advance_owner_priority_phases()
        used to re-run _umr_genuinely_completed() for EVERY member on EVERY tick
        (no memoization) with no bound on how many real evidence checks happen in
        one tick -- for phase 3/4's live-discovery membership (179/70 real hits in
        the SPEC's own evidence) this could mean hundreds of synchronous real git
        subprocess calls in a single tick. This test proves both fixes directly:
        (1) a phase with more members than the per-tick cap only evaluates the
        capped subset in one call, (2) a member already confirmed complete is
        never re-evaluated on a later call."""
        # Synthetic phase inserted directly (seed_owner_priority_sequence is a
        # no-op once owner_priority_sequence already has rows), so this test
        # controls membership size precisely without depending on how many
        # real live rows OCID-020/021 discovery currently returns.
        sbr._ensure_owner_priority_tables(self.conn)
        real_complete_members = list(sbr.OWNER_PRIORITY_PHASE1_MEMBERS)  # 3 real umr_tasks rows
        fake_members = [f"UMR-TESTONLY-{i:03d}" for i in range(12)]  # real rows do not exist -> False, not fabricated-True
        members = real_complete_members + fake_members
        now = sbr._now_iso()
        self.conn.execute(
            "INSERT INTO owner_priority_sequence "
            "(phase_order, phase_name, governing_umr, real_member_umrs, status, created_at, updated_at) "
            "VALUES (1, 'test phase', 'UMR-TESTONLY-GOV', ?, 'active', ?, ?)",
            (json.dumps(members), now, now),
        )
        self.conn.commit()

        # Give the 3 real members genuine file-path evidence so they are
        # real, independently-verifiable completions (same convention as
        # test_advances_phase_1_to_phase_2_once_members_genuinely_completed).
        for umr_id in real_complete_members:
            self.conn.execute(
                "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
                (sbr._now_iso(), json.dumps({"file_path": SBR_PATH, "repo": "veridian-scripts"}), umr_id),
            )
        self.conn.commit()

        cap = 5
        orig_cap = sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK
        sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK = cap
        call_log = []
        orig_fn = sbr._umr_genuinely_completed

        def counting_wrapper(conn, umr_id, repos_root="/opt/veridian/repos"):
            call_log.append(umr_id)
            return orig_fn(conn, umr_id, repos_root=repos_root)

        sbr._umr_genuinely_completed = counting_wrapper
        try:
            result1 = sbr.advance_owner_priority_phases(self.conn)
            # Real bound: exactly `cap` members got a real evidence check
            # this tick, never all 15.
            self.assertEqual(result1["members_evaluated_this_tick"], cap)
            self.assertEqual(len(call_log), cap)
            self.assertEqual(len(result1["members_still_pending"]), len(members) - cap)
            self.assertFalse(result1["transitioned"])

            row = dict(self.conn.execute(
                "SELECT confirmed_complete_members FROM owner_priority_sequence WHERE phase_order=1"
            ).fetchone())
            confirmed_after_1 = set(json.loads(row["confirmed_complete_members"]))
            # The first `cap` members in list order are the 3 real ones (they
            # sort first) plus 2 fake ones -- only the real ones can be
            # genuinely confirmed complete.
            self.assertEqual(confirmed_after_1, set(real_complete_members))

            # Second tick: real members already confirmed must NOT be
            # re-checked -- the call log must contain zero repeats of them.
            call_log.clear()
            result2 = sbr.advance_owner_priority_phases(self.conn)
            self.assertNotIn(real_complete_members[0], call_log,
                              "a member already in confirmed_complete_members must not be re-evaluated")
            self.assertNotIn(real_complete_members[1], call_log)
            self.assertNotIn(real_complete_members[2], call_log)
            # Still bounded to `cap` real NEW evaluations this tick too.
            self.assertEqual(result2["members_evaluated_this_tick"], cap)
        finally:
            sbr._umr_genuinely_completed = orig_fn
            sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK = orig_cap

    def test_umr_genuinely_completed_accepts_real_commit_sha_ancestor_of_main_evidence(self):
        """Real review finding (PR #256 review.json): no test exercised the
        commit_sha evidence branch at all, only the file_path branch -- this
        proves _umr_genuinely_completed() (and by extension
        advance_owner_priority_phases()) also genuinely accepts a real
        --commit-sha that IS a real ancestor of origin/main, against a real
        repo checkout, not a mock."""
        # 2026-08-13 (task-20260813-103224, UMR-20260813-101142-5d24):
        # _umr_genuinely_completed() now special-cases repo="veridian-scripts"
        # to the real live checkout (/opt/veridian/scripts), not the
        # orphaned /opt/veridian/repos/veridian-scripts mirror -- assert
        # against the path the real code now actually uses.
        real_repo_root = "/opt/veridian/scripts"
        self.assertTrue(os.path.isdir(os.path.join(real_repo_root, ".git")))
        real_ancestor_sha = "4d751bfa94a25a92163def398db08fd96c024dd9"  # real origin/main~1, verified ancestor

        test_umr_id = sbr.OWNER_PRIORITY_PHASE1_MEMBERS[0]
        self.conn.execute(
            "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
            (sbr._now_iso(), json.dumps({"commit_sha": real_ancestor_sha, "repo": "veridian-scripts"}), test_umr_id),
        )
        self.conn.commit()

        ok, reason = sbr._umr_genuinely_completed(self.conn, test_umr_id, repos_root="/opt/veridian/repos")
        self.assertTrue(ok, reason)
        self.assertIn(real_ancestor_sha, reason)

    def test_phase3_4_scale_git_subprocess_calls_bounded_and_lock_not_held(self):
        """Real review finding (PR #256 review.json, both rounds): (1) once
        phase 3/4's real live-discovery membership (179/70 real hits per the
        SPEC's own evidence) is active, a tick evaluating every not-yet-
        confirmed commit_sha-backed member could shell out to hundreds of
        real git subprocess calls in one tick; (2) round 2 found those
        subprocess calls used to run while superboss-register.py's own
        cross-process _write_lock() was held, serializing every other
        writer in the system behind them. This test proves both fixes
        directly at phase-3/4 scale: builds a synthetic phase with 150 real
        commit_sha-evidenced umr_tasks rows (more than the per-tick cap),
        monkeypatches the one real subprocess entry point
        (_default_ocid_resolver_runner) with a counting fake that also
        records whether sbr._write_lock() was held at call time, and
        asserts (a) the number of real git subprocess calls issued in any
        single tick never exceeds a fixed bound tied to
        OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK -- staying flat
        across repeated ticks rather than growing as more members remain
        unconfirmed -- and (b) not one of those calls happened while the
        cross-process write lock was held."""
        sbr._ensure_owner_priority_tables(self.conn)

        member_count = 150  # comparable order of magnitude to the SPEC's own 179/70 phase 3/4 evidence
        members = [f"UMR-SCALETEST-{i:04d}" for i in range(member_count)]
        now = sbr._now_iso()
        for umr_id in members:
            self.conn.execute(
                "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
                "source_trigger, outputs_json) VALUES (?, ?, ?, ?, 'completed', ?, ?)",
                (umr_id, f"scale test row {umr_id}", now, 3, "test",
                 json.dumps({"commit_sha": "deadbeef" + umr_id[-4:], "repo": "veridian-scripts"})),
            )
        self.conn.execute(
            "INSERT INTO owner_priority_sequence "
            "(phase_order, phase_name, governing_umr, real_member_umrs, status, created_at, updated_at) "
            "VALUES (1, 'phase 3/4 scale test', 'UMR-SCALETEST-GOV', ?, 'active', ?, ?)",
            (json.dumps(members), now, now),
        )
        self.conn.commit()

        cap = 25
        orig_cap = sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK
        sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK = cap
        orig_runner = sbr._default_ocid_resolver_runner
        call_log = []  # each entry records the write-lock reentrancy depth at call time

        class _FakeResult:
            returncode = 0

        def counting_runner(cmd, cwd=None):
            call_log.append({"cmd": cmd, "lock_depth": sbr._write_lock_depth[0]})
            return _FakeResult()

        sbr._default_ocid_resolver_runner = counting_runner
        try:
            per_tick_counts = []
            max_ticks = (member_count // cap) + 3  # real safety margin, not a tight bound
            for _ in range(max_ticks):
                before = len(call_log)
                result = sbr.advance_owner_priority_phases(self.conn)
                per_tick_counts.append(len(call_log) - before)
                # A None evaluated_phase means no phase is 'active' any more
                # (this phase fully confirmed and transitioned to 'complete'
                # on a prior tick, and there is no next phase to activate) --
                # real, unambiguous stop condition, real convergence proven.
                if result.get("evaluated_phase") is None:
                    break

            # (a) real bound: at most 4 real subprocess calls (fetch+cat-file
            # for commit-exists, fetch+merge-base for is-ancestor) per member
            # evaluated, and never more than `cap` members evaluated per
            # tick -- so no single tick's real subprocess-call count ever
            # exceeds cap * 4, regardless of how many total members remain
            # unconfirmed.
            max_calls_per_tick = cap * 4
            for count in per_tick_counts:
                self.assertLessEqual(
                    count, max_calls_per_tick,
                    f"a single tick issued {count} real git subprocess calls, expected at most "
                    f"{max_calls_per_tick} (cap={cap}) -- the original PR #256 review.json defect "
                    "was exactly this growing unbounded with phase size"
                )
            self.assertGreater(len(per_tick_counts), 1,
                                "expected more than one tick to be needed to confirm all 150 members "
                                "at this cap -- otherwise this test isn't exercising real convergence")

            # (b) real proof the fix moved these calls outside the write
            # lock: not one recorded call happened while
            # sbr._write_lock_depth was nonzero (i.e. while the
            # cross-process lock was held).
            held_during = [c for c in call_log if c["lock_depth"] != 0]
            self.assertEqual(
                held_during, [],
                f"{len(held_during)} real git subprocess call(s) happened while _write_lock() was "
                "held -- round 2 PR #256 review.json regression (system-wide dispatch starvation)"
            )

            # Real convergence: memoization means every real member is
            # eventually confirmed across however many ticks it took, never
            # stalled by an already-confirmed member being re-checked.
            row = dict(self.conn.execute(
                "SELECT confirmed_complete_members FROM owner_priority_sequence WHERE phase_order=1"
            ).fetchone())
            confirmed = set(json.loads(row["confirmed_complete_members"]))
            self.assertEqual(confirmed, set(members))
        finally:
            sbr._default_ocid_resolver_runner = orig_runner
            sbr.OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK = orig_cap

    def test_live_db_untouched(self):
        """The copy-based tests above must never have written to the real
        live DB -- confirm no owner_priority_sequence table exists there
        as a side effect of importing/running this test module."""
        live_conn = sqlite3.connect(LIVE_DB)
        try:
            row = live_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='owner_priority_sequence'"
            ).fetchone()
            self.assertIsNone(row, "owner_priority_sequence must not exist on the LIVE db from this test run")
        finally:
            live_conn.close()


if __name__ == "__main__":
    unittest.main()
