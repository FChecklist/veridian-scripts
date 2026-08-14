# task-20260814-135001: fix PR #374's real AUDIT:FAIL, use Claude Code CLI

TARGET: veridian-scripts PR #374 (branch
`worker/task-20260814-131322-add-aider-chat-plus-litellm-execution-ba`),
worked on that SAME branch per the task SPEC -- not a new branch.
Cites UMR-20260814-131248-baed.

## Completed

- [x] Checked out PR #374's real branch in this workspace (not a new
      branch) and read the real diff vs `origin/main` to confirm the
      AUDIT:FAIL's 4 findings against the actual code.
- [x] Confirmed the Owner's policy citation live: `claude-control`'s
      `SUPERBOSS_DISPATCH_PROMPT.md` "CRITICAL FIX 2026-07-18" section --
      OpenRouter/GLM-5.2 caused a real credit-exhaustion outage; Claude
      Code CLI via the existing subscription is the sole execution engine
      on this server.
- [x] Finding #1 (policy conflict, OpenRouter/GLM-5.2 reintroduced) fixed:
      dropped aider-chat + litellm + OpenRouter/GLM-5.2 entirely from
      `dispatch-owner-task.sh`'s tier-3/4 branch. Replaced with a single
      non-interactive `claude -p ... --output-format json` (Claude Code CLI
      headless/print mode) invocation against a real disposable git
      worktree -- the same real mechanism worker-entrypoint.sh /
      doc-worker-entrypoint.sh / supervisor-entrypoint.sh already use.
      `execution_path` for this branch renamed `claude_code_cli_headless`
      (tier 0-2's existing `claude_code_cli` interactive-relay path is
      unchanged).
- [x] Added `tier_execution_config.json` (new file, repo root): the one
      real, single-source-of-truth tier -> execution-backend/model/effort/
      timeout/budget config, so the system stays AI-model-agnostic in
      DESIGN (config-driven mapping) even though `claude_code_cli`/
      `claude_code_cli_headless` is the only real backend allowed to run
      right now. `task-gateway.py`'s `execution_path_for_tier()` is now a
      thin accessor over a new `tier_execution_settings()`, both reading
      this file (with a fail-closed, same-shape fallback if it's ever
      unreadable). `cmd_submit`'s JSON response now also carries
      `execution_settings`. `dispatch-owner-task.sh` reads the same file
      directly as its own local fallback (used only if the
      `task-gateway.py submit` call fails), and otherwise takes
      `task-gateway.py`'s own answer -- same "one real single source of
      truth, local computation is only the fallback" pattern the branch
      already used for `execution_path` before this fix.
- [x] Finding #2 (prompt-derived `--file` args via an unvalidated regex,
      path-traversal risk) fixed structurally, not re-validated: dropped
      the whole mechanism. `claude -p` (unlike aider) never takes a
      caller-built `--file`/`--add-dir` list from prompt text -- the model
      reads/writes through its own tools, sandboxed to its cwd
      (`$CLI_WORKSPACE`) since no `--add-dir` is passed. There is no
      regex-extracted path argument left for a prompt-injected `../` to
      reach in this branch.
- [x] Finding #3 (no timeout, can hang the pipeline) fixed: the real
      invocation is wrapped in `timeout "$CLI_TIMEOUT_SECONDS"`
      (config-driven, default 900s). Defined escalation behavior on
      timeout: exit 124 is recorded with a distinct, greppable
      `timed_out=1`/`TIMEOUT after Ns` reason string, real partial
      progress (if any landed) is still committed+pushed and marked
      `completed_unmerged` (never silently discarded), and a timeout with
      zero real progress is marked `failed` with that same distinguishing
      reason -- never folded into a generic failure a later RCA/redispatch
      sweep can't tell apart from a real model/tool error. Also
      proactively ported the existing `is_error` JSON-payload check
      (worker-entrypoint.sh's own 2026-07-20 RCA fix for `claude -p`
      returning exit 0 on an underlying API failure) to this new call site
      so it doesn't regress into that same bug class.
- [x] Finding #4 (worktrees/task dirs never cleaned up) fixed: a
      `trap _cli_headless_cleanup EXIT` is registered the moment
      `$CLI_TASK_DIR` is created, firing on every real exit path (normal
      completion, an early `exit 1` elsewhere in the script, or an
      uncaught error under `set -euo pipefail`) -- `git worktree remove
      --force`, a local branch-ref cleanup, and `rm -rf` of the task dir
      and temp log/output files.
- [x] Added `tests/test_tier_execution_config.py` (10 tests, all passing):
      real config-file shape/values, `task-gateway.py`'s config-driven
      lookups against the real file, fail-closed behavior on a missing
      config, and structural regression guards on `dispatch-owner-task.sh`
      (real timeout wrapper present, real cleanup trap present, no
      executable aider/OpenRouter/GLM-5.2 call sites left -- comments
      referencing the dropped design by name are fine, an executable
      invocation is not).
- [x] Ran the existing real test suites that exercise this code
      (`tests/test_dispatch_owner_task_status_write.py` 14/14,
      `tests/test_task_gateway_zoekt_search.py` +
      `tests/test_task_start_gate.py` +
      `tests/test_mark_umr_terminal_structured_evidence.py` 31/31) --
      all still pass; `bash -n dispatch-owner-task.sh` and
      `python3 -m py_compile task-gateway.py` both clean.

## Remaining

- [ ] Push this commit to PR #374's real branch (same branch, not a new
      PR) and request a fresh AUDIT:PASS against the new head -- this task
      is not self-certified complete until that real, independent
      AUDIT:PASS lands.
- [ ] Record completion via
      `agent_work_briefing.py record-completion --umr-id UMR-20260814-134953-c33d`
      once the fresh AUDIT:PASS is confirmed.
