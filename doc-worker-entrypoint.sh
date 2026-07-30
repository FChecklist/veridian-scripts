#!/bin/bash
# Entrypoint for a systemd-managed VERIDIAN DOC worker. Same checkpoint /
# resume / commit-push pattern as worker-entrypoint.sh, but deliberately
# DIFFERENT in two ways, both intentional for this task family (browser-based
# reverse-engineering documentation of external systems, dispatched 2026-07-20
# at Owner's explicit request):
#
# 1. Uses the REAL Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN from
#    /opt/veridian/shared/.env, loaded via systemd EnvironmentFile=) instead
#    of the GLM-5.2/OpenRouter proxy that worker-entrypoint.sh force-routes
#    through. That GLM-only routing was a fail-closed fix for the 2026-07-19
#    11K-call cost incident and is intentionally NOT inherited here -- the
#    Owner explicitly asked for subscription-model execution, not GLM, for
#    this task. Because that safety net is bypassed, this script substitutes
#    its OWN hard caps (invocation count below, wall-clock timeout on the
#    claude invocation) so a stuck/looping task still can't run unbounded.
# 2. Wires a Playwright MCP server (headless Chromium, no root/apt available
#    on this box, browser + its shared-library deps were extracted user-space
#    into /opt/veridian/workspace/browser-tools/local-libs) into the task via
#    a workspace-local .mcp.json, since this task needs to actually browse
#    external websites -- ordinary code workers never do this.
set -uo pipefail
TASK_ID="$1"
TASK_DIR="/opt/veridian/ai-os/tasks/$TASK_ID"
export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"
START_TS=$(date +%s)

# Hard caps (this task family has no GLM-proxy cost backstop to fall back on,
# see header comment) -- both configurable per-dispatch via env override.
MAX_LIFETIME_INVOCATIONS="${VERIDIAN_DOC_MAX_LIFETIME_INVOCATIONS:-8}"
MAX_WALL_SECONDS="${VERIDIAN_DOC_MAX_WALL_SECONDS:-14400}"  # 4h default per invocation

INVOCATION_COUNT_FILE="$TASK_DIR/.invocation_count"
PRIOR_COUNT=$(cat "$INVOCATION_COUNT_FILE" 2>/dev/null || echo 0)
NEW_COUNT=$((PRIOR_COUNT + 1))
echo "$NEW_COUNT" > "$INVOCATION_COUNT_FILE"
if [ "$NEW_COUNT" -gt "$MAX_LIFETIME_INVOCATIONS" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PREVENTION CAP HIT: this doc-worker task has been started/restarted $NEW_COUNT times (lifetime max $MAX_LIFETIME_INVOCATIONS). Stopping to prevent an unbounded retry loop against the real Claude subscription. Needs human review, not an automatic retry."
  systemctl --user disable "veridian-docworker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi

WORKSPACE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['workspace'])")
BRANCH=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['branch'])")
CHECKPOINT_COUNT=$(python3 -c "import yaml; print(len(yaml.safe_load(open('$TASK_DIR/task.yaml')).get('checkpoints', [])))")

if [ "$CHECKPOINT_COUNT" -gt 0 ]; then
  IS_RESUME=1
else
  IS_RESUME=0
fi

# --- Pre-flight guard (2026-07-20, Owner zero-waste directive): this task
# family had ZERO protection until now -- confirmed gap found during the
# "ensure 100% for all AI tasks" audit. Static checks + circuit breaker
# only (--no-proxy): this path uses the real subscription, not the GLM
# proxy, so proxy-health/canary/budget checks don't apply here.
# --- PREFLIGHT-GUARD-BLOCK-START (tests/preflight_guard_hardstop_test.sh extracts this)
GUARD_OUT=$(python3 /opt/veridian/scripts/preflight-guard.py "$TASK_DIR" "$WORKSPACE" --no-proxy 2>&1)
GUARD_EXIT=$?
if [ "$GUARD_EXIT" -ne 0 ]; then
  GUARD_REASON=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','unknown'))" 2>/dev/null || echo "unknown")
  GUARD_DETAIL=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null || echo "$GUARD_OUT")
  if [ "$GUARD_REASON" = "circuit_breaker_tripped" ] || [ "$GUARD_REASON" = "tight_task_schema_violation" ] || [ "$GUARD_REASON" = "crontab_unauthorized_change" ]; then
    # tight_task_schema_violation added 2026-07-23 (RCA fix, see
    # worker-entrypoint.sh's identical fix for the full rationale): this is
    # a static, content-based check of prompt.txt -- a retry against an
    # unchanged prompt.txt reproduces the identical rejection every time, so
    # it must be a hard stop, not routed to the transient/retry branch below.
    # crontab_unauthorized_change added 2026-07-27 (RCA fix, see
    # worker-entrypoint.sh's identical fix for the full rationale -- same
    # static/deterministic-rejection property, this dispatch path calls the
    # identical preflight-guard.py and was equally exposed to the restart-storm
    # bug even though the specific incident happened via the worker path).
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "PRE-FLIGHT HARD STOP ($GUARD_REASON): $GUARD_DETAIL"
    systemctl --user disable "veridian-docworker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
    exit 0
  else
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "PRE-FLIGHT REJECTED ($GUARD_REASON, transient): $GUARD_DETAIL -- no model call made"
    exit 1
  fi
fi
# --- PREFLIGHT-GUARD-BLOCK-END

python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status in_progress --note "doc-worker started (resume=$IS_RESUME, lifetime invocation $NEW_COUNT/$MAX_LIFETIME_INVOCATIONS, real-subscription mode, pre-flight passed)"

# Background checkpoint loop: snapshots git state + PROGRESS.md every 5 minutes
# regardless of whether the AI itself remembers to checkpoint.
(
  while true; do
    sleep 300
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --auto --note "periodic checkpoint"
  done
) &
CHECKPOINT_PID=$!
trap 'kill $CHECKPOINT_PID 2>/dev/null' EXIT

cd "$WORKSPACE"
mkdir -p "$WORKSPACE/screenshots"

# Workspace-local MCP config: Playwright browser tools, headless, screenshots
# written straight into this task's own worktree so they get committed +
# pushed like any other change. LD_LIBRARY_PATH points at the user-space
# extraction of Chromium's shared-lib deps (no root on this box).
cat > "$WORKSPACE/.mcp.json" <<MCPEOF
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--headless", "--no-sandbox", "--isolated", "--output-dir", "$WORKSPACE/screenshots", "--viewport-size", "1440x900"],
      "env": {
        "LD_LIBRARY_PATH": "/opt/veridian/workspace/browser-tools/local-libs/usr/lib/x86_64-linux-gnu"
      }
    }
  }
}
MCPEOF

PROGRESS_INSTRUCTION="Maintain a PROGRESS.md file in the repository root with '## Completed' and '## Remaining' sections, each a markdown checklist (- [x] done thing / - [ ] pending thing). Update it as you complete each meaningful step — this is how the orchestration system tracks your progress and how you (or a resumed instance of you) would recover if interrupted. Commit and push your work-in-progress periodically (every few pages/modules documented), not just at the very end — this task may be interrupted and resumed."

if [ "$IS_RESUME" -eq 1 ]; then
  RESUME_CONTEXT=$(python3 /opt/veridian/scripts/veridian-task.py resume-context "$TASK_ID")
  # 2026-07-20: stop re-embedding the full original prompt.txt on every
  # resume -- confirmed the single biggest source of redundant tokens on
  # worker-entrypoint.sh's identical pattern, same fix applied here.
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
timeout "$MAX_WALL_SECONDS" claude -p "$PROMPT" --model sonnet --effort high --dangerously-skip-permissions --output-format json > "$MAIN_OUT" 2>>"$TASK_DIR/worker.log"
EXIT_CODE=$?
cat "$MAIN_OUT" >> "$TASK_DIR/result.json"

# --- AI response logging (2026-07-24, governance item 15: ai_response_logging) ---
# Same fix as worker-entrypoint.sh's own MAIN_OUT block: this docworker path
# is the "docworker-bypass caveat" items 14/15 explicitly flagged (real
# subscription auth, never routed through glm-response-cache.sqlite). Reuses
# the existing superboss-register.py log-action CLI, no new function.
# Best-effort: a logging failure must never fail the docworker task itself.
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
    --source ai_response --medium claude_code_cli --campaign doc-worker-entrypoint-main-invocation \
    --content "$AI_RESPONSE_TEXT" --term "$TASK_ID" --result "exit_code=$EXIT_CODE" \
    >> "$TASK_DIR/worker.log" 2>&1 || true
fi

kill "$CHECKPOINT_PID" 2>/dev/null || true
wait "$CHECKPOINT_PID" 2>/dev/null || true

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
python3 /opt/veridian/scripts/veridian-task.py record-usage "$TASK_ID" --elapsed "$ELAPSED"

# Report increment 1 (the single propose call preflight-guard.py made for
# this task) back to the credit ledger. --actual-spend-usd is always 0 --
# see header comment on this patch / this script's own header comment for
# why (real subscription, $0 from the metered pool this mechanism
# protects). The CLI's own Anthropic-rate cost estimate is captured in the
# outcome text for informational tracking only, not as the ledger's dollar
# figure.
DOC_CLI_REPORTED_COST=$(python3 -c "
import json
try:
    with open('$MAIN_OUT') as f:
        d = json.load(f)
    print(d.get('total_cost_usd', 0) or 0)
except Exception:
    print(0)
")
if [ "$EXIT_CODE" -eq 124 ]; then
  DOC_OUTCOME="hit the ${MAX_WALL_SECONDS}s wall-clock cap (not a crash); CLI-reported cost estimate (Anthropic list rate, informational only, real spend is \$0 from the metered pool): \$DOC_CLI_REPORTED_COST"
elif [ "$EXIT_CODE" -ne 0 ]; then
  DOC_OUTCOME="invocation FAILED, exit code $EXIT_CODE; CLI-reported cost estimate (informational only, real spend is \$0 from the metered pool): \$DOC_CLI_REPORTED_COST"
else
  DOC_OUTCOME="invocation completed, exit 0; CLI-reported cost estimate (informational only, real spend is \$0 from the metered pool): \$DOC_CLI_REPORTED_COST"
fi
python3 /opt/veridian/scripts/credit-accountant.py report --task-id "$TASK_ID" --increment 1 --actual-spend-usd 0 --outcome "$DOC_OUTCOME" >> "$TASK_DIR/worker.log" 2>&1 || true

if [ "$EXIT_CODE" -eq 124 ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status in_progress --note "hit the $MAX_WALL_SECONDS-second wall-clock cap for this invocation (not a crash) -- committing whatever progress exists, systemd will restart into a fresh invocation up to the lifetime cap"
  git -C "$WORKSPACE" add -A
  git -C "$WORKSPACE" commit -m "Doc-worker $TASK_ID: checkpoint commit (wall-clock cap hit)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 1
fi

if [ "$EXIT_CODE" -ne 0 ]; then
  # 2026-07-20: record a failure signature for the circuit breaker's next
  # pre-flight check -- same mechanism as worker-entrypoint.sh, closing the
  # confirmed gap that this task family had no loop-prevention at all.
  python3 -c "
import hashlib, json, os
sig_file = '$TASK_DIR/.failure_signatures.json'
try:
    with open('$TASK_DIR/worker.log') as f:
        tail = f.read()[-400:]
except FileNotFoundError:
    tail = 'no-worker-log'
normalized = ' '.join(tail.split())
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
  # Commit+push whatever real progress exists even on a hard failure -- a
  # crash/non-zero exit must never be the reason in-progress documentation
  # work (already-written docs/screenshots on disk) is lost, only unpushed.
  git -C "$WORKSPACE" add -A
  git -C "$WORKSPACE" commit -m "Doc-worker $TASK_ID: checkpoint commit (invocation failed, exit $EXIT_CODE)" >> "$TASK_DIR/worker.log" 2>&1 || true
  git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1 || true
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status failed --note "worker exited with code $EXIT_CODE; failure signature recorded for circuit breaker; pushed whatever progress existed on disk; systemd will retry from this checkpoint up to the configured burst limit before giving up"
  exit 1
fi

if git -C "$WORKSPACE" diff --quiet && git -C "$WORKSPACE" diff --cached --quiet && [ -z "$(git -C "$WORKSPACE" status --porcelain)" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status completed --note "worker finished, no changes to commit"
  systemctl --user disable "veridian-docworker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
  exit 0
fi

# No code quality gates apply to a docs+screenshots repo; quality-gate.sh
# already detects that (no package.json/pyproject.toml/requirements.txt) and
# no-ops cleanly, so it's still safe to reuse as-is for consistency.
bash /opt/veridian/scripts/quality-gate.sh "$WORKSPACE" "$TASK_DIR/quality-gate-0.json" >> "$TASK_DIR/worker.log" 2>&1
GATE_PASSED=$?

git -C "$WORKSPACE" add -A
git -C "$WORKSPACE" commit -m "Doc-worker $TASK_ID: automated checkpoint commit" >> "$TASK_DIR/worker.log" 2>&1 || true
git -C "$WORKSPACE" push -u origin "$BRANCH" >> "$TASK_DIR/worker.log" 2>&1

if [ "$GATE_PASSED" -eq 0 ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status pending_review --note "pushed branch $BRANCH, awaiting review"
else
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status pending_review --note "pushed branch $BRANCH (quality-gate reported non-zero but no code gates apply to this docs repo -- check quality-gate-0.json), awaiting review"
fi
systemctl --user disable "veridian-docworker@${TASK_ID}.service" >> "$TASK_DIR/worker.log" 2>&1 || true
