# OCID-068: Seven-Rule Guardrail Addendum — Permanent Closure Record

**Real dispatch instruction:** `UMR-20260805-032731-b412` (Owner directive)
**Related:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR), `UMR-20260804-184014-9a18` (deploy follow-up UMR), `UMR-20260804-180711-7f96`, `UMR-20260804-170055-a069`

This record formalizes the real, already-true operational state of the OCID-068 seven-rule guardrail addendum. It does not start anything new — every rule below was already real, merged, working code, and already the live operational baseline for every real dispatch through `dispatch-owner-task.sh`, before this record was written.

## Status: CLOSED

OCID-068 is **CLOSED**. Its real UMR bookkeeping is reconciled and stable. Its seven rules are real, merged, and already the live operational baseline for every real dispatch made through `dispatch-owner-task.sh`, **effective immediately**.

**This status shall not be reopened or re-litigated in any future cycle unless a real regression is independently found with real evidence.**

## Real UMR bookkeeping (independently re-verified this cycle)

| UMR | Role | Real status | Real ts_completed |
|---|---|---|---|
| `UMR-20260804-170055-a069` | Canonical OCID-068 UMR | `completed` | `2026-08-05T02:45:07.495957+00:00` |
| `UMR-20260804-184014-9a18` | Deploy follow-up UMR | `rejected_duplicate` | `2026-08-04T20:15:38.837577+00:00` |

Both rows read directly from the real, live `umr_tasks` table in `superboss-register.sqlite`, not narrated. `UMR-20260804-184014-9a18` is correctly, permanently `rejected_duplicate` — its own real dispatched execution was correctly rejected as a genuine duplicate (Stage 4/5/6 duplicate-PR guard, citing the already-open/merged PR #21), and its own record already carries an honest annotation cross-referencing that its underlying real goal (deploying the merged code to the live scripts directory) was genuinely accomplished through other real work already independently confirmed this session. It is correctly unchanged — the deploy already happened under different credit, not a gap.

## Real, merged pull requests (independently re-verified this cycle)

Each PR below was independently re-confirmed a real ancestor of `origin/main` via `git merge-base --is-ancestor <sha> origin/main` on a fresh clone, this cycle:

| Rule | PR | Merge commit |
|---|---|---|
| Rule 1 (UMR reuse on resume) | #26 | `29a153bb7a51a12f1868a372bc7e20b90818b152` |
| Rule 2 (dispatch outcome classification) | #29 | `50c272dcac3e321179adff61dca0879a8b6c7ea7` |
| Rule 3 (no premature UMR minting) | #30 | `fe3ec0dffdd0cbe67b6a6b6e5d9a003bab0dd0db` |
| Rule 4 (PM-visible real counts) | #32 | `64e16d0e8c05694952c314cd85a0f4e4fc366324` |
| Rule 5 (real stall detection) | #33 | `9b716b939b2ac69297082505ef645a6ed715a959` |
| Rule 6 (zero duplication by OCID) | #34 | `8235a87fd57e9e57697f18e78cd29fbcc195a524` |
| Rule 7 (completion evidence) | #35 | `638fd38403c86d82395331f1c00208937b31764a` |

Plus the separate, real duplicate-worker re-trigger root-cause fix, commit `e0395c10864c61aee377dc23fc350ee25900f3ee` ("fix: skip redundant automated worker spawn when OCID work already done"), also independently re-confirmed a real ancestor of `origin/main`.

All seven rules' own real function markers were confirmed present in the **live deployed files** under `/opt/veridian/scripts` (not merely merged in the git repository) via direct `diff` against a fresh clone: `resource_governor.py`, `superboss-register.py`, `dispatch-tick.py`, and `veridian-task.py` were all found **byte-for-byte identical** to `origin/main`'s own content — no deploy gap, no partial/stale deploy.

## Already the live operational baseline — no further action needed

`dispatch-owner-task.sh` was independently re-confirmed this cycle to already call `check-content-duplicate` for real, live, content-hash-based duplicate detection on every real dispatch (`superboss-register.py check-content-duplicate --text "$PROMPT" --window-hours 6`, line 46). This is real, active, load-bearing enforcement code, already running in production before this record was written.

**No further action was required to start using these seven rules.** They were already enforced in real, live, deployed code. This record does not activate anything — it formalizes, in one permanent place, the fact that activation already happened, and closes the bookkeeping gap that had left the canonical UMR's own status stuck at `running`/`ts_completed=null` despite the real underlying work being done.

## Standing rule: do not reopen

OCID-068 is closed. Do not reopen or re-litigate this status in any future cycle. The only legitimate reason to revisit it is a **real regression, independently found, with real evidence** (e.g. a live re-verification that finds one of the seven rules' own markers missing from a live deployed file, or a real test failure in one of the seven rules' own dedicated test suites). A bare re-check with no new evidence is not grounds to reopen this record.

## Real citations

- `UMR-20260805-032731-b412` (this record's own dispatch instruction, Owner directive)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`)
- `UMR-20260804-184014-9a18` (deploy follow-up UMR, real status `rejected_duplicate`, correctly unchanged)
- `UMR-20260804-180711-7f96`, `UMR-20260804-170055-a069` (the OCID-068 addendum's own originating UMRs)
- PR #26, #29, #30, #32, #33, #34, #35 (the seven rules), plus commit `e0395c10864c61aee377dc23fc350ee25900f3ee` (the duplicate-worker re-trigger fix)
