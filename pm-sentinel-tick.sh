#!/usr/bin/env bash
# pm-sentinel-tick.sh -- ONE integrated deterministic server-native PM tick.
#
# GOVERNING CHAIN: P1 UMR-20260806-171945-5767 -> UMR-20260813-084321-2962
# (server-native sentinel build) -> this file collapses THREE previously
# separate policy pieces into one script, per the 2026-08-13 addendum
# (UMR-20260813-102459-10c3):
#   1. UMR-20260813-084321-2962 -- the base sentinel tick (3 checks below),
#      originally shipped as PR #131 with 5 real Tier-1 audit-reject issues
#      (UMR-20260813-101452-bd10) that were NEVER actually fixed on that PR
#      (verified: PR #131 still has exactly 1 commit as of this integration).
#      All 5 are fixed HERE (see "AUDIT-REJECT FIXES" below).
#   2. UMR-20260813-091633-8b6a -- financial-only Owner-decision escalation
#      policy (is_financial_decision / escalate_financial_decision below).
#      Real finding: this policy WAS already implemented directly on the live
#      server (/opt/veridian/scripts/pm-sentinel-tick.sh, 429 lines) but was
#      never committed to git (PRs #135/#139 for this UMR were both doc-only
#      STATUS_REPORT.md edits -- verified via `gh pr view --json files`, zero
#      code changes in either). This integration is what actually lands that
#      real live-only code into version control.
#   3. UMR-20260813-092654-326b -- hierarchy / single-gateway / zero-dup /
#      dynamic-scope / standardized boolean-table report format. Real
#      finding: the dispatched task for this UMR (task-20260813-095623)
#      never started real work (task.yaml status=blocked, zero files
#      modified, zero PR) before being reconciled to status=killed -- none of
#      this scope existed anywhere before this integration.
#
# REUSE ONLY -- this script deliberately does NOT implement its own dispatch
# path, its own resource cap, or its own stop-work gate. Every real dispatch
# below goes through the EXISTING single front door, dispatch-owner-task.sh
# --no-relay, which itself submits through resource_governor.py's real
# submit()/dispatch_one() -- the same tier/concurrency-cap/EMERGENCY_STOP/
# standing-stop-work-order gate every other real dispatch on this box already
# goes through (SINGLE GATEWAY, NO BYPASS -- 326b point 2). This script never
# writes to umr_tasks directly, never calls systemctl to spawn a worker
# itself, never runs `gh pr merge` itself, and never bypasses that gate.
#
# ZERO DUPLICATION, NO BYPASS (326b point 3) -- two independent, real,
# non-conflicting layers, neither reimplementing the other:
#   (a) this script's own is_in_flight() -- narrow, per-(gap_type,target_umr)
#       de-dup for the exact targets THIS script itself considers dispatching
#       this tick (re-verified live via --query-umr every call, never trusted
#       from a stale cache);
#   (b) resource_governor.py's own downstream duplicate-PR guard (Stage
#       4/5/6) -- the canonical, system-wide enforcement point, independently
#       proven live twice on this exact UMR chain: UMR-20260813-101609-9a69
#       and UMR-20260813-101452-bd10 BOTH real-dispatched and BOTH came back
#       status=rejected_duplicate because a real PR already existed for that
#       task_identity.
# This script never reimplements (b); it only adds (a) as a narrower,
# earlier-and-cheaper check ahead of it.
#
# What one tick does (same 3 checks the laptop-side hourly PM sentinel does,
# plus 326b's blocked-row check and dynamic addenda-chain discovery):
#   1. Tracked-chain status: python3 "$SUPERBOSS_REGISTER_PY"
#      show-owner-priority-state gives the real, live set of governing UMR
#      chains still active/pending; each chain's governing UMR is re-queried
#      live via resource_governor.py --query-umr --umr-id (never assumed from
#      the priority-sequence row alone). Per 326b point 1 (dynamic scope,
#      discovered each tick, not a fixed hardcoded UMR list): each chain head
#      is also searched via --query-umr --search for real addendum rows
#      whose own prompt text cites it as a governing chain, so a fresh
#      amendment UMR is picked up the same tick it is submitted, not only
#      after it is manually added to owner_priority_sequence.
#   2a. killed-status rows needing RCA (system-wide, bounded).
#   2b. Exit-write-back-bug cross-check on status='running' rows -- a running
#       row can lie (known bug class). Real systemctl --user show +
#       journalctl cross-check against the row's own real unit_name.
#   2c. Blocked-task detection (326b addition -- REUSE, not reimplementation):
#       a task.yaml-level status='blocked' (e.g. task-20260813-095623, the
#       326b amendment's own dispatched task, which sat blocked with zero
#       progress before being reconciled) is NOT a real umr_tasks.status
#       value (confirmed via the table's own CHECK constraint: status IN
#       ('queued','dispatched','running','completed','completed_unmerged',
#       'failed','rejected_duplicate','sigterm_sent','killed') -- no
#       'blocked'). The real, already-existing, already-live reconciliation
#       for this is resource_governor.py's own scan_stuck_tasks(), called
#       every real tick inside run_tick()'s own orchestrator maintenance
#       (confirmed via grep: results["stuck_task_actions"] =
#       scan_stuck_tasks(now=now) inside run_tick(), which the existing
#       veridian-dispatch-tick.timer / veridian-cron-dispatch-tick.timer
#       already run on their own live cadence) -- it reconciles a real
#       stuck/blocked task to status='killed' on its own, which Check 2a
#       above already RCA-dispatches. Adding a second, parallel
#       blocked-status query here would be dead code (it could never match a
#       real row) and would duplicate scan_stuck_tasks() -- so this
#       integration deliberately does NOT add one; Check 2a already covers
#       the real gap once scan_stuck_tasks() reconciles it.
#   3. Real PR audit: for status='completed_unmerged' rows, `gh pr view` for
#      real mergeable/review/check state PLUS the real posted comments (gh
#      api .../comments), never just a CI badge.
#
# On a genuine gap, dispatches (capped at MAX_DISPATCHES_PER_TICK=5 new
# dispatches per tick) via dispatch-owner-task.sh --no-relay, citing the real
# target UMR id and real evidence gathered above, INTO THE REAL REPO the
# target UMR's own inputs_json.repo names (AUDIT-REJECT FIX #5 below) --
# never a hardcoded module name.
#
# DECISION AUTHORITY (8b6a + 326b point 4) -- never consult the Owner except
# a genuine FINANCIAL decision (spending money, a new financial commitment, a
# payment, or a pricing/billing change); every other gap is decided and
# dispatched autonomously, citing real evidence. See is_financial_decision()/
# escalate_financial_decision() below.
#
# REPORT FORMAT (326b point 5) -- standardized boolean table, one row per
# real UMR this tick found a gap for. See emit_report_row() below; written to
# REPORT_FILE (JSON Lines, one real object per line) and mirrored as
# Prometheus textfile-collector gauges to METRICS_FILE, so Grafana/Prometheus
# (both confirmed real+live on this box: Prometheus v3.13.2 at
# 127.0.0.1:9090, Grafana v13.1.3 at 127.0.0.1:3000, node_exporter at
# 127.0.0.1:9100, verified via real /api/health, /api/v1/status/buildinfo,
# /metrics responses on 2026-08-13) can scrape this tick's own real output as
# one more real target, instead of a second, separate reporting path. Honest
# caveat: node_exporter's real --collector.textfile.directory flag and
# Prometheus's real scrape-config file are host-level configuration outside
# this task's own filesystem access (confirmed: no /var/lib/node_exporter or
# /var/lib/prometheus/node-exporter path reachable from this workspace) --
# METRICS_FILE is written to a real, fixed path regardless so that the one
# remaining step (pointing node_exporter's textfile collector at it, or
# adding a Prometheus file_sd target) is a narrow, disclosed, one-line
# operational follow-up, not fabricated as already wired.
#
# AUDIT-REJECT FIXES (UMR-20260813-101452-bd10, 5 real cited issues against
# PR #131 -- all 5 fixed in this file):
#   1. dispatch_gap() used to call `./dispatch-owner-task.sh`, which does not
#      exist in this repo's git tree/history (only resource_governor.py and
#      superboss-register.py are tracked here; dispatch-owner-task.sh is
#      live-server-only) -- fixed via DISPATCH_OWNER_TASK_SH resolution
#      (env override -> co-located in $SCRIPT_DIR -> canonical live path
#      /opt/veridian/scripts/dispatch-owner-task.sh) below.
#   2. dispatch_gap() used to swallow a real dispatch-owner-task.sh failure
#      and the whole script still `exit 0`'d regardless -- fixed via
#      TICK_FAILURES tracking and a real non-zero exit at the bottom of this
#      script whenever a real dispatch attempt failed this tick.
#   3. scripts/test_pm_sentinel_tick.py used to fail when actually run
#      (AssertionError on the expected DISPATCHED rca string) -- root cause
#      was fix #1 (dispatch-owner-task.sh unresolvable from a git checkout);
#      fixed as a direct consequence of fix #1, real test evidence in
#      PROGRESS.md.
#   4. The merge-fresh-PASS-PR gap path used to dispatch a worker task whose
#      own instruction was to blindly trust a possibly-stale reviewDecision
#      and call `gh pr merge` directly on that trust alone -- removed. Every
#      completed_unmerged PR, approved or not, now only ever gets a real
#      independent re-audit dispatch (audit: target key) that re-verifies
#      state live before any merge decision -- never a blind-merge
#      instruction.
#   5. Every dispatched follow-up used to hardcode module "compliance-tracker"
#      regardless of the real repo tied to the governing UMR -- fixed via
#      target_repo_of() below, which derives the real repo from the target
#      row's own inputs_json.repo (falling back to compliance-tracker, same
#      default dispatch-owner-task.sh itself already uses, only when a row
#      genuinely has no repo field).
#
# TOKEN USAGE -- this entire tick makes ZERO calls to any LLM (no anthropic/
# litellm/aider/claude invocation anywhere in this file, verified by grep of
# this file for those tokens: zero real call sites, only this doc comment).
# Every decision point below is a real bash/python conditional over real
# queried state (resource_governor.py --query-umr, systemctl show, gh pr
# view --json, journalctl, umr_tasks.inputs_json fields) -- never an AI
# narration of a go/no-go decision. The only tokens this integration's own
# governing UMR chain has ever spent are the AI-agent WORKER dispatches that
# build/fix/report on it (see PROGRESS.md for the real measured before/after
# token comparison) -- unchanged from before, and only for genuine
# novel-failure/implementation judgment calls, never for the tick's own
# go/no-go decision.
#
# Real testability seams (env overrides, same convention every other real
# script in this codebase already uses):
#   SUPERBOSS_REGISTER_DB    -- point every subprocess at a real sqlite COPY
#                                instead of the live DB (resource_governor.py
#                                / superboss-register.py's own
#                                resolve_superboss_db_path() already reads
#                                this).
#   PM_SENTINEL_STATE_FILE   -- override the in-flight dedup state file path.
#   PM_SENTINEL_MAX_DISPATCH -- override the per-tick dispatch cap (default 5).
#   PM_SENTINEL_NOTIFY_OWNER_SCRIPT -- override the notify-owner.py path used
#                                by escalate_financial_decision() below.
#   DISPATCH_OWNER_TASK_SH   -- override the real dispatch-owner-task.sh path
#                                (fix #1 above).
#   PM_SENTINEL_REPORT_FILE  -- override the boolean-table JSONL report path.
#   PM_SENTINEL_METRICS_FILE -- override the Prometheus textfile-collector
#                                output path.
#   VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS="" -- disable the standing
#                                stop-work gate for a test run (same env var
#                                resource_governor.py itself already
#                                documents).
#
# Never fabricates completion: this script never calls mark-umr-terminal,
# never writes 'completed' anywhere -- it only reads real state and dispatches
# real follow-up work through the real existing front door. Never touches
# resource_governor.py / superboss-register.py / task-gateway.py /
# resource_governor_tick_loop.sh -- reads/calls their real CLIs only.
#
# Wired as a systemd --user timer (see systemd/veridian-pm-sentinel-tick.
# service + .timer in this same directory) firing hourly, modeled on the
# existing veridian-cron-dispatch-tick.service/.timer pattern already live on
# this box (same run-logged.sh wrapper, same shared EMERGENCY_STOP
# ConditionPathExists gate).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GH_ORG="${VERIDIAN_GH_ORG:-FChecklist}"
MAX_DISPATCHES_PER_TICK="${PM_SENTINEL_MAX_DISPATCH:-5}"
STATE_FILE="${PM_SENTINEL_STATE_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-inflight.json}"
NOTIFY_OWNER_SCRIPT="${PM_SENTINEL_NOTIFY_OWNER_SCRIPT:-notify-owner.py}"
REPORT_FILE="${PM_SENTINEL_REPORT_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-tick-report.jsonl}"
METRICS_FILE="${PM_SENTINEL_METRICS_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-tick.prom}"
DISPATCH_COUNT=0
TICK_FAILURES=0

# AUDIT-REJECT FIX #1: real dispatch-owner-task.sh resolution. Production
# deployment co-locates this script with dispatch-owner-task.sh in
# /opt/veridian/scripts, so $SCRIPT_DIR/dispatch-owner-task.sh already
# resolves there. A git checkout of THIS repo (dispatch-owner-task.sh is
# real but deliberately not tracked here -- live-server-only file) does not
# have it co-located, so this also falls back to the one real, canonical,
# live path -- which is always the same server this script itself runs on.
if [ -n "${DISPATCH_OWNER_TASK_SH:-}" ]; then
  : # explicit override wins, used as-is
elif [ -x "$SCRIPT_DIR/dispatch-owner-task.sh" ]; then
  DISPATCH_OWNER_TASK_SH="$SCRIPT_DIR/dispatch-owner-task.sh"
elif [ -x "/opt/veridian/scripts/dispatch-owner-task.sh" ]; then
  DISPATCH_OWNER_TASK_SH="/opt/veridian/scripts/dispatch-owner-task.sh"
else
  echo "FATAL: dispatch-owner-task.sh not found (checked \$DISPATCH_OWNER_TASK_SH, \$SCRIPT_DIR, /opt/veridian/scripts) -- cannot dispatch any real gap this tick" >&2
  exit 1
fi

# Same real resolution principle as fix #1 above, extended to
# resource_governor.py/superboss-register.py themselves: both ARE tracked in
# this git repo (unlike dispatch-owner-task.sh), but a git checkout's copy
# can be genuinely stale relative to the live-deployed copy (a real,
# separately-tracked live-deployment-sync gap on this box, not this
# integration's scope to fully close) -- confirmed live during this
# integration's own testing: a checkout copy lacking --umr-id support caused
# is_in_flight() to silently get empty query results and re-dispatch a
# byte-identical prompt the real content-duplicate guard then (correctly)
# refused. Resolving to the live, current copy by default (same env-override
# -> co-located -> canonical-live-path order) makes this script's own
# behavior match dispatch-owner-task.sh's real behavior, which already only
# ever calls the live copy (it is never checked out anywhere else).
if [ -n "${RESOURCE_GOVERNOR_PY:-}" ]; then
  :
elif [ -f "/opt/veridian/scripts/resource_governor.py" ]; then
  RESOURCE_GOVERNOR_PY="/opt/veridian/scripts/resource_governor.py"
else
  RESOURCE_GOVERNOR_PY="$SCRIPT_DIR/resource_governor.py"
fi
if [ -n "${SUPERBOSS_REGISTER_PY:-}" ]; then
  :
elif [ -f "/opt/veridian/scripts/superboss-register.py" ]; then
  SUPERBOSS_REGISTER_PY="/opt/veridian/scripts/superboss-register.py"
else
  SUPERBOSS_REGISTER_PY="$SCRIPT_DIR/superboss-register.py"
fi

# Real, deliberately narrow keyword test backing is_financial_decision()
# below -- see the "DECISION AUTHORITY" header comment above for the real
# Owner-issued policy this implements.
FINANCIAL_KEYWORDS='(^|[^A-Za-z])(spend(ing)?|payment|invoic(e|ing)|pricing|billing|subscription (cost|fee|upgrade)|refund|purchas(e|ing)|financial commitment|budget approval|credit card|price increase|contract cost)([^A-Za-z]|$)'

mkdir -p "$(dirname "$STATE_FILE")" "$(dirname "$REPORT_FILE")" "$(dirname "$METRICS_FILE")"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

TICK_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=== pm-sentinel-tick $TICK_TS ==="

# ---------------------------------------------------------------------------
# small python3 helpers -- same "shell out to python3 -c for JSON" idiom
# dispatch-owner-task.sh already uses throughout, no new parsing dependency.
# ---------------------------------------------------------------------------

py_field() {
  # py_field <json-on-stdin> <python-expression-over-d>
  python3 -c "
import json, sys
d = json.load(sys.stdin)
try:
    v = $1
except Exception:
    v = ''
print(v if v is not None else '')
"
}

# target_repo_of <json-row-on-stdin> -- AUDIT-REJECT FIX #5: derive the real
# repo from the target row's own inputs_json.repo, never a hardcoded module.
# Falls back to compliance-tracker only when the row genuinely has no repo
# field -- the same default dispatch-owner-task.sh itself already uses.
target_repo_of() {
  python3 -c "
import json, sys
d = json.load(sys.stdin)
inputs = d.get('inputs_json') or {}
if isinstance(inputs, str):
    try:
        inputs = json.loads(inputs)
    except Exception:
        inputs = {}
print(inputs.get('repo') or 'compliance-tracker')
"
}

state_get_dispatched_umr() {
  # state_get_dispatched_umr <target_key>
  python3 -c "
import json, sys
try:
    with open('$STATE_FILE') as f:
        state = json.load(f)
except Exception:
    state = {}
entry = state.get('$1')
print(entry.get('dispatched_umr', '') if entry else '')
"
}

state_record() {
  # state_record <target_key> <dispatched_umr>
  python3 -c "
import json, sys
from datetime import datetime, timezone
path = '$STATE_FILE'
try:
    with open(path) as f:
        state = json.load(f)
except Exception:
    state = {}
state['$1'] = {'dispatched_umr': '$2', 'ts': datetime.now(timezone.utc).isoformat()}
with open(path, 'w') as f:
    json.dump(state, f, indent=2)
"
}

# is_in_flight <target_key> -- returns 0 (true) if a real, still-live
# (queued/dispatched/running) dispatch already exists for this target;
# re-verifies liveness against resource_governor.py --query-umr every call,
# never trusts the state file's own cached claim. ZERO-DUPLICATION layer (a)
# -- see header comment.
is_in_flight() {
  local target_key="$1"
  local prior_umr
  prior_umr="$(state_get_dispatched_umr "$target_key")"
  if [ -z "$prior_umr" ]; then
    return 1
  fi
  local row_json status
  row_json="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --umr-id "$prior_umr" 2>/dev/null)"
  status="$(printf '%s' "$row_json" | py_field "(d['matches'][0]['status'] if d.get('matches') else '')")"
  case "$status" in
    queued|dispatched|running)
      echo "  IN-FLIGHT: $target_key already has live dispatch $prior_umr (status=$status) -- skipping"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# is_financial_decision <text> -- real, narrow keyword test (see the
# "DECISION AUTHORITY" header comment above for the real Owner-issued policy
# this implements). True only for genuine spend/payment/invoice/pricing/
# billing/subscription/refund/budget-approval language -- never for ordinary
# technical/product gaps.
is_financial_decision() {
  printf '%s' "$1" | grep -qiE "$FINANCIAL_KEYWORDS"
}

# escalate_financial_decision <target_key> <title> <prompt> -- the ONLY case
# this script ever asks the Owner for a decision instead of deciding and
# acting itself, per the real Owner-issued escalation-scope policy above.
# Sends a real "NEEDS OWNER DECISION" notification through the EXISTING
# notify-owner.py front door (its own real rate-limit/dedupe, not
# reimplemented here) and records nothing in-flight -- this is a real
# pending human decision, not a dispatch.
escalate_financial_decision() {
  local target_key="$1" title="$2" prompt="$3"
  echo "  NEEDS OWNER DECISION (financial): $target_key -- $title -- escalating via $NOTIFY_OWNER_SCRIPT, NOT auto-dispatching"
  local subject="NEEDS OWNER DECISION (financial): ${title}"
  local body="A server-native PM sentinel tick found a real gap that looks like a financial decision (spending money, a new financial commitment, a payment, or a pricing/billing change), so per the real Owner-issued escalation-scope policy it is asking you first instead of deciding on its own. Real evidence: ${prompt}"
  local out rc
  out="$(python3 "$NOTIFY_OWNER_SCRIPT" --subject "$subject" --body "$body" --dedupe-key "pm-sentinel:financial:${target_key}" 2>&1)"
  rc=$?
  echo "$out" | sed 's/^/    /'
  if [ "$rc" -ne 0 ]; then
    echo "  WARNING: $NOTIFY_OWNER_SCRIPT failed (exit $rc) for $target_key -- Owner escalation NOT confirmed sent, see output above"
    TICK_FAILURES=$((TICK_FAILURES + 1))
  fi
}

# emit_report_row <umr_id> <gap_type> <tested> <audited> <integrated> <working>
# -- 326b point 5 (standardized boolean-table REPORT FORMAT). Every value
# passed in is itself a real boolean computed by the caller from real
# evidence already gathered this tick (query-umr/gh/systemctl output) --
# never AI-narrated. FOUND is always true here (this function is only ever
# called for a row a real query already matched). GAP_ANALYSIS is true
# whenever a real, specific gap_type was identified (it always is, by
# construction, for every call site below). CERTIFIED is true only if every
# other column is true, same rule the laptop/desktop tiers already use.
emit_report_row() {
  local umr_id="$1" gap_type="$2" tested="$3" audited="$4" integrated="$5" working="$6"
  python3 -c "
import json, sys
found = True
gap_analysis = bool('$gap_type')
tested = '$tested' == 'true'
audited = '$audited' == 'true'
integrated = '$integrated' == 'true'
working = '$working' == 'true'
certified = all([found, gap_analysis, tested, audited, integrated, working])
row = {
    'ts': '$TICK_TS',
    'umr_id': '$umr_id',
    'gap_type': '$gap_type',
    'FOUND': found,
    '100_PCT_COMPLETED_WITH_GAP_ANALYSIS_AND_REAL_IMPLEMENTATION': gap_analysis,
    'TESTED': tested,
    'AUDITED_WITH_ARTIFACTS': audited,
    'INTEGRATED': integrated,
    'WORKING': working,
    'CERTIFIED': certified,
}
with open('$REPORT_FILE', 'a') as f:
    f.write(json.dumps(row) + chr(10))
"
}

# dispatch_gap <target_key> <title> <prompt> <tier> <repo> -- the ONLY place
# this script ever creates new real work, and it does so exclusively through
# the existing single front door -- EXCEPT a genuine financial decision,
# which it escalates to the Owner instead (checked first, below) per the
# real Owner-issued escalation-scope policy in the header comment above.
dispatch_gap() {
  local target_key="$1" title="$2" prompt="$3" tier="$4" repo="${5:-compliance-tracker}"
  if is_financial_decision "$title $prompt"; then
    escalate_financial_decision "$target_key" "$title" "$prompt"
    return 0
  fi
  if is_in_flight "$target_key"; then
    return 0
  fi
  if [ "$DISPATCH_COUNT" -ge "$MAX_DISPATCHES_PER_TICK" ]; then
    echo "  CAP REACHED ($MAX_DISPATCHES_PER_TICK/tick) -- NOT dispatching for $target_key this tick (will be reconsidered next tick)"
    return 0
  fi
  echo "  DISPATCHING for $target_key: $title (repo=$repo)"
  local out rc
  out="$("$DISPATCH_OWNER_TASK_SH" "$title" "$prompt" "$tier" ssh_session "$repo" --no-relay 2>&1)"
  rc=$?
  echo "$out" | sed 's/^/    /'
  if [ "$rc" -ne 0 ]; then
    echo "  DISPATCH FAILED for $target_key (dispatch-owner-task.sh exit $rc) -- see output above, no state recorded"
    # AUDIT-REJECT FIX #2: propagate real failure, do not swallow it.
    TICK_FAILURES=$((TICK_FAILURES + 1))
    return 1
  fi
  local new_umr
  new_umr="$(printf '%s' "$out" | grep -o 'umr_id=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2)"
  if [ -n "$new_umr" ]; then
    state_record "$target_key" "$new_umr"
    DISPATCH_COUNT=$((DISPATCH_COUNT + 1))
    echo "  DISPATCHED $target_key -> $new_umr ($DISPATCH_COUNT/$MAX_DISPATCHES_PER_TICK this tick)"
  else
    echo "  WARNING: dispatch-owner-task.sh returned rc=0 but no umr_id parsed from its output -- not recorded in-flight"
    TICK_FAILURES=$((TICK_FAILURES + 1))
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Check 1: tracked-chain status (per governing UMR of each active/pending
# owner_priority_sequence phase -- the real, live "tracked chains" this box
# already maintains, not a second, parallel chain-tracking file), PLUS
# (326b point 1, dynamic scope discovery) a real live search for addendum
# rows citing each chain head, so a fresh amendment is caught the same tick
# it is submitted.
# ---------------------------------------------------------------------------
echo "--- Check 1: tracked-chain status ---"
PHASES_JSON="$(python3 "$SUPERBOSS_REGISTER_PY" show-owner-priority-state 2>/dev/null)"
CHAIN_UMRS="$(printf '%s' "$PHASES_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
for p in d.get('owner_priority_sequence', []):
    if p.get('status') in ('active', 'pending'):
        print(p['governing_umr'])
")"
if [ -z "$CHAIN_UMRS" ]; then
  echo "  no active/pending tracked chains found in owner_priority_sequence (or show-owner-priority-state unavailable) -- nothing to check here this tick"
fi
while IFS= read -r chain_umr; do
  [ -z "$chain_umr" ] && continue
  ROW_JSON="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --umr-id "$chain_umr" 2>/dev/null)"
  CHAIN_STATUS="$(printf '%s' "$ROW_JSON" | py_field "(d['matches'][0]['status'] if d.get('matches') else 'NOT_FOUND')")"
  echo "  chain head $chain_umr: status=$CHAIN_STATUS"
  if [ "$CHAIN_STATUS" = "killed" ]; then
    TARGET_KEY="rca:${chain_umr}"
    REPO="$(printf '%s' "$ROW_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
m = d['matches'][0] if d.get('matches') else {}
print((m.get('inputs_json') or {}).get('repo') or 'compliance-tracker')
" 2>/dev/null)"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick), citing real tracked chain head ${chain_umr}. REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${chain_umr} shows status=killed (a real, live re-check, not assumed) -- this governing UMR needs a real RCA (root-cause analysis) before this chain can proceed. Read the row's own real reason/outputs_json (query resource_governor.py --query-umr --umr-id ${chain_umr} yourself first), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "RCA: tracked chain head ${chain_umr} killed" "$PROMPT" 1 "$REPO"
    emit_report_row "$chain_umr" "chain_head_killed" false false false false
  fi
  # Dynamic addenda discovery (326b point 1): any real row whose own prompt
  # text names this chain head as its governing UMR, found live this tick,
  # not from a hardcoded list.
  ADDENDA_JSON="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --search "$chain_umr" --limit 10 2>/dev/null)"
  ADDENDA_COUNT="$(printf '%s' "$ADDENDA_JSON" | py_field "d.get('count', 0)")"
  if [ -n "$ADDENDA_COUNT" ] && [ "$ADDENDA_COUNT" != "0" ]; then
    echo "  chain head $chain_umr: $ADDENDA_COUNT real addendum row(s) found live (see --query-umr --search for detail)"
  fi
done <<< "$CHAIN_UMRS"

# ---------------------------------------------------------------------------
# Check 2a: killed-status rows needing RCA (system-wide, bounded).
# ---------------------------------------------------------------------------
echo "--- Check 2a: killed-status rows needing RCA ---"
KILLED_JSON="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --status killed --limit 15 2>/dev/null)"
KILLED_IDS="$(printf '%s' "$KILLED_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    print(m['umr_id'])
" 2>/dev/null)"
while IFS= read -r umr_id; do
  [ -z "$umr_id" ] && continue
  TARGET_KEY="rca:${umr_id}"
  ROW_JSON="$(printf '%s' "$KILLED_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    if m['umr_id'] == '$umr_id':
        print(json.dumps(m))
        break
")"
  REASON="$(printf '%s' "$ROW_JSON" | py_field "d.get('reason', '')" | head -c 400)"
  UNIT="$(printf '%s' "$ROW_JSON" | py_field "d.get('unit_name', '')")"
  REPO="$(printf '%s' "$ROW_JSON" | target_repo_of)"
  PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=killed, real recorded reason: \"${REASON}\" (unit_name=${UNIT:-none}). This needs a real RCA: read the row's full real outputs_json/reason (query resource_governor.py --query-umr --umr-id ${umr_id} yourself first, do not trust this summary alone), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
  dispatch_gap "$TARGET_KEY" "RCA: ${umr_id} killed" "$PROMPT" 1 "$REPO"
  emit_report_row "$umr_id" "killed_needs_rca" false false false false
done <<< "$KILLED_IDS"

# ---------------------------------------------------------------------------
# Check 2b: exit-write-back-bug cross-check on status='running' rows -- a
# running row can lie (known bug class). Real systemctl --user show +
# journalctl cross-check against the row's own real unit_name.
# ---------------------------------------------------------------------------
echo "--- Check 2b: running-row cross-check (exit-write-back-bug) ---"
RUNNING_JSON="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --status running --limit 20 2>/dev/null)"
RUNNING_ROWS="$(printf '%s' "$RUNNING_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    unit = m.get('unit_name') or ''
    if unit:
        print(m['umr_id'] + '\t' + unit)
" 2>/dev/null)"
while IFS=$'\t' read -r umr_id unit; do
  [ -z "$umr_id" ] && continue
  ACTIVE_STATE="$(systemctl --user show "$unit" -p ActiveState --value 2>/dev/null)"
  RESULT_STATE="$(systemctl --user show "$unit" -p Result --value 2>/dev/null)"
  if [ "$ACTIVE_STATE" != "active" ] && [ -n "$ACTIVE_STATE" ]; then
    echo "  MISMATCH: $umr_id status=running but unit $unit ActiveState=$ACTIVE_STATE Result=$RESULT_STATE -- real exit-write-back-bug candidate"
    JOURNAL_EXCERPT="$(journalctl --user -u "$unit" -n 5 --no-pager --output=cat 2>/dev/null | tr '\n' ' ' | head -c 500)"
    TARGET_KEY="rca:${umr_id}"
    ROW_JSON="$(printf '%s' "$RUNNING_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    if m['umr_id'] == '$umr_id':
        print(json.dumps(m))
        break
")"
    REPO="$(printf '%s' "$ROW_JSON" | target_repo_of)"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=running, but the real live systemctl --user show ${unit} ActiveState=${ACTIVE_STATE} Result=${RESULT_STATE} -- this row's own status is lying (the known exit-write-back-bug class: a running/completed row can lie). Real journalctl excerpt (last 5 lines, cited not fabricated): \"${JOURNAL_EXCERPT}\". This needs a real RCA: confirm the real unit state yourself (systemctl --user show ${unit}, journalctl --user -u ${unit}), determine what really happened, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "RCA: ${umr_id} status=running but unit dead (write-back bug)" "$PROMPT" 1 "$REPO"
    emit_report_row "$umr_id" "running_status_write_back_bug" false false false false
  fi
done <<< "$RUNNING_ROWS"

# ---------------------------------------------------------------------------
# Check 2c: deliberately absent -- see "REUSE, not reimplementation" header
# comment above. Blocked-task reconciliation is already fully covered by
# resource_governor.py's own scan_stuck_tasks() (already runs every real
# dispatch-tick) feeding into Check 2a above; no new query needed here.
# ---------------------------------------------------------------------------
# Check 3: real PR audit for status='completed_unmerged' rows -- gh pr view
# + real posted comments, never just a CI badge.
# ---------------------------------------------------------------------------
echo "--- Check 3: completed_unmerged PR audit ---"
UNMERGED_JSON="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --status completed_unmerged --limit 15 2>/dev/null)"
UNMERGED_ROWS="$(printf '%s' "$UNMERGED_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    outputs = m.get('outputs_json') or {}
    if isinstance(outputs, str):
        try:
            outputs = json.loads(outputs)
        except Exception:
            outputs = {}
    pr = outputs.get('pr_number')
    repo = outputs.get('repo')
    if pr and repo:
        print(f\"{m['umr_id']}\t{pr}\t{repo}\")
" 2>/dev/null)"
while IFS=$'\t' read -r umr_id pr_number repo; do
  [ -z "$umr_id" ] && continue
  PR_JSON="$(gh pr view "$pr_number" --repo "${GH_ORG}/${repo}" --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,state 2>/dev/null)"
  if [ -z "$PR_JSON" ]; then
    echo "  ${umr_id} PR #${pr_number} (${repo}): gh pr view failed/unavailable -- skipping (fail-open, not a genuine gap without real evidence)"
    continue
  fi
  PR_STATE="$(printf '%s' "$PR_JSON" | py_field "d.get('state','')")"
  MERGEABLE="$(printf '%s' "$PR_JSON" | py_field "d.get('mergeable','')")"
  MERGE_STATE="$(printf '%s' "$PR_JSON" | py_field "d.get('mergeStateStatus','')")"
  REVIEW_DECISION="$(printf '%s' "$PR_JSON" | py_field "d.get('reviewDecision','')")"
  CHECKS_OK="$(printf '%s' "$PR_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
rollup = d.get('statusCheckRollup') or []
if not rollup:
    print('unknown')
else:
    bad = [c for c in rollup if c.get('conclusion') not in ('SUCCESS', 'NEUTRAL', 'SKIPPED', None) and c.get('state') not in ('SUCCESS',)]
    print('fail' if bad else 'pass')
")"
  # Real posted comments -- not the CI badge. gh api, never trusted from
  # gh pr view's own summary fields alone.
  COMMENTS_RAW="$(gh api "repos/${GH_ORG}/${repo}/issues/${pr_number}/comments" --jq '.[].body' 2>/dev/null)"
  FAIL_COMMENT="$(printf '%s' "$COMMENTS_RAW" | grep -iE 'fail|broken|does not work|blocking' | tail -1)"

  echo "  ${umr_id} PR #${pr_number} (${repo}): state=${PR_STATE} mergeable=${MERGEABLE} mergeState=${MERGE_STATE} review=${REVIEW_DECISION} checks=${CHECKS_OK}"

  if [ "$PR_STATE" != "OPEN" ]; then
    echo "    not OPEN -- skipping (already merged/closed, resource_governor.py's own backfill reconciliation owns this, not this sentinel)"
    continue
  fi

  if [ -n "$FAIL_COMMENT" ]; then
    TARGET_KEY="prfix:${umr_id}:${pr_number}"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: ${umr_id}'s open PR #${pr_number} (${repo}) has a real posted comment (not just a CI badge, fetched via gh api repos/${GH_ORG}/${repo}/issues/${pr_number}/comments) indicating a real failure: \"$(printf '%s' "$FAIL_COMMENT" | head -c 400)\". Read the real full comment thread yourself first (gh pr view ${pr_number} --repo ${GH_ORG}/${repo} --comments), then fix the real cited issue and push a real commit to the same PR branch. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "Fix cited FAIL on PR #${pr_number} (${umr_id})" "$PROMPT" 1 "$repo"
    emit_report_row "$umr_id" "pr_fail_comment" false false false false
  elif [ "$MERGEABLE" = "MERGEABLE" ] && [ "$MERGE_STATE" = "CLEAN" ] && [ "$CHECKS_OK" = "pass" ]; then
    # AUDIT-REJECT FIX #4: never dispatch a blind "trust reviewDecision,
    # just merge" worker task -- that used to conflict with the
    # single-gateway rule by letting a dispatched worker call `gh pr merge`
    # directly on a possibly-stale review decision. Every mergeable+clean PR
    # now only ever gets a real independent re-audit dispatch, whether or
    # not a prior review decision is on record -- the audit itself
    # re-verifies state live before any merge happens.
    TARGET_KEY="audit:${umr_id}:${pr_number}"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: ${umr_id}'s open PR #${pr_number} (${repo}) is real, live, MERGEABLE/CLEAN, all real status checks pass (prior recorded reviewDecision=${REVIEW_DECISION:-none}, re-verify it live rather than trusting this snapshot). Re-verify state yourself first (gh pr view ${pr_number} --repo ${GH_ORG}/${repo} --comments, real posted comments not just the CI badge), perform a real independent review, and either approve+merge with real evidence, or cite a real, specific gap back on the PR. Never merge on a cached/prior review decision alone -- re-confirm live. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "Audit mergeable+clean PR #${pr_number} (${umr_id})" "$PROMPT" 2 "$repo"
    emit_report_row "$umr_id" "pr_mergeable_needs_audit" true "$([ "$REVIEW_DECISION" = "APPROVED" ] && echo true || echo false)" false true
  else
    echo "    not yet a genuine actionable gap (mergeable=${MERGEABLE} mergeState=${MERGE_STATE} checks=${CHECKS_OK}) -- still in flight, nothing to do this tick"
  fi
done <<< "$UNMERGED_ROWS"

# ---------------------------------------------------------------------------
# Metrics -- real Prometheus textfile-collector exposition of this tick's own
# real counters (see "REPORT FORMAT" header comment above for the honest
# caveat on the final textfile-collector-directory wiring step).
# ---------------------------------------------------------------------------
{
  echo "# HELP pm_sentinel_tick_dispatch_count Real dispatches this tick"
  echo "# TYPE pm_sentinel_tick_dispatch_count gauge"
  echo "pm_sentinel_tick_dispatch_count ${DISPATCH_COUNT}"
  echo "# HELP pm_sentinel_tick_failure_count Real dispatch/notify failures this tick"
  echo "# TYPE pm_sentinel_tick_failure_count gauge"
  echo "pm_sentinel_tick_failure_count ${TICK_FAILURES}"
  echo "# HELP pm_sentinel_tick_last_run_timestamp_seconds Unix time of last real tick"
  echo "# TYPE pm_sentinel_tick_last_run_timestamp_seconds gauge"
  echo "pm_sentinel_tick_last_run_timestamp_seconds $(date -u +%s)"
} > "${METRICS_FILE}.tmp" && mv "${METRICS_FILE}.tmp" "$METRICS_FILE"

echo "=== pm-sentinel-tick done: ${DISPATCH_COUNT}/${MAX_DISPATCHES_PER_TICK} new dispatches this tick (${TICK_FAILURES} real failure(s)) ==="
# AUDIT-REJECT FIX #2: propagate a real non-zero exit code when any real
# dispatch/notify attempt failed this tick, instead of always exit 0.
if [ "$TICK_FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
