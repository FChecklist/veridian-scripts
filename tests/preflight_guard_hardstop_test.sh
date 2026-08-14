#!/bin/bash
# Real tests for worker-entrypoint.sh's PREFLIGHT-GUARD-BLOCK +
# LIFETIME-INVOCATION-CHARGE-BLOCK (2026-08-14 RCA fix, UMR-20260814-034225-3392).
#
# Real defect this closes, confirmed live by the PM Sentinel 2026-08-14: the lifetime
# invocation counter (.invocation_count, capped at MAX_LIFETIME_INVOCATIONS=20) used
# to be incremented at the very top of worker-entrypoint.sh, BEFORE the preflight
# guard ran -- so a purely infrastructural preflight rejection (disk_low, memory_low,
# worktree contention, ...) that never called the model still permanently burned a
# lifetime invocation slot, and systemd's Restart=on-failure retried up to
# StartLimitBurst=3 times, each charging another slot for zero work done. A
# host-level full-volume event on 2026-08-14 rejected 11 real tasks on guard reason
# disk_low this way; one (task-20260718-171007-commercial--subscription---pricing-model)
# was already at 18/20 lifetime invocations despite never having executed a single
# real model call.
#
# The fix (see worker-entrypoint.sh's own 2026-08-14 header comments):
#   1. The lifetime invocation counter is now written ONLY in
#      LIFETIME-INVOCATION-CHARGE-BLOCK, immediately after preflight passes and a
#      real model call is imminent -- never on any preflight-rejection path (neither
#      the hard-stop branch nor the transient branch).
#   2. A SEPARATE .infra_rejection_count counter, with its own
#      MAX_INFRA_REJECTIONS cap and its own growing sleep-based backoff, now bounds
#      the transient/infrastructural rejection path so a genuinely broken host still
#      cannot retry this task forever -- without draining the real model-invocation
#      budget to do it.
#
# This test extracts the REAL PREFLIGHT-GUARD-BLOCK and
# LIFETIME-INVOCATION-CHARGE-BLOCK verbatim from the real worker-entrypoint.sh (the
# sed range the marker comments in that file promise -- "tests/preflight_guard_
# hardstop_test.sh extracts this"), wraps them with the same variable setup the real
# script uses immediately before that block, and runs the REAL block text as a real
# bash subprocess with only python3/systemctl/sleep shimmed via PATH (never the
# block's own control flow, arithmetic, or file I/O, which all run for real against a
# real scratch TASK_DIR). A change to the real script is picked up by this test
# automatically -- there is no separate copy of the guard logic to keep in sync.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_SCRIPT="$REPO_ROOT/worker-entrypoint.sh"
REAL_PYTHON3="$(command -v python3)"

PASS_COUNT=0
FAIL_COUNT=0

pass() { echo "PASS: $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$desc (got '$actual')"; else fail "$desc (expected '$expected', got '$actual')"; fi
}
assert_file_absent() {
  local desc="$1" path="$2"
  if [ ! -e "$path" ]; then pass "$desc"; else fail "$desc (file unexpectedly exists: $(cat "$path" 2>/dev/null))"; fi
}
assert_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then pass "$desc"; else fail "$desc (did not find '$needle')"; fi
}

# Extract the real block(s) from the real script -- both markers, contiguous region.
EXTRACTED_BLOCK=$(sed -n '/# --- PREFLIGHT-GUARD-BLOCK-START/,/# --- LIFETIME-INVOCATION-CHARGE-BLOCK-END/p' "$WORKER_SCRIPT")
if [ -z "$EXTRACTED_BLOCK" ]; then
  echo "FATAL: could not extract PREFLIGHT-GUARD-BLOCK / LIFETIME-INVOCATION-CHARGE-BLOCK from $WORKER_SCRIPT -- markers missing/renamed?"
  exit 2
fi

# --- Mock bin dir: python3 (dispatches on script-path argv), systemctl, sleep ------
MOCK_BIN=$(mktemp -d)
cat > "$MOCK_BIN/python3" <<PYEOF
#!/bin/bash
REAL_PYTHON3="$REAL_PYTHON3"
if [ "\$1" = "-c" ]; then
  exec "\$REAL_PYTHON3" "\$@"
fi
case "\$1" in
  */preflight-guard.py)
    echo "{\"reason\": \"\${MOCK_GUARD_REASON:-disk_low}\", \"detail\": \"\${MOCK_GUARD_DETAIL:-mock infra detail}\"}"
    exit "\${MOCK_GUARD_EXIT:-1}"
    ;;
  */veridian-task.py)
    echo "veridian-task.py \$*" >> "\$CALL_LOG"
    exit 0
    ;;
  *)
    exec "\$REAL_PYTHON3" "\$@"
    ;;
esac
PYEOF
chmod +x "$MOCK_BIN/python3"

cat > "$MOCK_BIN/systemctl" <<'SYSEOF'
#!/bin/bash
echo "systemctl $*" >> "$CALL_LOG"
exit 0
SYSEOF
chmod +x "$MOCK_BIN/systemctl"

cat > "$MOCK_BIN/sleep" <<'SLEOF'
#!/bin/bash
echo "sleep $*" >> "$CALL_LOG"
exit 0
SLEOF
chmod +x "$MOCK_BIN/sleep"

# --- Harness: writes the real setup vars the block relies on, then the real block
# text itself, then runs it as a real bash subprocess. ----------------------------
run_block() {
  local task_dir="$1" max_lifetime="$2" max_infra="$3"
  local run_script
  run_script=$(mktemp)
  {
    echo '#!/bin/bash'
    echo 'set -uo pipefail'
    echo "TASK_ID=\"test-task\""
    echo "TASK_DIR=\"$task_dir\""
    echo "WORKSPACE=\"$task_dir/workspace\""
    echo "IS_RESUME=0"
    echo "MAX_LIFETIME_INVOCATIONS=$max_lifetime"
    echo "INVOCATION_COUNT_FILE=\"\$TASK_DIR/.invocation_count\""
    echo 'PRIOR_COUNT=$(cat "$INVOCATION_COUNT_FILE" 2>/dev/null || echo 0)'
    echo "MAX_INFRA_REJECTIONS=$max_infra"
    echo "INFRA_REJECTION_COUNT_FILE=\"\$TASK_DIR/.infra_rejection_count\""
    echo "$EXTRACTED_BLOCK"
  } > "$run_script"
  PATH="$MOCK_BIN:$PATH" CALL_LOG="$task_dir/.call_log" bash "$run_script"
  local rc=$?
  rm -f "$run_script"
  return $rc
}

echo "=== Test 1: a single transient (infra) preflight rejection must NOT charge the lifetime invocation counter ==="
T1=$(mktemp -d)
: > "$T1/.call_log"
MOCK_GUARD_EXIT=1 MOCK_GUARD_REASON=disk_low MOCK_GUARD_DETAIL="disk at 97%" \
  run_block "$T1" 20 5
RC1=$?
assert_eq "transient rejection exits 1 (systemd retries)" "1" "$RC1"
assert_file_absent "lifetime invocation counter file was never created" "$T1/.invocation_count"
assert_eq "infra rejection counter is now 1" "1" "$(cat "$T1/.infra_rejection_count" 2>/dev/null)"
CALL_LOG_1=$(cat "$T1/.call_log")
assert_contains "checkpoint note says lifetime counter NOT charged" "$CALL_LOG_1" "lifetime invocation counter NOT charged (still 0/20)"
assert_contains "checkpoint status is failed, not blocked" "$CALL_LOG_1" "--status failed"
assert_contains "backoff sleep was invoked (5s for 1st infra rejection)" "$CALL_LOG_1" "sleep 5"
rm -rf "$T1"

echo
echo "=== Test 2: repeated infra rejections increment their OWN counter, back off, and eventually stop retrying -- all while the lifetime counter stays untouched ==="
T2=$(mktemp -d)
: > "$T2/.call_log"
MAX_INFRA=3
for i in 1 2 3; do
  MOCK_GUARD_EXIT=1 MOCK_GUARD_REASON=disk_low MOCK_GUARD_DETAIL="disk at 97%" \
    run_block "$T2" 20 "$MAX_INFRA"
  RC=$?
  assert_eq "attempt $i: still under cap, exits 1 (retryable)" "1" "$RC"
  assert_eq "attempt $i: infra counter is $i" "$i" "$(cat "$T2/.infra_rejection_count" 2>/dev/null)"
  assert_file_absent "attempt $i: lifetime counter still never created" "$T2/.invocation_count"
done
# One more rejection pushes infra count to 4, past MAX_INFRA_REJECTIONS=3 -- must now
# hard-stop (exit 0, disable the unit) instead of letting systemd retry again.
MOCK_GUARD_EXIT=1 MOCK_GUARD_REASON=disk_low MOCK_GUARD_DETAIL="disk at 97%" \
  run_block "$T2" 20 "$MAX_INFRA"
RC2=$?
assert_eq "infra cap exceeded (4 > 3): exits 0, systemd will NOT retry" "0" "$RC2"
assert_eq "infra counter is now 4 (past its own cap)" "4" "$(cat "$T2/.infra_rejection_count" 2>/dev/null)"
assert_file_absent "lifetime counter STILL never created after infra cap hit" "$T2/.invocation_count"
CALL_LOG_2=$(cat "$T2/.call_log")
assert_contains "unit was disabled once infra cap hit (stops retrying)" "$CALL_LOG_2" "systemctl --user disable veridian-worker@test-task.service"
assert_contains "checkpoint records INFRASTRUCTURE-REJECTION CAP HIT" "$CALL_LOG_2" "INFRASTRUCTURE-REJECTION CAP HIT"
assert_contains "backoff grew across attempts (5s, 10s, 15s)" "$CALL_LOG_2" "sleep 5"
assert_contains "backoff grew across attempts (5s, 10s, 15s)" "$CALL_LOG_2" "sleep 10"
assert_contains "backoff grew across attempts (5s, 10s, 15s)" "$CALL_LOG_2" "sleep 15"
rm -rf "$T2"

echo
echo "=== Test 3: a hard-stop preflight reason (deterministic, e.g. circuit_breaker_tripped) also never charges the lifetime counter ==="
T3=$(mktemp -d)
: > "$T3/.call_log"
MOCK_GUARD_EXIT=1 MOCK_GUARD_REASON=circuit_breaker_tripped MOCK_GUARD_DETAIL="2 consecutive identical failures" \
  run_block "$T3" 20 5
RC3=$?
assert_eq "hard stop exits 0 (systemd will not retry, unit disabled)" "0" "$RC3"
assert_file_absent "hard stop never charges the lifetime invocation counter" "$T3/.invocation_count"
assert_file_absent "hard stop never touches the infra-rejection counter either" "$T3/.infra_rejection_count"
CALL_LOG_3=$(cat "$T3/.call_log")
assert_contains "checkpoint records PRE-FLIGHT HARD STOP" "$CALL_LOG_3" "PRE-FLIGHT HARD STOP (circuit_breaker_tripped)"
assert_contains "unit was disabled" "$CALL_LOG_3" "systemctl --user disable veridian-worker@test-task.service"
rm -rf "$T3"

echo
echo "=== Test 4: once preflight PASSES, the lifetime invocation counter IS charged (a real model call is imminent) ==="
T4=$(mktemp -d)
: > "$T4/.call_log"
MOCK_GUARD_EXIT=0 \
  run_block "$T4" 20 5
RC4=$?
assert_eq "preflight pass falls through the block with exit 0 (script continues below)" "0" "$RC4"
assert_eq "lifetime invocation counter is now 1 (charged exactly once preflight passed)" "1" "$(cat "$T4/.invocation_count" 2>/dev/null)"
assert_file_absent "infra rejection counter untouched on a clean pass" "$T4/.infra_rejection_count"
rm -rf "$T4"

echo
echo "=== Test 5: MAX_LIFETIME_INVOCATIONS cap check (unrelated to this fix, must still work) is unaffected -- PRIOR_COUNT>=MAX still hard-stops without incrementing further ==="
# This exercises the code just ABOVE the extracted block (not part of the marker
# range, since it's identical in shape to before this fix) -- included here as an
# end-to-end sanity check that the cap-check refactor (NEW_COUNT>MAX -> PRIOR_COUNT>=MAX)
# is behaviorally equivalent, run against the real script text directly.
CAP_CHECK_BLOCK=$(sed -n '/^MAX_LIFETIME_INVOCATIONS=/,/^fi$/p' "$WORKER_SCRIPT" | sed -n '1,/^fi$/p')
T5=$(mktemp -d)
echo 20 > "$T5/.invocation_count"
: > "$T5/.call_log"
run_script5=$(mktemp)
{
  echo '#!/bin/bash'
  echo 'set -uo pipefail'
  echo "TASK_ID=\"test-task\""
  echo "TASK_DIR=\"$T5\""
  echo "$CAP_CHECK_BLOCK"
  echo 'echo "REACHED_AFTER_CAP_CHECK"'
} > "$run_script5"
OUT5=$(PATH="$MOCK_BIN:$PATH" CALL_LOG="$T5/.call_log" bash "$run_script5")
RC5=$?
rm -f "$run_script5"
assert_eq "at 20/20 prior invocations, cap check exits 0 before doing anything else" "0" "$RC5"
if grep -qF "REACHED_AFTER_CAP_CHECK" <<<"$OUT5"; then fail "cap check should have exited before reaching further code"; else pass "cap check correctly stopped script execution (exit, not fall-through)"; fi
assert_eq "invocation count file is untouched at exactly 20 (no further charge)" "20" "$(cat "$T5/.invocation_count")"
rm -rf "$T5"

rm -rf "$MOCK_BIN"

echo
echo "================================================================"
echo "RESULTS: $PASS_COUNT passed, $FAIL_COUNT failed"
echo "================================================================"
if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
