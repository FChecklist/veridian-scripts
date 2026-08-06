# PROGRESS -- task-20260806-155323-deterministic-triage-of-the-trailing-24h

Governing parent: UMR-20260806-071025-1d28 (standing 24h owner-dispatch closure mandate).

## Independent verification of the SPEC before acting (per project's own
recurring false-premise pattern -- see memory)

- SPEC's exact 100/closed25/failed27/killed21/queued14/rejected_duplicate11/running2
  breakdown for the trailing-24h owner_dispatch_gateway set was checked directly
  against the live `umr_tasks` table (same query `generate_pm_report_v3.py`'s
  `get_owner_dispatch_umr_status_counts()` uses). Using the exact
  2026-08-05T08:56Z-2026-08-06T08:56Z window the SPEC cites, the real total (100)
  and `failed`/`rejected_duplicate` (27/11) match, but `killed` (real 25, not 21),
  `running` (real 8, not 2), and `closed`/`completed` (real 29, not 25) do not, and
  `queued` (claimed 14) has **zero** real rows in that exact window. Not fatal --
  the SPEC's own step 1 explicitly requires recomputing fresh from `umr_tasks`
  every run rather than trusting any hardcoded count, which is what got built/run.
- **The triage script this task was dispatched to build already exists, merged,
  and deployed**: `/opt/veridian/scripts/triage_owner_umr_24h.py`, PR #154
  (`feat/triage-owner-umr-24h-backlog-umr20260806091345-d90c`), merged
  2026-08-06T09:56:32Z, ~6h before this task's own dispatch, plus two follow-up
  hardening commits (`dc3521a` cooldown fix, `75b25c2` ARG_MAX-bound fix). 26/26
  of its own tests pass. It was, however, **never actually run with `--apply`**
  against the live DB (zero `umr_tasks` rows carry a
  `metadata_json.triage_UMR-20260806-091345-d90c` key; zero `owner_proposal` rows
  cite it) -- so the real remaining work was running it for real, not rebuilding it.
- Real bug found while verifying the existing script before trusting its output
  enough to `--apply`: `gather_evidence()`'s already_done detection
  (`PR_NUMBER_RE`/`REPO_HINT_RE`) scans the *entire* `metadata_json` blob
  including the `reuse_check_result` key -- confirmed live to be a 1-4MB
  "similar prior work" search dump attached to virtually every dispatched row,
  containing dozens of real but **unrelated** PR references from across the
  whole codebase (verified concretely: `UMR-20260805-002929-5560`, a "continue
  OCID-047/OCID-050" stall-recovery row with nothing to do with
  compliance-tracker's Prompt Compiler Engine, was classified `already_done`
  purely because `reuse_check_result` happened to mention "PR #562" from an
  unrelated prior search, and PR #562 has since genuinely merged). Real,
  reproducible false positive -- confirmed present in 18-23 of the 24
  `already_done` rows from an unpatched dry run (all citing the identical
  `PR #562` / commit `ee541a6a...`). Fixed by excluding `reuse_check_result`
  from the evidence-scanning text (still available in the row's own `reason`
  column and any other metadata key); added a regression test reproducing this
  exact shape. Full diff + before/after dry-run counts in the PR.
- The immediately-preceding merge commit (sibling task
  `task-20260806-155334-independently-review-then-merge-pr-150`) independently
  reconfirmed, via its own unrelated PR-review SPEC, that this same governing
  UMR `UMR-20260806-071025-1d28` is `status=failed` -- corroborates the finding
  above from a second, independent angle.

## Completed
- [x] Verified SPEC numbers independently against live `umr_tasks` (see above).
- [x] Found the already-merged `triage_owner_umr_24h.py` (PR #154) and confirmed
      it had never been run with `--apply`/`--file-proposals`.
- [x] Ran its own test suite (26/26 pass) and two independent dry runs against
      live data -- confirmed per-row bucket assignments are byte-identical
      across runs (deterministic/reproducible as designed).
- [x] Found and fixed the `reuse_check_result` false-positive bug in
      `already_done` detection (see above); added a regression test.

## Remaining
- [ ] Re-run dry run with the fix, confirm the false already_done rows fall
      through to a real, evidence-backed bucket.
- [ ] Run for real: `--apply --file-proposals` against the live trailing-24h set.
- [ ] Print/report final real bucket counts, confirm they sum to the script's
      own computed failed+killed total.
- [ ] Rebase onto latest origin/main, commit, push, open PR with the fix.
