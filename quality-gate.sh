#!/bin/bash
# Runs whatever quality gates are actually detectable in a workspace (lint,
# build, test) before a worker's changes are allowed to reach pending_review.
# Gracefully skips gates that don't apply (e.g. a docs-only repo). Writes a
# JSON summary and exits non-zero if any detected gate failed.
set -uo pipefail
WORKSPACE="$1"
OUT="$2"
cd "$WORKSPACE"

# UMR-20260806-123316-cf9f (proposal 62, child UMR-20260806-121247-a93a):
# TASK_DIR/TASK_ID are derived from $OUT rather than passed as new args or
# relying on worker-entrypoint.sh exporting them (it does not) --
# worker-entrypoint.sh always calls this script with
# OUT="$TASK_DIR/quality-gate-$GATE_ATTEMPT.json" and
# TASK_DIR="/opt/veridian/ai-os/tasks/$TASK_ID", so both are 100% recoverable
# from $OUT alone.
TASK_DIR="$(dirname "$OUT")"
TASK_ID="$(basename "$TASK_DIR")"
BUILD_LOCK_CONTENDED_EXIT_CODE=75

RESULTS_FILE=$(mktemp)
echo "{}" > "$RESULTS_FILE"
OVERALL=0

# Mandatory resume behavior (UMR-20260806-123316-cf9f design point 3): a task
# requeued after losing the build-lock race (see the build gate below) must
# resume into the SAME gate-result state it had before losing the lock, not
# re-run lint/install/test from scratch -- otherwise every lock loss
# redundantly re-runs those steps and net throughput falls instead of rises.
# This marker is written by the build gate immediately before it requeues
# (see below) and is a real per-task file under the task's own persistent
# TASK_DIR, so it survives the systemd unit going inactive and being
# restarted fresh by the next dispatch tick.
RESUME_MARKER="$TASK_DIR/.quality-gate-lock-resume.json"
if [ -f "$RESUME_MARKER" ]; then
  echo "[quality-gate.sh] resuming after a prior build_lock_contended requeue: seeding gate results from $RESUME_MARKER -- already-passed gates below will be skipped, not re-run"
  cp "$RESUME_MARKER" "$RESULTS_FILE"
  rm -f "$RESUME_MARKER"
fi

# True if $RESULTS_FILE already has a passed=true entry for gate $1 (from the
# resume marker above) -- used by run_gate and the install/build steps to
# skip real re-execution of a step that already genuinely passed before this
# task lost the build lock and got requeued.
already_passed() {
  python3 - "$RESULTS_FILE" "$1" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
sys.exit(0 if d.get(sys.argv[2], {}).get("passed") else 1)
PYEOF
}

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
  if already_passed "$name"; then
    echo "[quality-gate.sh] gate '$name' already passed in a prior attempt (build_lock_contended resume) -- skipping re-run"
    return
  fi
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

# Root-caused 2026-08-13 (task-20260813-143152, against 3 blocked RCA-for-
# killed-task documentation tasks -- task-20260813-104656/-105054/-105503,
# all RCA'ing an already-SIGKILLed task and landing a PROGRESS.md/RCA.md-only
# diff): gate detection above is purely repo-type-based (package.json
# present -> run node lint/build/test), never diff-content-based, so a
# doc-only change in this Next.js monorepo still pays the full `next build`
# -- which the BUILD_LOCK_* comments above already document as routinely
# taking 900s+ and timing out under host contention. That timeout then feeds
# worker-entrypoint.sh's auto-fix-then-blocked pipeline a "fix the build"
# instruction for a change that touched zero build-relevant files -- nothing
# for the AI to fix, and credit-accountant.py correctly refused the spend
# every time (confirmed live in all 3 tasks' worker.log), leaving them
# permanently status=blocked instead of ever reaching pending_review.
#
# Considered gating this on the task's own title/type label (e.g. a
# `rca--umr-*-killed` pattern) instead. Rejected: a title is caller-supplied
# free text, not verified content -- trusting it to bypass real gates is
# exactly the kind of unverified-claim shortcut this fix must not introduce,
# and it would only cover this one task-naming convention, not every other
# genuinely docs-only change (e.g. a plain README fix) that hits the same
# bug. Gating on the actual diff is general, verifiable from the commits
# themselves, and does not trust anything the task claims about itself.
#
# DOCS_ONLY is deliberately conservative: it must fail CLOSED (gates run) on
# anything it doesn't explicitly recognize as docs-only, never fail open
# (gates skipped) on anything it doesn't explicitly recognize as
# code-relevant. A false negative here (running gates on a genuinely
# doc-only diff) just costs the same wasted build time this fix targets; a
# false positive (skipping gates on a diff that touched real code) would
# let a real defect reach pending_review unchecked, which is the one
# failure mode this must never risk.
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
DEFAULT_BRANCH="${DEFAULT_BRANCH:-master}"
CHANGED_FILES=$(git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD" 2>/dev/null || true)
DOCS_ONLY=0
# AUDIT:FAIL on this PR's own first head (b315ae9) caught a real
# detection-precision bug: the original check was a code-relevant-extension
# BLOCKLIST (\.(ts|js|py|...)$ etc.), so anything not on that list -- .txt,
# .toml, .yml/.yaml, or any extensionless build filename at first, then a
# fresh audit on the very next head (a63def8e) proved setup.cfg, tox.ini,
# pytest.ini, yarn.lock, Cargo.lock, poetry.lock STILL slipped through --
# was wrongly classified DOCS_ONLY=1 and gates got skipped. A blocklist can
# only ever cover extensions someone remembered to add; it can never close
# against an extension nobody has thought of yet. Inverted here to a small,
# closed docs-only ALLOWLIST instead: prose (*.md, *.rst), anything under a
# docs/ directory, LICENSE (with or without a suffix/extension), and common
# image formats. Anything that does NOT match this allowlist -- including
# every case above, plus any future unrecognized extension or extensionless
# filename -- now fails closed to code-relevant (gates run), the one
# direction this check is allowed to get wrong.
DOCS_ONLY_EXT_PATTERN='\.(md|rst|png|jpe?g|gif|svg|ico|webp|bmp|tiff?|avif)$'
# Root-caused 2026-08-14 (task-20260814-062124, RCA of UMR-20260808-150937-43d0):
# a genuinely docs-only RCA task -- diff was exactly one new registry .md doc
# plus the two governance bookkeeping files AGENTS.md Rule 11 and this repo's
# own CLAUDE.md mandate every such task touch (ai-os/boss/ACTIVE-CLAIMS.yaml,
# the claim-register every session must write to before starting work; and
# ai-os/OS.yaml, the doc-index every new registry doc gets indexed into) --
# still paid the full node lint/build/test gates and failed on build-lock
# contention, because those two files are .yaml and the allowlist above only
# recognized prose extensions and a docs/ directory, not these paths. Verified
# LIVE before allowlisting (not assumed) that neither file is code-relevant:
# `grep -rn "ai-os.*\.yaml" src/` finds zero `import`/`require`/`readFileSync`
# of either file anywhere in the Next.js module graph -- every hit is a prose
# comment citing the file as a reference, not a build-time or type-check-time
# dependency; both are read (if at all) at request-time by API route handlers,
# never at `next build`/`eslint`/test time. Deliberately NOT widened to
# `ai-os/**/*.yaml` generally -- e.g. ai-os/engines/ENGINES.yaml is unverified
# and this allowlist's own stated posture (see comment above) is to fail
# closed on anything not explicitly checked, not to guess by directory.
DOCS_ONLY_NAME_PATTERN='(^|/)(docs/.*|LICENSE([.][A-Za-z0-9]+)?)$|^ai-os/boss/ACTIVE-CLAIMS\.yaml$|^ai-os/OS\.yaml$'
if [ -n "$CHANGED_FILES" ] \
   && ! echo "$CHANGED_FILES" | grep -qvE "${DOCS_ONLY_EXT_PATTERN}|${DOCS_ONLY_NAME_PATTERN}"; then
  DOCS_ONLY=1
fi

if [ "$DOCS_ONLY" -eq 1 ]; then
  echo "[quality-gate.sh] diff vs origin/${DEFAULT_BRANCH} touches no code-relevant files (only: $(echo "$CHANGED_FILES" | tr '\n' ' ')) -- skipping node/python lint/build/test gates as not applicable to this change, same 'gracefully skip gates that don't apply' posture this script already uses for a repo with no package.json at all"
  NAME=docs_only_skip RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
results_file = os.environ["RESULTS_FILE"]
with open(results_file) as f:
    r = json.load(f)
r["docs_only_skip"] = {"ran": False, "passed": True, "exit_code": 0,
                        "output_tail": "diff vs default branch touched no code-relevant files -- lint/build/test gates skipped as not applicable"}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
elif [ -f package.json ]; then
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
  # Heap ceiling configurable (2026-08-01, same precedent as
  # GATE_STEP_TIMEOUT_SECONDS above): default unchanged at 2048MB for every
  # existing caller -- an explicit BUILD_MAX_OLD_SPACE_MB override is opt-in
  # only, for a specific dispatch known to need more headroom (confirmed via
  # a real manual build outside this pipeline needing ~8GB to complete in a
  # reasonable time on a large route-count repo), not a change to the
  # default safety ceiling this incident fix established.
  BUILD_MAX_OLD_SPACE_MB="${BUILD_MAX_OLD_SPACE_MB:-2048}"
  export NODE_OPTIONS="${NODE_OPTIONS:-} --max-old-space-size=${BUILD_MAX_OLD_SPACE_MB}"
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

  if already_passed install; then
    echo "[quality-gate.sh] install already completed in a prior attempt (build_lock_contended resume) -- skipping re-run"
  elif ! [ -d node_modules ]; then
    echo "--- installing deps ---"
    timeout -k 30 "$GATE_STEP_TIMEOUT_SECONDS" $PKG_MGR install 2>&1 | tail -20
    INSTALL_CODE=${PIPESTATUS[0]}
    NAME=install CODE="$INSTALL_CODE" RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
name = os.environ["NAME"]
code = int(os.environ["CODE"])
results_file = os.environ["RESULTS_FILE"]
with open(results_file) as f:
    r = json.load(f)
r[name] = {"ran": True, "passed": code == 0, "exit_code": code}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
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
    # Configurable (2026-08-01, same precedent as GATE_STEP_TIMEOUT_SECONDS/
    # BUILD_MAX_OLD_SPACE_MB above): default unchanged at 700s for every
    # existing caller. Root-caused during the 800-task ai-os/tasks audit --
    # confirmed LIVE that a merge-only task (no code changes, nothing that
    # should ever touch `build` at all) failed with exit_code=1 and a
    # completely EMPTY output_tail, which is flock's own documented behavior
    # on a wait-timeout (the wrapped command never runs at all, so there is
    # no build output to capture) -- not a real build failure. With several
    # tasks dispatched inside the same short window all queuing for this one
    # host-wide build slot, 700s was not enough for every queued task to get
    # its turn. An explicit BUILD_LOCK_WAIT_SECONDS override is opt-in only,
    # for a specific dispatch batch known to need more queueing headroom, not
    # a change to the default this incident's original fix established.
    #
    # UMR-20260806-123316-cf9f (proposal 62, child UMR-20260806-121247-a93a)
    # superseded the ${BUILD_LOCK_WAIT_SECONDS:-700}-then-block-here shape
    # above: live kernel wchan inspection proved all 5 systemd worker slots
    # were routinely serializing on this one lock (4 of 5 genuinely blocked
    # in locks_lock_inode_wai for 582-1376s in one sample, chronic not a
    # blip), i.e. effective concurrency was 1, not the configured ceiling of
    # 5 -- a long in-process wait just made every OTHER queued worker slot
    # sit idle instead of picking up different, unblocked work. Also: this
    # host's systemd --user manager has BUILD_LOCK_WAIT_SECONDS=1700/
    # GATE_STEP_TIMEOUT_SECONDS=1800 set in its own global in-memory
    # environment (confirmed via live /proc/<pid>/environ on
    # worker-entrypoint.sh's PID and its full descendant chain; origin:
    # systemctl --user show-environment on manager PID 1023, no file/unit
    # anywhere sets it, undocumented -- see UMR-20260806-123316-cf9f
    # completion evidence for the full trail). Because bash's ${VAR:-default}
    # only applies when VAR is genuinely unset, a plain default-value edit to
    # BUILD_LOCK_WAIT_SECONDS here would have done nothing real -- the
    # deliberate fix below is a fixed, hardcoded 20s short wait with NO
    # ${...:-...} env-var indirection at all for this specific value, so it
    # can never be silently overridden by that (or any future) global
    # systemd-manager-level environment value. Same reasoning applies to the
    # 700s starvation-guard fallback inside acquire_build_lock_fd below.
    #
    # New shape: try for a short, fixed BUILD_LOCK_SHORT_WAIT_SECONDS. If
    # that fails, do NOT block here at all -- cleanly give the slot back
    # (requeue this task's own umr_tasks row via the canonical
    # superboss-register.py CLI, reason=build_lock_contended, structurally
    # distinct from dispatch-tick.py's resume_interrupted_workers_tick()
    # crash-recovery path -- see requeue-build-lock-contended's own
    # docstring) and exit so the systemd slot is genuinely free for a
    # DIFFERENT queued task immediately, instead of one slot sitting idle in
    # a long flock wait while 4 others do the same. The build command, its
    # exit code, and pass/fail semantics for a build that actually RUNS are
    # completely unchanged; only what happens while WAITING for the lock is
    # different. A per-task consecutive-loss counter (persisted in TASK_DIR
    # so it survives the requeue) falls back to one real long wait (the
    # original 700s) on the 4th consecutive loss, to guarantee eventual
    # forward progress and prevent starvation.
    # Configurable (same precedent as GATE_STEP_TIMEOUT_SECONDS/
    # BUILD_MAX_OLD_SPACE_MB above): default unchanged for every existing
    # caller. Overrides exist so tests/test_build_lock_untracked_task_long_wait.py
    # can exercise the real short-wait/long-wait/timeout code paths against
    # an isolated lock file in bounded real time, instead of the shared
    # production lock path and the real 700s wait.
    BUILD_LOCK_FILE="${BUILD_LOCK_FILE:-/tmp/veridian-quality-gate-build.lock}"
    BUILD_LOCK_SHORT_WAIT_SECONDS="${BUILD_LOCK_SHORT_WAIT_SECONDS:-20}"
    BUILD_LOCK_LONG_WAIT_SECONDS="${BUILD_LOCK_LONG_WAIT_SECONDS:-700}"
    BUILD_LOCK_LOSS_COUNT_FILE="$TASK_DIR/.build-lock-loss-count"

    # Returns 0 with the lock held open on fd 9 (caller runs the build, then
    # must `flock -u 9; exec 9>&-`), 1 if genuinely not acquired within the
    # short wait (caller must requeue, never internally retry), 2 if not
    # acquired even after the long-wait starvation-guard fallback (caller
    # treats this as a real gate failure, same as before this fix existed).
    acquire_build_lock_fd() {
      exec 9>"$BUILD_LOCK_FILE"
      if flock -w "$BUILD_LOCK_SHORT_WAIT_SECONDS" 9; then
        rm -f "$BUILD_LOCK_LOSS_COUNT_FILE"
        return 0
      fi
      local loss_count=0
      [ -f "$BUILD_LOCK_LOSS_COUNT_FILE" ] && loss_count=$(cat "$BUILD_LOCK_LOSS_COUNT_FILE" 2>/dev/null || echo 0)
      loss_count=$((loss_count + 1))
      if [ "$loss_count" -ge 4 ]; then
        echo "[quality-gate.sh] build lock: ${loss_count}th consecutive contention -- falling back to a single ${BUILD_LOCK_LONG_WAIT_SECONDS}s wait (starvation guard) instead of requeuing again"
        if flock -w "$BUILD_LOCK_LONG_WAIT_SECONDS" 9; then
          rm -f "$BUILD_LOCK_LOSS_COUNT_FILE"
          return 0
        fi
        echo "$loss_count" > "$BUILD_LOCK_LOSS_COUNT_FILE"
        exec 9>&-
        return 2
      fi
      echo "$loss_count" > "$BUILD_LOCK_LOSS_COUNT_FILE"
      exec 9>&-
      return 1
    }

    if already_passed build; then
      echo "[quality-gate.sh] gate 'build' already passed in a prior attempt (build_lock_contended resume) -- skipping re-run"
    else
      acquire_build_lock_fd
      BUILD_LOCK_RC=$?
      if [ "$BUILD_LOCK_RC" -eq 0 ]; then
        run_gate build "$PKG_MGR run build"
        flock -u 9
        exec 9>&-
      elif [ "$BUILD_LOCK_RC" -eq 1 ]; then
        cp "$RESULTS_FILE" "$RESUME_MARKER"
        UNIT_NAME="veridian-worker@${TASK_ID}.service"
        REQUEUE_OUT=$(python3 /opt/veridian/scripts/superboss-register.py requeue-build-lock-contended \
            --task-identity "$TASK_ID" --unit-name "$UNIT_NAME" 2>&1)
        REQUEUE_RC=$?
        if [ "$REQUEUE_RC" -eq 0 ]; then
          echo "[quality-gate.sh] build lock contended after ${BUILD_LOCK_SHORT_WAIT_SECONDS}s wait -- task requeued (reason=build_lock_contended), exiting cleanly so this systemd slot frees up for a different task"
          rm -f "$RESULTS_FILE"
          exit "$BUILD_LOCK_CONTENDED_EXIT_CODE"
        elif echo "$REQUEUE_OUT" | grep -q "no active (queued/dispatched/running) umr_tasks row found"; then
          # Root-caused 2026-08-13 (task-20260813-132414, against 3 RCA-for-
          # killed-task resumption tasks -- task-20260813-104656,-105054,
          # -105503 -- all blocked the same way): these tasks were created
          # directly (a task.yaml + systemd unit) and never went through
          # resource_governor.submit()'s umr_tasks INSERT, confirmed LIVE via
          # the superboss_gateway /read endpoint returning zero rows for
          # their task_identity. requeue-build-lock-contended's own
          # find_active_umr_by_identity() check (see its docstring) correctly
          # refuses to requeue a row that does not exist -- that refusal is
          # not a bug. The bug was here: this branch treated ANY requeue
          # failure as unusual/unexpected and recorded a FAKE "build" gate
          # failure for it, which fed the auto-fix-then-blocked pipeline a
          # nonexistent code defect to "fix" (correctly refused downstream by
          # credit-accountant.py, but the task stayed wrongly blocked).
          # There is nothing wrong with this task's code; it simply has no
          # umr_tasks row to hand the slot back to, so instead of requeuing,
          # wait out the lock right here (same fixed long-wait shape as the
          # loss_count>=4 starvation guard above) -- a real wait for a real
          # lock, not a fabricated result. Any OTHER requeue failure (DB
          # unreachable, gateway down, etc.) still falls through to the
          # original fake-gate-failure path below, preserving that
          # defense-in-depth visibility for genuinely unexpected errors.
          echo "[quality-gate.sh] build lock contended, but this task has no active umr_tasks row to requeue (not dispatched via resource_governor.submit()) -- waiting up to ${BUILD_LOCK_LONG_WAIT_SECONDS}s in-process for the lock instead of faking a gate failure"
          rm -f "$RESUME_MARKER"
          exec 9>"$BUILD_LOCK_FILE"
          if flock -w "$BUILD_LOCK_LONG_WAIT_SECONDS" 9; then
            run_gate build "$PKG_MGR run build"
            flock -u 9
            exec 9>&-
          else
            exec 9>&-
            OVERALL=1
            NAME=build CODE=1 RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
name = os.environ["NAME"]
results_file = os.environ["RESULTS_FILE"]
with open(results_file) as f:
    r = json.load(f)
r[name] = {"ran": False, "passed": False, "exit_code": 1,
           "output_tail": "build lock not acquired even after an untracked-task long wait -- real capacity failure, not a code defect"}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
          fi
        else
          echo "[quality-gate.sh] build lock contended but the requeue CLI call itself failed unexpectedly -- NOT silently dropping this: falling through to a normal gate failure instead ($REQUEUE_OUT)"
          rm -f "$RESUME_MARKER"
          OVERALL=1
          NAME=build CODE=1 RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
name = os.environ["NAME"]
results_file = os.environ["RESULTS_FILE"]
with open(results_file) as f:
    r = json.load(f)
r[name] = {"ran": True, "passed": False, "exit_code": 1,
           "output_tail": "build lock contended and the requeue-build-lock-contended CLI call itself failed; see worker.log"}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
        fi
      else
        # BUILD_LOCK_RC == 2: genuine long-wait exhaustion after 3 prior
        # requeues -- a real capacity failure, not contention-churn. Record
        # it exactly like any other failed gate (same shape run_gate itself
        # writes) so it flows into the existing auto-fix-then-blocked
        # pipeline instead of being requeued a 5th time.
        OVERALL=1
        NAME=build CODE=1 RESULTS_FILE="$RESULTS_FILE" python3 <<'PYEOF'
import json, os
name = os.environ["NAME"]
results_file = os.environ["RESULTS_FILE"]
with open(results_file) as f:
    r = json.load(f)
r[name] = {"ran": False, "passed": False, "exit_code": 1,
           "output_tail": f"build lock not acquired even after the {os.environ.get('BUILD_LOCK_LONG_WAIT_SECONDS', '700')}s starvation-guard fallback wait (4th+ consecutive contention) -- real capacity failure, not requeued again"}
with open(results_file, "w") as f:
    json.dump(r, f)
PYEOF
      fi
    fi
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
