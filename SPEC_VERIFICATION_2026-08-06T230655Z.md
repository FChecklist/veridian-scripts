# SPEC verification -- task-20260806-230655-reopen-umr-20260806-100604-4591--it-is-f

Per this repo's documented false-premise-pattern history (23+ prior cases; see e.g.
`ab23324`, `6d2795a`, `48c96bc`), verified the SPEC's three "proofs" and all six requested
steps against the real, canonical DB and live filesystem
(`/opt/veridian/ai-os/memory/superboss-register.sqlite` -- **not**
`scripts/superboss-register.sqlite`, which is a 0-byte stale decoy per that file's own
`resolve_superboss_db_path()` docstring) before taking any action.

## Claim vs. real current state (checked 2026-08-06T23:1x-23:2x UTC)

| SPEC proof/step | Real state, verified directly |
|---|---|
| Proof 1: `/opt/veridian/scripts/find_code.sh` doesn't exist | **It exists now**: `-rwxr-xr-x`, 5216 bytes, mtime `Aug 6 11:18`. Ran it live: `find_code.sh "def resolve_superboss_db_path" /opt/veridian/scripts` correctly returned `superboss-register.py` + its own `.bak-pre-lockfix-*` copy. `tests/test_find_code.py` (9312 bytes) and `pruned_code_search_capability_record.json` (1640 bytes) also exist alongside it. |
| Proof 2: `outputs_json` is `{}` | **Not empty now**: real JSON with 3 file paths (each carrying `confirmed_exists`/`confirmed_executable`), `capability_registry_id: "CAP-20260806-101956-bce6"`, `pr_numbers: [159, 160]`, and both real merge-commit SHAs. |
| Proof 3: unpruned D-state scans still running now | `ps -eo pid,stat,etimes,cmd` filtered on stat `D` right now: **0 processes**. The row's own reason field records the same measurement at correction time ("D-state unpruned-scan count at time of this correction: 0, down from the PM's own measured baseline of 5 at 10:34 UTC"). |
| Step 1: reopen the row via `superboss-register.py`, never raw SQL | **Already done**, and correctly -- not by this task. `umr_tasks.status` for `UMR-20260806-100604-4591` is `completed`, but the `reason` field was corrected *in place* (per this SPEC's own instruction not to delete/rewrite the original claim) to: `"CORRECTED per UMR-20260806-103641-2a1f (real Hard Rule 3 finding, all 3 proofs independently re-verified): at the original ts_completed (2026-08-06T10:31:26Z), find_code.sh had been merged to origin/main (PR #159/#160, real merge commits f5c83ed/9f40437) but had NOT yet been pulled into the live deployed /opt/veridian/scripts checkout -- a real deploy-lag gap..."` This is the honest outcome the SPEC actually wants (truthful status + real evidence), just already applied by a prior task under governing UMR `UMR-20260806-103641-2a1f`. |
| Step 2: create `find_code.sh`, prove it's executable, paste real output | Already real (see Proof 1 row). Independently re-ran it myself this task, live, with a different pattern than the prior task used, and got a correct real result (see below). |
| Step 3: close the two guidance gaps (find over `/`, never grep for a UMR id) | Both already present in the **live** `/opt/veridian/ai-os/MASTER_INDEX.yaml`, `exclusion_rules` block: `never_find_over_root_filesystem` and `never_grep_for_a_umr_id` keys, both citing `UMR-20260806-103641-2a1f`. Additionally, technically enforced (not just documented): `/opt/veridian/scripts/hooks/find_root_walk_guard.py` is a live PreToolUse Bash hook that actually blocked my own test `find /` command during this task's verification (exit 2, real rejection message citing the same guidance). One loose end found: the git-tracked record of this guidance change, `github.com/FChecklist/veridian-ai-os#4`, is still **OPEN, unmerged** -- but its diff is 14,073 additions / 14 files (includes an apparently unrelated `KERNEL_CONSOLIDATION_STATUS.md` and other bulk content), i.e. not a clean, mergeable, in-scope diff for this task to merge blind. Since the live guidance file already has the content and the live hook already enforces it, this does not block correctness; flagging it rather than merging a large, out-of-scope PR. |
| Step 4: register the helper via `superboss-register.py`, prove `lookup-capability` finds it | Already done. `python3 superboss-register.py lookup-capability --capability-name "pruned_code_search"` returns `"found": true`, `capability_id: "CAP-20260806-101956-bce6"`, `mechanism_path: "scripts/find_code.sh"`, `confidence: 1.0` -- independently re-run by me this task, real output above. |
| Step 5: recount D-state scans, real before/after | Before (SPEC's own baseline): 5 at 10:34 UTC. After, measured twice independently -- once by the prior correction and again by me this task at ~23:1x UTC: **0**. The number did fall, and stayed fallen. |
| Step 6: record completion via `superboss-register.py` with real non-empty `outputs_json`, real paths, real commit hash, real PR number, existence-checked first | Already done by the prior task, and I independently re-verified every cited artifact exists before accepting this: `find_code.sh` (exists, executable, runs), `tests/test_find_code.py` (exists), `MASTER_INDEX.yaml` (exists, exclusion_rules extended, confirmed by reading it), commits `f5c83ed`/`9f40437` (exist in `git log`, both real `MERGED` PRs #159/#160 per `gh pr view --json state,mergedAt`). |

## Conclusion

This SPEC describes a real, genuine Hard Rule 3 violation that **did** occur at
`ts_completed = 2026-08-06T10:31:26Z` -- `find_code.sh` genuinely didn't exist yet on the live
deployed checkout at that moment (a real deploy-lag gap between the merged PR and the pulled
working tree), and `outputs_json` genuinely was empty. Both are accurately described.

But by the time this task was dispatched (23:06:55Z, ~12.5 hours later), that exact violation
had **already been found and honestly corrected** by an earlier task under governing UMR
`UMR-20260806-103641-2a1f` -- through the canonical `superboss-register.py` path, with the
original claim corrected in place (not deleted/rewritten) and real, existence-checked evidence
attached. All three of this SPEC's proofs, when re-checked against current live state rather
than the state described in the SPEC text, are now false: the file exists and runs, the
outputs are populated with verified real paths, and the D-state scan count is 0, not "running
right now."

Taking this SPEC's Step 1 literally now (reopening the row to a non-completed status) would
itself be a **false status write** against a row that is currently accurate -- exactly the
failure mode Hard Rule 3 exists to prevent, just in the opposite direction. No `umr_tasks`
write was made by this task. No new `find_code.sh`, capability registration, or PR was created,
since all three already exist, are correct, and were independently re-verified live (not
assumed from the DB row's own claims).

One real, still-open item is flagged rather than acted on: `veridian-ai-os#4` (the guidance
PR) is unmerged and carries a large, apparently out-of-scope diff -- worth a dedicated,
narrowly-scoped follow-up to extract just the `MASTER_INDEX.yaml` guidance delta and land it
cleanly, but not something to merge blind under this task's scope.

No PR opened against a code change -- there is no real code change or row correction to make.
Reporting this verification in `PROGRESS.md` and this file instead, matching established
convention for verified-stale-premise findings in this repo (see `48c96bc`, `6d2795a`).
