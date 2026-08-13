#!/usr/bin/env bash
# pm-sentinel-tick.sh -- server-native equivalent of the laptop-side hourly PM
# sentinel, so PM oversight of the real UMR chains does not depend on the
# Owner's laptop app staying open. Addendum to P1 UMR-20260806-171945-5767.
#
# REUSE ONLY -- this script deliberately does NOT implement its own dispatch
# path, its own resource cap, or its own stop-work gate. Every real dispatch
# below goes through the EXISTING single front door, dispatch-owner-task.sh
# --no-relay, which itself submits through resource_governor.py's real
# submit()/dispatch_one() -- the same tier/concurrency-cap/EMERGENCY_STOP/
# standing-stop-work-order gate every other real dispatch on this box already
# goes through. This script never writes to umr_tasks directly, never calls
# systemctl to spawn a worker itself, and never bypasses that gate.
#
# What one tick does (same 3 checks the laptop-side hourly PM sentinel does):
#   1. Tracked-chain status: python3 superboss-register.py
#      show-owner-priority-state gives the real, live set of governing UMR
#      chains still active/pending; each chain's governing UMR is re-queried
#      live via resource_governor.py --query-umr --umr-id (never assumed from
#      the priority-sequence row alone).
#   2. Exit-write-back-bug cross-check: a real, live systemctl --user show
#      (ActiveState/SubState/Result) plus a real journalctl excerpt against
#      each status='running' row's own unit_name -- catches the known bug
#      class (documented in dispatch-owner-task.sh / resource_governor.py's
#      own comments) where a umr_tasks row can say running/completed while
#      the real systemd unit is actually dead. status='killed' rows are
#      already-known genuine gaps needing RCA, no cross-check needed.
#   3. Real PR audit: for status='completed_unmerged' rows (a real PR opened
#      but not yet merged -- see dispatch-owner-task.sh's own
#      mark-umr-terminal completion instruction), `gh pr view` for real
#      mergeable/review/check state PLUS the real posted comments (gh api
#      .../comments), never just a CI badge.
#
# On a genuine gap, dispatches (capped at MAX_DISPATCHES_PER_TICK=5 new
# dispatches per tick) via dispatch-owner-task.sh --no-relay, citing the real
# target UMR id and real evidence gathered above. Before dispatching, checks
# a small local in-flight map (STATE_FILE below) so an already-in-flight
# (queued/dispatched/running) dispatch for the same (gap_type, target_umr_id)
# is never duplicated -- that map's own liveness claims are themselves
# re-verified live via --query-umr before being trusted (never assumed
# stale-but-still-true), same "never trust a status without re-checking it
# live" discipline as the rest of this script.
#
# Never fabricates completion: this script never calls mark-umr-terminal,
# never writes 'completed' anywhere -- it only reads real state and dispatches
# real follow-up work through the real existing front door. Never touches
# resource_governor.py / superboss-register.py / task-gateway.py /
# resource_governor_tick_loop.sh -- reads/calls their real CLIs only.
#
# Wired as a new systemd --user timer (see systemd/veridian-pm-sentinel-tick.
# service + .timer in this same directory) firing hourly, modeled on the
# existing veridian-cron-dispatch-tick.service/.timer pattern already live on
# this box (same run-logged.sh wrapper, same shared EMERGENCY_STOP
# ConditionPathExists gate). This is a genuinely new cadence/purpose (server-
# native PM oversight, no old-crontab/closed-set-18 equivalent) -- the same
# real Owner-directive-is-the-authorization precedent already used for
# veridian-cron-prune-memory-backups (see ~/.config/systemd/user/README.md
# "Unit #20 ... explicit Owner-authorized exception").
#
# Real testability seams (env overrides, same convention every other real
# script in this codebase already uses):
#   SUPERBOSS_REGISTER_DB   -- point every subprocess at a real sqlite COPY
#                              instead of the live DB (resource_governor.py /
#                              superboss-register.py's own resolve_superboss_
#                              db_path() already reads this).
#   PM_SENTINEL_STATE_FILE  -- override the in-flight dedup state file path.
#   PM_SENTINEL_MAX_DISPATCH -- override the per-tick dispatch cap (default 5).
#   VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS="" -- disable the standing
#                              stop-work gate for a test run (same env var
#                              resource_governor.py itself already documents).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GH_ORG="${VERIDIAN_GH_ORG:-FChecklist}"
MAX_DISPATCHES_PER_TICK="${PM_SENTINEL_MAX_DISPATCH:-5}"
STATE_FILE="${PM_SENTINEL_STATE_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-inflight.json}"
DISPATCH_COUNT=0

mkdir -p "$(dirname "$STATE_FILE")"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

echo "=== pm-sentinel-tick $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

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
# never trusts the state file's own cached claim.
is_in_flight() {
  local target_key="$1"
  local prior_umr
  prior_umr="$(state_get_dispatched_umr "$target_key")"
  if [ -z "$prior_umr" ]; then
    return 1
  fi
  local row_json status
  row_json="$(python3 resource_governor.py --query-umr --umr-id "$prior_umr" 2>/dev/null)"
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

# dispatch_gap <target_key> <title> <prompt> <tier> -- the ONLY place this
# script ever creates new real work, and it does so exclusively through the
# existing single front door.
dispatch_gap() {
  local target_key="$1" title="$2" prompt="$3" tier="$4"
  if is_in_flight "$target_key"; then
    return 0
  fi
  if [ "$DISPATCH_COUNT" -ge "$MAX_DISPATCHES_PER_TICK" ]; then
    echo "  CAP REACHED ($MAX_DISPATCHES_PER_TICK/tick) -- NOT dispatching for $target_key this tick (will be reconsidered next tick)"
    return 0
  fi
  echo "  DISPATCHING for $target_key: $title"
  local out rc
  out="$(./dispatch-owner-task.sh "$title" "$prompt" "$tier" ssh_session compliance-tracker --no-relay 2>&1)"
  rc=$?
  echo "$out" | sed 's/^/    /'
  if [ "$rc" -ne 0 ]; then
    echo "  DISPATCH FAILED for $target_key (dispatch-owner-task.sh exit $rc) -- see output above, no state recorded"
    return 0
  fi
  local new_umr
  new_umr="$(printf '%s' "$out" | grep -o 'umr_id=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2)"
  if [ -n "$new_umr" ]; then
    state_record "$target_key" "$new_umr"
    DISPATCH_COUNT=$((DISPATCH_COUNT + 1))
    echo "  DISPATCHED $target_key -> $new_umr ($DISPATCH_COUNT/$MAX_DISPATCHES_PER_TICK this tick)"
  else
    echo "  WARNING: dispatch-owner-task.sh returned rc=0 but no umr_id parsed from its output -- not recorded in-flight"
  fi
}

# ---------------------------------------------------------------------------
# Check 1: tracked-chain status (per governing UMR of each active/pending
# owner_priority_sequence phase -- the real, live "tracked chains" this box
# already maintains, not a second, parallel chain-tracking file).
# ---------------------------------------------------------------------------
echo "--- Check 1: tracked-chain status ---"
PHASES_JSON="$(python3 superboss-register.py show-owner-priority-state 2>/dev/null)"
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
  ROW_JSON="$(python3 resource_governor.py --query-umr --umr-id "$chain_umr" 2>/dev/null)"
  CHAIN_STATUS="$(printf '%s' "$ROW_JSON" | py_field "(d['matches'][0]['status'] if d.get('matches') else 'NOT_FOUND')")"
  echo "  chain head $chain_umr: status=$CHAIN_STATUS"
  if [ "$CHAIN_STATUS" = "killed" ]; then
    TARGET_KEY="rca:${chain_umr}"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick), citing real tracked chain head ${chain_umr}. REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${chain_umr} shows status=killed (a real, live re-check, not assumed) -- this governing UMR needs a real RCA (root-cause analysis) before this chain can proceed. Read the row's own real reason/outputs_json (query resource_governor.py --query-umr --umr-id ${chain_umr} yourself first), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "RCA: tracked chain head ${chain_umr} killed" "$PROMPT" 1
  fi
done <<< "$CHAIN_UMRS"

# ---------------------------------------------------------------------------
# Check 2a: killed-status rows needing RCA (system-wide, bounded).
# ---------------------------------------------------------------------------
echo "--- Check 2a: killed-status rows needing RCA ---"
KILLED_JSON="$(python3 resource_governor.py --query-umr --status killed --limit 15 2>/dev/null)"
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
  PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=killed, real recorded reason: \"${REASON}\" (unit_name=${UNIT:-none}). This needs a real RCA: read the row's full real outputs_json/reason (query resource_governor.py --query-umr --umr-id ${umr_id} yourself first, do not trust this summary alone), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
  dispatch_gap "$TARGET_KEY" "RCA: ${umr_id} killed" "$PROMPT" 1
done <<< "$KILLED_IDS"

# ---------------------------------------------------------------------------
# Check 2b: exit-write-back-bug cross-check on status='running' rows -- a
# running row can lie (known bug class). Real systemctl --user show +
# journalctl cross-check against the row's own real unit_name.
# ---------------------------------------------------------------------------
echo "--- Check 2b: running-row cross-check (exit-write-back-bug) ---"
RUNNING_JSON="$(python3 resource_governor.py --query-umr --status running --limit 20 2>/dev/null)"
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
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=running, but the real live systemctl --user show ${unit} ActiveState=${ACTIVE_STATE} Result=${RESULT_STATE} -- this row's own status is lying (the known exit-write-back-bug class: a running/completed row can lie). Real journalctl excerpt (last 5 lines, cited not fabricated): \"${JOURNAL_EXCERPT}\". This needs a real RCA: confirm the real unit state yourself (systemctl --user show ${unit}, journalctl --user -u ${unit}), determine what really happened, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion."
    dispatch_gap "$TARGET_KEY" "RCA: ${umr_id} status=running but unit dead (write-back bug)" "$PROMPT" 1
  fi
done <<< "$RUNNING_ROWS"

# ---------------------------------------------------------------------------
# Check 3: real PR audit for status='completed_unmerged' rows -- gh pr view
# + real posted comments, never just a CI badge.
# ---------------------------------------------------------------------------
echo "--- Check 3: completed_unmerged PR audit ---"
UNMERGED_JSON="$(python3 resource_governor.py --query-umr --status completed_unmerged --limit 15 2>/dev/null)"
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
    dispatch_gap "$TARGET_KEY" "Fix cited FAIL on PR #${pr_number} (${umr_id})" "$PROMPT" 1
  elif [ "$MERGEABLE" = "MERGEABLE" ] && [ "$MERGE_STATE" = "CLEAN" ] && [ "$CHECKS_OK" = "pass" ]; then
    if [ "$REVIEW_DECISION" = "APPROVED" ]; then
      TARGET_KEY="merge:${umr_id}:${pr_number}"
      PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: ${umr_id}'s open PR #${pr_number} (${repo}) is real, live, MERGEABLE/CLEAN, all real status checks pass, and review is APPROVED (fresh real PASS, re-verified live via gh pr view, not assumed from a stale record) -- it needs a real merge. Re-verify state yourself first (gh pr view ${pr_number} --repo ${GH_ORG}/${repo}), then merge it (gh pr merge ${pr_number} --repo ${GH_ORG}/${repo}) and record real completion via superboss-register.py mark-umr-terminal --umr-id ${umr_id} --status completed --commit-sha <real merge commit SHA> --pr-number ${pr_number}. Do not fabricate completion."
      dispatch_gap "$TARGET_KEY" "Merge fresh-PASS PR #${pr_number} (${umr_id})" "$PROMPT" 1
    else
      TARGET_KEY="audit:${umr_id}:${pr_number}"
      PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: ${umr_id}'s open PR #${pr_number} (${repo}) is real, live, MERGEABLE/CLEAN, all real status checks pass, but has no real review decision yet (reviewDecision=${REVIEW_DECISION:-none}) -- it needs a real independent audit before merge. Re-verify state yourself first (gh pr view ${pr_number} --repo ${GH_ORG}/${repo} --comments, real posted comments not just the CI badge), perform a real independent review, and either approve+merge with real evidence, or cite a real, specific gap back on the PR. Do not fabricate completion."
      dispatch_gap "$TARGET_KEY" "Audit mergeable+clean PR #${pr_number} (${umr_id})" "$PROMPT" 2
    fi
  else
    echo "    not yet a genuine actionable gap (mergeable=${MERGEABLE} mergeState=${MERGE_STATE} checks=${CHECKS_OK}) -- still in flight, nothing to do this tick"
  fi
done <<< "$UNMERGED_ROWS"

echo "=== pm-sentinel-tick done: ${DISPATCH_COUNT}/${MAX_DISPATCHES_PER_TICK} new dispatches this tick ==="
exit 0
