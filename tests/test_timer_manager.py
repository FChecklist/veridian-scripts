#!/usr/bin/env python3
"""
Real test for timer-manager.py's list_timers() column-parsing bug fix
(task-20260815-231659): reproduced live 2026-08-15 against the real
currently-stopped veridian-*.timer units -- the old implementation sliced
line.split()[4] to find the unit name, which only lands on UNIT when NEXT
and LEFT are both populated with a real date+time+duration triple. When a
timer is stopped, systemctl prints a bare "-" for NEXT/LEFT instead,
shifting every later column left and making parts[4] land on a time-of-day
token instead -- so the tool silently printed nothing for every real
currently-stopped timer.

These tests never shell out to the real systemctl -- they monkeypatch
timer_manager._run with fixed sample output shaped exactly like real
`systemctl --user list-timers --all --no-pager` output (both the "some
timers active" and "all timers stopped" real shapes), so the assertions are
deterministic regardless of what state the live timers happen to be in when
this test runs.
"""
import importlib.util as _ilu
import io
import os
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TIMER_MANAGER_PATH = os.path.join(os.path.dirname(HERE), "timer-manager.py")

_spec = _ilu.spec_from_file_location("timer_manager_under_test", TIMER_MANAGER_PATH)
timer_manager = _ilu.module_from_spec(_spec)
sys.modules["timer_manager_under_test"] = timer_manager
_spec.loader.exec_module(timer_manager)


# Real shape reproduced live 2026-08-15: every veridian-*.timer unit
# currently stopped (bare "-" for NEXT and LEFT).
ALL_STOPPED_OUTPUT = """NEXT LEFT LAST                         PASSED UNIT                              ACTIVATES
-       - Sat 2026-08-15 11:42:44 UTC 11h ago veridian-cron-dispatch-tick.timer -
-       - Sat 2026-08-15 11:55:04 UTC 11h ago veridian-cron-sync-repos.timer    -
-       - Sat 2026-08-15 12:00:04 UTC 11h ago veridian-cron-zoekt-reindex.timer -

3 timers listed.
"""

# Real shape when at least one timer is active: NEXT/LEFT populated with a
# real date+time+duration triple, shifting column count for that row.
MIXED_OUTPUT = """NEXT                            LEFT LAST                         PASSED UNIT                              ACTIVATES
Sun 2026-08-16 03:38:04 UTC 4h 19min Sat 2026-08-15 03:38:04 UTC 19h ago launchpadlib-cache-clean.timer    launchpadlib-cache-clean.service
-                                  - Sat 2026-08-15 11:42:44 UTC 11h ago veridian-cron-dispatch-tick.timer -
Sun 2026-08-16 00:05:00 UTC 30min Sat 2026-08-15 12:00:04 UTC 11h ago veridian-pm-report-tick.timer     veridian-pm-report-tick.service

3 timers listed.
"""

EMPTY_OUTPUT = """NEXT LEFT LAST PASSED UNIT ACTIVATES

0 timers listed.
"""


class ListTimersTest(unittest.TestCase):
    def _run_list_timers(self, stdout_text, rc=0):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return stdout_text, "", rc

        real_run = timer_manager._run
        timer_manager._run = fake_run
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                timer_manager.list_timers()
        finally:
            timer_manager._run = real_run
        return buf.getvalue(), calls

    def test_all_stopped_timers_print_with_real_unit_names(self):
        """The real bug: every currently-stopped veridian timer must still
        be printed, with its correct unit name -- not silently dropped."""
        out, _calls = self._run_list_timers(ALL_STOPPED_OUTPUT)
        self.assertIn("veridian-cron-dispatch-tick.timer", out)
        self.assertIn("veridian-cron-sync-repos.timer", out)
        self.assertIn("veridian-cron-zoekt-reindex.timer", out)
        # Header and summary line must never be printed as if they were rows.
        self.assertNotIn("NEXT", out)
        self.assertNotIn("timers listed.", out)

    def test_mixed_active_and_stopped_timers_both_print_correctly(self):
        out, _calls = self._run_list_timers(MIXED_OUTPUT)
        self.assertIn("veridian-cron-dispatch-tick.timer", out)  # stopped row
        self.assertIn("veridian-pm-report-tick.timer", out)      # active row

    def test_empty_result_prints_nothing_but_does_not_error(self):
        out, _calls = self._run_list_timers(EMPTY_OUTPUT)
        self.assertEqual(out.strip(), "")

    def test_nonzero_exit_reports_error_not_silence(self):
        buf = io.StringIO()
        real_run = timer_manager._run
        timer_manager._run = lambda cmd: ("", "boom", 1)
        try:
            with redirect_stdout(io.StringIO()) as _out, \
                 unittest.mock.patch("sys.stderr", new=buf):
                timer_manager.list_timers()
        finally:
            timer_manager._run = real_run
        self.assertIn("Error", buf.getvalue())

    def test_query_filters_server_side_with_veridian_pattern(self):
        """Second half of the fix: systemctl itself is asked to filter by a
        veridian-*.timer pattern, rather than this tool re-deriving the
        filter from a positionally-indexed column."""
        _out, calls = self._run_list_timers(ALL_STOPPED_OUTPUT)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertIn("list-timers", cmd)
        self.assertIn("veridian-*.timer", cmd)


if __name__ == "__main__":
    unittest.main()
