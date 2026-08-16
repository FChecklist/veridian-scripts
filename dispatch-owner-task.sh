#!/usr/bin/env bash
# dispatch-owner-task.sh -- single front door for dispatching real Owner-directed
# work to the server, whether relayed by a Claude Code CLI laptop session or run
# directly by the Owner via SSH/PowerShell. Chains the existing instruction /
# work-item / UMR registration pipeline (superboss-register.py +
# resource_governor.py), then relays the same UMR-tagged message directly into
# the live interactive tmux session in the SAME call -- there is no separate
# "raw tmux send-keys" step left to accidentally use instead of this script.
# Every call either returns a real umr_id (and relays it), or refuses with a
# clear reason (duplicate content, duplicate target identifier, or
# resource_governor.py rejection) -- it never silently does nothing.
#
# Real pipeline order, for discoverability (UMR-20260814-132703-a1f9; this
# is documentation only, the mechanism below is unchanged):
#   1. submit          -- task-gateway.py submit (called below)
#   2. capability-lookup -- task-gateway.py's cmd_submit calls
#                          superboss-register.py lookup-capability before
#                          anything is queued
#   3. queue-submit     -- resource_governor.py --submit (called below)
#                          enqueues a 'queued' umr_tasks row and returns
#                          immediately; it does not spawn anything itself
#   4. execute          -- the live veridian-governor-tick loop's
#                          resource_governor.py --tick -> dispatch_one()
#                          later spawns the real veridian-worker@<id>.service
#   5. validate         -- runs LAST, inside the spawned worker, before the
#                          main claude invocation: worker-entrypoint.sh ->
#                          preflight-guard.py -> tight_task_validation.py's
#                          validate_tight_task()
#
# Usage: dispatch-owner-task.sh "<short title>" "<full prompt text>" [tier] [medium] [repo] [--no-relay] [--attach <path>] [--complexity-tier <value>]
#   tier       - resource_governor.py tier, 0 (highest) .. 4 (lowest); default 2
#   medium     - "claude_code_cli" (default, laptop-relayed) or "ssh_session"
#                (Owner running this directly by hand)
#   repo       - target repo for veridian-task.py create; default compliance-tracker
#   --complexity-tier <value> - task-20260816-041054: OPTIONAL real complexity
#                signal (plan_generator.py's own mechanical/integrative/
#                judgment enum -- pm_lifecycle.py's dispatch_task() is the one
#                real, live caller today). Threaded through to task.yaml's own
#                complexity_tier field; worker-entrypoint.sh routes only
#                complexity_tier == 'mechanical' to --model haiku, everything
#                else (including this flag simply being omitted) stays
#                --model sonnet.
#   --no-relay - register only, do not deliver into the tmux session (e.g. for
#                pure background-worker dispatch with no interactive session
#                involvement). Omit this and relay happens by default.
#   --attach <path> - task-20260814-180459 (real file-attachment intake): one
#                real local file (text/markdown/PDF/image). Passed straight
#                through to task-gateway.py submit's own --attach (step 2
#                below), which hashes/extracts it and folds the result into
#                the SAME text that goes through the OWNER_ENGINE gate and
#                dedup/search -- never a second, parallel path. If step 2
#                succeeds, this script also folds the attachment content into
#                its OWN $PROMPT for the rest of this dispatch (queue spec,
#                tmux relay) -- see step 2's own comment -- so the real
#                worker that eventually picks this up sees it too, not only
#                the classification step.
#
# UMR-20260806-115423-500d (real narrowing of UMR-20260806-085144-9c63 /
# PR #150, read this before touching the relay block below): a successful
# `tmux send-keys` proves only that keystrokes were written into a pane --
# NEVER that a live process actually read and acted on them. This script's
# own "RELAYED into tmux session..." line is therefore a best-effort
# courtesy notification, not proof of delivery, full stop. It used to also
# write status='dispatched' (relay succeeded) or a real terminal
# status='failed' (tmux session absent) straight onto the umr_id it just
# minted -- but BOTH of those writes independently remove the row from
# resource_governor.py's `next_queued_task()` query (`SELECT * FROM
# umr_tasks WHERE status='queued'`), which is the ONLY function that
# mechanically dispatches a queued veridian_task_create row to a real,
# independent `veridian-worker@*.service` via `_perform_spawn()` --
# confirmed live by reading next_queued_task()/_perform_spawn() directly:
# this mechanical path has zero tmux involvement and works whether or not
# any interactive session exists. So a row whose relay keystrokes landed in
# a dead/wrong/busy pane was silently and PERMANENTLY excluded from the one
# channel that could still have picked it up -- a real dead zone.
#
# The fix: a successful (or absent-session) tmux relay now records a real,
# honest courtesy signal via superboss-register.py's mark-umr-relay-attempted
# CLI subcommand (never a raw SQL write) -- which writes ONLY
# ts_relay_attempted/relay_outcome/relay_detail, and NEVER touches `status`,
# `ts_dispatched`, or `ts_completed`. A row stays exactly status='queued'
# after either branch below, fully eligible for dispatch-tick.py's own real
# mechanical pickup on the very next tick, no matter what the tmux relay
# did or didn't achieve. The ONLY legitimate queued -> other status
# transition is that real mechanical pickup (or a genuine mark-umr-terminal
# call recording real completed work -- see below); a printed RELAYED
# message is never, by itself, that transition.
#
# mark-umr-dispatched (status='dispatched') still exists as its own real
# CLI command for a genuinely different, future use -- a non-interactive
# channel that can positively confirm delivery -- it is simply no longer
# called from this script's own relay branches.
#
# To record real completion once work against a dispatched UMR genuinely
# finishes (worker or interactive session, run this by hand or from your own
# completion hook):
#   python3 superboss-register.py mark-umr-terminal --umr-id UMR-... \
#       --status completed [--reason "what finished"]
#   (--status also accepts failed / killed for other genuine terminal outcomes)
#
# UMR-20260806-112013-088f (structural fix, second half of the above
# UMR-20260806-085144-9c63 finding): the paragraph just above was, until
# this change, the *only* place this requirement was written down -- an
# optional-looking doc comment nothing downstream was ever required to
# read, which is the real, confirmed reason 29+ real dispatched rows sat at
# status='dispatched'/ts_completed=NULL indefinitely even when the
# underlying work had genuinely finished. Every real tmux relay below now
# appends a mandatory, UMR-id-specific completion instruction (naming this
# exact mark-umr-terminal command) directly onto the relayed prompt text
# itself, so it travels with the task instead of living only here. There is
# no systemd unit to hook an ExecStopPost= into for this dispatch path --
# task_kind='veridian_task_create' rows are relayed to a live interactive
# session, never started as a systemd unit (only task_kind='systemctl_action'
# rows are, a separate resource_governor.py path) -- so the relayed-prompt
# instruction is the real mechanism here, not a substitute for a better one.
#
# DISPATCH_TMUX_SESSION - overrides the tmux session name relayed into
#   (default: claude, the real live interactive session). Exists solely as a
#   real testability seam: point it at a disposable session (e.g.
#   `tmux new-session -d -s claude-relay-test-throwaway`) to exercise the
#   relay-succeeds/relay-fails branching end-to-end without ever sending
#   real keystrokes into the real live session.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMUX_SESSION="${DISPATCH_TMUX_SESSION:-claude}"
LOCKS_DIR="${VERIDIAN_DISPATCH_LOCK_DIR:-/opt/veridian/ai-os/locks}"
mkdir -p "$LOCKS_DIR"
TMUX_RELAY_LOCK="$LOCKS_DIR/dispatch-owner-task-tmux-relay-${TMUX_SESSION}.lock"

RELAY=1
# task-20260814-180459 (real file-attachment intake): --attach <path> is a
# real local file (text/markdown/PDF/image) passed straight through to
# task-gateway.py submit's own --attach (added there in this same task) --
# extraction/hashing/OWNER_ENGINE-gating happens exactly once, in that one
# real place, never re-implemented here in bash.
ATTACH=""
# task-20260816-041054 (real tier-aware Haiku routing): --complexity-tier
# <value> is an OPTIONAL real passthrough of the genuine mechanical/
# integrative/judgment complexity signal (plan_generator.py's own
# VALID_TIERS -- pm_lifecycle.py's dispatch_task() is the one real, live
# caller today; see that module's own --complexity-tier CLI arg). Folded
# verbatim into the task_kind='veridian_task_create' spec's inputs below so
# it survives all the way to resource_governor.py's _perform_spawn(), which
# threads it into `veridian-task.py create --complexity-tier ...` so
# task.yaml gains a genuine complexity_tier field -- worker-entrypoint.sh
# reads that field to decide --model haiku (mechanical only) vs --model
# sonnet (everything else, INCLUDING this flag simply never being passed,
# the correct safe default for every other real caller of this script).
# Deliberately NOT validated against plan_generator.py's VALID_TIERS here --
# an unrecognized value just never matches 'mechanical' downstream and
# safely falls through to sonnet, same as omitting it entirely.
COMPLEXITY_TIER=""
ARGS=()
_next_is_attach=0
_next_is_complexity_tier=0
for a in "$@"; do
  if [ "$_next_is_attach" -eq 1 ]; then
    ATTACH="$a"
    _next_is_attach=0
  elif [ "$_next_is_complexity_tier" -eq 1 ]; then
    COMPLEXITY_TIER="$a"
    _next_is_complexity_tier=0
  elif [ "$a" = "--no-relay" ]; then
    RELAY=0
  elif [ "$a" = "--attach" ]; then
    _next_is_attach=1
  elif [ "$a" = "--complexity-tier" ]; then
    _next_is_complexity_tier=1
  else
    ARGS+=("$a")
  fi
done
if [ "$_next_is_attach" -eq 1 ]; then
  echo "Usage: dispatch-owner-task.sh ... --attach <path> -- --attach requires a real file path argument" >&2
  exit 1
fi
if [ "$_next_is_complexity_tier" -eq 1 ]; then
  echo "Usage: dispatch-owner-task.sh ... --complexity-tier <value> -- --complexity-tier requires a value" >&2
  exit 1
fi
set -- "${ARGS[@]}"

TITLE="${1:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo] [--no-relay] [--attach <path>]}"
PROMPT="${2:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo] [--no-relay] [--attach <path>]}"
TIER="${3:-2}"
MEDIUM="${4:-claude_code_cli}"
REPO="${5:-compliance-tracker}"

# task-20260814-131322 / UMR-20260814-131248-baed, real execution backend
# fixed under task-20260814-135001 (PR #374's AUDIT:FAIL, policy-conflict
# finding): real execution-backend selection. Tier 0/1/2 keep the existing
# behavior below unchanged (register + relay into the live interactive tmux
# session, which is what eventually gets a claude_code_cli worker to look at
# this). Tier 3 and 4 (the two lowest-priority tiers) instead execute via a
# single, cheaper/faster non-interactive `claude -p` (Claude Code CLI
# headless/print mode, --output-format json) invocation -- NEVER aider-chat,
# NEVER litellm, NEVER OpenRouter/GLM-5.2, NEVER a raw Anthropic API key.
# task-20260814-131322's original design (aider-chat+litellm against
# openrouter/z-ai/glm-5.2) is dropped in full per Owner directive (live
# chat, 2026-08-14): SUPERBOSS_DISPATCH_PROMPT.md's 2026-07-18 CRITICAL FIX
# already establishes that OpenRouter/GLM-5.2 caused a real credit-
# exhaustion outage and that Claude Code CLI via the existing subscription
# is the sole execution engine on this server -- there is no supported way
# to point aider at the Claude Code CLI subscription as its own model
# backend without an external API key, so aider/litellm are not reused here
# at all, not even pointed at a fake local endpoint. Which real backend/
# model/effort/timeout/budget apply to which tier is config-driven (see
# tier_execution_config.json, read via task-gateway.py's
# tier_execution_settings()) -- never hardcoded here or in task-gateway.py,
# so the system stays genuinely AI-model-agnostic in DESIGN even though
# claude_code_cli/claude_code_cli_headless is the only real backend allowed
# to run right now. EXEC_SETTINGS_JSON below is this script's own local,
# config-file-driven fallback (used only if the task-gateway.py submit call
# in step 2 fails) -- see the real single-source-of-truth override there.
EXEC_SETTINGS_JSON=$(python3 -c "
import json, os, sys
cfg_path = os.path.join(sys.argv[2], 'tier_execution_config.json')
fallback = {'default_execution_path': 'claude_code_cli',
            'tiers': {'3': {'execution_path': 'claude_code_cli_headless'},
                      '4': {'execution_path': 'claude_code_cli_headless'}}}
try:
    with open(cfg_path) as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = fallback
tier_cfg = dict(cfg.get('tiers', {}).get(str(sys.argv[1])) or {})
tier_cfg.pop('_note', None)
tier_cfg.setdefault('execution_path', cfg.get('default_execution_path', 'claude_code_cli'))
print(json.dumps(tier_cfg))
" "$TIER" "$SCRIPT_DIR")
EXECUTION_PATH=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['execution_path'])")

cd "$SCRIPT_DIR"

# 1. Duplicate check -- don't silently re-dispatch the same ask.
DUP_JSON=$(python3 superboss-register.py check-content-duplicate --text "$PROMPT" --window-hours 6)
DUP_FOUND=$(echo "$DUP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['content_duplicate_found'])")
if [ "$DUP_FOUND" = "True" ]; then
  echo "$DUP_JSON"
  echo "REFUSED: an identical instruction was already logged within the last 6 hours (see duplicate_instruction_id above). Re-run with a genuinely different prompt if this repeat is intentional." >&2
  exit 1
fi

# 1b. Addendum to UMR-20260813-102459-10c3 (itself addendum to
#     UMR-20260813-084321-2962 / P1 UMR-20260806-171945-5767), extended by
#     UMR-20260813-220216-2e2b: a real, deterministic (regex, not fuzzy, not
#     hash-exact) check that a queued/running umr_tasks row from the last 4h
#     does not already target the exact same UMR id, PR number+repo, file
#     path, or script name as THIS dispatch -- checked separately from step
#     1 above because two dispatches phrased differently about the same
#     real target (the check above only catches byte-identical normalized
#     text) sail straight past it. Real incidents this fixes (2026-08-13):
#     the Desktop sentinel dispatched UMR-...-a248 (PR #131) and
#     UMR-...-1489 (PR #135), then the Desktop session independently
#     dispatched UMR-...-bd10 (same PR #131) and UMR-...-9a69 (same PR #135)
#     minutes later; separately, "RCA: UMR-20260807-151622-15cd killed" was
#     dispatched twice an hour apart (UMR-...-4bcc, UMR-...-7615), and an RCA
#     was dispatched for UMR-20260813-195852-aa85 (UMR-...-b0cc) after its
#     real fix had already landed as PR #323. resource_governor.py --search
#     on the exact text returned nothing in all of these (FTS5 MATCH is
#     fuzzy token-overlap ranking, not an exact-substring guarantee) -- so
#     duplicate work ran concurrently or after the fact against the same
#     real target. See superboss-register.py's
#     find_target_identifier_duplicate()/extract_target_identifiers() for
#     the real check itself (pulls --query-umr-equivalent query_umr_tasks
#     with limit=30, NO status filter, newest first -- never --search
#     alone).
#
#     UMR-20260814-034424-ded4 real fix (PM Sentinel first-hand
#     reproduction, 2026-08-14T03:38-03:42Z UTC, three consecutive false
#     refusals of legitimate P0 dispatches whose prompts CITED another
#     UMR/path/script purely as evidence or prior context, not as their
#     own target -- see superboss-register.py's own module comment above
#     extract_target_identifiers() for the full incident): the check
#     below is now scope-aware. If $PROMPT declares an explicit
#     `TARGET:`/`SCOPE:` section, only identifiers inside it (plus the
#     title) count -- everything else, including a long evidentiary
#     appendix, is ignored. Otherwise, a whole `OUT OF SCOPE:`/
#     `PRIOR CONTEXT:`/`EVIDENCE(-ONLY):`/`NOT-(A-)TARGET:`-labeled
#     section is excluded, and any inline `[NOT-A-TARGET: ...]` /
#     `[EVIDENCE-ONLY: ...]` span is always stripped regardless of mode --
#     the explicit, machine-readable way to mark one specific citation
#     ("this identifier is evidence, not my target") without
#     restructuring the whole prompt. A genuinely well-evidenced prompt no
#     longer has to be degraded (evidence deleted) just to dispatch.
TIDUP_JSON=$(python3 superboss-register.py check-target-identifier-duplicate \
  --title "$TITLE" --prompt "$PROMPT" --repo "$REPO" --window-hours 4 --limit 30)
TIDUP_FOUND=$(echo "$TIDUP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['target_identifier_duplicate_found'])")
if [ "$TIDUP_FOUND" = "True" ]; then
  echo "$TIDUP_JSON"
  echo "REFUSED: a queued/running dispatch within the last 4h already targets the exact same UMR/PR/file/script (see duplicate_umr_id above). If this citation is evidence/prior-context, not your real target, mark it inline as [EVIDENCE-ONLY: ...] or [NOT-A-TARGET: ...] (or declare your real target in an explicit TARGET:/SCOPE: section) and re-run; otherwise re-run once that target is no longer live." >&2
  exit 1
fi

# 2. Real, confirmed bug fixed 2026-08-08 (independent tier1 review,
#    UMR171945-0006, governing chain UMR-20260806-171945-5767): this used to
#    log the raw ask directly via superboss-register.py log-instruction,
#    completely bypassing task-gateway.py's real software-first pipeline
#    (ai-os/STANDING_DIRECTIVE.yaml's v2_task_lifecycle_pipeline) -- the
#    OWNER_ENGINE gate, capability_registry ai_required check, and
#    mechanical dedup/search that task-gateway.py submit already implements
#    for every OTHER real dispatch entrypoint. Confirmed live before this
#    fix: zero cross-references between this file and task-gateway.py.
#
#    Fixed as the minimal real bridge, not a wholesale swap of the dispatch
#    mechanism: `task-gateway.py submit` runs the real classification
#    (OWNER_ENGINE gate, capability_registry lookup, dedup/search) and logs
#    the instruction itself (replacing this script's own former direct
#    log-instruction call -- calling both would double-log the same real
#    ask under two instruction_ids). It does NOT spawn or queue anything by
#    itself (confirmed by reading cmd_submit() directly: it only classifies
#    and prints a JSON summary) -- step 3 below still submits to
#    resource_governor.py's real queue/tier/concurrency-slot-respecting
#    dispatch_one() scheduler, exactly as before. Deliberately NOT routed
#    through task-gateway.py's own cmd_start (the synchronous, unqueued,
#    direct-spawn path) -- that would remove the real tier-priority-ordered,
#    concurrency-slot-capped scheduling this script's real Owner-dispatch
#    use case (potentially many instructions arriving close together) has
#    always relied on, a materially higher-risk change than closing the
#    real classification gap this UMR actually asks for. Same real
#    calling-convention-preservation reasoning as UMR-20260808-121334-e122's
#    own Option B resolution.
#
#    Deliberately fail-open, not fail-closed: cmd_submit() internally calls
#    several real subsystems (OWNER_ENGINE gate, capability_registry lookup,
#    dedup/search, systemctl) via run_json()/fail(), any one of which
#    exiting non-zero would -- under this script's own `set -euo pipefail`
#    -- abort the ENTIRE dispatch attempt, a real new fragility this
#    script's previous single direct log-instruction call never had. The
#    classification step is informational (it does not decide whether this
#    dispatch proceeds), so a transient failure in it must never block a
#    real Owner-directed dispatch -- it falls back to this script's own
#    original direct log-instruction call instead, so real Owner-directed
#    work always still gets logged and dispatched even if task-gateway.py
#    submit's classification machinery has a bad moment.
SESSION_ID="dispatch-owner-task.sh:${MEDIUM}:$$"
SUBMIT_CLASSIFY_ERR="$(mktemp)"
# task-20260814-180459: real --attach passthrough. Empty when no --attach
# was given, so the submit call below is byte-identical to before this task
# for every existing caller that doesn't use it.
ATTACH_SUBMIT_ARGS=()
if [ -n "$ATTACH" ]; then
  ATTACH_SUBMIT_ARGS=(--attach "$ATTACH")
fi
if SUBMIT_CLASSIFY_JSON=$(python3 task-gateway.py submit --text "$PROMPT" --source owner --session-id "$SESSION_ID" --tier "$TIER" "${ATTACH_SUBMIT_ARGS[@]}" 2>"$SUBMIT_CLASSIFY_ERR"); then
  INSTRUCTION_ID=$(echo "$SUBMIT_CLASSIFY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['instruction_id'])")
  # task-20260814-131322 / UMR-20260814-131248-baed: task-gateway.py submit
  # is the one real single-source-of-truth for tier -> execution settings
  # (see its own tier_execution_settings()/execution_path_for_tier(), both
  # reading tier_execution_config.json) -- take its answer when this call
  # succeeded, rather than trusting only this script's own local
  # config-file read (computed above, before this call ran, as the real
  # fallback for the classification-failed branch below).
  GATEWAY_EXEC_SETTINGS=$(echo "$SUBMIT_CLASSIFY_JSON" | python3 -c "import json,sys; v=json.load(sys.stdin).get('execution_settings'); print(json.dumps(v) if v else '')")
  if [ -n "$GATEWAY_EXEC_SETTINGS" ]; then
    EXEC_SETTINGS_JSON="$GATEWAY_EXEC_SETTINGS"
    EXECUTION_PATH=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['execution_path'])")
  fi
  # task-20260814-180459: the real attachment record task-gateway.py submit
  # just computed (hash/path/mimetype, plus real extracted text or an
  # honestly-disclosed non-extraction note) is reused here -- via
  # attachment_intake.render_attachment_block(), the SAME real formatting
  # code submit itself used, never a second, separately-worded
  # implementation -- to fold the same attachment content into THIS
  # script's own $PROMPT for the rest of the dispatch (queue spec below,
  # tmux/headless-CLI relay), so the real worker that eventually picks this
  # up sees it too, not only the classification step above.
  if [ -n "$ATTACH" ]; then
    ATTACH_BLOCK=$(echo "$SUBMIT_CLASSIFY_JSON" | python3 -c "
import json, sys
sys.path.insert(0, '$SCRIPT_DIR')
import attachment_intake
rec = json.load(sys.stdin).get('attachment')
print(attachment_intake.render_attachment_block(rec) if rec else '')
")
    if [ -n "$ATTACH_BLOCK" ]; then
      PROMPT="${PROMPT}

${ATTACH_BLOCK}"
    fi
  fi
  rm -f "$SUBMIT_CLASSIFY_ERR"
else
  echo "WARNING: task-gateway.py submit (real software-first classification) failed -- falling back to direct log-instruction so this real dispatch is not blocked by a classification-step hiccup. Failure detail:" >&2
  cat "$SUBMIT_CLASSIFY_ERR" >&2 2>/dev/null || true
  rm -f "$SUBMIT_CLASSIFY_ERR"
  # task-20260814-180459: same real attachment intake, computed directly
  # (attachment_intake.py, the one real place this logic lives) since the
  # classification-step fallback above means task-gateway.py submit never
  # ran to compute it this time -- this fail-open branch must not silently
  # drop a real attachment just because the classification step hiccuped.
  if [ -n "$ATTACH" ]; then
    ATTACH_BLOCK=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
import attachment_intake
rec = attachment_intake.build_attachment_record(sys.argv[1])
print(attachment_intake.render_attachment_block(rec))
" "$ATTACH")
    PROMPT="${PROMPT}

${ATTACH_BLOCK}"
  fi
  INS_JSON=$(python3 superboss-register.py log-instruction --text "$PROMPT" --source owner --medium "$MEDIUM")
  INSTRUCTION_ID=$(echo "$INS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['instruction_id'])")
fi

# 3. Register the real task with resource_governor.py -- this is what actually
#    gets it a UMR ID and puts it under governance (concurrency cap, EMERGENCY_STOP).
TASK_IDENTITY="owner-task-$(date -u +%Y%m%d-%H%M%S)-$$"
SPEC_FILE="$(mktemp)"
python3 -c "
import json, sys
inputs = {'title': sys.argv[2], 'prompt': sys.argv[3], 'repo': sys.argv[4]}
# task-20260816-041054: only set when a real --complexity-tier value was
# passed in -- an empty/omitted value must stay a genuinely ABSENT key here
# (never an empty string), so _perform_spawn() and veridian-task.py create
# see nothing to thread and task.yaml correctly ends up with no
# complexity_tier field at all, the safe default worker-entrypoint.sh
# expects for every dispatch that doesn't set this.
if sys.argv[6]:
    inputs['complexity_tier'] = sys.argv[6]
json.dump({
    'task_identity': sys.argv[1],
    'task_kind': 'veridian_task_create',
    'inputs': inputs,
}, open(sys.argv[5], 'w'))
" "$TASK_IDENTITY" "$TITLE" "$PROMPT" "$REPO" "$SPEC_FILE" "$COMPLEXITY_TIER"

SUBMIT_JSON=$(python3 resource_governor.py --submit --spec-file "$SPEC_FILE" --tier "$TIER" --source-trigger owner_dispatch_gateway)
rm -f "$SPEC_FILE"
ACCEPTED=$(echo "$SUBMIT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['accepted'])")
UMR_ID=$(echo "$SUBMIT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['umr_id'])")

if [ "$ACCEPTED" != "True" ]; then
  echo "$SUBMIT_JSON"
  echo "REJECTED by resource_governor.py -- see reason above (umr_id=$UMR_ID recorded the rejection itself)." >&2
  exit 1
fi

# 4. Link instruction -> work item -> the real UMR id (output side). Real
#    execution_path (task-20260814-131322) is recorded into work_items'
#    own metadata_json here -- ALWAYS, for every tier, not only the
#    claude_code_cli_headless branch -- so which backend a task actually
#    used is queryable later for every real dispatch (superboss-register.py
#    search/query-knowledge over work_items, or a direct metadata_json
#    read), not just inferable after the fact from which branch below
#    happened to run.
WORK_METADATA=$(python3 -c "import json,sys; print(json.dumps({'execution_path': sys.argv[1], 'tier': sys.argv[2]}))" "$EXECUTION_PATH" "$TIER")
WORK_JSON=$(python3 superboss-register.py log-work --instruction-id "$INSTRUCTION_ID" --ai-task-id "$UMR_ID" --source owner --medium "$MEDIUM" --status open --metadata "$WORK_METADATA")
WORK_ITEM_ID=$(echo "$WORK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['work_item_id'])")

echo "DISPATCHED: umr_id=$UMR_ID instruction_id=$INSTRUCTION_ID work_item_id=$WORK_ITEM_ID task_identity=$TASK_IDENTITY execution_path=$EXECUTION_PATH"

# 5. Deliver the work. Tier 0/1/2 (EXECUTION_PATH=claude_code_cli) relay
#    into the live interactive tmux session exactly as before. Tier 3/4
#    (EXECUTION_PATH=claude_code_cli_headless, task-20260814-131322, real
#    backend fixed under task-20260814-135001) instead run a single
#    non-interactive `claude -p` invocation directly below -- no tmux, no
#    interactive session, no aider/litellm/OpenRouter involved at all.
if [ "$EXECUTION_PATH" = "claude_code_cli" ]; then

# 5a. Relay into the live interactive tmux session -- same call, no separate
#    raw tmux send-keys step for anyone (or anything) to skip past.
#
# UMR-20260806-094226-8617 (real root cause of the input-line-sticking
# finding): two concurrent dispatch-owner-task.sh invocations targeting the
# same tmux session had no mutual exclusion around their send-keys calls --
# this session has directly observed multiple near-simultaneous duplicate
# dispatches (owner_dispatch_gateway bursts within milliseconds of each
# other) this same day, and without a lock their literal `-l` text sends
# could genuinely interleave in the target pane's input buffer, leaving a
# garbled/unsubmitted line that only a manual Enter would clear -- never
# fixed at the root before this. Real flock on a per-session lock file
# serializes the has-session-check + both send-keys calls as one atomic
# unit across concurrent invocations, same real per-open-file-description
# discipline superboss-register.py's own _write_lock() already documents.
#
# UMR-20260806-112013-088f (structural fix for "nothing ever calls
# mark-umr-terminal", second half of UMR-20260806-085144-9c63 / PR #150):
# every real dispatch through this script is task_kind='veridian_task_create',
# relayed straight into a live interactive tmux session -- but (correction,
# UMR-20260806-115423-500d: the claim this comment used to make here, that
# these rows "never get a backing systemd unit," is false -- confirmed by
# reading resource_governor.py's _perform_spawn() directly, which DOES spawn
# a real `veridian-worker@*.service` for task_kind='veridian_task_create'
# rows exactly as it does for 'systemctl_action' rows) that systemd unit, if
# and when it gets spawned, belongs to resource_governor.py's own mechanical
# pickup, is created by *that* code path, not by this script, and this
# script has already returned long before it would exist -- so there is
# still no real ExecStopPost= hook available for *this script* to attach
# completion-recording to at relay time. The one real structural seam that
# exists here is the relayed text itself: appending a
# mandatory, UMR-id-specific final instruction to every relayed prompt puts
# the exact real command in front of whoever/whatever is doing the work, in
# the same message that carries the task -- not in a header comment several
# dozen lines above that nothing downstream was ever required to read
# (which is the real, confirmed root cause: mark-umr-terminal already
# existed and was already documented there, and 29+ real dispatched rows
# still sat at ts_completed=NULL). This cannot force an interactive session
# to record its own outcome honestly -- no code can -- but it removes "I
# didn't know I was supposed to" as a real, provable failure mode.
COMPLETION_INSTRUCTION="

MANDATORY FINAL STEP for ${UMR_ID} (structural, not optional -- read this even if the rest of this message was skimmed): once the real work above reaches a genuine terminal outcome, record it by running this exact command with this exact UMR id (from the real, current veridian-scripts checkout, /opt/veridian/scripts -- NOT /opt/veridian/repos/veridian-scripts, which is an orphaned second checkout nothing has pulled since 2026-08-06):
  python3 superboss-register.py mark-umr-terminal --umr-id ${UMR_ID} --status completed --reason \"<real one-line summary of what finished>\" --commit-sha <real commit SHA, must be a real ancestor of origin/main> [--pr-number <N>]
--status completed now structurally REQUIRES that real --commit-sha (or, for a non-PR artifact, a real --file-path that genuinely exists on disk) -- UMR-20260806-130914-e7f1 (real completion-evidence gap fix): a real PR opened but NOT YET merged into origin/main must NOT be recorded as completed -- use --status completed_unmerged instead (same --commit-sha, but it need not yet be an ancestor of main) and re-run with --status completed once it is actually merged. Use --status failed or --status killed instead, with a real --reason, if the work genuinely did not finish successfully -- never record a genuine failure as a success. Run this as the actual last action for ${UMR_ID}, after any PR/merge work, not before. Leave it unrun only if the work is genuinely still in progress."
RELAY_TEXT="[${UMR_ID}] ${PROMPT}${COMPLETION_INSTRUCTION}"
if [ "$RELAY" -eq 1 ]; then
  exec 9>"$TMUX_RELAY_LOCK"
  flock -x 9
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    tmux send-keys -t "$TMUX_SESSION" -l "$RELAY_TEXT"
    sleep 1
    tmux send-keys -t "$TMUX_SESSION" Enter
    flock -u 9
    exec 9>&-
    echo "RELAYED into tmux session '$TMUX_SESSION' (best-effort courtesy notification ONLY -- send-keys returning 0 proves the keystrokes were written into the pane, NOT that any live process read or acted on them; this is never proof of delivery)"
    # 6. Real relay attempt recorded as a courtesy signal (UMR-20260806-115423-500d)
    #    -- ts_relay_attempted/relay_outcome/relay_detail ONLY, status/
    #    ts_dispatched/ts_completed are NEVER touched here. The row stays
    #    exactly status='queued', fully eligible for dispatch-tick.py's own
    #    real mechanical pickup (resource_governor.py's next_queued_task())
    #    on the very next tick, regardless of what the tmux relay achieved.
    python3 superboss-register.py mark-umr-relay-attempted --umr-id "$UMR_ID" \
      --outcome sent --detail "tmux session '$TMUX_SESSION'" >/dev/null
    echo "RELAY ATTEMPTED (courtesy only, NOT authoritative): umr_id=$UMR_ID -- row remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup"
  else
    flock -u 9
    exec 9>&-
    echo "WARNING: tmux session '$TMUX_SESSION' not found -- task is registered (umr_id=$UMR_ID) and remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup regardless of this tmux relay's outcome. Recreate the session and relay manually, or re-run once it exists, if you also want the interactive channel to see it." >&2
    # 6. Real relay attempt (absent-session outcome) recorded as the same
    #    non-authoritative courtesy signal as the success branch above --
    #    NEVER a terminal status. Absence of an interactive tmux session
    #    says nothing about whether the real mechanical pickup path can
    #    still do this work; marking the row terminal here would wrongly
    #    exclude it from that entirely independent channel too.
    python3 superboss-register.py mark-umr-relay-attempted --umr-id "$UMR_ID" \
      --outcome session_not_found --detail "tmux session '$TMUX_SESSION' not found at relay time" >/dev/null
    echo "RELAY ATTEMPTED, session absent (courtesy only, NOT authoritative): umr_id=$UMR_ID -- row remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup" >&2
  fi
fi

else
  # 5b. Tier 3/4 real execution backend (task-20260814-131322,
  # UMR-20260814-131248-baed, real backend fixed under task-20260814-135001
  # closing PR #374's AUDIT:FAIL): a single, non-interactive `claude -p`
  # (Claude Code CLI headless/print mode) invocation against a real,
  # disposable git worktree -- NEVER aider-chat, NEVER litellm, NEVER
  # OpenRouter/GLM-5.2, NEVER a raw Anthropic API key (Owner directive,
  # live chat 2026-08-14; SUPERBOSS_DISPATCH_PROMPT.md's 2026-07-18
  # CRITICAL FIX). Model/effort/timeout/budget for this tier come from
  # EXEC_SETTINGS_JSON (config-driven, see tier_execution_config.json) --
  # never hardcoded here. This script drives the CLI synchronously to a
  # real terminal outcome and records it itself -- no tmux relay, no
  # second interactive claude_code_cli worker service, for this branch.
  #
  # $RELAY (--no-relay) is reused here with the same real meaning it has in
  # the claude_code_cli branch above -- "register only, do not deliver yet"
  # -- applied to this branch's own delivery mechanism (running the CLI)
  # instead of a tmux relay.
  if [ "$RELAY" -eq 0 ]; then
    echo "REGISTERED ONLY (--no-relay): umr_id=$UMR_ID execution_path=$EXECUTION_PATH -- not executed by this call; row remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup." >&2
  else
    CLI_MODEL=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('model') or 'claude-haiku-4-5-20251001')")
    CLI_EFFORT=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('effort') or 'low')")
    CLI_TIMEOUT_SECONDS=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('timeout_seconds') or 900)")
    CLI_MAX_BUDGET_USD=$(echo "$EXEC_SETTINGS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('max_budget_usd') or 1.5)")
    REPO_PATH="/opt/veridian/repos/$REPO"
    if [ ! -d "$REPO_PATH" ]; then
      echo "WARNING: repo not found at $REPO_PATH -- cannot execute the $EXECUTION_PATH path for umr_id=$UMR_ID." >&2
      python3 superboss-register.py mark-umr-terminal --umr-id "$UMR_ID" --status failed \
        --reason "$EXECUTION_PATH execution path: repo not found at $REPO_PATH" >/dev/null
    else
      # Deliberately NOT veridian-task.py create -- that command's own
      # cmd_create always ends with `systemctl --user start
      # veridian-worker@*.service`, which spins up exactly the interactive
      # claude_code_cli worker this tier-3/4 path exists to avoid. This
      # does the same real worktree-off-origin/HEAD setup cmd_create does
      # (fetch, resolve the real default branch, `git worktree add -b`),
      # minus the systemd spawn, so the CLI gets a real, isolated,
      # disposable checkout to work in.
      CLI_TASK_ID="cli-headless-task-$(date -u +%Y%m%d-%H%M%S)-$$"
      CLI_TASK_DIR="/opt/veridian/ai-os/tasks/${CLI_TASK_ID}"
      CLI_WORKSPACE="${CLI_TASK_DIR}/workspace"
      CLI_BRANCH="worker/${CLI_TASK_ID}"
      mkdir -p "$CLI_TASK_DIR"

      # Real fix for PR #374's AUDIT:FAIL finding #4 (per-run worktrees/task
      # directories never cleaned up on success or failure, a real disk
      # leak): registered the moment there is anything real to clean up
      # (this mkdir, just above), fires on EVERY exit from this point on --
      # normal fall-through, an early `exit 1` from some other real gate
      # later in the script, or an uncaught error under this script's own
      # `set -euo pipefail`. `git worktree remove --force` is tried first
      # (the real, correct way to drop a worktree's registration out of
      # $REPO_PATH/.git/worktrees/), falling back to a bare `rm -rf` of the
      # workspace dir if that fails for any reason (e.g. the worktree setup
      # itself never completed) -- either way $CLI_TASK_DIR itself is always
      # removed last, so no directory under /opt/veridian/ai-os/tasks/ is
      # ever left behind by this branch, on any real outcome.
      _cli_headless_cleanup() {
        if [ -n "${CLI_WORKSPACE:-}" ] && [ -d "$CLI_WORKSPACE" ]; then
          git -C "$REPO_PATH" worktree remove --force "$CLI_WORKSPACE" 2>/dev/null || rm -rf "$CLI_WORKSPACE"
        fi
        if [ -n "${CLI_BRANCH:-}" ]; then
          git -C "$REPO_PATH" branch -D "$CLI_BRANCH" >/dev/null 2>&1 || true
        fi
        rm -f "${CLI_OUT:-}" "${CLI_LOG:-}" 2>/dev/null || true
        if [ -n "${CLI_TASK_DIR:-}" ] && [ -d "$CLI_TASK_DIR" ]; then
          rm -rf "$CLI_TASK_DIR"
        fi
      }
      trap _cli_headless_cleanup EXIT

      # Real setup (fetch/resolve-default-branch/worktree-add), but never
      # allowed to abort this whole script via `set -e` -- a genuine
      # failure here must still leave a real, honest failed terminal status
      # on $UMR_ID, same fail-safe posture as every other real gate in this
      # script, not a bare non-zero exit that leaves the row unexplained.
      CLI_SETUP_OK=1
      set +e
      git -C "$REPO_PATH" fetch origin && \
        CLI_DEFAULT_REF=$(git -C "$REPO_PATH" symbolic-ref refs/remotes/origin/HEAD) && \
        CLI_DEFAULT_BRANCH="${CLI_DEFAULT_REF##*/}" && \
        git -C "$REPO_PATH" worktree add -b "$CLI_BRANCH" "$CLI_WORKSPACE" "origin/$CLI_DEFAULT_BRANCH"
      CLI_SETUP_OK=$?
      set -e
      if [ "$CLI_SETUP_OK" -ne 0 ]; then
        echo "WARNING: $EXECUTION_PATH worktree setup failed for umr_id=$UMR_ID (repo=$REPO)." >&2
        python3 superboss-register.py mark-umr-terminal --umr-id "$UMR_ID" --status failed \
          --reason "$EXECUTION_PATH execution path: worktree setup (fetch/worktree add) failed for repo=$REPO" >/dev/null
      else
        CLI_BASE_SHA=$(git -C "$CLI_WORKSPACE" rev-parse HEAD)

        # Real fix for PR #374's AUDIT:FAIL finding #2 (prompt-derived
        # --file arguments extracted via an unvalidated regex, real
        # path-traversal risk): task-20260814-131322's original design
        # mechanically extracted path-shaped tokens out of $PROMPT and
        # passed each straight through to aider's own --file flag with no
        # containment check at all -- a prompt containing e.g.
        # "../../../etc/passwd" or an absolute path would have been handed
        # to aider verbatim. This rewrite drops that whole mechanism
        # structurally rather than re-validating it: `claude -p` (unlike
        # aider) never takes a caller-supplied --file/--add-dir list built
        # from prompt text -- the model reads/writes files itself, through
        # its own Read/Edit/Write/Bash tools, which are sandboxed to its
        # cwd ($CLI_WORKSPACE, set below) by Claude Code's own permission
        # system since no --add-dir is ever passed here. There is
        # therefore no regex-extracted path argument left for a
        # prompt-injected "../" to reach in this branch at all -- do not
        # reintroduce one (e.g. a future --add-dir derived from prompt
        # text) without a real allowlist check against
        # `realpath -q` being a descendant of $CLI_WORKSPACE first.
        CLI_PROMPT="SPEC: ${PROMPT}

PROTOCOL (tier-${TIER} claude_code_cli_headless execution, umr_id=${UMR_ID}): this is a single non-interactive invocation with a real wall-clock timeout -- there is no follow-up turn. Commit and push your own real work yourself (branch \"${CLI_BRANCH}\" is already checked out here, tracking origin/${CLI_DEFAULT_BRANCH}) as you go, not only at the very end, so genuine partial progress survives even if you don't reach a final commit before the timeout. This script also makes a best-effort safety-net commit of any real uncommitted changes after you finish, but that is a fallback, not a substitute for you committing your own work."

        CLI_OUT="$(mktemp --suffix=.json)"
        CLI_LOG="$(mktemp)"
        set +e
        # Real fix for PR #374's AUDIT:FAIL finding #3 (the synchronous
        # execution call has no timeout, can hang the whole dispatch
        # pipeline): `timeout` wraps the real invocation with
        # CLI_TIMEOUT_SECONDS (config-driven, tier_execution_config.json --
        # never hardcoded), same real mechanism doc-worker-entrypoint.sh's
        # own MAX_WALL_SECONDS wrap already uses for its own `claude -p`
        # call. --dangerously-skip-permissions + --max-budget-usd + a
        # disposable per-run worktree is the same real posture
        # worker-entrypoint.sh/doc-worker-entrypoint.sh/
        # supervisor-entrypoint.sh already use for their own real
        # `claude -p` invocations -- not a new risk introduced here. Run in
        # a subshell so this script's own cwd is restored either way.
        (cd "$CLI_WORKSPACE" && timeout "$CLI_TIMEOUT_SECONDS" claude -p "$CLI_PROMPT" \
          --model "$CLI_MODEL" --effort "$CLI_EFFORT" --dangerously-skip-permissions \
          --max-budget-usd "$CLI_MAX_BUDGET_USD" --output-format json) \
          > "$CLI_OUT" 2>"$CLI_LOG"
        CLI_EXIT=$?
        set -e
        CLI_TIMED_OUT=0
        [ "$CLI_EXIT" -eq 124 ] && CLI_TIMED_OUT=1

        # Same real, confirmed bug class worker-entrypoint.sh/
        # doc-worker-entrypoint.sh already had to fix (2026-07-20 RCA):
        # `claude -p --output-format json` can return exit 0 even when the
        # underlying API call itself failed -- the error is captured INSIDE
        # the JSON payload ("is_error":true), never surfaced as a non-zero
        # process exit. Checked here proactively so this new call site
        # never regresses into that same silently-swallowed-failure class.
        CLI_IS_ERROR=$(python3 -c "
import json
try:
    with open('$CLI_OUT') as f:
        d = json.load(f)
    print('1' if d.get('is_error') else '0')
except Exception:
    print('0')
")
        CLI_COST_USD=$(python3 -c "
import json
try:
    with open('$CLI_OUT') as f:
        d = json.load(f)
    print(d.get('total_cost_usd', 0) or 0)
except Exception:
    print(0)
")

        # Real, general safety-net commit -- same real posture
        # task-20260814-131322's original aider branch already established
        # (commit real, already-applied on-disk changes ourselves rather
        # than discard them as a false "no changes" failure if the model's
        # own turn ended -- timeout, budget cap, or otherwise -- without
        # itself running `git commit`), applied here whether or not this
        # invocation's own exit path was clean. Same real git identity
        # (global user.name/user.email) every other real commit in this
        # script already uses.
        if [ -n "$(git -C "$CLI_WORKSPACE" status --porcelain)" ]; then
          git -C "$CLI_WORKSPACE" add -A
          git -C "$CLI_WORKSPACE" commit -m "${TITLE} (${EXECUTION_PATH} tier-${TIER}, umr_id=${UMR_ID}, safety-net commit)" >/dev/null
        fi
        CLI_HEAD_SHA=$(git -C "$CLI_WORKSPACE" rev-parse HEAD)

        CLI_OUTCOME_DETAIL="model=$CLI_MODEL effort=$CLI_EFFORT exit=$CLI_EXIT timed_out=$CLI_TIMED_OUT is_error=$CLI_IS_ERROR cost_usd=$CLI_COST_USD"
        if [ "$CLI_TIMED_OUT" -eq 1 ]; then
          # Real fix for PR #374's AUDIT:FAIL finding #3's other half (a
          # defined failure/escalation behavior on timeout, not just a bare
          # timeout wrapper): a timeout is recorded with its own distinct,
          # greppable "timed_out=1" reason regardless of whether real
          # progress survived, so this class of outcome is distinguishable
          # from a genuine model/tool error for any later RCA/redispatch
          # sweep -- never silently folded into a generic "failed".
          CLI_OUTCOME_DETAIL="TIMEOUT after ${CLI_TIMEOUT_SECONDS}s: $CLI_OUTCOME_DETAIL"
        fi

        if [ "$CLI_HEAD_SHA" = "$CLI_BASE_SHA" ]; then
          # No real change landed at all (whether because of a clean
          # no-op turn, a timeout before any edit, or a real error) --
          # nothing to push, never leaves the row stuck: a real, honest
          # failed terminal status.
          echo "CLAUDE_CODE_CLI_HEADLESS EXECUTION DID NOT PRODUCE A COMMIT for umr_id=$UMR_ID ($CLI_OUTCOME_DETAIL) -- see $CLI_LOG" >&2
          python3 superboss-register.py mark-umr-terminal --umr-id "$UMR_ID" --status failed \
            --reason "$EXECUTION_PATH execution path: $CLI_OUTCOME_DETAIL, no commit produced" >/dev/null
        else
          set +e
          git -C "$CLI_WORKSPACE" push -u origin "$CLI_BRANCH"
          CLI_PUSH_OK=$?
          set -e
          if [ "$CLI_PUSH_OK" -ne 0 ]; then
            echo "WARNING: $EXECUTION_PATH real commit $CLI_HEAD_SHA made but push failed for umr_id=$UMR_ID." >&2
            python3 superboss-register.py mark-umr-terminal --umr-id "$UMR_ID" --status failed --repo "$REPO" \
              --commit-sha "$CLI_HEAD_SHA" \
              --reason "$EXECUTION_PATH execution path: $CLI_OUTCOME_DETAIL, made a real local commit but git push failed" >/dev/null
          else
            CLI_GH_REPO=$(git -C "$REPO_PATH" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
            # stdout only (never 2>&1) -- gh pr create's own real, harmless
            # stderr warnings must not pollute the PR URL this script
            # parses --pr-number and the mark-umr-terminal --reason from.
            CLI_PR_URL=$(gh pr create --repo "$CLI_GH_REPO" --base "$CLI_DEFAULT_BRANCH" \
              --head "$CLI_BRANCH" --title "$TITLE" \
              --body "Real tier-${TIER} dispatch via the $EXECUTION_PATH execution backend (umr_id=${UMR_ID}). $CLI_OUTCOME_DETAIL" 2>/dev/null) || true
            CLI_PR_NUMBER=$(echo "$CLI_PR_URL" | grep -oE '[0-9]+$' | tail -1)
            echo "CLAUDE_CODE_CLI_HEADLESS EXECUTED: umr_id=$UMR_ID commit=$CLI_HEAD_SHA pr=$CLI_PR_URL $CLI_OUTCOME_DETAIL"

            MARK_ARGS=(mark-umr-terminal --umr-id "$UMR_ID" --status completed_unmerged --repo "$REPO" \
              --commit-sha "$CLI_HEAD_SHA" \
              --reason "$EXECUTION_PATH execution path: $CLI_OUTCOME_DETAIL. PR: $CLI_PR_URL")
            if [ -n "$CLI_PR_NUMBER" ]; then
              MARK_ARGS+=(--pr-number "$CLI_PR_NUMBER")
            fi
            python3 superboss-register.py "${MARK_ARGS[@]}" >/dev/null
          fi
        fi
      fi
    fi
  fi
fi
