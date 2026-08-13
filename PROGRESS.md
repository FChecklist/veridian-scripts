# PROGRESS -- task-20260813-171208-fix-pm-sentinel-tick-sh-positional-activ

## SPEC
REDISPATCH of UMR-20260813-145511-5aca (governing this redispatch:
UMR-20260813-170956-5385): fix pm-sentinel-tick.sh's positional
ActiveState/Result parse (Check 2b, ~line 755) and its non-zero-exit-on-
cap-reached-adjacent defect; coordinate with open PR #299
(FChecklist/veridian-scripts).

## Completed
- [x] Re-verified all evidence independently before acting (per the
      redispatch note and the known task-dispatch false-premise pattern):
  - Confirmed `/opt/veridian/repos/veridian-scripts` exists on disk (the
    prior redispatch's own claimed repair), so `veridian-task.py create`
    can actually clone from it.
  - Confirmed PR #299 (FChecklist/veridian-scripts, head 5e3eeeb at start)
    is OPEN/MERGEABLE/CLEAN and really contains the exact buggy pattern at
    `pm-sentinel-tick.sh:755-757`
    (`systemctl --user show ... --value` + `sed -n 1p`/`sed -n 2p`
    positional read).
  - Confirmed the live-deployed `/opt/veridian/scripts/pm-sentinel-tick.sh`
    has the byte-identical bug at the same lines, and that
    `veridian-pm-sentinel-tick.service` is really in `Active: failed
    (Result: exit-code)` (last run exited 1).
  - Confirmed via the real cron tick log
    (`/opt/veridian/ai-os/logs/pm-sentinel-tick-cron.log`) that the swap
    really happens live: rows read `ActiveState=success Result=active` /
    `ActiveState=success Result=inactive`, both semantically impossible
    unless the two fields were swapped by output position.
  - Re-checked the three false-positive UMR rows named in the original
    evidence (UMR-20260813-141620-94c7, UMR-20260813-141628-e66b,
    UMR-20260813-141633-f0fc) via `resource_governor.py --query-umr`: all
    three are already `status=completed`, each closed by real,
    independently-dispatched RCA work (not by this task) with an honest,
    evidence-cited terminal reason. No action needed or taken on these
    three; did not touch the killed-path rows
    UMR-20260813-141610-273a / UMR-20260813-141605-0ece, per instruction.
- [x] Fixed `pm-sentinel-tick.sh` Check 2b's ActiveState/Result parse to be
      order-independent: dropped `--value`, extract each field by its own
      `Key=` prefix via `sed`, same single `systemctl` call preserved
      (query-once-per-tick optimization intact).
- [x] Root-caused and fixed the real non-zero-exit defect: `dispatch_gap()`
      was counting dispatch-owner-task.sh's own content-duplicate refusal
      (an identical prompt already logged within its real 6h window) as a
      genuine `TICK_FAILURES` failure. Now recognized and skipped quietly
      (return 0, not counted), while every other real dispatch failure
      still counts and still propagates non-zero (AUDIT-REJECT FIX #2
      preserved, its existing regression test untouched and still passes).
- [x] Added two real regression tests to `test_pm_sentinel_tick.py`:
      `PmSentinelTickRunningRowOrderIndependentParseTest` (feeds both the
      documented and swapped systemctl property order via a real PATH-shim
      fake `systemctl`, asserts an active unit is never classified dead,
      and that a genuinely dead unit is still correctly flagged) and
      `PmSentinelTickDuplicateContentRefusalDoesNotFailTickTest` (asserts a
      tick that only hits a duplicate-content refusal still exits 0).
- [x] Ran the full real test suite as real subprocesses against an isolated
      sqlite3 copy of the live DB: **8 passed in 311.47s, exit 0**
      (`python3 -m pytest test_pm_sentinel_tick.py -v`) -- 6 pre-existing
      tests unchanged/still passing + 2 new.
- [x] Landed the fix by pushing directly onto PR #299's own branch
      (`worker/task-20260813-123933-add-query-once-decide-and-fix`) as a
      clean fast-forward commit (5e3eeeb -> 32b4276), per SPEC point 4 --
      no competing PR opened. Verified live afterward: PR #299's
      `headRefOid` is now `32b4276...`.

## Remaining
- [ ] None outstanding on this task's own scope. PR #299 (now carrying this
      fix) is still open and unmerged -- merging it is outside this task's
      authority per the governing UMR chain's own review process, not a
      gap left by this task.
