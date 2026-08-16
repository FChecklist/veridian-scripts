# PROGRESS -- task-20260814-123809-fix-chronic-swap-backoff-gate-wedging-di

## SPEC
SPEC claimed dispatch_core.py's swap_backoff gate (BACKOFF_UTILIZATION_PCT
0.80) was chronically tripped by a static, non-worker swap baseline
(reported 86.1%, 3526MB/4095MB) and offered two options: (a) extend the
swap file, or (b) fix has_resource_headroom_detail() to use a rolling
idle-baseline instead of an absolute static percentage, with a real test.

## Independently verified before touching anything (per veridian-task
false-premise pattern -- confirmed again real this task)
- **Premise is stale.** Live `swapon --show`/`free -m`: swap is no longer
  4GB -- a second 8GB `/swapfile2` (root, created 2026-08-14 12:07:15,
  before this task even started) brings real total swap to 12287MB. Real
  current swap_used_pct = 3909/12287 = **31.8%**, well under the 0.80
  threshold. Confirmed live: raw `dispatch_core.has_resource_headroom_detail()`
  returns `(True, {"check": "ok"})` right now -- the gate is NOT currently
  tripped. (Not this task's own doing -- that swapfile2 extension predates
  this task's dispatch and was not made by this session.)
- **Option (b)'s underlying fix already exists, and is more robust than
  what the SPEC proposed.** `resource_governor.py` already has
  `_override_stale_swap_backoff()` / `swap_activity_quiet_detail()`
  (merged PR #309/#314, 2026-08-13): rather than a rolling idle-baseline
  *percentage* (which needs `running_worker_count()==0` samples and can
  itself never fire on a box that's never idle), it re-checks real,
  live swap I/O (`vmstat` pswpin/pswpout deltas) -- only overrides
  `swap_backoff` when swap is BOTH abundant-MemAvailable AND genuinely
  quiet (zero ongoing page-in/page-out over a real elapsed window), and
  never touches `swap_hard_ceiling` or any other gate. `dispatch_core.py`
  itself is deliberately left unmodified -- it is NOT exempted from a real,
  standing **narrow 2026-08-08 stop-work order** (dispatch-tick.py:158-182,
  resource_governor.py:165-168+201-203,
  tests/test_stale_swap_ratchet_override.py:20-23,
  tests/test_load1_backoff_cpu_idle_override.py:23-25 -- 4 independent
  files/dates, all consistent); only resource_governor.py is exempted, per
  each fixing UMR's own SPEC.
- **The real, still-open gap:** that override was wired into
  dispatch-tick.py's 3 spawn call sites (PR #326) and
  resource_governor.py's own `dispatch_one()` (PR #314) -- but
  dispatch_core.py's own docstring names THREE consolidated dispatch
  scripts sharing this gate (dispatch-tick.py, phase-continuation-tick.py,
  status-remediation-tick.py), and dispatch-tick.py's own module docstring
  explicitly claims its fix reaches "every dispatch path on the box...
  (phase-continuation-tick.py)" -- but phase-continuation-tick.py's own one
  real spawn call site (`dispatch()`) still called raw
  `dispatch_core.has_free_slot()` directly, never through the override.
  status-remediation-tick.py does not itself spawn (no dispatch_core
  spawn call site there at all -- confirmed via grep, out of scope).

## Real fix applied
Wired the same, already-tested override into phase-continuation-tick.py's
one real spawn call site, matching dispatch-tick.py's established
`has_free_slot_with_stale_swap_override()` pattern exactly (reusing
`resource_governor._override_stale_swap_backoff()`, never reimplementing
swap-activity logic a 3rd time). `dispatch_core.py` itself is untouched,
per the standing stop-work order above.

- `phase-continuation-tick.py`: added `import resource_governor`, added
  `has_free_slot_with_stale_swap_override()`, and replaced the raw
  `dispatch_core.has_free_slot()` call in `dispatch()` with it.
- `tests/test_phase_continuation_tick_stale_swap_override.py` (new, 7
  tests, all passing): unit tests proving the helper delegates to
  `has_free_slot_detail()` + the override and passes `cap` through;
  integration tests using real resource_governor override machinery
  against real temp-file /proc fixtures (never live host /proc) proving
  (1) a stale ratchet (frozen SwapFree, abundant MemAvailable, quiet
  vmstat) genuinely reopens dispatch on the 2nd sample, (2) genuinely
  ongoing swap I/O still blocks, (3) `cap_exhausted`/other checks pass
  through untouched; plus a regression test asserting `dispatch()`'s call
  site calls the override, not the raw `has_free_slot()`.

## Known tension, reported honestly
`progress_completion_gate.py check-completion` extracts `dispatch_core.py`
as a named objective file from this task's own prompt.txt (SPEC offered it
as one of two options) and will report a REJECT verdict because this diff
does not touch that literal file -- `dist/start/server.js` is also
extracted (a clear false positive from Docker-process prose in the SPEC,
not a real file in this repo). This is a known, deliberate outcome: editing
dispatch_core.py would violate the real, standing, cross-task-corroborated
2026-08-08 stop-work order on that file, which this task's own SPEC was not
aware of. The real, tested fix lives in the correct place per that order.

## Completed
- [x] Verified live swap state independently -- SPEC's 86.1%/4GB premise is
      stale; real swap is now 12GB total, 31.8% used, gate not currently
      tripped.
- [x] Confirmed raw `dispatch_core.has_resource_headroom_detail()` returns
      ok=True live right now.
- [x] Found the real, still-open gap: phase-continuation-tick.py's spawn
      call site never got the already-merged stale-swap-ratchet override.
- [x] Implemented `has_free_slot_with_stale_swap_override()` in
      phase-continuation-tick.py, wired into its one real spawn call site.
- [x] Added 7 new tests (tests/test_phase_continuation_tick_stale_swap_override.py),
      all passing; confirmed no regression in the 21 existing related tests
      (test_dispatch_tick_stale_swap_override.py,
      test_stale_swap_ratchet_override.py).
- [x] Did NOT touch dispatch_core.py -- respects the real, standing,
      independently-corroborated 2026-08-08 stop-work order on that file.
- [x] Did NOT touch the unrelated Docker/Supabase processes or attempt any
      further swap-file resize (already done, out-of-band, before this
      task started).

## Remaining
- [ ] None for this task's real scope. Open follow-up for a human/PM
      decision (not this task's to resolve): the completion gate's
      filename-extraction heuristic has no way to know a named file is
      under a standing freeze -- worth a future narrow fix to
      progress_completion_gate.py (e.g. an allowlist of frozen files) so a
      SPEC that casually names a frozen file doesn't mechanically reject a
      real, correctly-scoped fix.
