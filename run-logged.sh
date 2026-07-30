#!/bin/bash
# Universal execution wrapper for the Superboss Register.
#
# Owner directive 2026-07-20: "in modern software without AI, how do you
# ensure that tasks, actions are properly logged with id and time date."
# Answer, applied here: the middleware/aspect pattern -- instrumentation
# lives in ONE wrapper every job runs through, not scattered across each
# script. Coverage does not depend on a script's own author remembering
# to log it (the exact failure mode fixed twice already today for the
# interactive session and the worker-dispatch fleet) -- it depends on
# nothing, because the wrapper runs regardless of what's inside.
#
# Usage: run-logged.sh "<job_name>" <real command> [args...]
# - Generates a unique execution ID + UTC start timestamp before running.
# - Runs the real command, capturing combined stdout+stderr.
# - Logs BOTH a "started" action and a "completed"/"failed" action
#   (with exit code, duration, output tail) to the register.
# - Echoes the original output and exits with the ORIGINAL exit code --
#   fully transparent to cron/systemd and to any existing `>> log 2>&1`
#   redirect already in place. Does not change what the wrapped job does,
#   only adds logging around it.
# - Fails open: if the register itself is slow/broken, a 10s timeout on
#   each logging call guarantees the wrapped job is never blocked or
#   delayed by a logging failure. Logging is best-effort, never load-bearing
#   for the job it wraps.

set -uo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: run-logged.sh <job_name> <command> [args...]" >&2
    exit 64
fi

JOB_NAME="$1"
shift

EXEC_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])" 2>/dev/null || echo "noexecid")
START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_EPOCH=$(date +%s)

timeout 10 python3 /opt/veridian/scripts/superboss-register.py log-action \
  --source software --medium cron_systemd_wrapper \
  --campaign auto-execution-log \
  --content "job_start:${JOB_NAME} exec_id=${EXEC_ID}" \
  --term "auto_log,execution_wrapper,started,${JOB_NAME}" \
  --result "started_at=${START_TS}" >/dev/null 2>&1 || true

OUTPUT=$("$@" 2>&1)
EXIT_CODE=$?

END_EPOCH=$(date +%s)
DURATION=$((END_EPOCH - START_EPOCH))
STATUS="completed"
if [ "$EXIT_CODE" -ne 0 ]; then
    STATUS="failed"
fi

# Cap the logged output excerpt -- the full output still goes to stdout
# (and any existing `>> logfile` redirect) below, unabridged; this is
# only capping what gets written into the register's result column.
TAIL_EXCERPT=$(printf '%s' "$OUTPUT" | tail -c 1500)

timeout 10 python3 /opt/veridian/scripts/superboss-register.py log-action \
  --source software --medium cron_systemd_wrapper \
  --campaign auto-execution-log \
  --content "job_end:${JOB_NAME} exec_id=${EXEC_ID} status=${STATUS} exit_code=${EXIT_CODE} duration_s=${DURATION}" \
  --term "auto_log,execution_wrapper,${STATUS},${JOB_NAME}" \
  --result "$TAIL_EXCERPT" >/dev/null 2>&1 || true

printf '%s\n' "$OUTPUT"
exit "$EXIT_CODE"
