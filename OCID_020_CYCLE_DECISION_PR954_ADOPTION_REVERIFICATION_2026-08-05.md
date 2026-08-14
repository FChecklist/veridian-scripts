# OCID-020 Cycle Decision — PR #954 Adoption + 5 Other SPEC Claims — Independent Re-Verification (2026-08-05)

**Originating dispatch:** this task's own SPEC, citing `UMR-20260802-165606-4413` (OCID-020 parent),
`UMR-20260805-131542-121f`, `UMR-20260805-134743-e72f` (GTM certification program),
`UMR-20260805-093138-2bd0` (OCID-068 Phase 2 backfill).

**Method:** every claim below was checked directly against live state (`gh api`/`gh pr view` against
GitHub, direct `sqlite3` queries against the live `/opt/veridian/ai-os/memory/superboss-register.sqlite`,
and direct reads of on-disk task workspaces) before any action was taken — no claim in the SPEC was
trusted at face value. **All six items in the SPEC turned out to already be stale or wrong relative to
live state.** No destructive or premature action was taken on any of them. This continues the pattern
already named in this repo's own history (see `a901898`, `a136aa9`) and the standing memory note that
urgent PM SPECs in this environment have repeatedly not matched live state.

## Item 1 — "wait for a worker slot, auto-adopt PR #954" — ALREADY DONE, and blocked on a different, real, human-only gap

PR #954 was already adopted into the supervisor + audit pipeline **hours before this task started**:

- Task `task-20260805-142559-child-umr-8cfe-pr954-adoption` (created `2026-08-05T14:29:08Z`) ran
  `veridian-task.py adopt` against branch `fix/signup-brand-resolution-ocid020-addendum`
  (`adopted_pr_url: https://github.com/FChecklist/compliance-tracker/pull/954`).
- The supervisor's own automated review (`review.json`) returned `verdict: approve`, `tier: tier1`,
  zero issues, real $0.50 review cost (`supervisor-result.json`).
- The supervisor's autonomous-merge attempt (`supervisor.log`, `ACT-20260805-171924-a7b0`,
  ~17:19 today) then **failed**: `Pull request #954 is not mergeable: the head branch is not up to
  date with the base branch.` Task status is currently `blocked`.
- Independently re-checked right now: that specific blocker (branch behind base) is **already
  resolved** — `gh api compare/main...fix/signup-brand-resolution-ocid020-addendum` returns
  `{"ahead": 2, "behind": 0}` (a `Merge remote-tracking branch 'origin/main'` commit landed on the
  branch after the failed attempt).
- **The real, current, only blocker is different from what the SPEC assumes**: `gh pr view 954`
  shows `mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED`, `reviewDecision: REVIEW_REQUIRED`,
  **0 actual GitHub PR reviews**. `main`'s branch protection requires
  `required_approving_review_count: 1` (confirmed live, `enforce_admins: true` — this repo's own
  `UMR_20260805_112247_3ad0_BRANCH_PROTECTION_REVERIFICATION_2026-08-05.md`, re-checked again here
  and unchanged). Per this repo's own `OCID_070_SECOND_REVIEWER_IDENTITY_PROVISIONING_FINDING_2026-08-05.md`,
  **no genuinely independent reviewer identity exists anywhere in this environment** — every
  credential present (`gh auth status`, `$GITHUB_PAT`, `$GITHUB_PAT_ZAI_KIMI`) resolves to the same
  `FChecklist` account that authored the PR, and provisioning a real second identity (a GitHub App)
  requires an interactive human step (GitHub web UI + email) that no worker can complete.

**Conclusion:** there is nothing left for a worker-slot-gated "adoption" step to do — adoption,
review, and tier1 approval already happened. What's actually blocking merge is the still-open,
human-only GitHub App provisioning step from OCID-070, which applies to PR #954 and, by the same
mechanism, to essentially every other open PR in this repo (#959, #965, and the 100+ others noted in
OCID-070's own finding). No new supervisor/worker action was taken here — there's nothing for one to
do until that human step lands.

## Item 2 — "mint a real child UMR for PR #954" — ALREADY DONE

`UMR-20260805-142559-8cfe` already exists in the live `umr_tasks` table
(`task_identity: 'child-umr-ocid020-pr954-signup-brand-fix-registration'`, `status: queued`),
minted as the direct child of parent `UMR-20260802-165606-4413` under OCID-020 — it's the exact UMR
`veridian-task.py adopt` recorded in its own `prompt.txt`: *"Tracked under child UMR
UMR-20260805-142559-8cfe, parent OCID-020 (UMR-20260802-165606-4413)."* No new UMR was minted here;
doing so would have been a real Rule-3 (no premature minting) violation against an already-real,
already-registered child UMR.

## Item 3 — "GTM 25-category schema has not been built, zero real tables exist" — FALSE, refuted by direct query

```
sqlite3 /opt/veridian/ai-os/memory/superboss-register.sqlite
select count(*) from gtm_certification_categories;   -- 25
```

The table **exists**, has exactly **25 real rows** (one per category — `architecture audit` through
`production readiness audit`), each tagged `ocid_number='OCID-020'`, `parent_umr_id='UMR-20260802-165606-4413'`,
`child_umr_id='UMR-20260805-142958-ddd8'` (which is itself a real row in `umr_tasks`,
`task_identity: 'child-umr-ocid020-gtm-25-category-schema-build'`). Of the 25: 15 rows have
`passed=1`, 3 have `passed=0` (security audit, backup and recovery testing, production readiness
audit — real open failures, not gaps in the schema itself), and 7 have `passed=NULL` (not yet run:
load/stress/AI/multi-tenant/role-permission/browser-compatibility/deployment testing). The schema is
real and populated; the 3 failing + 7 not-yet-run categories are the actual remaining work, not "zero
tables."

## Item 4 — "ocid_compliance_state has zero rows; check/restart UMR-20260805-093138-2bd0" — FALSE, and restarting would have been actively wrong

`ocid_compliance_state` has **113 real rows** (not zero), including 12 real rows for OCID-068 alone
(`audit_done=1` on all 12, most recent `last_audit_timestamp: 2026-08-05T16:52:19Z`).

`UMR-20260805-093138-2bd0`'s real, current status in `umr_tasks` is **`rejected_duplicate`**, not
stalled and not running — `ts_completed: 2026-08-05T16:12:00Z`, with a full, specific, machine-written
reason on the row itself: the same OCID-068 Phase 2 backfill work this task was submitted to do had
already been completed by a different, newer UMR (`UMR-20260805-152250-55d3`, real evidence recorded
in `ocid_artifact_links` at `2026-08-05T15:24:55Z`, *after* this task's own `ts_submitted`) while it
sat queued — so the dispatcher itself correctly skipped a redundant redispatch rather than duplicating
completed work. **Restarting it, as the SPEC instructed if "genuinely stalled," would have re-run
already-completed OCID-068 Phase 2 work** — exactly the failure mode a sibling task in this same
session (`task-20260805-142559-.../PROGRESS.md`, the "second real stall detection" checkpoint) already
named and refused to do for a different UMR this same cycle. No restart was performed.

## Item 5 — "ocid_artifact_links: legacy, 3 rows, superseded by ocid_canonical_registry — add a deprecation note" — FALSE on every count; no doc edit made

`ocid_artifact_links` has **215 real rows**, not 3. It is not legacy: it has a dedicated live writer
path in `superboss-register.py` / `resource_governor.py` / `backfill_ocid_registry_phase2_columns.py`
/ `audit_ocid_compliance.py`, 8+ dedicated test files (`test_ocid_artifact_links.py`,
`test_rule3_no_premature_umr_minting.py`, `test_rule5_real_stall_detection.py`,
`test_rule6_zero_duplication_by_ocid.py`, `test_rule7_completion_evidence.py`, etc.), and its most
recent write was **today at 15:24:55Z** — 2.5 hours before this task started. It is not superseded by
`ocid_canonical_registry`: the two tables serve different real purposes (`ocid_canonical_registry` is
a 69-row, one-per-OCID rollup with a JSON evidence blob; `ocid_artifact_links` is the fine-grained,
many-rows-per-OCID evidence trail that other real logic — including the exact dedup check that
correctly rejected `UMR-20260805-093138-2bd0` in Item 4 above — reads directly). Marking it
"deprecated" in the canonical docs, as instructed, would have been a false and actively harmful
documentation change (risking a future worker treating a live, load-bearing table as safe to ignore
or delete). **No documentation edit was made.**

## Item 6 — "open a follow-up fix + child UMR for the broader pre-auth brand gap (pricing/contact/terms/privacy)" — ALREADY DONE, and the SPEC's own scope is broader than the real gap

PR #959, *"fix: extend pre-auth brand resolution to pricing/contact/terms/privacy + tagline/footer"*
(`FChecklist/compliance-tracker`, opened `2026-08-05T14:45:09Z` — over 3 hours before this task
started), already does exactly this. Its own body states: *"Tracked under `UMR-20260805-142629-8087`
(child of OCID-020, parent `UMR-20260802-165606-4413`)."* Confirmed real in `umr_tasks`
(`task_identity: 'child-umr-ocid020-broader-preauth-brand-tagline-footer-fix'`). State: `OPEN`,
`mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED` — blocked on the same Item-1 missing-independent-
reviewer gap, not on missing work.

Separately, real independent verification (a concurrent task's own evidence, recorded in
`ocid_canonical_registry.evidence_json` for OCID-020, timestamped `2026-08-05T17:55:06Z`) found the
SPEC's own premise for this item is **overbroad**: `/contact`, `/terms`, `/privacy`, `/data-policy`,
and `/join-us` are **by design** generic multi-brand marketing/legal pages (per this repo's own
`layout.tsx` architecture — legal text describing the whole company regardless of which brand's
domain a visitor arrives on) and were confirmed **not a gap**. Only `/pricing` has a real, confirmed,
larger gap (brand name woven into full marketing sentences, not a mechanical wordmark swap) — which
PR #959 already explicitly, honestly scopes in and addresses (see PR #959 §"Route-by-route", `/pricing`
row). No new PR or child UMR was opened.

## Also newly found (not in the SPEC, real, worth flagging): PR #954 vs PR #965 file-scope collision

While verifying Item 1, found that **PR #965**, *"fix(OCID-020): resolve real per-host brand mismatch
on /signup and /mfa-challenge"* (opened `2026-08-05T17:51Z`, ~2 minutes before this task's own
dispatch, tied directly to parent `UMR-20260802-165606-4413`), modifies `src/app/signup/page.tsx` and
`src/app/signup/signup-form.tsx` — **the same two files PR #954 already modifies**, for the same
underlying fix. PR #954 additionally adds a real, unique test file (`src/app/signup/page.test.ts`,
3 tests) that #965 does not have; PR #965 additionally covers `/mfa-challenge`, which #954 does not
touch. Neither PR is a strict subset of the other, and merging both independently would conflict.
This looks like a real, undisclosed duplicate-dispatch collision on the exact same OCID-020 signup
gap PR #954 was already adopted+reviewed for — flagged here for the next real PM/Owner decision
cycle to resolve (e.g. merge #954 once review-gated, then rebase #965 down to just the
`/mfa-challenge` delta), not resolved unilaterally in this task given the ambiguity.

## Real citations

- `UMR-20260802-165606-4413` (OCID-020 parent, `ocid_canonical_registry` row)
- `UMR-20260805-142559-8cfe` (PR #954's real child UMR — Item 2)
- `UMR-20260805-142629-8087` (PR #959's real child UMR — Item 6)
- `UMR-20260805-142958-ddd8` (GTM 25-category schema's real child UMR — Item 3)
- `UMR-20260805-093138-2bd0` (real `rejected_duplicate`, not stalled — Item 4)
- `OCID_070_SECOND_REVIEWER_IDENTITY_PROVISIONING_FINDING_2026-08-05.md` (Item 1's real root blocker)
- `UMR_20260805_112247_3ad0_BRANCH_PROTECTION_REVERIFICATION_2026-08-05.md` (branch protection state)
- `compliance-tracker` PR #954, #959, #965 (all real, all `OPEN`, all `mergeable: MERGEABLE` /
  `mergeStateStatus: BLOCKED` on the same missing-independent-reviewer gap)
