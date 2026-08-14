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

# --- Lifetime invocation budget (2026-08-14 RCA fix, UMR-20260814-034225-3392) ---
# INVARIANT: this counter must reflect REAL model invocations only. It used to be
# incremented right here, at the very top of the script, before ANY preflight check
# ran -- so a purely infrastructural preflight rejection (disk_low, memory_low,
# worktree contention, ...) that never calls the model still permanently burned one
# of these MAX_LIFETIME_INVOCATIONS slots, and systemd's Restart=on-failure retried
# up to StartLimitBurst=3 times, each one charging another slot for zero work done.
# Confirmed live 2026-08-14 (PM Sentinel): a host-level full-volume event rejected 11
# tasks on guard reason disk_low; one of them
# (task-20260718-171007-commercial--subscription---pricing-model) was already at
# 18/20 lifetime invocations despite having NEVER actually executed a single model
# call -- two more infra hiccups away from being permanently, silently unrunnable for
# a condition the task itself had zero power to affect.
#
# Fix: the increment now happens ONLY once preflight has passed and a real model
# call ($claude -p, below) is imminent -- see the LIFETIME-INVOCATION-CHARGE-BLOCK
# right after PREFLIGHT-GUARD-BLOCK-END. The cap check here is unchanged in effect
# (PRIOR_COUNT >= MAX is equivalent to the old NEW_COUNT > MAX), it just no longer
# writes the file itself. Infrastructure rejections are now bounded on their OWN,
# separate counter -- see MAX_INFRA_REJECTIONS/INFRA_REJECTION_COUNT_FILE below and
# the transient branch inside PREFLIGHT-GUARD-BLOCK -- so a broken host still cannot
# spin this task forever; it just no longer does so by draining the real
# model-invocation budget to do it.
MAX_LIFETIME_INVOCATIONS="${VERIDIAN_MAX_LIFETIME_INVOCATIONS:-20}"
INVOCATION_COUNT_FILE="$TASK_DIR/.invocation_count"
PRIOR_COUNT=$(cat "$INVOCATION_COUNT_FILE" 2>/dev/null || echo 0)
if [ "$PRIOR_COUNT" -ge "$MAX_LIFETIME_INVOCATIONS" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: this task has already made $PRIOR_COUNT real model invocations (lifetime max $MAX_LIFETIME_INVOCATIONS) -- stopping to prevent an unbounded slow-drip retry loop across restarts, the same shape as the 2026-07-18 incident. Needs human review, not an automatic retry."
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi

# --- Infrastructure-rejection budget (2026-08-14, separate from the above) ---
# Bounds purely infrastructural preflight rejections (the transient branch inside
# PREFLIGHT-GUARD-BLOCK below) on their OWN counter/cap/backoff, so a genuinely
# broken host still cannot spin this task forever -- without that protection ever
# again borrowing from the real model-invocation budget above. Deliberately a much
# smaller cap than MAX_LIFETIME_INVOCATIONS: an infra rejection costs ~0 (no model
# call), so there is no reason to give it 20 chances -- it exists only to stop a
# genuinely wedged host from restart-storming this unit forever between systemd's
# StartLimitBurst windows.
MAX_INFRA_REJECTIONS="${VERIDIAN_MAX_INFRA_REJECTIONS:-5}"
INFRA_REJECTION_COUNT_FILE="$TASK_DIR/.infra_rejection_count"

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
    # Transient (disk/mem/proxy/worktree) -- purely infrastructural, no model call
    # was ever made. 2026-08-14 fix: bounded by its OWN counter/cap
    # (MAX_INFRA_REJECTIONS above), NOT the lifetime model-invocation cap -- see the
    # header comment on that counter. Backoff grows with the infra-rejection count so
    # a persistently-broken host still can't hot-loop inside systemd's
    # StartLimitBurst window even while under its own cap.
    INFRA_PRIOR_COUNT=$(cat "$INFRA_REJECTION_COUNT_FILE" 2>/dev/null || echo 0)
    INFRA_NEW_COUNT=$((INFRA_PRIOR_COUNT + 1))
    echo "$INFRA_NEW_COUNT" > "$INFRA_REJECTION_COUNT_FILE"
    if [ "$INFRA_NEW_COUNT" -gt "$MAX_INFRA_REJECTIONS" ]; then
      # Same shape as the hard-stop branch above: stop letting systemd retry this,
      # a human needs to look at host health. Lifetime model-invocation counter is
      # still untouched -- this task's real MAX_LIFETIME_INVOCATIONS budget is
      # exactly as fresh as it was before the host ever had a problem.
      python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "INFRASTRUCTURE-REJECTION CAP HIT ($GUARD_REASON): $GUARD_DETAIL -- rejected $INFRA_NEW_COUNT times on purely infrastructural grounds (infra cap $MAX_INFRA_REJECTIONS); lifetime model-invocation counter is UNCHANGED, still $PRIOR_COUNT/$MAX_LIFETIME_INVOCATIONS, since no model call has ever been made by this rejection path. Stopping to prevent an unbounded infra-retry loop against a broken host. Needs human review of host health (or a raised VERIDIAN_MAX_INFRA_REJECTIONS override), not an automatic retry."
      systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
      exit 0
    fi
    # Let systemd's normal Restart=on-failure retry after RestartSec -- counted
    # against the infra-rejection cap above ONLY, never the lifetime
    # model-invocation cap (that is charged below, only once preflight passes).
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "PRE-FLIGHT REJECTED ($GUARD_REASON, transient, infra-rejection $INFRA_NEW_COUNT/$MAX_INFRA_REJECTIONS): $GUARD_DETAIL -- no model call made, no cost incurred, lifetime invocation counter NOT charged (still $PRIOR_COUNT/$MAX_LIFETIME_INVOCATIONS)"
    sleep "$((INFRA_NEW_COUNT * 5))"
    exit 1
  fi
fi
# --- PREFLIGHT-GUARD-BLOCK-END

# --- LIFETIME-INVOCATION-CHARGE-BLOCK-START (tests/preflight_guard_hardstop_test.sh
# extracts this too, alongside PREFLIGHT-GUARD-BLOCK, to prove the charge only
# happens on this path) -- preflight has passed, a real model call is now imminent.
# THIS is the one and only place the lifetime invocation counter is written; see the
# 2026-08-14 header comment above MAX_LIFETIME_INVOCATIONS for why.
NEW_COUNT=$((PRIOR_COUNT + 1))
echo "$NEW_COUNT" > "$INVOCATION_COUNT_FILE"
# --- LIFETIME-INVOCATION-CHARGE-BLOCK-END

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

# --- GITLINK GUARD (2026-08-13, UMR-20260813-235552-dc9a) ---
# Real incident: a worker whose workspace was checked out from the WRONG
# repo improvised a nested `git clone` of the correct repo to do its real
# work (observed directory names `veridian-scripts-work`,
# `veridian-scripts-clean`), and every checkpoint commit below used to do a
# blind `git add -A` that swept that nested .git directory in as a bare
# submodule gitlink (mode 160000) -- which then got pushed and, via
# supervisor-entrypoint.sh's `gh pr create`, shipped as a real PR containing
# nothing but that one gitlink entry (claude-control PRs #146, #170, #191:
# diff stat 1 file changed, 1 insertion(+), zero real content). Use this in
# place of a bare `git -C "$WORKSPACE" add -A` at every checkpoint site
# below: it stages everything exactly like before, then unstages (never
# deletes -- the nested checkout's real work stays on disk, just untracked)
# any newly-introduced gitlink that isn't a genuine, pre-existing, declared
# submodule of this repo. Deliberately non-fatal (these checkpoints are
# best-effort safety nets, several on hard-stop paths that must never fail)
# -- it just keeps the poisonous entry out of the commit; every other real
# change still gets staged and committed normally. See gitlink_guard.py.
safe_stage_all() {
  git -C "$WORKSPACE" add -A
  local violations
  violations=$(python3 /opt/veridian/scripts/gitlink_guard.py "$WORKSPACE" HEAD --staged 2>>"$TASK_DIR/worker.log")
  if [ -n "$violations" ]; then
    echo "GITLINK GUARD tripped -- unstaging illegitimate gitlink(s) before commit: $(echo "$violations" | tr '\n' ' ')" >> "$TASK_DIR/worker.log"
    while IFS= read -r gpath; do
      [ -n "$gpath" ] && git -C "$WORKSPACE" reset -- "$gpath" >> "$TASK_DIR/worker.log" 2>&1
    done <<< "$violations"
  fi
}

# 2026-08-13 (UMR-20260813-195922-f548, real defect confirmed live against
# FChecklist/veridian-scripts PRs #315/#317/#321): every worker used to
# "maintain PROGRESS.md" -- ONE shared file, same path on every branch. Two
# compounding failures resulted: (a) a worker could satisfy this literal
# instruction by only ever editing PROGRESS.md, so 3 separate "fixes" for
# real dispatch defects shipped as prose with the named source file
# untouched, and got recorded as real completed work; (b) every long-lived
# branch that touched PROGRESS.md conflicted with every OTHER branch that
# also touched it, regardless of whether their real code overlapped at all
# -- 17 of 25 parseable open/DIRTY PRs on veridian-scripts were
# PROGRESS.md-only diffs stuck CONFLICTING for exactly this reason. Fix:
# each task now owns a PER-TASK progress file (progress/<task_id>.md) -- a
# new path per branch, so two branches never touch the same line and never
# conflict on merge. A rolled-up view (if wanted) is generated
# deterministically from every progress/*.md file by
# progress_completion_gate.py's own `rollup` subcommand, never hand-edited.
PROGRESS_FILE="progress/${TASK_ID}.md"
PROGRESS_INSTRUCTION="PROTOCOL: maintain $PROGRESS_FILE (## Completed / ## Remaining, markdown checkboxes), update after each step. This is YOUR OWN per-task file, not a shared PROGRESS.md -- do not edit any other task's progress/*.md, and do not recreate a shared PROGRESS.md. commit+push after each meaningful unit, not only at the end. COMPLETION GATE: if your task's objective names a specific source file or script, that file MUST be present in your real committed diff -- a diff containing only progress/doc artifacts for a code-named objective will be rejected as a real failure (not marked complete), see progress_completion_gate.py check-completion. on a 2nd consecutive failure of the identical approach: STOP, do not attempt a 3rd time -- this is enforced by a circuit breaker on the next invocation regardless, so stopping yourself first saves a wasted restart."

# --- Deterministic pre-work briefing (2026-08-06, direct correction/extension
# to UMR-20260806-121332-6ba4, see scripts/agent_work_briefing.py) --------
# Real, best-effort, ONLY on a genuine first start (never on resume -- the
# agent already has this from its own PROGRESS.md/checkpoint history by
# then, and umr_id below can legitimately point at a DIFFERENT umr_id than
# a prior resume of this same unit did, see the UMR-reuse-on-resume note
# just below). Resolves this exact worker unit's own CURRENT real umr_id via
# umr_tasks.unit_name (the one stable identity across resumes -- the umr_id
# itself can rotate when the SAME unit is reused for a resumed/corrected
# re-dispatch, see upsert_umr_task()'s own docstring in
# superboss-register.py -- so this is always looked up fresh, never cached),
# then hands the result to agent_work_briefing.py's own assemble-briefing
# (never a second, competing lookup). A failure anywhere here (DB
# unreadable, no umr_id row yet, etc.) must NEVER block real dispatch --
# same fail-open posture as every other purely-additive traceability write
# in this file (see insert_ocid_artifact_link's own docstring) -- so this
# degrades to an empty BRIEFING_INSTRUCTION, never a non-zero exit.
BRIEFING_INSTRUCTION=""
if [ "$IS_RESUME" -eq 0 ]; then
  # python3's own sqlite3 module, not the `sqlite3` CLI binary -- every other
  # DB read in this file already goes through python3 (see WORKSPACE/BRANCH
  # above), and the CLI binary is not a guaranteed-present dependency on
  # every real deployment host.
  UMR_ID_FOR_BRIEFING=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('/opt/veridian/ai-os/memory/superboss-register.sqlite')
    row = conn.execute(
        \"SELECT umr_id FROM umr_tasks WHERE unit_name=? ORDER BY ts_submitted DESC LIMIT 1\",
        ('veridian-worker@${TASK_ID}.service',),
    ).fetchone()
    print(row[0] if row else '')
except Exception:
    pass
" 2>>"$TASK_DIR/worker.log")
  if [ -n "$UMR_ID_FOR_BRIEFING" ]; then
    # intent-text is the task's own concise title (task.yaml), not the full
    # prompt.txt spec -- lookup_capability()'s own keyword stage is a plain
    # OR-of-terms FTS match (see its own docstring), so a multi-sentence
    # query balloons the match count on incidental vocabulary alone. Only
    # close_ended_facts (a handful of compact lines), not the full briefing
    # JSON (matches/metadata_json can run tens of KB), is put in the prompt
    # itself -- real cost discipline, same concern this file's own header
    # cites (COST-CONTROL.md); the full JSON is one command away if the
    # agent actually needs it.
    TASK_TITLE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml')).get('title',''))" 2>>"$TASK_DIR/worker.log")
    BRIEFING_FACTS=$(python3 /opt/veridian/scripts/agent_work_briefing.py assemble-briefing \
      --umr-id "$UMR_ID_FOR_BRIEFING" --scope-term "$TASK_ID" \
      --intent-text "${TASK_TITLE:-$TASK_ID}" \
      2>>"$TASK_DIR/worker.log" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('\n'.join('- ' + f for f in d.get('close_ended_facts', [])))
except Exception:
    pass
")
    if [ -n "$BRIEFING_FACTS" ]; then
      BRIEFING_INSTRUCTION="DETERMINISTIC BRIEFING (umr_id=$UMR_ID_FOR_BRIEFING, from scripts/agent_work_briefing.py assemble-briefing, run before you started -- real, close-ended fact, not a suggestion to re-derive; re-run it yourself with --scope-term for any specific file/keyword if you need the full matches):
$BRIEFING_FACTS

When real work completes, call: python3 /opt/veridian/scripts/agent_work_briefing.py record-completion --umr-id \"$UMR_ID_FOR_BRIEFING\" --entry-text \"<real summary of what you actually did>\" -- this is the one canonical write-back into this UMR's own ai_agent_registry memory row. Add --new-entity-record-file <path> only if you registered a genuinely new wiring_registry entity (search-first dedup is automatic), and --gtm-category-index only if this work maps to a real gtm_certification_categories row."
    fi
  fi
fi

if [ "$IS_RESUME" -eq 1 ]; then
  RESUME_CONTEXT=$(python3 /opt/veridian/scripts/veridian-task.py resume-context "$TASK_ID")
  PROMPT="RESUME task=$TASK_ID invocation=$NEW_COUNT/$MAX_LIFETIME_INVOCATIONS
DO_NOT restart from scratch. run: git status && git log --oneline -10 && read $PROGRESS_FILE (your own per-task progress file, not a shared PROGRESS.md).
LAST_CHECKPOINT:
$RESUME_CONTEXT
SPEC: full task spec is prompt.txt in cwd (provided once already, not restated here -- read it only if you need it).
$PROGRESS_INSTRUCTION"
else
  PROMPT="SPEC: $(cat "$TASK_DIR/prompt.txt")

$PROGRESS_INSTRUCTION
$BRIEFING_INSTRUCTION"
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

# --- Account-wide rate/usage-limit hard stop (2026-08-06 RCA fix) ---
# Confirmed root cause of task-20260805-193951's 3 consecutive exit-1,
# 14s-total, 0-token failures (and, in the same ~19:33-19:41 UTC window on
# 2026-08-05, 27 OTHER tasks' MAIN_OUT files independently showing the
# identical api_error_status=429 "You've hit your weekly limit" -- this was
# never a per-task launcher/env/argument bug, the account-wide Claude
# subscription quota was exhausted mid-burst and every concurrent worker's
# very first API call was rejected before any tokens were spent). Before
# this fix a 429 fell through to the generic API_IS_ERROR branch above,
# which is correct for FLAGGING it as a failure but wrong for how it gets
# RETRIED: record_failure_signature() (further below) hashes worker.log's
# last 400 chars, which always contains this invocation's own random
# action_id/session_id, so 3 retries of the SAME account-wide 429 produced
# 3 DIFFERENT signatures ("79c7a27d...", "1eb09d87...", "ead465bf...") --
# the circuit breaker in check_circuit_breaker() (preflight-guard.py) only
# trips on 2 CONSECUTIVE IDENTICAL signatures, so it never saw this as the
# same failure twice and never had a chance to stop it. What actually
# stopped this task at 3 invocations was systemd's own unrelated
# StartLimitBurst ("Start request repeated too quickly") -- a coincidental
# safety net, not a deliberate one, and one that leaves the unit in a
# terminal systemd 'failed' state that still silently eats a lifetime
# invocation slot each time. This is the identical "blind retry against an
# unresolvable wall" shape as the openrouter_balance_exhausted /
# error_max_budget_usd hard stops already handled above and below -- an
# exhausted weekly quota does not clear until the API's own reset time
# (echoed back in the error text, e.g. "resets 2am (UTC)"), so retrying
# before then reproduces the identical rejection every time. Detect it by
# api_error_status==429 (Anthropic's real rate/usage-limit status code) OR
# a "limit" match in the error text as a fallback if the status code field
# is ever absent, and hard-stop exactly like CLI_HIT_BUDGET_CAP below:
# checkpoint blocked with the real reset-time text surfaced in the note,
# disable the unit so systemd does not restart-storm it, exit 0. A human
# (or a scheduler that already knows the reset time) re-enables the unit
# once the quota window has actually rolled over -- see step 2 of this
# task's own SPEC for the concrete re-dispatch this unblocks.
API_RATE_LIMITED=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    status = d.get('api_error_status')
    text = (d.get('result') or '') if d.get('is_error') else ''
    print('1' if status == 429 or 'weekly limit' in text.lower() or 'usage limit' in text.lower() or 'rate limit' in text.lower() else '0')
except Exception:
    print('0')
")
if [ "$API_RATE_LIMITED" = "1" ]; then
  RATE_LIMIT_TEXT=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    print((d.get('result') or 'no detail in result field').strip())
except Exception:
    print('no detail (could not parse $MAIN_OUT)')
")
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "ACCOUNT-WIDE RATE/USAGE LIMIT HARD STOP (api_error_status=429): $RATE_LIMIT_TEXT -- 0 tokens consumed, the model was never reached. Stopping rather than retrying -- this is account-wide quota exhaustion, not a per-task problem, and will reproduce identically until the quota window resets. Needs human/scheduler re-enable AFTER the reset time above, not an automatic retry."
  safe_stage_all
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (account-wide rate/usage limit, 429)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  kill "$CHECKPOINT_PID" 2>/dev/null || true
  wait "$CHECKPOINT_PID" 2>/dev/null || true
  exit 0
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
  safe_stage_all
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
  safe_stage_all
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: checkpoint commit (invocation failed, exit $EXIT_CODE)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "worker exited with code $EXIT_CODE; failure signature recorded for circuit breaker; pushed whatever progress existed; systemd will retry up to the burst limit"
  exit 1
fi

MAIN_COST=$(real_invocation_cost_usd "$MAIN_START_EPOCH")
python3 /opt/veridian/scripts/credit-accountant.py report --task-id "$TASK_ID" --increment 1 --actual-spend-usd "$MAIN_COST" --outcome "main invocation completed, exit 0, real cost \$$MAIN_COST" >> "$TASK_DIR/worker.log" 2>&1 || true
if [ "$(budget_exceeded "$MAIN_COST")" = "1" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: this invocation's REAL OpenRouter/GLM-5.2 cost was \$$MAIN_COST, at/above the \$$WORKER_BUDGET_CAP_USD budget cap -- stopped rather than continuing unbounded. Needs human review before further retries (likely a stuck/looping task, not ordinary progress)."
  safe_stage_all
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

# --- COMPLETION-GATE-BLOCK-START (2026-08-13, UMR-20260813-195922-f548) ---
# Real gate, not a prompt instruction: if this task's own prompt.txt names a
# specific source/script file as its objective, that file must be present
# in the REAL diff (committed + staged + unstaged, vs the merge-base with
# origin/$DEFAULT_BRANCH) before this is allowed anywhere near
# pending_review/completed. This is what actually closes the PROGRESS.md-only-
# fix hole the PROGRESS_INSTRUCTION rewrite above only discourages by
# convention -- a worker could still ignore the instruction, so the diff
# itself is checked here, mechanically, every single run. A rejection here
# is a REAL, terminal failure (status=blocked with the explicit reason),
# never silently downgraded to success. See progress_completion_gate.py and
# tests/test_progress_completion_gate.py.
#
# 2026-08-14 (UMR-20260814-070059-6484, governing chain
# UMR-20260806-171945-5767): the diff-vs-merge-base check above only ever
# looked at THIS task's own branch/repo. That is wrong for legitimate
# cross-repo work -- the real victim, task-20260814-060148 (repo
# claude-control), deliberately built its real code fix + 8 passing tests
# in an isolated clone of a DIFFERENT repo (veridian-scripts), opened a real
# PR there, and closed two superseded PRs; its task branch diff was 6
# markdown/txt files only, so this gate rejected genuinely successful work
# and worker-exit-status-bridge.py then wrote umr_tasks.status=failed for a
# task that actually succeeded. progress_completion_gate.py's
# check_completion() now also accepts a real, `gh`-confirmed PR in another
# repo (find_cross_repo_pr_evidence()) before falling through to this
# rejection -- a task that touched no code in ANY repo is still rejected
# here exactly as before.
GATE_CHECK_OUT=$(python3 /opt/veridian/scripts/progress_completion_gate.py check-completion \
  --task-dir "$TASK_DIR" --workspace "$WORKSPACE" --default-branch "$DEFAULT_BRANCH" 2>>"$TASK_DIR/worker.log")
GATE_CHECK_RC=$?
if [ "$GATE_CHECK_RC" -ne 0 ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "COMPLETION GATE REJECTED: $GATE_CHECK_OUT -- real failure, not success; a human must either supply the named file's real change or correct the task's stated objective"
  safe_stage_all
  git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (completion gate rejected: objective named a code file the diff never touches)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi
# --- COMPLETION-GATE-BLOCK-END ---

# Quality gates: up to 2 auto-fix attempts (same conversation via --continue)
# before giving up and marking blocked for human review.
GATE_ATTEMPT=0
GATE_PASSED=0
while [ "$GATE_ATTEMPT" -lt 3 ]; do
  echo "=== quality gate attempt $GATE_ATTEMPT ===" >> "$TASK_DIR/worker.log"
  bash /opt/veridian/scripts/quality-gate.sh "$WORKSPACE" "$TASK_DIR/quality-gate-$GATE_ATTEMPT.json" >> "$TASK_DIR/worker.log" 2>&1
  GATE_RC=$?
  if [ "$GATE_RC" -eq 0 ]; then
    GATE_PASSED=1
    break
  fi
  if [ "$GATE_RC" -eq 75 ]; then
    # UMR-20260806-123316-cf9f: quality-gate.sh's own build step lost the
    # host-wide build lock race (short 20s wait) and has ALREADY requeued
    # this task's umr_tasks row (reason=build_lock_contended, via the
    # canonical superboss-register.py CLI) and left a resume marker for the
    # next attempt to pick up already-passed gates -- this is this worker's
    # own cue to exit cleanly right now so the systemd slot genuinely frees
    # up, NOT a real gate failure. Must not fall through to the auto-fix
    # loop below (that would misdiagnose lock contention as a code defect
    # and burn a real auto-fix attempt on nothing real to fix) and must not
    # mark this task blocked -- the row is already back at status=queued for
    # the real dispatcher to pick back up on its own schedule.
    echo "quality gate exited $GATE_RC (build_lock_contended, already requeued via superboss-register.py) -- exiting worker cleanly, no auto-fix attempt, no blocked marking" >> "$TASK_DIR/worker.log"
    exit 0
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
  #
  # 2026-08-13 (RCA task-20260813-082632, UMR-20260808-183926-70b6): the
  # 2026-08-02 fix above narrowed the false-positive rate but did not close
  # it -- bare gate names like "build" are still single common words, OR'd
  # (not phrase-matched) by superboss-register.py's _fts_query() across
  # FOUR tables including wiring_registry (7,783+ rows, added Stage 6,
  # 2026-07-29, after the 2026-08-02 fix was written). Confirmed LIVE: a
  # real auto-fix attempt on task-20260808-192230 (a docs-only commit,
  # zero files_modified, blocked purely by a `next build` gate TIMEOUT)
  # was rejected with "existing software/mechanism already covers this
  # (system_index match)" backed by 1,966 unrelated FTS hits on the word
  # "build" alone -- task never reached pending_review, no PR ever opened,
  # eventually reconciled to killed. check-duplicate's OR-of-bare-words
  # design is deliberate and correct for its real callers (a human
  # reviewing a discovery list, per its own docstring) but wrong for this
  # one automated hard-reject gate. Fix: wrap the search terms in an exact
  # FTS5 phrase (double quotes) -- _fts_query() already special-cases
  # quoted input as one adjacent phrase clause instead of OR'd bare words
  # (see its own 2026-07-29 Stage 4 docstring), so this synthetic
  # generated string (never a real path/purpose/capability-name literal in
  # the registry) now correctly returns zero matches instead of flooding
  # on any one common word. Verified live: check-duplicate
  # '"quality gate auto-fix retry build"' -> found=0 (was found=1966
  # unquoted).
  FAILING_GATES=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); names=sorted(k for k,v in d.items() if not v.get("passed", True)); print(",".join(names) if names else "unknown")' "$TASK_DIR/quality-gate-$((GATE_ATTEMPT-1)).json" 2>/dev/null)
  FAILING_GATES="${FAILING_GATES:-unknown}"
  FIX_PROPOSE_OUT=$(python3 /opt/veridian/scripts/credit-accountant.py propose --task-id "$TASK_ID" --plan "auto-fix attempt $GATE_ATTEMPT/2 for quality gate failure on task $TASK_ID, see quality-gate-$((GATE_ATTEMPT-1)).json for the failing checks" --search-terms "\"quality gate auto-fix retry $FAILING_GATES\"")
  FIX_PROPOSE_RC=$?
  echo "$FIX_PROPOSE_OUT" >> "$TASK_DIR/worker.log"
  if [ "$FIX_PROPOSE_RC" -ne 0 ]; then
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "credit accountant rejected auto-fix attempt $GATE_ATTEMPT, no further metered spend without human review: $FIX_PROPOSE_OUT"
    safe_stage_all
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
    safe_stage_all
    git -C "$WORKSPACE" commit -m "Worker $TASK_ID: automated checkpoint commit (budget cap hit during auto-fix)" >> "$TASK_DIR/worker.log" 2>&1 || true
    git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
    systemctl --user disable "veridian-worker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  fi
done

safe_stage_all
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
