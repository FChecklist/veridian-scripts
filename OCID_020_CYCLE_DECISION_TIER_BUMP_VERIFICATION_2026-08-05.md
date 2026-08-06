# OCID-020 cycle decision: tier-bump request + category 14 scrutiny — independent verification

Task: task-20260805-185216-ocid-020-cycle-decision--tier-bump-plus
DB checked: `/opt/veridian/ai-os/memory/superboss-register.sqlite` (live, canonical). `PRAGMA integrity_check`
flags pre-existing corruption isolated to the `actions_fts` FTS index tree (page range ~175994-175999);
`umr_tasks` and `gtm_certification_categories` are unaffected and read cleanly.

## 1. Tier-bump request for UMR-20260805-093138-2bd0 — DECLINED, premise is false

The SPEC's diagnosis ("real position nine of thirty four in a real FIFO with aging priority
queue, tied at max aged priority with eight older real tasks", tier 1 needing a bump to tier 0)
does not match live state.

Live row (`umr_tasks`, exact `umr_id` match, single row, no duplicates):

```
umr_id      = UMR-20260805-093138-2bd0
tier        = 0                    <- already 0, not 1
status      = rejected_duplicate   <- NOT queued, not in any FIFO
ts_submitted= 2026-08-05T09:31:38.640058+00:00
reason      = "superseded: OCID-068 ... already has real, newer evidence in
               ocid_artifact_links -- umr_id='UMR-20260805-152250-55d3' ...
               the same OCID's real work was independently completed while
               this task sat queued; redispatch skipped, not spawned"
```

- This task isn't in the queue at all, so there is no FIFO position to bump it within. The live
  `queued` backlog (`SELECT * FROM umr_tasks WHERE status='queued'`) has **25** rows total (1 at
  tier 0, 24 at tier 1) — not 34, and this UMR is not among them.
- Its `tier` column already reads `0`, so the requested "bump tier 1 → tier 0" is a no-op even
  under the SPEC's own (incorrect) premise.
- Its `reason` shows the real dedup mechanism already resolved it: the underlying OCID-068 work it
  asked for was independently completed by `UMR-20260805-152250-55d3` before this one could
  dispatch, so it was correctly rejected as a duplicate. There is nothing left to "clear faster."
- Separately: no canonical tier-bump/escalation mechanism exists in this codebase to invoke even if
  the premise had been true. Checked CLI surfaces of `superboss-register.py` (canonical DB writer,
  51 subcommands — `init`, `heartbeat`, `log-instruction`, `search`, `check-duplicate`, etc.), plus
  `task-gateway.py` (`submit`/`start`/`log`/`close`/`register-automation`/`status`), `dispatch-tick.py`,
  and `resource_governor.py`: none expose a `set-tier`/`bump-tier`/`escalate` subcommand. Executing a
  raw `UPDATE umr_tasks SET tier=...` would itself be the "raw priority edit outside the canonical
  mechanism" the SPEC explicitly forbids, and would have no dispatch effect anyway since this row's
  `status` isn't `queued`.

**Decision: no write performed.** This is the same stale-dispatch-premise pattern already
root-caused this session in `a901898` — the instruction described a state that predates the DB's
current, more-advanced reality.

## 2. `gtm_certification_categories` table — confirmed real, matches SPEC description

```sql
CREATE TABLE gtm_certification_categories (
    category_index INTEGER PRIMARY KEY, category_name TEXT NOT NULL, ocid_number TEXT NOT NULL,
    parent_umr_id TEXT NOT NULL, child_umr_id TEXT, passed INTEGER, evidence_summary TEXT,
    evidence_json TEXT, fix_commit TEXT, fix_file_path TEXT, fix_pr_number INTEGER,
    validated_at TEXT, created_at TEXT NOT NULL, last_updated_at TEXT NOT NULL
);
```

25 rows confirmed live (`category_index` 1-25), schema matches the SPEC's description field-for-field.

## 3. Category 14 "governance testing" — evidence IS real and re-runnable; do NOT revert `passed`

The SPEC's concern was that `evidence_summary` reads as a narrated description of this session's own
operational discipline rather than a citation of a specific re-runnable script/audit log. Live data
does not support that concern:

```
category_index  = 14
ocid_number     = OCID-020
passed          = 1
evidence_summary= "both real mechanical checks pass: canonical DB resolver present+correct,
                    live duplicate-submission probe correctly rejected"
evidence_json   = {"sub_check_1_resolver_present": true,
                    "sub_check_1_resolved_path": "/opt/veridian/ai-os/memory/superboss-register.sqlite",
                    "sub_check_1_resolved_path_matches_live_db": true,
                    "sub_check_2_target_task_identity": "owner-task-20260805-154254-2720487",
                    "sub_check_2_submit_result": {"accepted": false, "reason":
                      "duplicate submission rejected: task_identity='owner-task-20260805-154254-2720487'
                       already queued as umr_id=UMR-20260805-154255-af7d
                       (source_trigger='owner_dispatch_gateway', tier=1)"},
                    "sub_check_2_dedup_works": true,
                    "script_path": "gtm_check_governance_testing.py"}
validated_at    = 2026-08-05T15:44:13.578405+00:00
```

**Exact source this `passed` boolean traces to:** `gtm_check_governance_testing.py`, added in commit
`b140051` ("feat: real deterministic GTM check scripts — database, API, governance testing"), which
runs two mechanical sub-checks and — per its own docstring — was written specifically to *replace* an
earlier narrated version of this same category's row (`UMR-20260805-152508-d4c9`, reverted for exactly
the reason this SPEC raises):
1. `resolve_superboss_db_path` is a real, callable function in `superboss-register.py`, imported and
   invoked directly (function-existence + callability check, not a text/grep match).
2. A **live** functional probe: submits a real task_spec via `resource_governor.py`'s own `submit()`
   with a `task_identity` colliding with a freshly-queried, currently-live queued row, and asserts the
   real return value is `accepted=False` with a `rejected_duplicate`-shaped reason.

I independently re-verified sub-check 2 against current live state rather than trusting the stored
JSON: `UMR-20260805-154255-af7d` exists in `umr_tasks` right now with `task_identity =
'owner-task-20260805-154254-2720487'`, `tier = 1`, `status = 'queued'` — an exact match to what
`evidence_json` claims. The evidence is genuine, mechanical, and independently reproducible.

**One real (different) gap, flagged for follow-up, not acted on unilaterally:** `gtm_check_governance_testing.py`
exists only on branch `feat/gtm-checks-db-api-governance-umr20260805153813`, open **PR #65**
(`mergeable`/CI state fetched, not yet merged) — it is **not present on `main`** (`git cat-file -e
origin/main:gtm_check_governance_testing.py` fails). So although the stored evidence is real and I
reproduced it, nobody checking out `main` today can re-run the script that produced it until #65
merges. That's a canonical-location gap, not a fabrication — I did not revert `passed` over it, since
the SPEC's actual test ("does this trace to one specific real re-runnable script, yes/no") is
satisfied. Recommend merging PR #65 as the next real step to close that gap.

## Net decisions this cycle

- [x] Tier bump: **not performed** — premise false (task not queued, tier already 0, work already
      superseded); no canonical mechanism exists to invoke even if it were true.
- [x] `gtm_certification_categories`: confirmed real, 25 rows, schema-accurate.
- [x] Category 14 `passed=1`: confirmed traces to a real, independently-reproduced, re-runnable
      mechanical script — left standing. Follow-up recommended: merge PR #65 so the script is
      reachable from `main`.
