## What this is

Lands PR #205's real, already-AUDIT:PASS'd commits, rebased onto current `main`, superseding it (same structural reason as #387/#388/#390/#391).

| PR | Title | Real AUDIT:PASS timestamp |
|----|-------|---------------------------|
| #205 | feat(superboss-register): steps one/two/four of the deterministic-first task gate (UMR-20260806-124654-a8d6) | 2026-08-07T08:55:10Z |

**This one is NOT a pure PROGRESS.md-only mechanical rebase**, unlike this task's earlier waves -- flagging per this sweep's own SPEC. PR #205's second commit conflicted for real in `superboss-register.py` against a `main`-side addition (`_write_lock_depth = [0]`, a process-local reentrancy counter landed after #205 was opened) that both insert at the same location as #205's own `VERIDIAN_ROOT = "/opt/veridian"` constant. Resolved by keeping **both** additions, concatenated (neither removes, edits, or reorders any existing code -- purely two independent, non-overlapping top-level constant additions landing side by side). Verified: exactly one occurrence of each symbol post-resolution (`grep -c` both), `ast`-parses/`py_compile`s clean, and `pytest tests/test_capability_graduation.py` -- 15/15 passed post-rebase (matches the original audit's own claimed count).

Additionally ran the one pre-existing test file that references `_write_lock_depth` (`test_owner_priority_sequence.py`) to check for regressions from the concatenation: 5/8 fail -- **confirmed pre-existing on `origin/main` before this PR's changes** (reproduced the identical 5 failures on a clean `origin/main` checkout with none of #205's changes present), so unrelated to this rebase.

Given the manual (if low-risk, additive-only) resolution, this is **not self-certified** -- see the independent audit posted as a PR comment below before merge.

Full sweep evidence: `progress/task-20260814-183604-sweep-veridian-scripts-for-real-audited.md` in this diff.
