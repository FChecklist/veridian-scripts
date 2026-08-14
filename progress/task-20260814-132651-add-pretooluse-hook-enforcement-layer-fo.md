# PROGRESS -- task-20260814-132651-add-pretooluse-hook-enforcement-layer-fo

## SPEC
Add a real PreToolUse hook-enforcement layer around dispatched-worker
execution: (1) block git commit/push/write outside a worker's own assigned
repo+branch/workspace, (2) block raw `tmux send-keys` and a small set of
queue-bypassing script/systemctl invocations from inside a worker session,
(3) append every tool call to a queryable audit log. Must be a real,
mechanical block (independent of prompt text), not just logging -- proven
with a real, captured test/output, not a citation.

## Completed
- [x] Researched the real, live worker execution environment before writing
      anything: confirmed `~/.claude/settings.json` already wires a real
      PreToolUse hook system (`hooks/find_root_walk_guard.py`, matcher
      `Bash`) that mechanically blocked one of MY OWN commands live during
      this task -- direct, first-hand proof the mechanism this task adds to
      is real and already load-bearing for every dispatched worker (they all
      share this same user-level settings.json, since worker-entrypoint.sh's
      systemd units run with no `User=` override).
- [x] Found the real, kernel-enforced way to identify "which task's worker
      am I" independent of `cwd` (so a worker that `cd`s away from its own
      workspace can't evade enforcement): this process's own
      `/proc/self/cgroup` contains the real systemd unit name
      `veridian-worker@<task_id>.service` -- confirmed live against this
      exact task's own cgroup path.
- [x] Added `hooks/pretooluse_worker_enforcement.py`, a new PreToolUse hook
      (matcher `*`, so it also sees Write/Edit/NotebookEdit, unlike the
      existing Bash-only hooks) implementing all three real checks:
      1. `check_git_write`/`check_write_tool_scope`: blocks git
         commit/push and Write/Edit/NotebookEdit calls whose effective
         target repo/path is outside the calling worker's own assigned
         workspace, or whose branch doesn't match the worker's assigned
         branch (read from that task's own `task.yaml`). Fails closed if
         `task.yaml` can't be read.
      2. `check_queue_bypass`: blocks raw `tmux ... send-keys` (the real
         dispatch front door is dispatch-owner-task.sh, never a worker
         issuing its own send-keys), direct invocation of
         `dispatch_core.py`/`dispatch-tick.py` (own/hold the shared
         dispatch lock and queue tick), and direct
         `systemctl ... start|enable|restart veridian-worker@...` (bypasses
         `dispatch_core.acquire_dispatch_lock()`/CONCURRENCY_CAP -- real
         cited incident: 24 units launched this way at once OOM-killed the
         box, systemd/veridian-worker@.service's own 2026-08-01 comment).
      3. `log_audit`: appends every observed tool call (allowed or denied)
         to a real sqlite table (`tool_call_audit` in
         `/opt/veridian/ai-os/logs/pretooluse_worker_audit.sqlite`),
         queryable directly via `sqlite3`. Logging failures are swallowed
         (never block a real tool call).
      Assignment resolution order: real cgroup membership (primary) ->
      `VERIDIAN_TASK_ID` env var -> `cwd` matching the task's own workspace
      path. When no worker can be identified, checks 1/2 are no-ops (allow)
      and check 3 still logs with `task_id=NULL` -- this hook's mandate is
      dispatched worker execution, not general interactive shell use.
- [x] Added `tests/test_pretooluse_worker_enforcement.py` -- 37 tests, all
      passing: pure-function unit tests (real temp git repos for the
      branch-comparison checks, no cgroup/environment dependency) plus 5
      real subprocess end-to-end tests that invoke the hook exactly the way
      Claude Code's own hook runner does, gated on actually running inside a
      real `veridian-worker` cgroup so they never spuriously fail outside
      one.
- [x] Deployed the hook to the LIVE checkout (`/opt/veridian/scripts/hooks/
      pretooluse_worker_enforcement.py`, byte-identical, verified) and
      registered it in the shared, live `~/.claude/settings.json` (new `*`
      matcher block, fully additive -- the existing `Bash`-matcher block
      with `snip hook`/`find_root_walk_guard.py` is untouched) BEFORE
      registering it, specifically to avoid the fleet-wide outage a
      dangling hook path (python3 exiting non-zero on every single tool
      call, fleet-wide) would have caused.
- [x] **Real, live proof (done criteria)**: immediately after wiring,
      issued a genuine `tmux send-keys -t nonexistent-session ...` Bash tool
      call (not a simulated payload) and captured the harness's own denial:
      ```
      PreToolUse:Bash hook error: [python3 /opt/veridian/scripts/hooks/pretooluse_worker_enforcement.py]:
      BLOCKED by pretooluse_worker_enforcement: raw `tmux send-keys` from inside a worker
      session is blocked -- the real dispatch/relay front door is dispatch-owner-task.sh,
      never a raw send-keys issued by a worker itself (see dispatch-owner-task.sh's own header)
      ```
      The command never ran (no tmux session was touched). Confirmed the
      same event was written to the real audit sqlite DB
      (`decision='deny'`, real reason text, real timestamp), and confirmed
      via the DB that concurrent, unrelated real worker tasks
      (task-20260814-133002-..., task-20260814-132607-...) kept getting
      `decision='allow'` for their own ordinary tool calls throughout --
      the live rollout did not break the rest of the fleet.
      Also verified (subprocess-level, same real live environment): a git
      commit issued while actually checked out on a different branch is
      denied with a real branch-name reason; a `Write` targeting `/tmp/...`
      is denied; direct `dispatch_core.py` invocation and direct
      `systemctl start veridian-worker@...` are denied; ordinary read-only
      commands and same-branch commits are allowed.
- [x] Added `VERIDIAN_TASK_ID` export to `worker-entrypoint.sh` (right
      before `cd "$WORKSPACE"`) as a defense-in-depth FALLBACK identity
      signal only (cgroup membership is primary and already fully proven
      live) -- committed to this task's own branch only, not deployed to
      the live entrypoint script separately (low-risk, purely additive
      line, goes out through the normal merge/deploy path like any other
      code change, no need to widen this task's live-production blast
      radius beyond the hook wiring itself, which was the one change that
      really did need to happen live to be real).

## Remaining
- [ ] None known -- all three SPEC checks implemented, tested (37/37
      passing), deployed live, and proven with real captured harness output
      (not a citation). Follow-on, out of this task's scope: once this
      branch merges to main and the live checkout resyncs, the version at
      `/opt/veridian/scripts/hooks/pretooluse_worker_enforcement.py` will
      naturally match main again (it is already byte-identical to this
      branch's copy right now).
