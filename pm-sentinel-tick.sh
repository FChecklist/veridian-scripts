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
# QUERY-ONCE-PER-TICK + DECIDE-AND-FIX (2026-08-13 addendum,
# UMR-20260813-105106-e9a7, addendum to UMR-20260813-102459-10c3 -- two
# Owner directives, both hard requirements of this integration, not
# optional follow-ups):
#   (1) QUERY ONCE PER TICK -- fetch each real UMR/PR/unit's real state at
#       most once per tick and reuse it; never re-query the same row again
#       just to double-check (real token/DB/API waste for zero new
#       information). Enforced by:
#         - get_umr_row()/cache_put_row()/already_queried_this_tick() below
#           -- a real per-tick cache (an in-process bash associative array,
#           this script IS one process for its whole tick) over every
#           individual resource_governor.py --query-umr --umr-id lookup.
#           Check 1's per-chain-head lookup and is_in_flight()'s
#           prior-dispatch check both go through get_umr_row(); every bulk
#           listing query (Checks 2a/2b/3) populates the same cache via
#           cache_put_row() for every row it returns, and each of those
#           checks' per-row loops calls already_queried_this_tick() FIRST
#           and skips (no re-fetch, no re-decide) any umr_id a prior check
#           this same tick already fetched+handled -- the real, concrete
#           case this closes: a tracked-chain head (Check 1) that is ALSO
#           status=killed would otherwise be independently re-fetched and
#           re-decided by Check 2a's system-wide killed-row scan.
#         - Check 2b's unit cross-check used to make two separate real
#           `systemctl --user show` calls against the identical unit (one
#           -p ActiveState, one -p Result) -- collapsed into one real call
#           with both -p flags below.
#         - Check 3's PR audit caches each real `gh pr view` /
#           `gh api .../comments` result by (repo, pr_number) via
#           PR_QUERY_CACHE, so two completed_unmerged rows that happen to
#           cite the same real PR never trigger two real GitHub API calls
#           for it in the same tick.
#   (2) DECIDE-AND-FIX, NOT DECIDE-AND-ASK -- for any non-financial finding
#       (technical/product/priority), the fix must be dispatched via the
#       existing gateway in the SAME tick it is found, not merely logged for
#       a future decision; a finding without an accompanying real dispatched
#       fix (or an explicit real blocker, e.g. the per-tick dispatch cap or
#       the standing stop-work gate refusing it) is incomplete work.
#       Enforced by real, runtime-checked bookkeeping, not just convention:
#       every call site below that identifies a real gap calls
#       record_finding() immediately before calling dispatch_gap()
#       (FINDINGS_LOGGED); dispatch_gap() itself -- the one real gateway
#       every finding must go through -- increments FINDINGS_ACTIONED on
#       every one of its own real terminal outcomes (dispatched, dispatch
#       attempted-but-failed [a real, loud, non-silent failure via
#       TICK_FAILURES], already in-flight from a prior tick, per-tick cap
#       reached, or genuine financial escalation). At the end of the tick,
#       if FINDINGS_LOGGED != FINDINGS_ACTIONED, the tick fails loudly
#       ("DECIDE-AND-FIX VIOLATION", non-zero exit via TICK_FAILURES) instead
#       of silently leaving an undecided finding on the table -- a real
#       regression guard against a future gap-detection branch being added
#       without a matching dispatch_gap() call, not merely a description of
#       today's (already-compliant) behavior.
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
#
# UMR-20260813-150004 addendum (self-amplifying RCA cascade -- addendum to
# P1 UMR-20260806-171945-5767; ADJACENT BUT DISTINCT from
# UMR-20260813-115911-df5c, which owns routing killed-task RCA through the
# code quality-gate auto-fix loop, not RCA generation itself):
#
# REAL INCIDENT: a real --query-umr pass (2026-08-13, 12:47-13:01 UTC) over
# the 120 most recent umr_tasks rows found (1) an exact duplicate --
# UMR-20260813-091810-5045 ("RCA: UMR-20260813-060311-6eea killed",
# completed_unmerged at 09:36:42) and UMR-20260813-124141-7641 (same title,
# submitted 12:41:41, ~3h15m later) both targeting the identical killed row
# -- and (2) real RCA-of-RCA recursion, e.g. UMR-20260813-131646-007b ("RCA:
# UMR-20260813-101802-3ad2 killed") where 3ad2 was ITSELF titled "RCA:
# UMR-20260808-110448-b85c killed" -- a second-generation RCA, no depth
# limit anywhere.
#
# Root cause (confirmed by reading UMR-20260813-060311-6eea's own real
# `reason` field live): its RCA (5045) genuinely finished -- "real primary
# deliverable WAS produced -- Tier-1 audit comment posted on veridian-scripts
# PR #249 ... Row was mislabeled by reconcile_owner_dispatch..." -- but 6eea
# itself was NEVER moved off status=killed (a separate, real bug in the
# reconciliation path, out of this addendum's scope to fix). Because
# is_in_flight() below only ever checks this script's own STATE_FILE for a
# prior dispatch still queued/dispatched/running, once 5045 itself reached a
# real terminal status (completed_unmerged), 6eea was no longer "in flight"
# by that narrow definition -- so the next tick that saw UMR-20260813-060311
# -6eea resurface in Check 2a's `--status killed` listing (which it always
# will, permanently, until something moves it off status=killed) dispatched
# a fresh, wasted duplicate RCA against it. Recursion has the same shape:
# nothing anywhere checks whether the killed row about to get an RCA is
# ITSELF an RCA (or how many generations deep it already is) before
# dispatching yet another one.
#
# Two real fixes, both added to dispatch_gap() below (gated on target_key
# starting with `rca:` -- the exact scope this addendum owns, not the
# prfix:/audit: targets Check 3 also dispatches through the same gateway):
#   (B) TARGET-IDENTIFIER DEDUP, REUSED NOT REIMPLEMENTED: `veridian-
#       scripts` PR #297 (open, unmerged at the time of this addendum) adds
#       exactly this class of check -- superboss-register.py's
#       extract_target_identifiers()/find_target_identifier_duplicate() plus
#       a `check-target-identifier-duplicate` CLI -- for dispatch-owner-
#       task.sh's own PR#/file/script dedup gap. This addendum branches
#       directly from PR #297's own head commit (not main) and extends
#       those SAME two functions (never a second, competing extractor):
#       a fourth identifier class (`umr:<UMR-ID>`, since none of PR #297's
#       three original classes ever match a bare UMR id), plus two
#       backward-compatible parameters on find_target_identifier_duplicate()
#       -- `statuses` (PR #297's own dispatch-owner-task.sh call site keeps
#       its original queued/running-only default; this addendum's own call
#       site below passes queued/dispatched/running/completed/
#       completed_unmerged, since a real prior RCA that already reached
#       status=completed(_unmerged) -- exactly 5045's own case -- is still a
#       real duplicate to skip against) and `window_hours<=0` meaning no
#       cutoff (a killed row can sit unreconciled for far longer than the
#       4h PR #297 defaults to). See superboss-register.py for the real
#       diff. THIS PR IS STACKED ON #297 AND DEPENDS ON IT MERGING FIRST --
#       see this addendum's own PR body for the real reason (PR #297 was
#       still open/unmerged when this addendum was written, so rather than
#       fork its real design into a second implementation, this addendum
#       branches from its exact head commit).
#   (C) RECURSION/DEPTH GUARD: every RCA prompt dispatched for a killed row
#       now carries a real, machine-checkable `RCA_DEPTH:<n>` marker (see
#       rca_next_depth() below) -- 1 if the killed row being RCA'd is not
#       itself an RCA task, or (that row's own carried depth + 1) if it is.
#       dispatch_gap() refuses (does not dispatch) once depth exceeds
#       MAX_RCA_DEPTH (default 3, PM_SENTINEL_MAX_RCA_DEPTH override) and
#       instead escalates a real, human-visible notification via the same
#       existing notify-owner.py front door escalate_financial_decision()
#       below already uses (never a competing notification mechanism) --
#       per spec: "Escalate to a human-visible report at the limit instead
#       of emitting another task."

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GH_ORG="${VERIDIAN_GH_ORG:-FChecklist}"
MAX_DISPATCHES_PER_TICK="${PM_SENTINEL_MAX_DISPATCH:-5}"
# UMR-20260813-150004 addendum (C): max real RCA-of-RCA generations before
# dispatch_gap() refuses and escalates instead -- see header comment above.
MAX_RCA_DEPTH="${PM_SENTINEL_MAX_RCA_DEPTH:-3}"
STATE_FILE="${PM_SENTINEL_STATE_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-inflight.json}"
NOTIFY_OWNER_SCRIPT="${PM_SENTINEL_NOTIFY_OWNER_SCRIPT:-notify-owner.py}"
REPORT_FILE="${PM_SENTINEL_REPORT_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-tick-report.jsonl}"
METRICS_FILE="${PM_SENTINEL_METRICS_FILE:-/opt/veridian/ai-os/logs/pm-sentinel-tick.prom}"
DISPATCH_COUNT=0
TICK_FAILURES=0
# QUERY-ONCE-PER-TICK + DECIDE-AND-FIX (UMR-20260813-105106-e9a7 addendum to
# UMR-20260813-102459-10c3) -- see header comment above.
FINDINGS_LOGGED=0
FINDINGS_ACTIONED=0
# Real, per-tick, on-disk cache (NOT a bash associative array): every real
# query function below (get_umr_row, cached_gh_pr_view) is invoked via
# command substitution ($(...)) at most of its real call sites, which bash
# always runs in a forked subshell -- an in-memory `declare -A` write inside
# that subshell is silently lost the instant the subshell exits, so it can
# never actually be read back by a later call in the parent shell. A real
# on-disk cache directory has no such problem (a file write is a real
# filesystem side effect, not shell-process memory) -- same reason
# STATE_FILE below is already a real file and not a bash variable. Unique
# per real tick invocation (mktemp), removed on exit (trap), never reused
# across ticks -- this cache is deliberately per-tick only.
CACHE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pm-sentinel-tick-cache.XXXXXX")"
trap 'rm -rf "$CACHE_DIR"' EXIT
mkdir -p "$CACHE_DIR/umr" "$CACHE_DIR/pr"
QUERY_LOG="$CACHE_DIR/.query-log"

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

# row_title_of / row_prompt_of <json-row-on-stdin> -- UMR-20260813-150004
# addendum (C): the same real inputs_json.title/prompt target_repo_of()
# already reads, needed here to tell whether the killed row about to get an
# RCA is ITSELF an RCA task (and if so, at what real carried depth) before
# dispatch_gap() decides whether to dispatch another one -- see
# rca_next_depth() below.
row_title_of() {
  python3 -c "
import json, sys
d = json.load(sys.stdin)
inputs = d.get('inputs_json') or {}
if isinstance(inputs, str):
    try:
        inputs = json.loads(inputs)
    except Exception:
        inputs = {}
print(inputs.get('title') or '')
"
}

row_prompt_of() {
  python3 -c "
import json, sys
d = json.load(sys.stdin)
inputs = d.get('inputs_json') or {}
if isinstance(inputs, str):
    try:
        inputs = json.loads(inputs)
    except Exception:
        inputs = {}
print(inputs.get('prompt') or '')
"
}

# rca_next_depth <killed_row_title> <killed_row_prompt> -- UMR-20260813-
# 150004 addendum (C): the real generation depth of the RCA about to be
# dispatched for a killed row. 1 if the killed row is not itself an RCA
# task (an original piece of real work being RCA'd for the first time); if
# the killed row's own title starts with "RCA: " (it IS itself an RCA
# task), this is its own carried RCA_DEPTH marker (embedded in its own
# prompt by this same function, the prior time it was dispatched) plus 1 --
# defaulting to a carried depth of 0 for a pre-addendum RCA row that
# predates this marker existing at all, so it is still correctly counted
# as generation 1 of the NEW guard.
rca_next_depth() {
  local row_title="$1" row_prompt="$2"
  if printf '%s' "$row_title" | grep -qE '^RCA: '; then
    local prior
    prior="$(printf '%s' "$row_prompt" | grep -oE 'RCA_DEPTH:[0-9]+' | head -1 | cut -d: -f2)"
    prior="${prior:-0}"
    echo $((prior + 1))
  else
    echo 1
  fi
}

# _cache_key <string> -- sanitize an arbitrary real id (umr_id, or
# repo#pr_number) into a safe filename under CACHE_DIR.
_cache_key() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_-' '_'
}

# get_umr_row <umr_id> -- QUERY-ONCE-PER-TICK: fetch a given UMR's real live
# state via resource_governor.py --query-umr --umr-id at most ONCE per tick
# and reuse it for every later check that needs the same row this same
# tick. Every call site that needs one specific UMR's row (Check 1's
# per-chain-head lookup, is_in_flight()'s prior-dispatch liveness check)
# goes through this function instead of shelling out directly.
get_umr_row() {
  local umr_id="$1"
  local f="$CACHE_DIR/umr/$(_cache_key "$umr_id")"
  if [ -f "$f" ]; then
    echo "HIT" >> "$QUERY_LOG"
    cat "$f"
    return 0
  fi
  local row_json
  row_json="$(python3 "$RESOURCE_GOVERNOR_PY" --query-umr --umr-id "$umr_id" 2>/dev/null)"
  printf '%s' "$row_json" > "$f"
  echo "MISS" >> "$QUERY_LOG"
  printf '%s' "$row_json"
}

# cache_put_row <umr_id> <row-json> -- populate the SAME per-tick cache
# get_umr_row() reads from, using a row already returned by a bulk listing
# query this tick (--status killed/running/completed_unmerged) -- a later
# individual lookup for that same umr_id (e.g. is_in_flight(), or another
# check that hits the same row) reuses it instead of re-querying. No-op if
# already cached (first real fetch wins, never overwritten mid-tick).
cache_put_row() {
  local umr_id="$1" row_json="$2"
  local f="$CACHE_DIR/umr/$(_cache_key "$umr_id")"
  [ -f "$f" ] && return 0
  printf '{"matches":[%s]}' "$row_json" > "$f"
}

# already_queried_this_tick <umr_id> -- true if this row is already cached
# this tick, meaning some earlier check already fetched its real state and
# (if it warranted one) already gave it a real same-tick decision. Checks
# 2a/2b/3 call this FIRST in their per-row loops and skip (no re-fetch, no
# re-decide) any umr_id already handled -- the real, concrete case this
# closes: a tracked-chain head (Check 1) that is ALSO status=killed would
# otherwise be independently re-fetched and re-decided by Check 2a's
# system-wide killed-row scan for zero new information. A real on-disk
# `-f` check, not an in-memory array read -- see CACHE_DIR comment above
# for why (this function is called directly, not via command substitution,
# but get_umr_row/cache_put_row populating it often ARE, so the cache
# itself must be real-filesystem-backed for either to see the other's
# writes).
already_queried_this_tick() {
  [ -f "$CACHE_DIR/umr/$(_cache_key "$1")" ]
}

# cached_gh_pr_view <repo> <pr_number> -- QUERY-ONCE-PER-TICK for Check 3:
# two completed_unmerged rows that happen to cite the same real PR never
# trigger two real GitHub API calls for it in the same tick.
cached_gh_pr_view() {
  local repo="$1" pr_number="$2"
  local f="$CACHE_DIR/pr/$(_cache_key "${repo}#${pr_number}")"
  if [ -f "$f" ]; then
    echo "HIT" >> "$QUERY_LOG"
    cat "$f"
    return 0
  fi
  local pr_json
  pr_json="$(gh pr view "$pr_number" --repo "${GH_ORG}/${repo}" --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,state 2>/dev/null)"
  printf '%s' "$pr_json" > "$f"
  echo "MISS" >> "$QUERY_LOG"
  printf '%s' "$pr_json"
}

# record_finding -- DECIDE-AND-FIX: call immediately before dispatch_gap()
# at every real "REAL GAP FOUND" call site below. Paired at the end of the
# tick against FINDINGS_ACTIONED (incremented once per dispatch_gap() call,
# regardless of its own real outcome) -- see header comment for the real
# runtime violation check this backs.
record_finding() {
  FINDINGS_LOGGED=$((FINDINGS_LOGGED + 1))
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
  row_json="$(get_umr_row "$prior_umr")"
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

# escalate_rca_recursion_limit <target_key> <title> <prompt> <depth> --
# UMR-20260813-150004 addendum (C): the real RCA-of-RCA recursion guard's
# own escalation path, reusing the EXACT SAME notify-owner.py front door
# escalate_financial_decision() above already uses (never a second,
# competing notification mechanism) -- per spec: "Escalate to a
# human-visible report at the limit instead of emitting another task."
# Records nothing in-flight and dispatches nothing; this is a real,
# disclosed refusal, not a silent drop.
escalate_rca_recursion_limit() {
  local target_key="$1" title="$2" prompt="$3" depth="$4"
  echo "  RCA RECURSION LIMIT: $target_key -- real depth=$depth exceeds MAX_RCA_DEPTH=$MAX_RCA_DEPTH -- escalating via $NOTIFY_OWNER_SCRIPT, NOT dispatching another RCA-of-RCA"
  local subject="RCA RECURSION LIMIT REACHED (depth ${depth}): ${title}"
  local body="A server-native PM sentinel tick found a killed row that is itself an RCA task, at real recursion depth ${depth} (limit ${MAX_RCA_DEPTH} via PM_SENTINEL_MAX_RCA_DEPTH) -- refusing to dispatch another RCA-of-RCA per UMR-20260813-150004 (self-amplifying RCA cascade). This needs real human review: an RCA chain this deep on the same lineage usually means the underlying root cause was never actually fixed, not that another automated RCA will find something new. Real evidence: ${prompt}"
  local out rc
  out="$(python3 "$NOTIFY_OWNER_SCRIPT" --subject "$subject" --body "$body" --dedupe-key "pm-sentinel:rca-depth-limit:${target_key}" 2>&1)"
  rc=$?
  echo "$out" | sed 's/^/    /'
  if [ "$rc" -ne 0 ]; then
    echo "  WARNING: $NOTIFY_OWNER_SCRIPT failed (exit $rc) for $target_key -- RCA recursion escalation NOT confirmed sent, see output above"
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
  # DECIDE-AND-FIX: this is the one real gateway every finding must go
  # through -- count it as actioned the moment it arrives here, regardless
  # of which real terminal outcome below actually applies (dispatched,
  # in-flight, cap-reached, or financial escalation are all real,
  # disclosed, non-silent dispositions of the finding this same tick).
  FINDINGS_ACTIONED=$((FINDINGS_ACTIONED + 1))
  if is_financial_decision "$title $prompt"; then
    escalate_financial_decision "$target_key" "$title" "$prompt"
    return 0
  fi
  if is_in_flight "$target_key"; then
    return 0
  fi
  # UMR-20260813-150004 addendum -- both new guards below are scoped to
  # target_key starting with "rca:" (Checks 1/2a/2b, the exact real gap this
  # addendum owns) and deliberately do NOT touch prfix:/audit: targets
  # (Check 3), which have their own, different, already-correct re-dispatch
  # semantics.
  case "$target_key" in
    rca:*)
      # (C) RECURSION/DEPTH GUARD -- refuse once this RCA's own carried
      # RCA_DEPTH marker (embedded into $prompt by the call site via
      # rca_next_depth(), see header comment) exceeds MAX_RCA_DEPTH.
      # Defaults to depth 1 (a first-generation RCA) if the marker is
      # somehow absent, so a caller that forgets to embed it is never
      # treated as already over the limit.
      local rca_depth
      rca_depth="$(printf '%s' "$prompt" | grep -oE 'RCA_DEPTH:[0-9]+' | head -1 | cut -d: -f2)"
      rca_depth="${rca_depth:-1}"
      if [ "$rca_depth" -gt "$MAX_RCA_DEPTH" ]; then
        escalate_rca_recursion_limit "$target_key" "$title" "$prompt" "$rca_depth"
        return 0
      fi
      # (B) TARGET-IDENTIFIER DEDUP, REUSED FROM veridian-scripts PR #297
      # (extended, not reimplemented -- see superboss-register.py and the
      # header comment above). Checks queued/dispatched/running AND
      # completed/completed_unmerged (PR #297's own default call site only
      # checks queued/running, correct for its own PR/file/script use --
      # this addendum's own real incident needed the completed states too,
      # see UMR-20260813-060311-6eea/UMR-20260813-091810-5045 above), with
      # no time cutoff (window-hours 0) since a killed row can resurface far
      # outside any fixed window.
      local tidup_json tidup_found tidup_umr tidup_status
      tidup_json="$(python3 "$SUPERBOSS_REGISTER_PY" check-target-identifier-duplicate \
        --title "$title" --prompt "$prompt" --repo "$repo" \
        --statuses "queued,dispatched,running,completed,completed_unmerged" \
        --window-hours 0 --limit 50 2>/dev/null)"
      tidup_found="$(printf '%s' "$tidup_json" | py_field "d.get('target_identifier_duplicate_found', False)")"
      if [ "$tidup_found" = "True" ]; then
        tidup_umr="$(printf '%s' "$tidup_json" | py_field "d.get('duplicate_umr_id', '')")"
        tidup_status="$(printf '%s' "$tidup_json" | py_field "d.get('duplicate_status', '')")"
        echo "  RCA TARGET DEDUP: $target_key already has an existing row $tidup_umr (status=$tidup_status) targeting the same real identifier -- skipping duplicate RCA dispatch (real incident this closes: UMR-20260813-091810-5045/UMR-20260813-124141-7641)"
        return 0
      fi
      ;;
  esac
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
  ROW_JSON="$(get_umr_row "$chain_umr")"
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
    ROW_TITLE="$(printf '%s' "$ROW_JSON" | row_title_of)"
    ROW_PROMPT_RAW="$(printf '%s' "$ROW_JSON" | row_prompt_of)"
    RCA_DEPTH="$(rca_next_depth "$ROW_TITLE" "$ROW_PROMPT_RAW")"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick), citing real tracked chain head ${chain_umr}. REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${chain_umr} shows status=killed (a real, live re-check, not assumed) -- this governing UMR needs a real RCA (root-cause analysis) before this chain can proceed. Read the row's own real reason/outputs_json (query resource_governor.py --query-umr --umr-id ${chain_umr} yourself first), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion. RCA_DEPTH:${RCA_DEPTH} (recursion-guard marker for pm-sentinel-tick.sh's own dispatch_gap(), UMR-20260813-150004 -- do not remove)."
    record_finding
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
  # QUERY-ONCE-PER-TICK: this exact row may already have been fetched (and,
  # if it warranted one, already given a real same-tick decision) by Check 1
  # above -- a tracked-chain head that is ALSO status=killed. Skip re-fetch
  # + re-decide entirely rather than re-querying for zero new information.
  if already_queried_this_tick "$umr_id"; then
    echo "  QUERY-ONCE: ${umr_id} already fetched+handled this tick (tracked-chain check) -- skipping redundant re-check"
    continue
  fi
  TARGET_KEY="rca:${umr_id}"
  ROW_JSON="$(printf '%s' "$KILLED_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('matches', []):
    if m['umr_id'] == '$umr_id':
        print(json.dumps(m))
        break
")"
  cache_put_row "$umr_id" "$ROW_JSON"
  REASON="$(printf '%s' "$ROW_JSON" | py_field "d.get('reason', '')" | head -c 400)"
  UNIT="$(printf '%s' "$ROW_JSON" | py_field "d.get('unit_name', '')")"
  REPO="$(printf '%s' "$ROW_JSON" | target_repo_of)"
  ROW_TITLE="$(printf '%s' "$ROW_JSON" | row_title_of)"
  ROW_PROMPT_RAW="$(printf '%s' "$ROW_JSON" | row_prompt_of)"
  RCA_DEPTH="$(rca_next_depth "$ROW_TITLE" "$ROW_PROMPT_RAW")"
  PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=killed, real recorded reason: \"${REASON}\" (unit_name=${UNIT:-none}). This needs a real RCA: read the row's full real outputs_json/reason (query resource_governor.py --query-umr --umr-id ${umr_id} yourself first, do not trust this summary alone), determine the real root cause, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion. RCA_DEPTH:${RCA_DEPTH} (recursion-guard marker for pm-sentinel-tick.sh's own dispatch_gap(), UMR-20260813-150004 -- do not remove)."
  record_finding
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
  # QUERY-ONCE-PER-TICK: this row was already fetched+handled by Check 1
  # (tracked-chain head) this same tick if applicable -- skip redundant
  # re-decide. (The systemctl/journalctl calls below are real UNIT state,
  # not UMR-row state, so this guard is about the UMR row only.)
  if already_queried_this_tick "$umr_id"; then
    echo "  QUERY-ONCE: ${umr_id} already fetched+handled this tick (tracked-chain check) -- skipping redundant re-check"
    continue
  fi
  # QUERY-ONCE-PER-TICK: one real systemctl call for BOTH ActiveState and
  # Result (was two identical-unit calls, one per field) -- newline-joined
  # output in the same order as the -p flags.
  UNIT_STATE="$(systemctl --user show "$unit" -p ActiveState -p Result --value 2>/dev/null)"
  ACTIVE_STATE="$(printf '%s\n' "$UNIT_STATE" | sed -n '1p')"
  RESULT_STATE="$(printf '%s\n' "$UNIT_STATE" | sed -n '2p')"
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
    cache_put_row "$umr_id" "$ROW_JSON"
    REPO="$(printf '%s' "$ROW_JSON" | target_repo_of)"
    ROW_TITLE="$(printf '%s' "$ROW_JSON" | row_title_of)"
    ROW_PROMPT_RAW="$(printf '%s' "$ROW_JSON" | row_prompt_of)"
    RCA_DEPTH="$(rca_next_depth "$ROW_TITLE" "$ROW_PROMPT_RAW")"
    PROMPT="GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). REAL GAP FOUND: resource_governor.py --query-umr --umr-id ${umr_id} shows status=running, but the real live systemctl --user show ${unit} ActiveState=${ACTIVE_STATE} Result=${RESULT_STATE} -- this row's own status is lying (the known exit-write-back-bug class: a running/completed row can lie). Real journalctl excerpt (last 5 lines, cited not fabricated): \"${JOURNAL_EXCERPT}\". This needs a real RCA: confirm the real unit state yourself (systemctl --user show ${unit}, journalctl --user -u ${unit}), determine what really happened, and either fix + redispatch the real remaining scope, or record a real, honest terminal outcome via superboss-register.py mark-umr-terminal citing real evidence. Do not fabricate completion. RCA_DEPTH:${RCA_DEPTH} (recursion-guard marker for pm-sentinel-tick.sh's own dispatch_gap(), UMR-20260813-150004 -- do not remove)."
    record_finding
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
  # QUERY-ONCE-PER-TICK: same UMR-row guard as Checks 2a/2b -- a
  # completed_unmerged row that is ALSO a tracked-chain head was already
  # fetched by Check 1. (This does not skip the real gh pr view below --
  # that is genuinely new PR-level information Check 1 never fetched.)
  if already_queried_this_tick "$umr_id"; then
    echo "  QUERY-ONCE: ${umr_id} UMR-row state already fetched this tick (tracked-chain check) -- reusing, only fetching real new PR state below"
  fi
  PR_JSON="$(cached_gh_pr_view "$repo" "$pr_number")"
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
    record_finding
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
    record_finding
    dispatch_gap "$TARGET_KEY" "Audit mergeable+clean PR #${pr_number} (${umr_id})" "$PROMPT" 2 "$repo"
    emit_report_row "$umr_id" "pr_mergeable_needs_audit" true "$([ "$REVIEW_DECISION" = "APPROVED" ] && echo true || echo false)" false true
  else
    echo "    not yet a genuine actionable gap (mergeable=${MERGEABLE} mergeState=${MERGE_STATE} checks=${CHECKS_OK}) -- still in flight, nothing to do this tick"
  fi
done <<< "$UNMERGED_ROWS"

# ---------------------------------------------------------------------------
# DECIDE-AND-FIX reconciliation (2026-08-13 addendum, UMR-20260813-105106-e9a7
# addendum to UMR-20260813-102459-10c3) -- see header comment above. Every
# real finding logged via record_finding() must have gone through
# dispatch_gap() (the one real gateway) this same tick; if the two real
# counters don't match, a finding was left undecided -- fail loudly instead
# of silently leaving it for a future tick that might never come.
# ---------------------------------------------------------------------------
if [ "$FINDINGS_LOGGED" -ne "$FINDINGS_ACTIONED" ]; then
  echo "DECIDE-AND-FIX VIOLATION: ${FINDINGS_LOGGED} real finding(s) logged this tick but only ${FINDINGS_ACTIONED} got a same-tick dispatch/escalation/blocker decision through dispatch_gap() -- incomplete work per the 2026-08-13 addendum (UMR-20260813-105106-e9a7). A finding without an accompanying real dispatched fix (or an explicit real blocker) is not done." >&2
  TICK_FAILURES=$((TICK_FAILURES + 1))
else
  echo "DECIDE-AND-FIX: ${FINDINGS_LOGGED} real finding(s) this tick, ${FINDINGS_ACTIONED} same-tick decision(s) through dispatch_gap() -- reconciled, none left undecided"
fi
QUERY_CACHE_MISSES="$(grep -c '^MISS$' "$QUERY_LOG" 2>/dev/null)"; QUERY_CACHE_MISSES="${QUERY_CACHE_MISSES:-0}"
QUERY_CACHE_HITS="$(grep -c '^HIT$' "$QUERY_LOG" 2>/dev/null)"; QUERY_CACHE_HITS="${QUERY_CACHE_HITS:-0}"
echo "QUERY-ONCE-PER-TICK: ${QUERY_CACHE_MISSES} real live query/queries issued, ${QUERY_CACHE_HITS} reused from this tick's own cache instead of re-querying"

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
  echo "# HELP pm_sentinel_tick_findings_logged Real findings identified this tick (DECIDE-AND-FIX)"
  echo "# TYPE pm_sentinel_tick_findings_logged gauge"
  echo "pm_sentinel_tick_findings_logged ${FINDINGS_LOGGED}"
  echo "# HELP pm_sentinel_tick_findings_actioned Real findings given a same-tick dispatch/escalation/blocker decision (DECIDE-AND-FIX)"
  echo "# TYPE pm_sentinel_tick_findings_actioned gauge"
  echo "pm_sentinel_tick_findings_actioned ${FINDINGS_ACTIONED}"
  echo "# HELP pm_sentinel_tick_query_cache_hits Real UMR/PR queries served from this tick's own cache instead of re-querying (QUERY-ONCE-PER-TICK)"
  echo "# TYPE pm_sentinel_tick_query_cache_hits gauge"
  echo "pm_sentinel_tick_query_cache_hits ${QUERY_CACHE_HITS}"
  echo "# HELP pm_sentinel_tick_query_cache_misses Real live UMR/PR queries actually issued this tick (QUERY-ONCE-PER-TICK)"
  echo "# TYPE pm_sentinel_tick_query_cache_misses gauge"
  echo "pm_sentinel_tick_query_cache_misses ${QUERY_CACHE_MISSES}"
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
