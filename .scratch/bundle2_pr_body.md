## Supersedes 2 mutually-conflicting PRs: #419 and #429 (bundle 2, task-20260816-094442)

Same branch-enforcement reasoning as #437 (bundle 1): this worker can only
push its own single assigned branch, so a real conflict-resolved merge is
opened as a new PR rather than pushed to either original branch directly.

**Why these two were split out of bundle 1:** both #419 and #429
independently committed `queue-manager.py`/`timer-manager.py` as
previously-uncommitted live CLI tools (each is a fresh add against its own
base -- two workers found the same live drift around the same time,
2026-08-15). Real diff of the two full files (not just titles): #429's
versions (401/156 lines) are a strict superset of #419's (227/128 lines) --
every function and CLI subcommand in #419 is present verbatim in #429, plus
#429 adds two real, documented bug fixes:
- `timer-manager.py`: a stopped-timer NEXT/LEFT column-shift bug that made
  `list_timers` print nothing for any currently-stopped
  `veridian-*.timer` unit (reproduced live -- every real veridian timer on
  the box was stopped at audit time).
- `queue-manager.py`: `list --status queued` only ever read post-dispatch
  `task.yaml` files, structurally blind to the real pre-dispatch
  `umr_tasks` backlog (33+ real rows at the time), giving a false "nothing
  queued" read. #429 adds `fetch_pre_dispatch_queue`/`stop-pending`/
  `resume-pending`/`priority-pending`, delegating to
  `resource_governor.py`'s own real queue functions.

Resolution: merged #419 first (its only conflict was the disposable
`PROGRESS.md` stub), then #429 (conflicts in `PROGRESS.md` +
`queue-manager.py` + `timer-manager.py`) -- kept #429's `queue-manager.py`/
`timer-manager.py` wholesale since it strictly contains 100% of #419's
content plus real fixes (verified by reading both full files side by side
in real git worktrees, not by title alone). `pm-sentinel-tick.sh` and
`pm_lifecycle.py` (part of #429's own real diff) auto-merged with zero
conflicts.

Diffstat vs `main`: 10 files changed, 1070 insertions(+), 5 deletions(-).
`bash -n` clean, `py_compile` clean, 13/13 new tests passing
(`tests/test_queue_manager.py`, `tests/test_timer_manager.py`).

Requesting a real independent audit against this exact head SHA before merge.
