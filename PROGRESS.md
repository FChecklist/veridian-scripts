# PROGRESS -- task-20260806-155323-deterministic-triage-of-the-trailing-24h

Governing parent: UMR-20260806-071025-1d28 (standing 24h owner-dispatch closure mandate).
This task's own real UMR: UMR-20260806-091345-d90c (already minted by an earlier
invocation of this same triage build, see below).

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
  of its own tests passed. It was, however, **never actually run with `--apply`**
  against the live DB before this task (zero `umr_tasks` rows carried a
  `metadata_json.triage_UMR-20260806-091345-d90c` key; zero `owner_proposal` rows
  cited it) -- so the real remaining work was hardening + running it for real,
  not rebuilding it from scratch.
- Real bug found while verifying the existing script before trusting its output
  enough to `--apply`: `gather_evidence()`'s evidence-signal regexes (already_done
  merge-commit/PR detection, and the retryable/blocked keyword signals) scanned
  the *entire* `metadata_json` blob, including the `reuse_check_result` key --
  confirmed live to be a 1-4MB "similar prior work?" search dump attached to
  virtually every dispatched row, containing dozens of real but **unrelated**
  PR/commit references from across the whole codebase (verified concretely:
  `UMR-20260805-002929-5560`, a "continue OCID-047/OCID-050" stall-recovery row
  with nothing to do with compliance-tracker's Prompt Compiler Engine, was
  classified `already_done` purely because `reuse_check_result` happened to
  mention "PR #562" from an unrelated prior search, and PR #562 has since
  genuinely merged; a "merge conflict" match for a different row came from a
  totally unrelated `claude-control` PR #79/#80/#82 discussion buried in the
  same blob). 18-23 of 24 `already_done` rows and all 38 `retryable` rows in an
  unpatched dry run shared this exact false-positive shape. Fixed by excluding
  `reuse_check_result` from all evidence-scanning text (still available: the
  row's own `reason` column and any other metadata key); added 3 regression
  tests reproducing this exact shape (26 -> 29 tests).
- Second real hardening: added a real, unconditional `gh pr list --head
  worker/<task_id>` branch lookup (`find_pr_by_branch`) for rows where a repo
  is already known (task.yaml or text hint) but no PR number was ever recorded
  anywhere in the row's own text -- the SPEC requires real PR state for EVERY
  failed/killed row, not only ones lucky enough to already have a number
  written back. Gated on an already-known real repo (never guesses one).
  Raised `already_done` from 4 -> 10 real rows on live data, all independently
  corroborated by this session's own memory log (e.g. PR #140/#141/#142/#143
  for the "resume-backlog"/"disk-emergency-remediation" cases, PR #73 for the
  "database-lock-contention" case) -- real, previously-confirmed merged fixes,
  not guesses.
- Two independent dry runs before and after each fix confirm per-row bucket
  assignments are byte-identical across runs on unchanged rows (deterministic/
  reproducible, as the script's own design requires).
- The immediately-preceding merge commit (sibling task
  `task-20260806-155334-independently-review-then-merge-pr-150`) independently
  reconfirmed, via its own unrelated PR-review SPEC, that this same governing
  UMR `UMR-20260806-071025-1d28` is `status=failed` -- corroborates the earlier
  finding above from a second, independent angle. Did not treat this as a
  reason to skip the standing mandate itself (a `failed` bookkeeping status on
  the mandate's own tracking row does not retire the mandate); only the
  fabricated hardcoded breakdown numbers were discounted.

## Real final run (2026-08-06, `--apply --file-proposals`)

```
Trailing-24h owner_dispatch_gateway total (all statuses, recomputed fresh): 276
failed+killed rows triaged: 69
  already_done: 10
  superseded: 1
  retryable: 0
  blocked: 58
bucket sum: 69 (matches triaged total: True)
child-UMR proposals filed: 58
```

- 69 = the script's own freshly-recomputed failed+killed total at run time (not
  the SPEC's stale 48/52), and the 4 real buckets sum to exactly that (10+1+0+58=69).
- All 69 rows now carry a `metadata_json.triage_UMR-20260806-091345-d90c` key
  (bucket + evidence + reason), written back only via `update_umr_task()` inside
  `superboss-register.py`'s own `_write_lock()` -- never raw SQL. Existing
  metadata keys (e.g. `reuse_check_result`) are preserved, not overwritten.
- All 58 bucket-3/4 rows got exactly one real child-UMR proposal each (ids
  114-171 in `pm_decisions_pending`, `decision_type='owner_proposal'`, each
  explicitly citing parent `UMR-20260806-071025-1d28`), via
  `insert-owner-proposal` -- propose only, per the standing
  propose-then-approve-then-execute mandate. No fix implemented for any
  bucket-3/4 row; no `decide-owner-proposal` call made. The PM approves these
  separately.
- 0 rows fell into `retryable` in this real run (down from 38 in the unpatched
  dry run, which were all `reuse_check_result` false positives -- see above).
  All rows lacking real merge/supersede evidence correctly fell through to the
  conservative `blocked` ("needs an Owner-only look") default rather than being
  guessed into `retryable`, per the script's own documented design philosophy.

## Completed
- [x] Verified SPEC numbers independently against live `umr_tasks` (see above).
- [x] Found the already-merged `triage_owner_umr_24h.py` (PR #154) and confirmed
      it had never been run with `--apply`/`--file-proposals`.
- [x] Ran its own test suite (26/26 pass) and two independent dry runs against
      live data -- confirmed per-row bucket assignments are byte-identical
      across runs (deterministic/reproducible as designed).
- [x] Found and fixed the `reuse_check_result` false-positive bug affecting all
      4 buckets; added regression tests.
- [x] Added a real, unconditional branch-name PR lookup so every row with a
      known repo gets real PR state checked, not only rows with a pre-recorded
      PR number; added regression tests (29/29 pass total).
- [x] Ran for real: `--apply --file-proposals` against the live trailing-24h
      set. 69 rows classified, 58 child-UMR proposals filed, bucket sum
      verified to equal the script's own computed failed+killed total.
- [x] Rebased onto latest `origin/main`, committed, pushed, opened PR.

## Remaining
- [ ] None from this task's side. The PM/Owner decides the 58 filed proposals
      separately, per the standing mandate this task does not implement fixes.
