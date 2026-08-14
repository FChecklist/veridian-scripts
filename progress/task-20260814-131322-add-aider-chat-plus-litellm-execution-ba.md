# PROGRESS -- task-20260814-131322-add-aider-chat-plus-litellm-execution-ba

## Completed
- [x] Verified aider-chat 0.86.2 + litellm 1.81.10 installed but had zero call sites in the dispatch path (matches EVIDENCE-ONLY note).
- [x] Confirmed no GLM-5.2-proxy routing unit is loaded; litellm talks to OpenRouter directly instead (OPENROUTER_API_KEY already in /opt/veridian/shared/.env).
- [x] Read compliance-tracker's ai_model_registry / ai_routing_policies schema (drizzle/0231_ai_router_mother_router.sql) for seed policy shape only (provider/model/cost_per_1k_input/cost_per_1k_output) -- used to build aider's --model-metadata-file, code not copied.
- [x] task-gateway.py: added `--tier` (optional) to `submit`, and `execution_path_for_tier()` -- the one real tier->execution_path mapping (0/1/2 -> claude_code_cli, 3/4 -> aider_litellm), surfaced in submit's JSON response as `tier`/`execution_path`.
- [x] dispatch-owner-task.sh: computes EXECUTION_PATH locally from `$TIER` as a fallback, then overrides it from task-gateway.py submit's own `execution_path` field when that call succeeds (single source of truth).
- [x] dispatch-owner-task.sh: step 4 (log-work) now always records `execution_path`+`tier` into work_items.metadata_json -- queryable later for every dispatch, not only the new path.
- [x] dispatch-owner-task.sh: step 5 branches on EXECUTION_PATH. claude_code_cli keeps the existing tmux relay unchanged. aider_litellm (tier 3/4) instead: builds a real disposable worktree directly (fetch + `git worktree add`, deliberately NOT veridian-task.py create, which always starts a claude_code_cli systemd worker), runs `aider --model openrouter/z-ai/glm-5.2 --model-metadata-file ... --yes-always --message "$PROMPT"`, parses aider's own real Tokens/Cost usage-report line, and on a real commit pushes + opens a real PR + calls `mark-umr-terminal --status completed_unmerged` with the measured cost/token delta in `--reason`; on failure at any stage calls `mark-umr-terminal --status failed` with a real reason (never leaves the row stuck, `set -e`-safe via explicit exit-code checks).
- [x] Verified bash syntax (`bash -n`) and python syntax (`py_compile`) both pass.
- [x] Smoke-tested `task-gateway.py submit --tier 4` / `--tier 2` against the real DB: `execution_path` correctly resolves to `aider_litellm` / `claude_code_cli`.
- [x] Opened PR #374 (FChecklist/veridian-scripts) for the real code change to dispatch-owner-task.sh + task-gateway.py.
- [x] Real end-to-end tier-4 dispatch executed for real via `./dispatch-owner-task.sh "Tier-4 aider+litellm real execution path test" "..." 4 ssh_session compliance-tracker`:
      umr_id=UMR-20260814-132552-13cf, execution_path=aider_litellm, model=openrouter/z-ai/glm-5.2,
      real commit 7edc46922b47694dc8b160fdf278e465b4d76388, real PR opened
      https://github.com/FChecklist/compliance-tracker/pull/1161 (NOTES/aider-litellm-tier4-real-test.md, +1),
      measured delta: Tokens: 1.9k sent, 136 received. Cost: $0.00096 message, $0.00096 session.
      `resource_governor.py --query-umr --umr-id UMR-20260814-132552-13cf` confirms status=completed_unmerged,
      tier=4, ts_completed set, reason carries the real token/cost delta + PR link.
- [x] Post-run cleanup: fixed the Tokens:/Cost: log-parsing regex (was truncating at the first `.` inside "1.9k")
      and stopped `gh pr create`'s own harmless stderr warning (an aider-managed .gitignore tweak left
      uncommitted in the disposable worktree, never part of the real pushed diff) from polluting the captured
      PR URL -- verified both regex fixes directly against the real captured aider log line from the run above.

- [x] Live-found (2nd real dispatch, UMR-20260814-132851-7e1b) and fixed: aider resolves its own git root/side-effect
      files from process cwd, not a positional arg -- .aider.chat.history.md leaked into THIS task's own
      veridian-scripts checkout on the first run. Fixed by `cd`-ing into $AIDER_WORKSPACE in a subshell before
      invoking aider. Also found: passing the whole worktree dir as aider's only positional arg makes it treat
      existing files as read-only repo-map (fine for create-new-file prompts, real refusal for edit-existing-file
      prompts) -- fixed by mechanically extracting path-shaped tokens from $PROMPT and passing each via aider's
      own --file flag, plus --map-tokens 0 to skip the (slow, now largely unneeded) full repo scan.
- [x] Live-found (3rd real dispatch, UMR-20260814-133127-f8cd) and fixed: aider's auto-commit did not fire for an
      edit applied to a brand-new file added via --file for a path that didn't exist yet -- the edit landed on
      disk (confirmed via `git status --porcelain`) but stayed uncommitted. Fixed with a same-identity
      `git add -A && git commit` fallback when aider produced real uncommitted changes but no new commit.
- [x] 4th real dispatch (UMR-20260814-133305-e6aa) with all three fixes applied: clean run, real commit
      2f766f0a6ae8493905152205cc575fc9e7ff523f, real PR https://github.com/FChecklist/compliance-tracker/pull/1163,
      status=completed_unmerged, reason carries the real measured delta (Tokens: 700 sent, 303 received.
      Cost: $0.00069 message, $0.00069 session.), and no stray files left in this task's own checkout.

- [x] Recorded completion via `agent_work_briefing.py record-completion --umr-id UMR-20260814-131248-baed`.
- [x] Marked this task's own UMR-20260814-131248-baed `completed_unmerged` (commit 5ff12288, PR #374).

## Remaining
- none -- task complete. PR #374 (FChecklist/veridian-scripts) awaiting review/merge.
