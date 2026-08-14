# SPEC verification — task-20260806-234552-merges-must-run-inside-a-real-worker-uni

Governing UMR: UMR-20260806-071025-1d28. Continues UMR-20260806-123547-e503.
Owner-proposal recorded: UMR-20260806-235757-087c (id 297, via `superboss-register.py insert-owner-proposal`).

## Step 1 — real worker-unit confirmation: CONFIRMED

This task genuinely executes inside `veridian-worker@task-20260806-234552-merges-must-run-inside-a-real-worker-uni.service`
(systemd `--user` unit, `systemctl --user status` shows `Active: active (running)`, Main PID 2188016 =
`worker-entrypoint.sh`, this `claude` process is a descendant in the same cgroup). Both signals the
interactive-session guard (`~/.claude-interactive-session-guard.bashrc-snippet`) checks pass genuinely:

- `INVOCATION_ID` is set (systemd-assigned, not exported by hand).
- `/proc/self/cgroup` genuinely reads
  `.../app-veridian\x2dworker.slice/veridian-worker@task-20260806-234552-merges-must-run-inside-a-real-worker-uni.service`,
  matching the guard's own regex.

This is real dispatched-worker context, not the interactive session. Confirmed by inspecting the guard's own
source (not just the SPEC's description of it) and cross-checking against `systemctl --user status`.

## Step 2 — PR staleness check: PREMISE FALSE for 2 of 3

The SPEC's claim ("Group one pull requests 169, 167, 166 and 165 were independently rebased, really tested
... and review approved with comments posted ... the only thing left undone is the merge itself for 169, 167
and 165") does **not** match live state:

### PR #169 — `fix/find-root-walk-guard-umr20260806121825-8ece`
- state: OPEN, mergeable: MERGEABLE, mergeStateStatus: CLEAN
- **Most recent comment (2026-08-06T13:27:16Z, after the 19/19-passed comment the SPEC is citing) is a real
  `AUDIT: FAIL`**, not an approval. Live-confirmed bypasses in the guard this PR ships:
  - `sudo -u root find / ...`, `nice -n 19 find / ...`, `ionice -c 3 -n 7 find / ...` are all silently
    **allowed** — `_find_invocation_argv`'s wrapper-skip only advances past bare flag tokens, not a
    flag's separate value argument, so any value-bearing flag usage (the normal way these commands are
    invoked) never reaches the `find` token and the segment is never recognized.
  - `true & find / ...` / `sleep 1 & find / ...` are also silently **allowed** — bare `&` is missing from
    `_SEGMENT_BREAKS`, so the pre-/post-`&` commands merge into one segment and `find` is no longer at
    segment-start.
  - Both are the exact wrapper patterns the module's own docstring and `_SKIP_WRAPPER_CMDS` claim to
    support. Merging as-is would ship a false fail-closed guarantee for the incident class this PR exists
    to close.
- **Not merged.** Needs the corrective fix described in its own AUDIT:FAIL comment plus a fresh audit.

### PR #167 — `fix/dispatched-dead-zone-auto-remediation-umr20260806115538-1e55-854d`
- **Already MERGED** 2026-08-06T16:46:39Z, merge commit `f6ab61145fe19d9b6e3f4ee6a5554289945b6b74`, via a
  separate re-dispatch (UMR-20260806-115605-854d) that ran after the SPEC's own snapshot of the world.
- Independently confirmed (Step 4): `f6ab6114...` is a real ancestor of `origin/main`
  (`git merge-base --is-ancestor` = true), and `/opt/veridian/scripts/reconcile_dispatched_dead_zone.py`
  is **byte-identical** to `origin/main`'s merged blob — real deployment confirmed, not just a merged commit.
- No action needed or taken.

### PR #165 — `feat/gtm-category-child-umr-linkage-umr20260806114728-d469`
- **Already CLOSED** 2026-08-06T16:31:55Z, correctly: a real finding (UMR-20260806-161614-5850) that the
  branch is 25 commits stale against `main` and merging it as-is would silently delete since-merged
  functionality (`mark-umr-relay-attempted`, `requeue-build-lock-contended`).
- There is nothing open to merge. Re-opening/merging this PR would be a real regression, not a completion.

## Step 3/4 — merge + deploy: N/A for 169/165 (correctly withheld), already true for 167

No merge was attempted for #169 (would ship known live security bypasses) or #165 (would regress shipped
functionality). #167's merge+deploy was independently verified real (see above) though performed by an
earlier, different dispatch, not this task.

## Step 5 — real remaining open PR count

**56** open PRs on `FChecklist/veridian-scripts` as of this check (includes #169, still open, still
FAIL-audited).

## Step 6 — evidence recorded, UMR terminal status

Recorded via `superboss-register.py insert-owner-proposal` (canonical script, no raw SQL) — id 297,
child UMR `UMR-20260806-235757-087c`.

**Did not** call `mark-umr-terminal --status completed` on UMR-20260806-071025-1d28 or
UMR-20260806-123547-e503. Both already read `status=failed` via
`superboss-register.py reconcile-umr-status` (checked directly, not assumed), and that status is accurate,
not stale: `reconcile-umr-status --umr-id UMR-20260806-123547-e503` finds `is_stale: false` and no
merged-PR evidence attributable to that UMR. The SPEC's instruction to mark it `completed` rested on the
same "169/167/165 all ready, just needed the merge" premise this document shows is false for 2 of 3 PRs —
forcing `completed` now would misrepresent that outcome. Leaving both at their current, already-honest
`failed` status.

## Bottom line

No merges were performed by this task. #167 was already merged and deployed by other work; #169 has a real,
live-confirmed reason not to merge yet; #165 is correctly closed. This matches the standing pattern in this
repo of urgent SPECs built on confident claims that don't match live state — verified independently before
any write, per the recurring lesson already on file.
