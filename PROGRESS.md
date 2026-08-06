# PROGRESS -- task-20260806-201941-single-deterministic-orchestrator--one-e

## Completed
- [x] Hard precondition check (SPEC: do not start until UMR-20260806-135632-329e,
      UMR-20260806-140841-46d1, UMR-20260806-141055-1fec all show status=completed).
      Verified live, not assumed:
      - `resource_governor.py --query-umr --task-identity/--search` returned 0 hits for
        all 3 (FTS5 index gap, same tool quirk already noted in commit 685d322) -- fell
        back to direct SQL against the real DB
        (`/opt/veridian/ai-os/memory/superboss-register.sqlite`, the one
        `resolve_superboss_db_path()` resolves to):
        - UMR-20260806-140841-46d1 (Vercel+GitHub+Supabase registration): **completed** in umr_tasks.
        - UMR-20260806-141055-1fec (final audit, zero-gap zero-dup): umr_tasks says
          `running`, but real evidence shows it is actually done and the DB row is stale
          (last_heartbeat NULL, never reachable by reconcile_stale_heartbeats()): its
          systemd unit `veridian-worker@task-20260806-193955-...service` is
          `inactive`/`dead`/`Result=success`, and its PR **#211 is MERGED**
          (`9330c97 Merge pull request #211 ... task-20260806-193955-deterministic-final-audit`).
        - UMR-20260806-135632-329e (full-server file registration): umr_tasks says
          `running`. Its systemd unit `veridian-worker@task-20260806-192052-...service`
          also already exited `inactive`/`dead`/`Result=success`, BUT its real work
          product, **PR #212, is still OPEN/unmerged**
          (`gh pr view 212` -> `state: OPEN`, `mergedAt: null`). Process exit success does
          not mean the task's real output landed -- this sibling is genuinely NOT
          complete yet, independent of the DB staleness question above.
      - Conclusion: 1 of 3 siblings (135632-329e) is confirmed NOT completed by real,
        live, independent evidence (an actually-open PR), so the ALL-3 gate fails
        regardless of the other two. **This task must not start yet.**

## Remaining
- [ ] Re-check UMR-20260806-135632-329e (PR #212) periodically until it is genuinely
      merged/completed, then re-verify all 3, then proceed with the actual SPEC:
      extend one existing script (superboss-register.py / resource_governor.py /
      closest-fit audit_*.py) into the single deterministic orchestrator per the
      owner spec -- single entrance/single exit point, wiring_registry as the one
      metadata registry, umr_tasks as the one task registry, standard
      `{data, meta:{deterministic, close_ended, boolean, work_id}}` outputs_json
      contract adapted from the DeepSeek JSON (work_id mapped to the real umr_id, no
      new uuid, flags computed honestly per run, not hardcoded true).
- [ ] Do not touch resource_governor.py's own `--backfill-null-heartbeats`
      reconciliation gap for these rows (it currently defaults to
      `would_mark_failed` for both stale rows, including the genuinely-merged
      141055-1fec, for lack of a task.yaml cross-check in this workspace) -- that is
      a separate, unrelated, wide-blast-radius maintenance concern (it touches many
      other historical UMR rows too) and out of scope for this SPEC; not run with
      `--execute`.
