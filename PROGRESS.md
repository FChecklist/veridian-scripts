# PROGRESS -- task-20260807-055009-precise-evidence--wiring-registry-integr

Governing chain: UMR-20260806-124055-bc80. This is (at least) the 6th SPEC in the
recurring "wiring_registry corruption" re-escalation chain -- see
`git log --all --oneline | grep -i 'wiring.registry\|corrupt'`: 0e7c19e, 17b1642,
cca322e ("5th duplicate in chain, DB already healthy"), 8eb46d0, dedca79, 2295c30
all previously verified-and-rejected the same underlying premise. This SPEC uses a
new artifact (PRAGMA integrity_check output) rather than the swap/lock claims used
before, but the pattern -- real artifact, false interpretive leap to "drop and
rebuild wiring_registry" -- is the same one flagged in persistent memory
(`veridian-task-prompt-false-premise-pattern`).

## Completed
- [x] Ran the exact command specified, myself, right now, fresh `mode=ro` connection:
  ```python
  conn = sqlite3.connect('file:///opt/veridian/ai-os/memory/superboss-register.sqlite?mode=ro', uri=True)
  cur = conn.cursor(); cur.execute('PRAGMA integrity_check'); cur.fetchall()
  ```
  **Real raw output (not fabricated, not paraphrased):** a single-row result
  listing 95 `Page N: never used` lines (pages 92-95, 1625, 20681, 20766-20791,
  454287.., 458351.., 461060.., 462012-462343). **Confirmed: this is NOT the
  literal string `ok`.** The SPEC's factual claim about what the raw command
  returns is accurate -- unlike prior SPECs in this chain, this one is not
  fabricating the artifact itself.
- [x] Did **not** stop at "not ok" -- investigated what the anomaly actually means,
  since a SELECT succeeding was correctly flagged by the SPEC as insufficient, but
  the inverse error (integrity_check emitting anything != "ok" therefore
  wiring_registry is corrupt and must be dropped/rebuilt) is an unproven leap and
  needed the same scrutiny:
  - `page_count` = 992,941; `freelist_count` = 397,659 (~40% of the file is already
    free/reclaimable). The 95 anomalous pages are 0.0096% of the file.
  - `PRAGMA quick_check` returns the identical 95-line result (not a distinct
    failure mode).
  - Cross-referenced the 95 flagged page numbers against `dbstat` (which lists the
    pages actually owned by each table/index btree): **zero overlap** with
    wiring_registry's 7,373 pages, or with any other table's pages. "Never used"
    pages are by definition owned by no btree and not in the freelist -- a leaked
    free-space bookkeeping gap, not corruption inside any table's data.
  - Direct query of `wiring_registry` itself: 24,322 live rows, clean sample data
    (`VERIFIED_MATCH` status), fully readable. All 5 expected FTS5 shadow objects
    (`wiring_registry_fts`, `_fts_data`, `_fts_idx`, `_fts_docsize`, `_fts_config`)
    plus its 3 sync triggers are present and structurally intact.
  - `dbstat` page ownership: `umr_tasks` alone owns 514,600 pages (~52% of the DB) --
    this table (heavy per-task-dispatch write/delete churn across this whole
    multi-agent system) is the plausible source of the freelist/leaked-page drift,
    not wiring_registry.

## Conclusion
The command's raw output is real and is genuinely not `ok` -- confirmed directly,
as demanded, with a fresh connection. But the specific anomaly (95 orphaned/
never-used pages out of ~993K, provably disjoint from wiring_registry's actual
page set) is a benign free-space accounting gap in a large, heavily-churned shared
DB, not evidence of wiring_registry table or FTS5 shadow-table corruption. It does
not meet the bar to justify Step 5 (drop + recreate wiring_registry and its FTS5
shadow tables + full re-registration run). Doing so would be a destructive,
hard-to-reverse action against a table independently verified healthy and holding
24,322 live rows, taken on the basis of a misread of what the anomaly actually
indicates.

No drop/rebuild performed. No write of any kind performed against the live DB
(all connections opened `mode=ro`). This is a 6th confirmation in the same
recurring false-premise re-escalation chain, this time with an evidentially-real
but misinterpreted artifact rather than a fabricated one.

Optional, non-urgent, low-risk follow-up for the owner to schedule at their
discretion during a maintenance window: a plain `VACUUM` would reclaim the ~40%
free space and clear the 95-page bookkeeping anomaly file-wide. Not performed here
-- it requires an exclusive lock on a live 4GB DB with an active writelock file
present, i.e. other concurrent agent tasks may hold it open; that is a real
production write action that needs explicit owner sign-off, not something to
unilaterally run off an unverified re-escalation SPEC.

## Remaining
- [ ] None for this SPEC -- awaiting owner decision on optional VACUUM maintenance
      (not blocking, not part of this task).
