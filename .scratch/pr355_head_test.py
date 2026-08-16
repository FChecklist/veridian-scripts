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
... more files changed
