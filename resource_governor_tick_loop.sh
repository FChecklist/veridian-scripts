#!/usr/bin/env bash
set -uo pipefail
LOG=/opt/veridian/ai-os/tasks/resource_governor_tick.log
ATTENTION=/opt/veridian/ai-os/logs/ATTENTION.md

# 2026-08-02 (Sentinel finding, restore-reconcile task): the deploy after
# 2026-07-30 silently dropped --reconcile-stale from resource_governor.py
# while this loop kept invoking it every 30s -- argparse's "unrecognized
# arguments: --reconcile-stale" got silently appended to LOG 7946 times
# before anyone noticed, because a non-zero exit here was never distinguished
# from a normal failed tick. argparse exits 2 specifically for a bad/unknown
# CLI invocation (missing subcommand, not a runtime failure of a real
# subcommand) -- run_governor treats THAT exit code as loud and distinct so
# this class of regression can never again go unnoticed for that long.
run_governor() {
  local subcmd="$1"
  python3 /opt/veridian/scripts/resource_governor.py "$subcmd" >> "$LOG" 2>&1
  local rc=$?
  if [ "$rc" -eq 2 ]; then
    local msg="$(date -u +%Y-%m-%dT%H:%M:%SZ) GOVERNOR-TICK-LOOP CRITICAL: \`resource_governor.py $subcmd\` exited 2 (argparse usage/unrecognized-arguments error) -- this subcommand this tick loop depends on may no longer exist in the live script. See $LOG for the argparse output immediately above this line. This loop will keep retrying every 30s until fixed."
    echo "$msg" >> "$LOG"
    mkdir -p "$(dirname "$ATTENTION")"
    printf '\n## %s -- RESOURCE GOVERNOR TICK LOOP\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >> "$ATTENTION"
  fi
  return "$rc"
}

while true; do
  run_governor --tick
  # Stage 3 (2026-07-29): reconciliation pass for the "task exits cleanly but
  # umr_tasks status never reconciles" bug. Scoped internally to rows with a
  # real, stale last_heartbeat only (NULL heartbeats -- e.g. all 5 real
  # in-flight tasks at the moment this deploys -- are always skipped, see
  # resource_governor.py's reconcile_stale_heartbeats() docstring).
  #
  # UMR-20260806-141429-f447 (proposal 88 priority one): reconcile_stale_
  # heartbeats() previously had NO execute gate at all -- it wrote/committed
  # the instant it found any stale non-NULL last_heartbeat, real writes every
  # 30s cadence, with zero dry-run path. It now has a real execute gate
  # (default off, matching --backfill-null-heartbeats' own convention), so
  # this invocation -- deliberately still WITHOUT --execute -- is now a
  # read-only report-only pass: it finds and logs would-reconcile rows but
  # writes nothing. Turning real writes back on here is a separate, explicit
  # decision (add --execute below) for whoever owns that call, not bundled
  # into this fix.
  run_governor --reconcile-stale
  # UMR-20260806-115538-1e55 / UMR-20260806-115605-854d: real, immediate,
  # automatic dead-zone sweep -- any real umr_tasks row stuck at
  # status='dispatched' >15min with no real task directory/systemd unit
  # ever created gets auto-reset to 'queued' right here, same 30s cadence
  # as --tick/--reconcile-stale above (never sits waiting for an AI to read
  # a report first). Standalone script (not a resource_governor.py
  # subcommand) -- see reconcile_dispatched_dead_zone.py's own module
  # docstring for the full real condition, the real ocid_artifact_links
  # safety guard, and the real second-occurrence escalation logic. Exit
  # code is always 0 on a clean run (auto-reset/escalated/nothing-to-do are
  # all real, expected steady-state outcomes) -- a non-zero exit here is a
  # genuine internal error, logged the same way as any other real tick
  # failure via the plain >> "$LOG" 2>&1 redirect below (no separate
  # run_governor()-style exit-2 special-case: this script is not
  # resource_governor.py's own argparse CLI, so that specific historical
  # failure mode does not apply here).
  python3 /opt/veridian/scripts/reconcile_dispatched_dead_zone.py >> "$LOG" 2>&1
  sleep 30
done
