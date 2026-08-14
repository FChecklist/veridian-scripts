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
- [ ] Real end-to-end tier-4 dispatch via `dispatch-owner-task.sh` proving the aider_litellm path actually completes, with measured token/cost delta recorded on the UMR row (real done criteria -- in progress, see next step).

## Remaining
- [ ] Run one real tier-4 `dispatch-owner-task.sh` call end-to-end and confirm: real commit + real PR opened via aider+litellm, `mark-umr-terminal --status completed_unmerged` recorded with real token/cost delta.
- [ ] Record completion via `agent_work_briefing.py record-completion --umr-id UMR-20260814-131248-baed`.
- [ ] Commit + push this real code change; open PR against main.
