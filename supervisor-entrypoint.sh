#!/bin/bash
# VERIDIAN-DEV server-side Superboss: reviews a task's branch for real (not a
# self-report), classifies risk tier deterministically, and either merges
# autonomously (tier1 + approved), holds for human sign-off (tier2 + approved),
# or leaves it blocked with review comments (rejected). Uses GitHub PRs for the
# actual merge (not local git merge) to avoid any conflict with the main repo
# clone's own periodic sync-repos.sh pulls.
set -uo pipefail
TASK_ID="$1"
TASK_DIR="/opt/veridian/ai-os/tasks/$TASK_ID"
export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"

# 2026-07-19 (Owner directive, following COST-INCIDENT-11K-CALLS-RCA): same
# GLM-5.2-via-OpenRouter routing as worker-entrypoint.sh, fail-closed (no
# fallback to real Anthropic auth). See that script's own header for the
# full rationale.
# 2026-07-23: switched from GLM/OpenRouter proxy to real Claude Max subscription auth, Owner directive (same fix as worker-entrypoint.sh).
unset CLAUDE_CODE_OAUTH_TOKEN
unset ANTHROPIC_API_KEY
unset ANTHROPIC_BASE_URL
SUPERVISOR_BUDGET_CAP_USD="${VERIDIAN_SUPERVISOR_BUDGET_CAP_USD:-10}"

if [ -f "$TASK_DIR/review.json" ]; then
  echo "Already reviewed, skipping (idempotency guard)."
  exit 0
fi

# --- Pre-flight guard (2026-07-20, constitution-audit gap #7): confirmed
# ZERO protection existed here before this -- no circuit breaker, no static
# checks. Reuses the exact same guard as worker-entrypoint.sh (real GLM
# proxy, not --no-proxy, since this script uses the same proxy). The
# tight-task-schema check inside it gracefully no-ops when $TASK_DIR/
# prompt.txt doesn't exist (a review task has no fresh task prompt to
# validate) -- safe to call unconditionally.
GUARD_OUT=$(python3 /opt/veridian/scripts/preflight-guard.py "$TASK_DIR" "$TASK_DIR" --no-proxy 2>&1)  # 2026-07-23: GLM proxy decommissioned, Owner directive -- real subscription auth now used
GUARD_EXIT=$?
if [ "$GUARD_EXIT" -ne 0 ]; then
  GUARD_REASON=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','unknown'))" 2>/dev/null || echo "unknown")
  GUARD_DETAIL=$(echo "$GUARD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null || echo "$GUARD_OUT")
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "SUPERVISOR PRE-FLIGHT REJECTED ($GUARD_REASON): $GUARD_DETAIL"
  exit 1
fi

WORKSPACE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['workspace'])")
BRANCH=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['branch'])")
REPO=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['repo'])")
TITLE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml'))['title'])")
# Real, machine-readable hold-for-signoff (2026-07-26, root-caused against the
# PR563 incident, see the HOLD-FOR-OWNER-SIGNOFF-BLOCK below): defaults to
# False for any task.yaml written before this field existed.
HOLD_FOR_OWNER_SIGNOFF=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/task.yaml')).get('hold_for_owner_signoff', False))")

cd "$WORKSPACE"
git fetch origin

# --- WORKSPACE-RESYNC-BLOCK-START (real gap: GAP-SUPERVISOR-RETRIGGER-STALE-WORKSPACE,
# UMR-20260803-025317-0c64, fixed UMR-20260803-040529-15c9): `veridian-task.py adopt`
# checks out this workspace to a DETACHED HEAD snapshot of $BRANCH at adoption time.
# Nothing previously re-synced that snapshot to the branch's current remote tip on a
# later retrigger (archive review.json + `systemctl --user restart` the same
# veridian-supervisor@<task_id> unit) -- a review after additional commits were pushed
# to an already-adopted branch silently reviewed stale, pre-push content, computed
# below from whatever HEAD happened to already be checked out. Real, directly-observed
# incident: claude-control PR #123 got 4 consecutive `AUDIT: FAIL` comments reporting an
# IDENTICAL `git diff --stat` line despite 4 real fix commits landing on the branch in
# between. Real fix: always fetch and hard-reset this workspace to the real current
# remote tip of $BRANCH before computing anything the review depends on -- a plain
# `git fetch origin` (above) only refreshes the remote-tracking ref, it does not move
# this workspace's own checked-out HEAD. `checkout -f` is safe here: this is a
# dedicated per-task worktree (not the shared main clone), and any local
# modifications in it are, by construction, either already pushed (real, wanted) or
# stray leftovers from an earlier run (safe to discard -- the branch's real content on
# GitHub is the only source of truth a review should ever trust).
RESYNC_BEFORE_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
git fetch origin "$BRANCH"
git checkout -f "origin/$BRANCH" >> "$TASK_DIR/supervisor.log" 2>&1
RESYNC_AFTER_SHA=$(git rev-parse HEAD)
echo "Workspace resync (branch=$BRANCH): $RESYNC_BEFORE_SHA -> $RESYNC_AFTER_SHA" >> "$TASK_DIR/supervisor.log"
# --- WORKSPACE-RESYNC-BLOCK-END ---

DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')

# --- NO-OP-BRANCH-GUARD-BLOCK-START (UMR-20260813-215742-db64, see
# tests/test_supervisor_no_op_branch_guard.py) ---
# Real incident: a worker branch can legitimately have ZERO commits ahead of
# the base branch -- e.g. its real deliverable was already merged by a prior
# task (real example: PR #323 already landed the fix a later task's own
# branch had nothing left to add). That is a real, evidenced SUCCESS with no
# new deliverable, never a plumbing failure -- but before this fix, nothing
# here distinguished it: execution fell through unconditionally to `gh pr
# create` further down, which always fails a 0-commit branch with GraphQL
# "No commits between $DEFAULT_BRANCH and $BRANCH", `gh pr list --head` then
# finds nothing either (no PR was ever open), PR_URL stayed empty, and the
# PR-URL-RESOLUTION-GUARD-BLOCK below correctly refused to continue but for
# the WRONG reason -- it cannot tell this apart from a real gh/plumbing
# break, so it always hard-failed (exit 1). That false failure reaches
# umr_tasks via worker-exit-status-bridge.py's ExecStopPost hook (any
# checkpoint status it does not specifically recognize as this real no-op
# case is treated as a self-reported-negative outcome and bridged to
# status=failed), which pm-sentinel-tick.sh's own checks then escalate to
# status=killed and dispatch a fresh RCA for -- itself another task whose own
# branch also legitimately has zero commits ahead once the RCA concludes
# there is nothing left to fix: an unbounded paid-AI re-dispatch loop.
# Real, directly observed evidence (2026-08-13): 44 of 147 task dirs today
# died at exactly this path (30% of all runs); RCA for
# UMR-20260807-151622-15cd was dispatched twice; RCA for
# UMR-20260813-195852-aa85 was dispatched even though its real fix had
# already merged as PR #323.
#
# Real fix: deterministically distinguish the two cases with a real `git
# rev-list --count` BEFORE ever attempting `gh pr create` -- never infer it
# from gh's own failure text after the fact. This also means a genuine no-op
# never pays for the AI review call below at all (real cost saved on a path
# that was hitting 30% of all runs). Base is fetched fresh right here (never
# assumed to be origin/main) and resolved from the SAME refs/remotes/
# origin/HEAD symbolic ref DEFAULT_BRANCH above already used, so this can
# never drift from what the rest of this script already treats as the real
# base branch -- for veridian-scripts that real default branch is master.
git fetch origin "$DEFAULT_BRANCH" >> "$TASK_DIR/supervisor.log" 2>&1
BASE_SHA=$(git rev-parse "origin/$DEFAULT_BRANCH")
BRANCH_SHA=$(git rev-parse HEAD)
AHEAD_COUNT=$(git rev-list --count "origin/$DEFAULT_BRANCH..HEAD")
echo "No-op guard: branch=$BRANCH branch_sha=$BRANCH_SHA base=$DEFAULT_BRANCH base_sha=$BASE_SHA ahead_count=$AHEAD_COUNT" >> "$TASK_DIR/supervisor.log"

if [ "$AHEAD_COUNT" = "0" ]; then
  NO_OP_REASON="branch '$BRANCH' (sha $BRANCH_SHA) has 0 commits ahead of base '$DEFAULT_BRANCH' (sha $BASE_SHA) -- real, legitimate no-op completion (deliverable already merged by a prior task), not a plumbing failure. No PR was created."
  echo "NO-OP COMPLETION: $NO_OP_REASON" >> "$TASK_DIR/supervisor.log"
  # Real, structured evidence hand-off to worker-exit-status-bridge.py's own
  # ExecStopPost hook (see that script's _bridge_no_op_completion()) -- values
  # passed via real environment variables and read with os.environ.get(),
  # never interpolated directly into this Python source text (BRANCH/reason
  # can contain quotes/backticks; same safer convention the OCID-linkage
  # block further down this same file already established, PR #20).
  NO_OP_TASK_DIR="$TASK_DIR" NO_OP_BASE_SHA="$BASE_SHA" NO_OP_BRANCH_SHA="$BRANCH_SHA" \
  NO_OP_BASE_BRANCH="$DEFAULT_BRANCH" NO_OP_BRANCH="$BRANCH" NO_OP_REASON="$NO_OP_REASON" \
  python3 -c "
import json, os
outp = os.path.join(os.environ['NO_OP_TASK_DIR'], 'no_op.json')
with open(outp, 'w') as f:
    json.dump({
        'base_sha': os.environ['NO_OP_BASE_SHA'],
        'branch_sha': os.environ['NO_OP_BRANCH_SHA'],
        'base_branch': os.environ['NO_OP_BASE_BRANCH'],
        'branch': os.environ['NO_OP_BRANCH'],
        'reason': os.environ['NO_OP_REASON'],
    }, f, indent=2)
" >> "$TASK_DIR/supervisor.log" 2>&1
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status completed_no_change --note "$NO_OP_REASON"
  exit 0
fi
# --- NO-OP-BRANCH-GUARD-BLOCK-END ---

TIER=$(python3 /opt/veridian/scripts/risk-tier.py "$WORKSPACE" "origin/$DEFAULT_BRANCH" 2>>"$TASK_DIR/supervisor.log")
echo "Risk tier: $TIER" >> "$TASK_DIR/supervisor.log"

DIFF_STAT=$(git diff --stat "origin/$DEFAULT_BRANCH"...HEAD)
DIFF=$(git diff "origin/$DEFAULT_BRANCH"...HEAD | head -c 60000)

REVIEW_PROMPT="You are the VERIDIAN-DEV Superboss performing a real code review of another AI worker's completed task. Do not trust a self-report — review the actual diff below for architecture soundness, correctness, and security issues.

Before reviewing, read /opt/veridian/repos/claude-control/SUPERBOSS_DISPATCH_PROMPT.md in full using your Read tool — it is the authoritative, current standing-instructions file for this role (tiered trust model, the hard rule against .github/workflows/** changes, module routing, retry policy). Apply its rules to this review, not just the summary below.

Risk tier (deterministic, pre-classified — you cannot override this, only record your verdict): $TIER
- tier1: if you approve, this may be merged autonomously by this process.
- tier2: even if you approve, this requires human sign-off before merge — your approval only means 'ready for a human to merge', not permission to merge it yourself.

Task title: $TITLE

Diff stat:
$DIFF_STAT

Diff:
$DIFF

Write a file named review-verdict.json in the current directory (repo root) with exactly this shape and nothing else:
{\"verdict\": \"approve\" or \"reject\", \"tier\": \"$TIER\", \"summary\": \"one paragraph\", \"issues\": [\"list, empty if none\"]}
Do not modify any other file. Do not attempt to merge, push, or run git commands beyond reading the diff. If this diff touches .github/workflows/** in a way that would need to be pushed (per the hard rule in SUPERBOSS_DISPATCH_PROMPT.md), note that explicitly in issues."

SUPERVISOR_START_EPOCH=$(date -u +%s)
# 2026-08-01: routed through the shared usage-limit auto-resume wrapper (see
# claude-usage-limit-retry.sh header) -- if this review invocation hits the
# CLI's own 5-hour usage limit, it sleeps until the CLI-reported resume time
# and retries automatically instead of surfacing as an ordinary review
# failure. Same out-file/exit-code contract as a direct `claude -p ...` call.
source /opt/veridian/scripts/claude-usage-limit-retry.sh
run_claude_usage_limit_retry "$TASK_DIR/supervisor-result.json" "$TASK_DIR/supervisor.log" -- \
  -p "$REVIEW_PROMPT" --model sonnet --effort high --dangerously-skip-permissions --max-budget-usd "$SUPERVISOR_BUDGET_CAP_USD" --output-format json

# --- AI response logging (2026-07-24, governance item 15: ai_response_logging) ---
# Same fix as worker-entrypoint.sh's own MAIN_OUT block, applied to the
# supervisor's own second-pass review call -- previously the only AI
# invocation on this server with no response-text record at all (its own
# review-verdict.json/review.json only carry the STRUCTURED verdict, not the
# CLI's raw response text). Reuses the existing log-action CLI, no new
# function. Best-effort: a logging failure must never fail the review.
SUPERVISOR_RESPONSE_TEXT=$(python3 -c "
import json
try:
    with open('$TASK_DIR/supervisor-result.json') as f:
        d = json.load(f)
    print((d.get('result') or '')[:2000])
except Exception:
    pass
")
if [ -n "$SUPERVISOR_RESPONSE_TEXT" ]; then
  python3 /opt/veridian/scripts/superboss-register.py log-action \
    --source ai_response --medium claude_code_cli --campaign supervisor-entrypoint-review-invocation \
    --content "$SUPERVISOR_RESPONSE_TEXT" --term "$TASK_ID" --result "tier=$TIER" \
    >> "$TASK_DIR/supervisor.log" 2>&1 || true
fi

# Real-cost check (see worker-entrypoint.sh for why this reads the proxy's
# own log instead of the CLI's self-reported total_cost_usd).
SUPERVISOR_COST=$(python3 -c "
import json
from datetime import datetime
start_epoch = float('$SUPERVISOR_START_EPOCH')
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
")
echo "Real review cost: \$$SUPERVISOR_COST" >> "$TASK_DIR/supervisor.log"

if [ ! -f "$WORKSPACE/review-verdict.json" ]; then
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "supervisor failed to produce a review verdict — see supervisor.log"
  exit 1
fi

cp "$WORKSPACE/review-verdict.json" "$TASK_DIR/review.json"
rm -f "$WORKSPACE/review-verdict.json"
VERDICT=$(python3 -c "import json; print(json.load(open('$TASK_DIR/review.json'))['verdict'])")
SUMMARY=$(python3 -c "import json; print(json.load(open('$TASK_DIR/review.json'))['summary'])")

# Create the PR (record exists regardless of outcome — auditability)
PR_URL=$(gh pr create --repo "FChecklist/$REPO" --base "$DEFAULT_BRANCH" --head "$BRANCH" \
  --title "$TITLE" \
  --body "Automated worker task \`$TASK_ID\`. Risk tier: $TIER.

Superboss review: $SUMMARY" 2>>"$TASK_DIR/supervisor.log") || PR_URL=""

if [ -z "$PR_URL" ]; then
  # PR may already exist (idempotent retry) — find it
  PR_URL=$(gh pr list --repo "FChecklist/$REPO" --head "$BRANCH" --json url -q '.[0].url' 2>>"$TASK_DIR/supervisor.log")
fi
echo "$PR_URL" > "$TASK_DIR/pr_url.txt"

# --- PR-URL-RESOLUTION-GUARD-BLOCK-START (see tests/supervisor_pr_url_guard_test.sh) ---
# Real incident (2026-07-26, claude-control PR #84): when `gh pr create --head
# "$BRANCH"` fails (e.g. the worker made no commits relative to its own
# recorded branch, or pushed to a differently-named branch) AND the `gh pr
# list --head "$BRANCH"` fallback above also finds nothing (it only searches
# OPEN PRs, so this also fires whenever $BRANCH's real PR is already
# merged/closed), PR_URL is left as an empty string. Every `gh pr
# comment/view/merge "$PR_URL"` call further down this script was then passed
# that empty string — and `gh` does NOT error on an empty PR argument: it
# silently resolves to "the PR associated with whatever branch is currently
# checked out in $WORKSPACE" instead. In the real incident, $WORKSPACE
# happened to be checked out on claude-control PR #84's own branch (the
# worker had checked it out to review it), so the AUDIT comment meant for
# THIS task's (empty) diff was posted to PR #84, and — because this task's
# trivial empty-diff review was tier1+approve — `gh pr merge "" --merge`
# went on to for-real merge PR #84 via the autonomous path, with no genuine
# Superboss review of PR #84's own diff ever having run. Fail loudly and
# stop here instead: never let an unresolved PR_URL reach any gh pr call.
#
# UMR-20260813-215742-db64: the one real, legitimate reason `gh pr create`
# can fail here -- a genuine zero-commits-ahead no-op branch -- is now
# handled and exited on well before this point by the NO-OP-BRANCH-GUARD-
# BLOCK above, so every PR_URL reaching this point with AHEAD_COUNT > 0 is,
# by construction, real plumbing breakage (a real `gh` failure, a real
# permissions/rate-limit issue, or the PR #84-shaped bug this block's own
# original incident describes) and must stay a hard failure.
if [ -z "$PR_URL" ]; then
  echo "PR_URL resolution FAILED for branch '$BRANCH': both 'gh pr create' and 'gh pr list --head' (open PRs only) found nothing — refusing to continue, since every later gh pr call in this script would silently fall back to whatever PR matches \$WORKSPACE's currently checked-out branch instead of this task's own PR (real incident: PR #84, 2026-07-26)." >> "$TASK_DIR/supervisor.log"
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "supervisor could not resolve a real PR for branch '$BRANCH' (gh pr create failed, no existing open PR found for it) — refusing to proceed rather than risk operating on an unrelated PR via gh's empty-argument fallback. See supervisor.log."
  exit 1
fi
echo "PR_URL resolved: $PR_URL" >> "$TASK_DIR/supervisor.log"
# --- PR-URL-RESOLUTION-GUARD-BLOCK-END ---

# mandatory-audit-check.yml requires a structured "AUDIT: PASS/FAIL" PR
# comment (8 labeled fields, see src/lib/audit-protocol.ts) before ANY merge
# can pass required-status-checks — post it before attempting a tier1
# merge, not after, or the merge silently fails while this script still
# reports "completed"/"merged" (real incident: PR #416, 2026-07-18).
ISSUES_TEXT=$(python3 -c "import json; d=json.load(open('$TASK_DIR/review.json')); i=d.get('issues') or []; print('; '.join(i) if i else 'none')")
if [ "$VERDICT" = "approve" ]; then
  AUDIT_VERDICT_LINE="AUDIT: PASS"
  AUDIT_SEVERITY="none"
  AUDIT_CORRECTIVE="Not required -- no issues found in this review."
  AUDIT_REAUDIT="Not required -- approved as-is, no follow-up needed."
else
  AUDIT_VERDICT_LINE="AUDIT: FAIL"
  AUDIT_SEVERITY="medium"
  AUDIT_CORRECTIVE="Worker to address the findings listed above and resubmit."
  AUDIT_REAUDIT="Required after corrective changes are pushed."
fi
AUDIT_BODY="$AUDIT_VERDICT_LINE
Objective Understood: Reviewed worker task '$TITLE' (risk tier: $TIER) by reading the actual diff, not a self-report.
Standards Reviewed: AGENTS.md Operating Rule 7c structured audit protocol; risk-tier.py's deterministic tier classification.
Scope Confirmed: $DIFF_STAT
Evidence Recorded: $SUMMARY Issues found: $ISSUES_TEXT
Severity Classified: $AUDIT_SEVERITY
Verdict: $(echo "$VERDICT" | tr 'A-Z' 'a-z' | sed 's/approve/pass/;s/reject/fail/')
Corrective Action Owner: $AUDIT_CORRECTIVE
Re-Audit Scheduled: $AUDIT_REAUDIT"
gh pr comment "$PR_URL" --body "$AUDIT_BODY" >> "$TASK_DIR/supervisor.log" 2>&1

# --- AUDIT-CHECK-RERUN-BLOCK-START (see tests/supervisor_audit_rerun_test.sh) ---
# mandatory-audit-check.yml (compliance-tracker's own required CI gate, and
# any other repo using the same job name) triggers on pull_request
# opened/synchronize -- i.e. the instant this script's own `gh pr create`
# above fired, well before the AUDIT: PASS/FAIL comment just above existed
# (that required this script's own real LLM review call to finish first).
# It fails immediately with "No structured audit verdict found" every single
# time as a result -- not a one-off flake, a guaranteed race on every PR.
# Real incident: PR #560, audit-check failed at 23:39:15Z, a human had to
# manually re-run the same job by hand after the AUDIT comment existed.
# Fixed HERE rather than by adding an issue_comment trigger to
# compliance-tracker's own workflow file: this script's GitHub token lacks
# the `workflow` OAuth scope (SUPERBOSS_DISPATCH_PROMPT.md's hard rule --
# any push touching .github/workflows/** is silently rejected by GitHub
# itself), so a workflow-file change is not actually deployable with this
# token; re-triggering the existing, already-correct job from here is.
# Gated on the target repo actually having this workflow (`gh workflow
# list`) so this costs ~0 time for repos (e.g. claude-control itself) that
# don't use it -- no blind retry-poll loop on every tier1 merge everywhere.
if gh workflow list --repo "FChecklist/$REPO" --json name 2>>"$TASK_DIR/supervisor.log" \
     | grep -q '"Mandatory Audit Check"'; then
  AUDIT_HEAD_SHA=$(gh pr view "$PR_URL" --json headRefOid -q .headRefOid 2>>"$TASK_DIR/supervisor.log")
  AUDIT_RUN_ID=""
  AUDIT_RUN_JSON="[]"
  for _ in $(seq 1 8); do
    AUDIT_RUN_JSON=$(gh run list --repo "FChecklist/$REPO" --branch "$BRANCH" \
      --workflow mandatory-audit-check.yml --json databaseId,status,conclusion,headSha \
      --limit 5 2>>"$TASK_DIR/supervisor.log") || AUDIT_RUN_JSON="[]"
    # Data passed via env vars, never interpolated into the python source
    # string itself -- the JSON payload contains double quotes, which would
    # otherwise break out of this bash double-quoted -c argument.
    AUDIT_RUN_ID=$(AUDIT_RUN_JSON="$AUDIT_RUN_JSON" AUDIT_HEAD_SHA="$AUDIT_HEAD_SHA" python3 -c "
import json, os
runs = json.loads(os.environ.get('AUDIT_RUN_JSON') or '[]')
head_sha = os.environ.get('AUDIT_HEAD_SHA', '')
for r in runs:
    if r.get('headSha') == head_sha and r.get('status') == 'completed':
        print(r.get('databaseId') or '')
        break
" 2>>"$TASK_DIR/supervisor.log")
    [ -n "$AUDIT_RUN_ID" ] && break
    sleep 10
  done
  if [ -n "$AUDIT_RUN_ID" ]; then
    AUDIT_RUN_CONCLUSION=$(AUDIT_RUN_JSON="$AUDIT_RUN_JSON" AUDIT_RUN_ID="$AUDIT_RUN_ID" python3 -c "
import json, os
runs = json.loads(os.environ.get('AUDIT_RUN_JSON') or '[]')
run_id = os.environ.get('AUDIT_RUN_ID', '')
for r in runs:
    if str(r.get('databaseId')) == run_id:
        print(r.get('conclusion') or '')
        break
" 2>>"$TASK_DIR/supervisor.log")
    if [ "$AUDIT_RUN_CONCLUSION" = "failure" ]; then
      echo "Re-running mandatory-audit-check.yml run $AUDIT_RUN_ID for $PR_URL (CI-timing race: it ran before this script's own AUDIT comment existed)" >> "$TASK_DIR/supervisor.log"
      gh run rerun "$AUDIT_RUN_ID" --repo "FChecklist/$REPO" --failed >> "$TASK_DIR/supervisor.log" 2>&1 || true
    fi
  fi
fi
# --- AUDIT-CHECK-RERUN-BLOCK-END ---

# Master/Supervisor pilot: if this task was dispatched through a module
# queue (module-queue-dispatcher.py), a module_scope.yaml sidecar declares
# its module + files_allowed. Deterministic scope-check.py enforcement --
# same trust-boundary posture as risk-tier.py, the AI reviewer's approve
# verdict cannot override a real scope violation. Tasks with no sidecar
# (the general gap-queue, pre-pilot) are unaffected -- additive only.
SCOPE_OK=1
if [ -f "$TASK_DIR/module_scope.yaml" ]; then
  MODULE=$(python3 -c "import yaml; print(yaml.safe_load(open('$TASK_DIR/module_scope.yaml'))['module'])")
  FILES_ALLOWED_CSV=$(python3 -c "import yaml; print(','.join(yaml.safe_load(open('$TASK_DIR/module_scope.yaml')).get('files_allowed') or []))")
  if ! python3 /opt/veridian/scripts/scope-check.py "$WORKSPACE" "origin/$DEFAULT_BRANCH" "$MODULE" "$FILES_ALLOWED_CSV" >> "$TASK_DIR/supervisor.log" 2>&1; then
    SCOPE_OK=0
  fi
fi

# --- HOLD-FOR-OWNER-SIGNOFF-BLOCK-START (see tests/hold_for_signoff_test.py) ---
# Real, machine-readable hold-for-signoff (2026-07-26, root-caused against the
# PR563 incident): that task's dispatch prompt carried an explicit prose
# instruction ("must be held for Owner sign-off, do not merge under any
# circumstance"), but nothing in this pipeline ever read prompt-level prose --
# only risk-tier.py's deterministic tier plus the Superboss's AI verdict --
# so it auto-merged anyway (as a separate, near-empty PR); a human had to
# notice and manually hold it after the fact. HOLD_FOR_OWNER_SIGNOFF was
# originally checked FIRST specifically so no tier/verdict/scope combination
# could silently override it.
#
# --- AUTONOMOUS-FULL-APPROVAL-2026-07-31 (Owner directive, quoted verbatim in
# AGENTS.md Rule 12) --- Owner (raajat.agarwal@gmail.com) explicitly
# instructed the server to operate independently of any laptop session, with
# approval decisions made on the Owner's behalf with "full autonomy, no
# exceptions" -- including HOLD_FOR_OWNER_SIGNOFF-flagged tasks and tier2
# (security/DB/billing/deletion-sensitive) tasks that were previously always
# held for a human regardless of verdict. This change removes ONLY the
# redundant human-confirmation step ON TOP OF an already-passing automated
# review -- it does NOT weaken the review itself: a REJECTED verdict (the
# final `else` branch below) still blocks, and a real SCOPE VIOLATION
# (file-ownership, scope-check.py) still blocks regardless of tier or
# hold-flag, exactly as it already did for tier1 before this change. Both
# HOLD_FOR_OWNER_SIGNOFF and tier2 tasks that pass verdict+scope now take the
# SAME merge path tier1 always used. The Owner is still notified afterward
# for transparency on what would previously have been held -- just not asked
# to act. To revert to the pre-2026-07-31 behavior, restore the prior
# if/elif chain from git history (see the PR that introduced this comment
# block) and remove Rule 12 from AGENTS.md.
echo "Merge-decision inputs: HOLD_FOR_OWNER_SIGNOFF=$HOLD_FOR_OWNER_SIGNOFF VERDICT=$VERDICT TIER=$TIER SCOPE_OK=$SCOPE_OK PR_URL=$PR_URL" >> "$TASK_DIR/supervisor.log"
if [ "$VERDICT" = "approve" ] && [ "$SCOPE_OK" = "1" ]; then
  # CI must actually go green (including audit-check, now satisfied above)
  # before a merge can succeed — poll briefly rather than firing the merge
  # immediately against checks that haven't finished running yet.
  for _ in $(seq 1 20); do
    STATE=$(gh pr view "$PR_URL" --json mergeStateStatus -q .mergeStateStatus 2>>"$TASK_DIR/supervisor.log")
    [ "$STATE" = "BLOCKED" ] || [ "$STATE" = "BEHIND" ] || break
    sleep 15
  done
  # --- MERGE-DETECTION-BLOCK-START (see tests/supervisor_merge_detection_test.sh) ---
  # Merge and branch-deletion are two independent calls, not one (real
  # incidents: PR #10, #13, #14, 2026-07-24 -- `gh pr merge --delete-branch`
  # merged successfully via the API but then exited non-zero because its
  # local git branch-delete step failed with "'master' is already used by
  # worktree at '/opt/veridian/repos/claude-control'" (this supervisor runs
  # from that same clone). The combined exit code made a real merge look
  # like a failure. Success is now judged solely by a fresh `gh pr view
  # --json state,mergedAt` call, never by any shell command's exit code.
  gh pr merge "$PR_URL" --merge >> "$TASK_DIR/supervisor.log" 2>&1
  PR_STATE=$(gh pr view "$PR_URL" --json state -q .state 2>>"$TASK_DIR/supervisor.log")
  MERGED_AT=$(gh pr view "$PR_URL" --json mergedAt -q .mergedAt 2>>"$TASK_DIR/supervisor.log")
  if [ "$PR_STATE" = "MERGED" ] && [ -n "$MERGED_AT" ] && [ "$MERGED_AT" != "null" ]; then
    # Branch deletion is best-effort and purely cosmetic -- use the GitHub
    # API directly (no local git object involved) so it can never collide
    # with a sibling worktree, and its failure must never affect the
    # already-confirmed merge outcome above.
    gh api -X DELETE "repos/FChecklist/$REPO/git/refs/heads/$BRANCH" >> "$TASK_DIR/supervisor.log" 2>&1 || true
    # Deployment logging (governance item 13, 2026-07-23): log the real merge
    # as an action via the register's existing generic log-action CLI --
    # deliberately NOT a new log_deployment() function, since log-action
    # already does exactly this INSERT and STANDING_DIRECTIVE.yaml's
    # zero_duplication_mandatory forbids a second parallel write path for
    # the same thing (see run-logged.sh for the same existing-CLI-reuse
    # pattern). Best-effort: a logging failure must never block the merge
    # result already recorded above.
    MERGE_COMMIT_SHA=$(gh pr view "$PR_URL" --json mergeCommit -q .mergeCommit.oid 2>>"$TASK_DIR/supervisor.log")
    timeout 10 python3 /opt/veridian/scripts/superboss-register.py log-action \
      --work-item-id "$TASK_ID" --source deployment --medium github-merge \
      --content "$REPO" --term "${MERGE_COMMIT_SHA:-unknown}" --result merged \
      >> "$TASK_DIR/supervisor.log" 2>&1 || true
    # OCID-068 real requirement addendum (UMR-20260804-170055-a069, Owner
    # real-time implementation override on the standing hard-rule-7 lock):
    # structured OCID -> UMR -> PR -> commit linkage, recorded at this real,
    # canonical merge chokepoint -- only after the merge above is
    # independently confirmed (never a self-report), same discipline the
    # surrounding merge-detection block already established. Best-effort,
    # `|| true`, same convention as log-action/backfill_phase_self_report.py
    # immediately above/below -- must never affect the already-confirmed
    # merge result. ocid_number is derived from the branch name via the same
    # "ocid-NNN"/"ocidNNN" naming convention every real OCID branch this
    # session used; umr_id is looked up by real task_identity match against
    # umr_tasks -- many real tasks (adopted branches, direct veridian-task.py
    # create calls) have no such row at all (a real, separately-documented
    # gap, see ai-os/VERIDIAN_OCID_068_..._OWNER_REVIEW_PACKAGE_2026-08-04.md),
    # so this silently records nothing rather than inventing a fake umr_id --
    # ocid_artifact_links.umr_id is a real NOT NULL foreign key, never
    # fabricated to satisfy it.
    #
    # Real fix (independent review, PR #20): the real values below (BRANCH
    # especially -- sourced from task.yaml's branch field, and git branch
    # names permit ', ", and backtick characters) are passed via real
    # environment variables and read with os.environ.get(), never
    # interpolated directly into the Python source text -- matching the
    # safer pattern this same file already established for
    # AUDIT_RUN_JSON/AUDIT_HEAD_SHA above. Raw bash-substitution into a
    # Python string literal (the prior version of this block) is a real
    # code-injection risk in a script that holds this pipeline's actual
    # merge/DB-write authority.
    OCID_LINK_BRANCH="$BRANCH" OCID_LINK_TASK_ID="$TASK_ID" OCID_LINK_REPO="$REPO" \
    OCID_LINK_PR_URL="$PR_URL" OCID_LINK_MERGE_SHA="$MERGE_COMMIT_SHA" \
    timeout 10 python3 -c "
import os, re, importlib.util
_spec = importlib.util.spec_from_file_location('superboss_register_supervisor', '/opt/veridian/scripts/superboss-register.py')
sbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbr)
branch = os.environ.get('OCID_LINK_BRANCH', '')
task_id = os.environ.get('OCID_LINK_TASK_ID', '')
repo = os.environ.get('OCID_LINK_REPO', '')
pr_url = os.environ.get('OCID_LINK_PR_URL', '')
merge_sha = os.environ.get('OCID_LINK_MERGE_SHA', '')
m = re.search(r'ocid-?0*([0-9]+)', branch, re.IGNORECASE)
if m:
    ocid_number = f'OCID-{int(m.group(1)):03d}'
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    rows = sbr.query_umr_tasks(conn, task_identity=task_id, limit=1)
    if rows:
        sbr.insert_ocid_artifact_link(
            conn, ocid_number=ocid_number, umr_id=rows[0]['umr_id'], repo=repo,
            pr_number=int(pr_url.rstrip('/').rsplit('/', 1)[-1]),
            commit_sha=merge_sha or None, link_kind='merge',
        )
        conn.commit()
    conn.close()
" >> "$TASK_DIR/supervisor.log" 2>&1 || true
    # Real conflict resolution (merge of the recovered pre-PR20 local hotfix
    # and PR #20's own change to this same checkpoint call -- see PR #21's
    # own description for the full real conflict record): both real
    # improvements kept together. The OCID-linkage wiring immediately above
    # is PR #20's own real addition; the more detailed note text below
    # (citing the real tier/hold_for_owner_signoff values and the actual
    # Owner directive this autonomous-merge authority traces back to) is the
    # recovered local hotfix's own real improvement over PR #20's plainer
    # "tier1, Superboss-approved, merged autonomously" text -- kept in favor
    # of discarding it, since it is real, more informative, and does not
    # conflict with anything the OCID-linkage wiring needs.
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status completed --note "Superboss-approved (tier=$TIER, hold_for_owner_signoff=$HOLD_FOR_OWNER_SIGNOFF), merged autonomously per Owner's 2026-07-31 full-approval-autonomy directive: $PR_URL"
    # Root cause 1 (real incidents: VERIDIAN_ARCHITECTURE_V2 phase_1/PR #559,
    # phase_2/PR #560 -- both merged for real, neither worker updated its own
    # phase-plan entry, both needed a human to hand-edit the YAML afterward,
    # permanently blocking auto_phase_continuation.py's is_phase_done() from
    # ever seeing them as done). Making this SOFTWARE's job instead of relying
    # on worker discipline: now that a real MERGED state is independently
    # confirmed above (never a self-report), backfill_phase_self_report.py
    # writes status/completed_by_task/evidence into the correct phase-plan
    # entry itself if -- and only if -- the worker didn't already do it
    # correctly (idempotent, see its own module docstring). Best-effort: this
    # must never affect the merge result already recorded above, and it no-ops
    # silently for the (large) majority of tasks that aren't a phase-plan
    # dispatch at all (no phase reference resolvable).
    timeout 120 python3 /opt/veridian/scripts/backfill_phase_self_report.py --task-id "$TASK_ID" >> "$TASK_DIR/supervisor.log" 2>&1 || true
    # Transparency notification -- only for what would previously have been
    # held (hold-flag or tier2), so tier1's already-normal autonomous-merge
    # volume doesn't suddenly start spamming a channel that never expected
    # it before. Informational only; Owner is not asked to act.
    if [ "$HOLD_FOR_OWNER_SIGNOFF" = "True" ] || [ "$TIER" = "tier2" ]; then
      INFO_BODY="Hi Rajat,

A task on your Veridian server merged automatically under your standing
full-approval-autonomy directive (2026-07-31) -- it previously would have
been held for your sign-off (tier=$TIER, hold_for_owner_signoff=$HOLD_FOR_OWNER_SIGNOFF),
but per your instruction it now merges without waiting for you.

Task: $TASK_ID
Pull request: $PR_URL

This is informational only -- no action needed. See AGENTS.md Rule 12 for
how to revert this standing directive if you ever want the hold back.

- Veridian supervisor"
      python3 /opt/veridian/scripts/notify-owner.py --subject "Veridian: task merged autonomously (previously would have needed your sign-off)" --body "$INFO_BODY" --dedupe-key "auto-merged-formerly-held-$TASK_ID" >> "$TASK_DIR/supervisor.log" 2>&1 || true
    fi
  else
    python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "Superboss-approved (tier=$TIER), but the merge itself FAILED (gh pr view confirms state=$PR_STATE, mergedAt=$MERGED_AT; see supervisor.log) — needs manual attention, NOT actually merged: $PR_URL"
  fi
  # --- MERGE-DETECTION-BLOCK-END ---
elif [ "$VERDICT" = "approve" ] && [ "$SCOPE_OK" = "0" ]; then
  gh pr comment "$PR_URL" --body "Superboss review: APPROVED, but BLOCKED by scope-check.py -- this diff touches files outside its declared module ownership. See supervisor.log for the exact violation. Not merged. (Scope enforcement is unaffected by the 2026-07-31 full-approval-autonomy directive -- it blocks regardless of tier or hold-flag.)" >> "$TASK_DIR/supervisor.log" 2>&1
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "Superboss-approved (tier=$TIER), but SCOPE VIOLATION (file-ownership) blocked the merge — see supervisor.log: $PR_URL"
else
  python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status blocked --note "Superboss rejected: $PR_URL — see review.json for issues"
fi
# --- HOLD-FOR-OWNER-SIGNOFF-BLOCK-END ---
