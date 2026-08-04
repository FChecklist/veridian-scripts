#!/bin/bash
# Entrypoint for a systemd-managed VERIDIAN AI worker. Runs Claude Code headlessly
# against an isolated git worktree, checkpoints periodically, resumes from the
# last checkpoint on restart (server reboot / crash / interruption), pushes the
# branch on success (never merges/deploys), and marks failed with no infinite
# retry loop (systemd StartLimitBurst caps FAST restarts; this script's own
# lifetime-invocation counter caps SLOW-drip retries across many hours/days,
# see 2026-07-19 update below).
#
# v2 (2026-07-20, Owner "zero credit wastage" directive) adds, on top of the
# above: a pre-flight guard (static checks + canary + circuit breaker) run
# BEFORE the main invocation so failure is caught before it costs anything,
# and a compact AI-to-AI directive prompt format that stops re-sending the
# full original task prompt on every resume (previously the single biggest
# source of redundant tokens -- confirmed directly from this exact script's
# prior behavior). Full rationale: /opt/veridian/repos/compliance-tracker/ai-os/COST-CONTROL.md
#
# v3 (2026-07-20, RCA fix for the 47-failed-unit / 71.9%-task-failure-rate
# incident): 2 real bugs found and fixed, see inline comments at each site --
# (1) preflight now checks the REAL, live OpenRouter balance (not just this
# proxy's own internal spend tracker) and is added to the hard-stop list;
# (2) `claude -p --output-format json` returns exit 0 even on a real
# API-level error (e.g. a 402), which silently skipped failure-signature
# recording and let the circuit breaker never see the failure -- now
# explicitly parsed and treated as a real failure.
set -uo pipefail
TASK_ID="$1"
TASK_DIR="/opt/veridian/ai-os/tasks/$TASK_ID"
export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"
START_TS=$(date +%s)

# 2026-07-23: switched from GLM/OpenRouter proxy to real Claude Max subscription auth, Owner directive.
# Stale CLAUDE_CODE_OAUTH_TOKEN in shared/.env unset here so the stored ~/.claude credentials session (real Max token) is used instead.
unset CLAUDE_CODE_OAUTH_TOKEN
unset ANTHROPIC_API_KEY
unset ANTHROPIC_BASE_URL
PROXY_URL="http://127.0.0.1:8787"  # proxy left running for its own balance-check endpoint only, no longer in the auth path

WORKER_BUDGET_CAP_USD="${VERIDIAN_WORKER_BUDGET_CAP_USD:-10}"

MAX_LIFETIME_INVOCATIONS="${VERIDIAN_MAX_LIFETIME_INVOCATIONS:-20}"
INVOCATION_COUNT_FILE="$TASK_DIR/.invocation_count"
PRIOR_COUNT=$(cat "$INVOCATION_COUNT_FILE" 2>/dev/null || echo 0)
NEW_COUNT=$((PRIOR_COUNT + 1))
echo "$NEW_COUNT" > "$INVOCATION_COUNT_FILE"
if [ "$NEW_COUNT" -gt "$MAX_LIFETIME_INVOCATIONS" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: this task has been started/restarted $NEW_COUNT times (lifetime max $MAX_LIFETIME_INVOCATIONS) -- stopping to prevent an unbounded slow-drip retry loop across restarts, the same shape as the 2026-07-18 incident. Needs human review, not an automatic retry."
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi

WORKSPACE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['workspace'])")
BRANCH=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['branch'])")
CHECKPOINT_COUNT=$(python3 -c "import yaml; print(len(yaml.safe_load(open('$TASK_DIR/task.yaml')).get('checkpoints', [])))")
DEFAULT_BRANCH=$(git -C "$WORKSPACE" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
DEFAULT_BRANCH="${DEFAULT_BRANCH:-master}"

if [ "$CHECKPOINT_COUNT" -gt 0 ]; then
  IS_RESUME=1
else
  IS_RESUME=0
fi

# --- Pre-flight guard (2026-07-20): static checks + canary + circuit breaker,
# all before the main (potentially tens-of-thousands-of-tokens) invocation.
# A rejection here costs $0-0.0002 (canary only) instead of a full invocation.
# --- PREFLIGHT-GUARD-BLOCK-START (tests/preflight_guard_hardstop_test.sh extracts this)
GUARD_OUT=$(python3 /opt/veridian/scripts/preflight-guard.py "$TASK_DIR" "$WORKSPACE" --no-proxy 2>&1)  # 2026-07-23: GLM proxy decommissioned, Owner directive -- real subscription auth now used, same as doc-worker-entrypoint.sh
GUARD_EXIT=$?
if [ "$GUARD_EXIT" -ne 0 ]; then
  GUARD_REASON=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','unknown'))" 2>/dev/null || echo "unknown")
  GUARD_DETAIL=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null || echo "$GUARD_OUT")
  if [ "$GUARD_REASON" = "circuit_breaker_tripped" ] || [ "$GUARD_REASON" = "budget_exhausted" ] || [ "$GUARD_REASON" = "openrouter_balance_exhausted" ] || [ "$GUARD_REASON" = "credit_accountant_rejected" ] || [ "$GUARD_REASON" = "tight_task_schema_violation" ] || [ "$GUARD_REASON" = "crontab_unauthorized_change" ]; then
    # Hard stops -- retrying will not help, do not let systemd restart this.
    # openrouter_balance_exhausted added 2026-07-20 (RCA fix): confirmed root
    # cause of a 47-failed-unit incident was a real, live OpenRouter 402 that
    # this preflight check now catches BEFORE the wasted call, but which
    # (like circuit_breaker_tripped/budget_exhausted) must be a hard stop,
    # not a retryable transient -- retrying an empty account produces the
    # identical failure every time until a human adds credits.
    # credit_accountant_rejected added 2026-07-20 (round-2 audit fix, same
    # day): the credit-accountant.py gate's own deterministic rejections
    # (balance/existing-capability/sequencing) share the identical property
    # -- blind retry produces the identical rejection until a human
    # intervenes. Confirmed live: 163 tasks were stuck in a restart-storm
    # before this fix because this reason fell through to the transient
    # branch below instead.
    # tight_task_schema_violation added 2026-07-23 (RCA fix for
    # task-20260723-162833-gap-closing-phase11-item29-auth-verifica's
    # watchdog-flagged stall/loop, signature "PRE-FLIGHT REJECTED
    # (tight_task_schema_violation, transient)"): this is a static,
    # content-based check of prompt.txt (tight_task_validation.py) -- its
    # verdict is a pure function of prompt.txt's own text, so on an unchanged
    # prompt.txt a retry reproduces the IDENTICAL rejection every time, same
    # property as the other reasons in this list. It was routed to the
    # "transient" branch below instead, which is worse than a plain wasted
    # retry: pre-flight rejections never call record_failure_signature()
    # (that only runs after a real claude -p invocation further below), so
    # the circuit breaker in preflight-guard.py never even saw these
    # failures to trip on -- the task could burn all
    # MAX_LIFETIME_INVOCATIONS retries on a rejection no retry could ever
    # fix, only a human editing prompt.txt (or the validator, if it's a
    # false positive) can resolve this.
    # crontab_unauthorized_change added 2026-07-27 (RCA fix for
    # task-20260726-083946-fix-task-lifecycle--real-branch-resoluti's
    # watchdog-flagged stall, signature "PRE-FLIGHT REJECTED
    # (crontab_unauthorized_change, transient)"): check_crontab_unauthorized_change()
    # in preflight-guard.py is a static comparison of the live crontab against
    # CRONTAB_APPROVED_SNAPSHOT.txt plus a citation check against this task's own
    # unchanged prompt.txt -- exactly the same "pure function of state, retry
    # reproduces the identical rejection" property as tight_task_schema_violation
    # above, and it shares that reason's exact bug: falling through to the
    # transient branch below means it never calls record_failure_signature()
    # either (that only runs after a real claude -p invocation further below),
    # so it burned 9 identical restarts up to MAX_LIFETIME_INVOCATIONS on this
    # task before superseding it, with no automatic recovery possible since only
    # a human approving the crontab change (or citing it in prompt.txt) can ever
    # change the outcome.
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PRE-FLIGHT HARD STOP ($GUARD_REASON): $GUARD_DETAIL"
    systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  else
    # Transient (disk/mem/proxy/worktree) -- let systemd's normal
    # Restart=on-failure retry after RestartSec, still counted against the
    # lifetime invocation cap above.
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "PRE-FLIGHT REJECTED ($GUARD_REASON, transient): $GUARD_DETAIL -- no model call made, no cost incurred"
    exit 1
  fi
fi
# --- PREFLIGHT-GUARD-BLOCK-END

python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status in_progress --note "worker started (resume=$IS_RESUME, lifetime invocation $NEW_COUNT/$MAX_LIFETIME_INVOCATIONS, pre-flight passed)"

# Background checkpoint loop: snapshots git state + PROGRESS.md every 5 minutes
# regardless of whether the AI itself remembers to checkpoint.
#
# 2026-07-27 RCA fix (task-20260727-044531, watchdog signature "periodic
# checkpoint" against task-20260727-034439): this loop's own checkpoint call
# is a plain child process of THIS service's cgroup, so it inherits that
# unit's MemoryHigh=2G/MemoryMax=3G (added by the 2026-07-26 RCA fix above
# this script's header, task-20260726-175957) exactly like the heavy
# `bun run build`/`next build` process it runs alongside. Confirmed live via
# `ps -o stat,wchan -p <checkpoint-pid>`: state D, wchan
# mem_cgroup_handle_over_high -- once the unit's real memory usage (build,
# not this 4MB script) crosses MemoryHigh, the kernel throttles EVERY
# process in that cgroup trying to force reclaim, including this one. That
# silently reintroduces the exact stall this loop exists to prevent (the
# 2026-07-26 RCA fix immediately above, task-20260726-175009, explicitly
# kept this loop alive through the whole quality-gate phase FOR this
# scenario -- a long memory-heavy build -- so it is self-defeating for the
# heartbeat to be throttled by the very memory pressure it must survive).
# Fix: run the actual checkpoint call in its own transient scope, in a
# separate slice with no memory limit, via systemd-run --user --scope --
# this is a SIBLING unit, not nested under this service's cgroup, so it is
# never subject to this unit's MemoryHigh/MemoryMax/MemorySwapMax
# regardless of how constrained the build is. Falls back to a direct call
# if systemd-run itself is unavailable/fails (e.g. a non-systemd host) --
# same fail-open choice as elsewhere in this file for the heartbeat's own
# infrastructure, since this is a liveness signal, not a spend gate.
(
  while true; do
    sleep 300
    systemd-run --user --scope --quiet --collect \
      --slice=veridian-checkpoint-heartbeat.slice \
      --property=MemoryHigh=infinity --property=MemoryMax=infinity --property=MemorySwapMax=infinity \
      -- python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --auto --note "periodic checkpoint" \
      || python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --auto --note "periodic checkpoint (systemd-run escape unavailable, ran in-cgroup)"
  done
) &
CHECKPOINT_PID=$!
trap 'kill $CHECKPOINT_PID 2>/dev/null' EXIT

cd "$WORKSPACE"

PROGRESS_INSTRUCTION="PROTOCOL: maintain PROGRESS.md (## Completed / ## Remaining, markdown checkboxes), update after each step. commit+push after each meaningful unit, not only at the end. on a 2nd consecutive failure of the identical approach: STOP, do not attempt a 3rd time -- this is enforced by a circuit breaker on the next invocation regardless, so stopping yourself first saves a wasted restart."

if [ "$IS_RESUME" -eq 1 ]; then
  RESUME_CONTEXT=$(python3 /opt/veridian/scripts/veridian-task.py resume-context "$TASK_ID")
  PROMPT="RESUME task=$TASK_ID invocation=$NEW_COUNT/$MAX_LIFETIME_INVOCATIONS
DO_NOT restart from scratch. run: git status && git log --oneline -10 && read PROGRESS.md.
LAST_CHECKPOINT:
$RESUME_CONTEXT
SPEC: full task spec is prompt.txt in cwd (provided once already, not restated here -- read it only if you need it).
$PROGRESS_INSTRUCTION"
else
  PROMPT="SPEC: $(cat "$TASK_DIR/prompt.txt")

$PROGRESS_INSTRUCTION"
fi

MAIN_OUT="$TASK_DIR/.claude-out-main.json"
MAIN_START_EPOCH=$(date -u +%s)
claude -p "$PROMPT" --model sonnet --effort high --dangerously-skip-permissions --max-budget-usd "$WORKER_BUDGET_CAP_USD" --output-format json > "$MAIN_OUT" 2>>"$TASK_DIR/worker.log"
EXIT_CODE=$?
cat "$MAIN_OUT" >> "$TASK_DIR/result.json"

# --- AI response logging (2026-07-23, governance item 15: ai_response_logging) ---
# Real subscription-based `claude -p` calls had no record of their own response
# text anywhere (anthropic_openrouter_proxy_v2.py's glm-response-cache.sqlite
# only ever covered GLM-proxied traffic, a dead path since the GLM decommission
# earlier today). Captures this invocation's --output-format json "result" field
# (truncated to 2000 chars) via superboss-register.py log-action directly --
# task-gateway.py's own `log` subcommand hardcodes utm_source=ai_agent, but this
# needs a distinct utm_source=ai_response so these rows are queryable separately
# from generic agent actions. Best-effort: a logging failure must never fail the
# worker itself.
AI_RESPONSE_TEXT=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    print((d.get('result') or '')[:2000])
except Exception:
    pass
")
if [ -n "$AI_RESPONSE_TEXT" ]; then
  python3 /opt/veridian/scripts/superboss-register.py log-action \
    --source ai_response --medium claude_code_cli --campaign worker-entrypoint-main-invocation \
    --content "$AI_RESPONSE_TEXT" --term "$TASK_ID" --result "exit_code=$EXIT_CODE" \
    >> "$TASK_DIR/worker.log" 2>&1 || true
fi

# --- API-level error detection (2026-07-20 RCA fix) ---
# Confirmed root cause of the 47-failed-unit incident: `claude -p
# --output-format json` returns exit code 0 even when the underlying API
# call itself failed (e.g. a real OpenRouter 402) -- the error is captured
# INSIDE the JSON payload ("is_error":true), never surfaced as a non-zero
# process exit. This silently skipped the EXIT_CODE!=0 branch below
# entirely -- no failure signature was ever recorded for this task, so the
# circuit breaker never had a chance to trip on the 2nd identical failure,
# and every failed attempt fell through toward the "no changes to commit"
# path instead, while systemd's OWN restart policy still cycled the unit.
# Explicitly parse the JSON result for is_error now, and treat it exactly
# like a non-zero EXIT_CODE -- this is the fix that makes the circuit
# breaker and failure-signature recording actually see this failure class.
API_IS_ERROR=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    print('1' if d.get('is_error') else '0')
except Exception:
    print('0')
")
if [ "$API_IS_ERROR" = "1" ] && [ "$EXIT_CODE" -eq 0 ]; then
  EXIT_CODE=1
  echo "API-level error detected in result JSON (is_error=true) despite exit code 0 -- treating as failure. See $MAIN_OUT for the real API error." >> "$TASK_DIR/worker.log"
fi

# --- CLI's own max-budget-usd hard stop (2026-07-20 RCA fix, 2nd distinct
# root cause of the same incident) ---
# A genuinely large/looping task can hit `claude -p`'s own --max-budget-usd
# ceiling ("subtype":"error_max_budget_usd", "terminal_reason":
# "budget_exhausted" in $MAIN_OUT) -- this is a DIFFERENT failure class from
# a plain API error: retrying will almost certainly just spend ANOTHER
# $WORKER_BUDGET_CAP_USD hitting the identical wall again, real avoidable
# waste, exactly what this whole guard system exists to prevent. Before this
# fix the generic EXIT_CODE!=0 branch below treated this the same as any
# other retryable failure, so it ALSO retry-stormed into a permanent
# systemd 'failed' state (confirmed: 2 of the 47 affected units in the
# 2026-07-20 incident were this exact pattern, not the OpenRouter-balance
# one -- found by checking real result.json content per unit before
# assuming one root cause explained all 47). This is a hard stop, same
# treatment as the pre-flight guard's own budget_exhausted/
# openrouter_balance_exhausted reasons: checkpoint blocked, disable the
# unit, exit 0 -- no retry.
CLI_HIT_BUDGET_CAP=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    print('1' if d.get('subtype') == 'error_max_budget_usd' or d.get('terminal_reason') == 'budget_exhausted' else '0')
except Exception:
    print('0')
")
if [ "$CLI_HIT_BUDGET_CAP" = "1" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "CLI HARD STOP (max_budget_usd): this invocation's own self-reported cost hit the \$$WORKER_BUDGET_CAP_USD per-task cap ($MAIN_OUT). Stopping rather than retrying -- a retry will very likely spend another \$$WORKER_BUDGET_CAP_USD hitting the identical wall. Needs human review: either the task is too large for one invocation (split it) or it is genuinely stuck/looping."
  git -C "$WORKSPACE" add -A
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (CLI hit its own max-budget-usd cap)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  kill "$CHECKPOINT_PID" 2>/dev/null || true
  wait "$CHECKPOINT_PID" 2>/dev/null || true
  exit 0
fi

# NOTE: the periodic-checkpoint heartbeat (started above, CHECKPOINT_PID) is
# deliberately NOT killed here anymore. It used to be killed at this exact
# point -- right after the main claude invocation returns but BEFORE the
# quality-gate + auto-fix loop below, which has no time bound (bun
# install/lint/build/test, plus up to 2 more `claude -p --continue` auto-fix
# invocations). With the heartbeat dead across that whole phase, ANY task
# whose gate+auto-fix phase runs longer than veridian-task-watchdog.py's
# STALL_MINUTES (20) got misdiagnosed as stalled off a frozen last checkpoint
# -- confirmed root cause of task-20260726-171926's watchdog escalation
# (RCA task-20260726-175009): invocation 1's quality-gate phase ran long
# under real memory contention from concurrent workers (invocation 2's own
# preflight-guard.py memory_low rejection, "resource contention from
# concurrent workers (Group B failure pattern)", confirms the contention was
# real, not a guess), the watchdog saw the same frozen "periodic checkpoint"
# note for >20 minutes and escalated a brand-new billed RCA task even though
# systemd's Restart=on-failure + preflight-guard's memory_low transient
# handling were already self-healing it (invocation 3 resumed cleanly).
# The trap set at CHECKPOINT_PID's definition ('trap ... EXIT') still reaps
# this background loop whenever the script actually exits, from any of the
# exit points below -- removing the early kill/wait here just lets it keep
# ticking (and keep last_checkpoint_at fresh) through the rest of this
# script's real, possibly-long-running work instead of stopping short.

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
python3 /opt/veridian/scripts/veridian-task.py record-usage "$TASK_ID" --elapsed "$ELAPSED"

real_invocation_cost_usd() {
  python3 -c "
import json
from datetime import datetime
start_epoch = float('$1')
total = 0.0
try:
    with open('/opt/veridian/ai-os/logs/glm-proxy-calls.jsonl') as f:
        for line in f:
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec['ts']).timestamp()
                if ts >= start_epoch and rec.get('real_cost_usd') is not None:
                    total += rec['real_cost_usd']
            except Exception:
                continue
except FileNotFoundError:
    pass
print(total)
"
}

budget_exceeded() {
  python3 -c "print(1 if float('$1' or 0) >= float('$WORKER_BUDGET_CAP_USD') * 0.95 else 0)"
}

# --- Failure-signature recording (2026-07-20) ---
# Feeds the circuit breaker in preflight-guard.py on the NEXT invocation.
# Signature = a stable fingerprint of the failure: last 400 chars of
# worker.log PLUS the result.json's own error text when present (2026-07-20
# RCA fix -- an API-level error like the 402 above produces ZERO worker.log
# output, so hashing worker.log alone always produced the same signature
# regardless of the REAL error). Two consecutive identical signatures trips
# the breaker before a 3rd attempt is ever made.
record_failure_signature() {
  python3 -c "
import hashlib, json, os
sig_file = '$TASK_DIR/.failure_signatures.json'
try:
    with open('$TASK_DIR/worker.log') as f:
        tail = f.read()[-400:]
except FileNotFoundError:
    tail = 'no-worker-log'
try:
    with open('$MAIN_OUT') as f:
        result = json.load(f)
    api_err = result.get('result', '') if result.get('is_error') else ''
except Exception:
    api_err = ''
normalized = ' '.join((tail + ' ' + api_err[:200]).split())
sig = hashlib.sha256(normalized.encode()).hexdigest()[:24]
sigs = []
if os.path.exists(sig_file):
    try:
        sigs = json.load(open(sig_file))
    except Exception:
        sigs = []
sigs.append(sig)
sigs = sigs[-10:]
json.dump(sigs, open(sig_file, 'w'))
"
}

if [ "$EXIT_CODE" -ne 0 ]; then
  record_failure_signature
  FAIL_COST=$(real_invocation_cost_usd "$MAIN_START_EPOCH")
  python3 /opt/veridian/scripts/credit-accountant.py report --task-id "$TASK_ID" --increment 1 --actual-spend-usd "$FAIL_COST" --outcome "main invocation FAILED, exit code $EXIT_CODE, real cost \$$FAIL_COST -- see worker.log" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" add -A
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: checkpoint commit (invocation failed, exit $EXIT_CODE)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "worker exited with code $EXIT_CODE; failure signature recorded for circuit breaker; pushed whatever progress existed; systemd will retry up to the burst limit"
  exit 1
fi

MAIN_COST=$(real_invocation_cost_usd "$MAIN_START_EPOCH")
python3 /opt/veridian/scripts/credit-accountant.py report --task-id "$TASK_ID" --increment 1 --actual-spend-usd "$MAIN_COST" --outcome "main invocation completed, exit 0, real cost \$$MAIN_COST" >> "$TASK_DIR/worker.log" 2>&1 || true
if [ "$(budget_exceeded "$MAIN_COST")" = "1" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: this invocation's REAL OpenRouter/GLM-5.2 cost was \$$MAIN_COST, at/above the \$$WORKER_BUDGET_CAP_USD budget cap -- stopped rather than continuing unbounded. Needs human review before further retries (likely a stuck/looping task, not ordinary progress)."
  git -C "$WORKSPACE" add -A
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (budget cap hit)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi

# Root-caused 2026-07-24 (task-20260724-041754 gap-close, against
# task-20260724-033446's real checkpoint history): a clean working tree here
# does NOT mean nothing happened -- the agent may have already committed its
# own real changes during the main invocation (exactly what task-20260724-033446
# did: 11 real files, self-committed, tree clean by the time this check ran).
# This used to short-circuit straight to --status completed, skipping quality
# gates, pending_review, and the supervisor entirely. veridian-task.py's
# checkpoint command now also hard-rejects any direct completed transition
# without a prior pending_review checkpoint (defense in depth), but the real
# fix is here: only skip the review pipeline when the branch has zero commits
# ahead of the default branch (a genuine no-op) -- otherwise fall through to
# the same quality-gate + pending_review + supervisor path every other real
# change takes.
#
# 2026-07-24 (task-20260724-074329 gap-close, against task-20260724-041754's
# own review.json rejection of PR #11): the AHEAD_COUNT==0 branch below used
# to call `checkpoint --status completed` directly. That is now rejected by
# veridian-task.py's cmd_checkpoint guard above (it hard-requires a prior
# 'pending_review' checkpoint before allowing 'completed'), and this script
# never checked that command's exit code -- so a genuine first-run no-op
# silently failed to checkpoint at all, leaving the task stuck at in_progress
# with its systemd service disabled and no automatic recovery. Exactly the
# stuck-task bug class this whole area exists to close, reintroduced for a
# different trigger.
#
# Considered routing a genuine no-op through the full quality-gate +
# pending_review + supervisor path unconditionally (i.e. deleting this
# whole branch and always falling through). Rejected: a genuine no-op has
# zero commits ahead of $DEFAULT_BRANCH, so supervisor-entrypoint.sh's own
# `gh pr create` has nothing to open a PR for -- read that script and
# confirmed it does not special-case an empty diff. It still always reaches
# a terminal checkpoint though (blocked, if the AI reviewer -- correctly --
# rejects reviewing an empty diff, or blocked via its own failed-merge
# fallback if it doesn't), so sending a no-op there does not reintroduce a
# silent-stuck-task bug, it is just wasted review spend for known-zero
# benefit. A brand-new terminal status (e.g. completed_no_changes) was also
# considered and rejected: "terminal" is hardcoded in several other places
# this fix must not touch (sync-controller-back.py's TERMINAL/STATUS_MAP,
# queue-dispatcher.py's TERMINAL_GOOD, health-check-15min.py) -- inventing a
# status those don't recognize would make a no-op task look permanently
# non-terminal to them, the same stuck-task bug class again, just moved.
#
# Fix: checkpoint pending_review first (satisfies the state-machine
# invariant, same status the non-no-op path already uses) with a note that
# makes the no-op nature explicit for whoever/whatever reads it next, then
# still start the supervisor -- cheap defense in depth, and it is the
# component with the actual authority to decide a status this task never
# self-reports directly.
# --- NOOP-COMPLETION-BLOCK-START (see tests/worker_noop_pending_review_test.sh) ---
if git -C "$WORKSPACE" diff --quiet && git -C "$WORKSPACE" diff --cached --quiet && [ -z "$(git -C "$WORKSPACE" status --porcelain)" ]; then
  AHEAD_COUNT=$(git -C "$WORKSPACE" rev-list --count "origin/${DEFAULT_BRANCH}..HEAD" 2>/dev/null || echo 0)
  if [ "$AHEAD_COUNT" -eq 0 ]; then
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status pending_review --note "worker finished, no changes to commit and zero commits ahead of $DEFAULT_BRANCH -- genuine no-op, routing through pending_review (not completed directly) so the state-machine invariant holds and the supervisor gets the final say"
    systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    systemctl --user start "veridian-supervisor@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  fi
  echo "clean working tree but $AHEAD_COUNT commit(s) ahead of $DEFAULT_BRANCH (worker self-committed) -- routing through quality gates + pending_review instead of a direct completed shortcut" >> "$TASK_DIR/worker.log"
fi
# --- NOOP-COMPLETION-BLOCK-END ---

# Quality gates: up to 2 auto-fix attempts (same conversation via --continue)
# before giving up and marking blocked for human review.
GATE_ATTEMPT=0
GATE_PASSED=0
while [ "$GATE_ATTEMPT" -lt 3 ]; do
  echo "=== quality gate attempt $GATE_ATTEMPT ===" >> "$TASK_DIR/worker.log"
  if bash /opt/veridian/scripts/quality-gate.sh "$WORKSPACE" "$TASK_DIR/quality-gate-$GATE_ATTEMPT.json" >> "$TASK_DIR/worker.log" 2>&1; then
    GATE_PASSED=1
    break
  fi
  GATE_ATTEMPT=$((GATE_ATTEMPT + 1))
  if [ "$GATE_ATTEMPT" -ge 3 ]; then
    break
  fi
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status in_progress --note "quality gate failed, attempting auto-fix ($GATE_ATTEMPT/2)"
  FIX_PROMPT="GATE_FAIL attempt=$GATE_ATTEMPT/2. Fix the underlying issue, do not silence the checker. output:
$(cat "$TASK_DIR/quality-gate-$((GATE_ATTEMPT-1)).json" | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(f"--{k}--\n{v.get(\"output_tail\",\"\")}") for k,v in d.items() if not v.get("passed", True)]' 2>/dev/null)

$PROGRESS_INSTRUCTION"
  # 2026-08-02: was a hardcoded literal ("quality gate auto-fix retry") identical
  # for every task fleet-wide, so check_existing_capability()'s system_index
  # lookup always matched ~60 unrelated generic entries (preflight-guard.py,
  # quality-gate.sh, risk-tier.py, ...) regardless of what actually failed --
  # false-positive-rejecting every single auto-fix attempt fleet-wide (root
  # cause of the Phase 2 / 8-clean-PR-merge blocks; a real, plausible major
  # contributor to the 484-blocked bucket under the 800-task audit,
  # UMR-20260801-153900-9100). credit-accountant.py's own check_existing_capability()
  # docstring requires curated, specific terms for exactly this reason. Surface
  # the real failing gate name(s) instead.
  FAILING_GATES=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); names=sorted(k for k,v in d.items() if not v.get("passed", True)); print(",".join(names) if names else "unknown")' "$TASK_DIR/quality-gate-$((GATE_ATTEMPT-1)).json" 2>/dev/null)
  FAILING_GATES="${FAILING_GATES:-unknown}"
  FIX_PROPOSE_OUT=$(python3 /opt/veridian/scripts/credit-accountant.py propose --task-id "$TASK_ID" --plan "auto-fix attempt $GATE_ATTEMPT/2 for quality gate failure on task $TASK_ID, see quality-gate-$((GATE_ATTEMPT-1)).json for the failing checks" --search-terms "quality gate auto-fix retry: $FAILING_GATES")
  FIX_PROPOSE_RC=$?
  echo "$FIX_PROPOSE_OUT" >> "$TASK_DIR/worker.log"
  if [ "$FIX_PROPOSE_RC" -ne 0 ]; then
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "credit accountant rejected auto-fix attempt $GATE_ATTEMPT, no further metered spend without human review: $FIX_PROPOSE_OUT"
    git -C "$WORKSPACE" add -A
    git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (credit accountant rejected auto-fix)" >> "$TASK_DIR/worker.log" 2>&1 || true
    git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
    systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  fi
  FIX_INCREMENT=$(echo "$FIX_PROPOSE_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('increment_number', $GATE_ATTEMPT + 1))" 2>/dev/null)
  FIX_INCREMENT="${FIX_INCREMENT:-$((GATE_ATTEMPT + 1))}"
  FIX_OUT="$TASK_DIR/.claude-out-fix-$GATE_ATTEMPT.json"
  FIX_START_EPOCH=$(date -u +%s)
  claude -p "$FIX_PROMPT" --model sonnet --effort high --continue --dangerously-skip-permissions --max-budget-usd "$WORKER_BUDGET_CAP_USD" --output-format json > "$FIX_OUT" 2>>"$TASK_DIR/worker.log"
  cat "$FIX_OUT" >> "$TASK_DIR/result.json"
  FIX_COST=$(real_invocation_cost_usd "$FIX_START_EPOCH")
  python3 /opt/veridian/scripts/credit-accountant.py report --task-id "$TASK_ID" --increment "$FIX_INCREMENT" --actual-spend-usd "$FIX_COST" --outcome "auto-fix attempt $GATE_ATTEMPT/2 completed, real cost \$$FIX_COST" >> "$TASK_DIR/worker.log" 2>&1 || true
  if [ "$(budget_exceeded "$FIX_COST")" = "1" ]; then
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: auto-fix attempt $GATE_ATTEMPT real cost \$$FIX_COST, at/above the \$$WORKER_BUDGET_CAP_USD budget cap. Stopping auto-fix loop for human review rather than continuing unbounded."
    git -C "$WORKSPACE" add -A
    git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (budget cap hit during auto-fix)" >> "$TASK_DIR/worker.log" 2>&1 || true
    git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
    systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  fi
done

git -C "$WORKSPACE" add -A
git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit" >> "$TASK_DIR/worker.log" 2>&1 || true

if [ "$GATE_PASSED" -eq 1 ]; then
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status pending_review --note "quality gates passed, pushed branch $BRANCH, awaiting review"
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  systemctl --user start "veridian-supervisor@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
else
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "quality gates still failing after $((GATE_ATTEMPT)) auto-fix attempts; branch $BRANCH pushed with failing state for human review, see quality-gate-*.json"
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
fi
