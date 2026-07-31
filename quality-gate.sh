#!/bin/bash
# Runs whatever quality gates are actually detectable in a workspace (lint,
# build, test) before a worker's changes are allowed to reach pending_review.
# Gracefully skips gates that don't apply (e.g. a docs-only repo). Writes a
# JSON summary and exits non-zero if any detected gate failed.
set -uo pipefail
WORKSPACE="$1"
OUT="$2"
cd "$WORKSPACE"

RESULTS_FILE=$(mktemp)
echo "{}" > "$RESULTS_FILE"
OVERALL=0

# Root-caused 2026-07-27 (task-20260727-043407 RCA against
# task-20260727-034439's watchdog "periodic checkpoint" stall signature):
# confirmed LIVE that a `next build` invoked from here can hang forever with
# zero forward progress -- the process sat in state S, wchan=ep_poll, with
# utime frozen across a 5s sample, for over an hour (task-20260727-034439's
# worker unit, PID 1205932). This is a DIFFERENT failure mode from the
# 2026-07-26 OOM fix above (that one crashed loudly and got restarted by
# systemd; this one never crashes, never exits, just sits idle in its own
# event loop). Nothing in this script (or worker-entrypoint.sh, which calls
# it synchronously in the foreground) ever bounded a gate command's
# wall-clock time -- eval "$cmd" blocks until the command exits, however
# long that takes. worker-entrypoint.sh's background periodic-checkpoint
# heartbeat (added 2026-07-26 specifically so a long-but-real quality-gate
# phase wouldn't be misdiagnosed as stalled) keeps firing "periodic
# checkpoint" notes the whole time via cheap `git status`/`git log` calls
# against the workspace -- those don't depend on the hung build at all, so
# they kept succeeding, which is exactly what made this LOOK like a healthy,
# slow-but-working task to the watchdog's LOOP_EXCLUDED_NOTES exemption
# instead of the permanent hang it actually was. Wrapping every gate command
# in `timeout` closes this: a hang now fails the gate (same as any other
# gate failure -- gets logged as a timeout in output_tail, feeds the
# existing auto-fix-then-blocked pipeline) instead of blocking the worker
# forever. `-k 30` sends SIGKILL 30s after the SIGTERM if the command
# ignores it (Node event-loop hangs like this one do not reliably die on
# SIGTERM alone). Applies to install too (same unbounded-`eval` shape, same
# class of risk, no evidence needed beyond that to close the gap
# consistently rather than patch only the one command that happened to hang
# this time).
GATE_STEP_TIMEOUT_SECONDS="${GATE_STEP_TIMEOUT_SECONDS:-900}"

run_gate() {
  local name="$1"; shift
  local cmd="$*"
  local logfile
  logfile=$(mktemp)
  timeout -k 30 "$GATE_STEP_TIMEOUT_SECONDS" bash -c "$cmd" > "$logfile" 2>&1
  local code=$?
  if [ "$code" -eq 124 ] || [ "$code" -eq 137 ]; then
    echo "[quality-gate.sh] gate '$name' TIMED OUT after ${GATE_STEP_TIMEOUT_SECONDS}s and was killed -- treating as a failed gate rather than blocking the worker forever (see task-20260727-043407 RCA)" >> "$logfile"
  fi
  tail -c 4000 "$logfile" > "${logfile}.tail"
  NAME="$name" CODE="$code" LOGFILE="${logfile}.tail" RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
name = os.environ["NAME"]
code = int(os.environ["CODE"])
results_file = os.environ["RESULTS_FILE"]
with open(os.environ["LOGFILE"]) as f:
    tail = f.read()
with open(results_file) as f:
    r = json.load(f)
r[name] = {"ran": True, "passed": code == 0, "exit_code": code, "output_tail": tail}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
  if [ $code -ne 0 ]; then OVERALL=1; fi
  echo "--- $name: exit $code ---"
  tail -50 "$logfile"
  rm -f "$logfile" "${logfile}.tail"
}

if [ -f package.json ]; then
  # Root-caused 2026-07-26 (task-20260726-180000 RCA against
  # task-20260726-171957's watchdog "periodic checkpoint" stall signature):
  # journalctl showed the worker unit "killed by the OOM killer" mid `next
  # build` here (3.7G memory peak for that one process, host at 13Gi/15Gi
  # used with swap already exhausted -- many veridian-worker@ units run
  # concurrent Node builds on this box with no per-process memory cap).
  # systemd's Restart=on-failure silently restarted the whole worker, which
  # re-ran this same unbounded-heap build again -- from the watchdog's side
  # that just looks like a gap in checkpointing, misread as a stall/loop
  # against the last "periodic checkpoint" note, even though nothing in the
  # AI's own logic was stuck. Capping V8's heap here bounds each concurrent
  # build's own contribution to system-wide memory pressure, same mitigation
  # Next.js's own docs recommend for constrained-memory build environments.
  # Preserves any NODE_OPTIONS already set rather than clobbering it.
  export NODE_OPTIONS="${NODE_OPTIONS:-} --max-old-space-size=2048"
  PKG_MGR="npm"
  [ -f pnpm-lock.yaml ] && PKG_MGR="pnpm"
  # Bun-managed repo (bun.lock / bun.lockb): prefer Bun. npm/pnpm cannot
  # resolve some of this repo's peer-dep graphs (e.g. zod v3/v4 split that
  # @memvid/sdk requires), so running npm here leaves node_modules empty and
  # every downstream gate fails with "eslint: not found" / "next: not found"
  # (exit 127) — an environment failure, not a code defect. Bun's lockfile
  # resolves the same graph cleanly. Bun may not be on PATH in the gate's
  # invocation shell, so also check the standard install location.
  if [ -f bun.lock ] || [ -f bun.lockb ]; then
    if command -v bun >/dev/null 2>&1 || [ -x /home/rajat/.bun/bin/bun ]; then
      BUN_BIN="$(command -v bun 2>/dev/null || echo /home/rajat/.bun/bin/bun)"
      PKG_MGR="$BUN_BIN"
    fi
  fi
  echo "Detected Node project (package manager: $PKG_MGR)"

  if ! [ -d node_modules ]; then
    echo "--- installing deps ---"
    timeout -k 30 "$GATE_STEP_TIMEOUT_SECONDS" $PKG_MGR install 2>&1 | tail -20
  fi

  if grep -q '"lint"' package.json; then
    run_gate lint "$PKG_MGR run lint"
  fi
  if grep -q '"build"' package.json; then
    # Root-caused 2026-07-31 (task-20260730-183017-rebase--ci-green--and-merge-pr-639
    # RCA, 3rd occurrence of the same host-wide-contention root cause after the
    # 2026-07-26 OOM fix and 2026-07-27 hang-timeout fix above): confirmed LIVE
    # that with several veridian-worker tasks each running their own unbounded
    # `next build` at the same instant, the host's 15Gi RAM + 4Gi swap saturates
    # (swap 100% exhausted, 1-min load average as high as 180) and manifests as a
    # THIRD distinct failure shape: Turbopack's own internal IPC to a worker
    # subprocess times out ("failed to receive message ... deadline has
    # elapsed") well before this script's 900s watchdog even fires. Caught in
    # the act: 6+ concurrent `claude -p` build-shaped processes plus a sibling
    # `timeout ... bun run build` from a different task were all live on this
    # box at the same moment this failure was captured. Per-process heap
    # capping (2026-07-26) and wall-clock bounding (2026-07-27) don't touch
    # this, because the exhausted resource is host-wide RAM/swap/CPU shared
    # across ALL concurrent builds, not any single build's own footprint.
    # Serializing the `build` step itself across every worker task via a
    # host-wide flock -- so at most one `next build` runs at a time instead of
    # N of them thrashing the same swap simultaneously -- fixes that shared
    # root cause directly without weakening what the gate checks: the build
    # command, its exit code, and pass/fail semantics are all unchanged;
    # concurrent callers now queue for a turn instead of all failing together.
    # `-w` bounds the wait so a genuinely deep backlog still fails honestly (a
    # real capacity limit) instead of hanging forever, and the existing outer
    # `timeout` in run_gate remains the hard backstop on total wall-clock time.
    run_gate build "flock -w 700 /tmp/veridian-quality-gate-build.lock -c '$PKG_MGR run build'"
  fi
  if grep -q '"test"' package.json; then
    run_gate test "$PKG_MGR test -- --run 2>/dev/null || $PKG_MGR test"
  fi
elif [ -f pyproject.toml ] || [ -f requirements.txt ]; then
  echo "Detected Python project"
  if [ -f pyproject.toml ] && grep -q ruff pyproject.toml 2>/dev/null; then
    run_gate lint "ruff check ."
  fi
  if [ -d tests ] || ls test_*.py >/dev/null 2>&1; then
    run_gate test "python3 -m pytest -q"
  fi
else
  echo "No recognized project type (package.json / pyproject.toml / requirements.txt) — no automated gates apply, skipping."
fi

cp "$RESULTS_FILE" "$OUT"
rm -f "$RESULTS_FILE"
exit $OVERALL
