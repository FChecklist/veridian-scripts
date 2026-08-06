# PROGRESS -- task-20260806-030104-owner-directive--source-proper-sqlite3-b

Two separate items, relates to UMR-20260806-025638-cbea and UMR-20260802-123246-f2e7.
Both UMR rows independently confirmed real in `umr_tasks` (source_trigger
`owner_dispatch_gateway`) before acting on either.

## Item 1 -- sqlite3 build + resume corruption recovery (steps 3-6)

Predecessor: `task-20260806-025647-owner-authorization--execute-sqlite3-dot`
(PR #97, veridian-scripts) correctly stopped at Step 3 -- installed
`sqlite3` (`~/.local/bin/sqlite3`) lacked `sqlite_dbpage`, `.recover` failed
with `no such table: sqlite_dbpage`. Live file untouched, backup +
working-copy already made.

### Pre-flight
- [x] Independently re-checked: live file still shows the same `file_inventory`
      corruption (`PRAGMA integrity_check` tree 38 btreeInitPage errors),
      confirming nothing changed since the predecessor task and no one has
      since touched the live path.
- [x] Current live baselines re-pulled fresh (they drift -- active system):
      `ocid_canonical_registry`=69, `gtm_certification_categories`=25,
      `umr_tasks`=6855 (was 6832 at predecessor task's check; grew from
      ongoing writes -- using 6855 as the floor, not the stale 6832).

### Source + verify proper sqlite3 build
- [x] Found `~/.local/bin/sqlite3` already reports v3.53.4 (mtime
      2026-08-06T03:02:05Z, ~seconds after the predecessor task's last
      checkpoint) -- did not assume this was good, verified directly:
      `sqlite_dbpage` queries successfully, `.recover` is a registered
      dot-command.
- [x] Ran the required real smoke test before touching the working copy
      again: built a small scratch DB (20 rows), ran `.recover` against it,
      imported the output into a fresh file, confirmed all 20 rows and
      values round-tripped correctly. `.recover` genuinely works on this
      binary.

### Resume safety sequence at Step 3
- [x] Step 3: `sqlite3 <working-copy> ".recover"` against
      `superboss-register.sqlite.working-copy-20260806T025938Z` (the working
      copy from the predecessor task, live path never touched) -- succeeded,
      exit 0, produced `superboss-register.sqlite.recover-sql-20260806T025938Z.sql`
      (1.59GB). Imported into
      `superboss-register.sqlite.recovered-20260806T025938Z` -- non-destructive,
      working copy only.
- [x] Step 4: independent verification of the recovered file --
      **FAILED, one specific check**:
      - `PRAGMA integrity_check` -> `ok` ✓
      - `ocid_canonical_registry` = 69 ✓ (exact match)
      - `gtm_certification_categories` = 25 ✓ (exact match)
      - `file_inventory` now queryable, 27,249 rows (was fully unreadable on
        live) -- the actual corruption is genuinely fixed in this recovered
        copy ✓
      - `umr_tasks` = 6,832 in the recovered file **vs 6,876 on live right
        now** -- **FAILS** the "at least as high as current live" check.
        Root cause: the working copy was snapshotted at 02:59:38Z (by the
        predecessor task); `.recover`+rebuild took ~14 min on this 1.6GB
        DB; live is actively receiving writes throughout (WAL mode,
        confirmed active writer PIDs). The gap was 23 rows when I first
        compared it and had grown to 44 by the time I finished a full
        per-table sweep -- it is still growing every second this stays
        open. A broader 40-table sweep confirms the same pattern on 6 other
        tables (`instructions`, `work_items`, `actions`,
        `directive_compliance_runs`, `wiring_registry`,
        `pm_report_snapshots` all show the recovered copy trailing live by
        similarly small amounts) -- consistent live-write drift, not a
        recovery defect.
- [ ] Step 5/6: **not attempted** -- per the sequence's own explicit
      instruction ("if any real check at any real step fails, stop
      immediately, do not proceed further, leave the real live file
      completely untouched, and report... instead of improvising
      further"), stopped here rather than swapping in a working copy that
      would silently roll back ~44+ rows of live `umr_tasks` writes (and
      smaller amounts on 6 other tables). **Live file confirmed untouched**
      -- read-only access only throughout Steps 3-4.

**This is a real, specific, reportable blocker, not a false premise**: the
recovery mechanism itself works correctly end-to-end (integrity check
clean, `file_inventory` genuinely recovered, exact matches on the two
static-reference tables) -- the remaining problem is purely that this
sequence spans two separate task invocations with an Owner-decision pause
in between (sourcing a working `sqlite3` build), during which the live DB
kept accepting writes, so the existing working copy is now stale relative
to live and getting staler. Did not improvise a fix (e.g. silently taking a
fresh working copy and re-running Steps 1-3 solo, or hand-patching the
row-count delta into the recovered file) since the current SPEC explicitly
said resume "starting again at step three" using the existing artifacts,
not restart from step one. Flagging the two real options for the Owner:
(a) authorize a fresh Steps 1-3 cycle right now (working copy taken
seconds before the Step 5 swap, minimizing the drift window to roughly the
`.recover` runtime, ~14 min for this DB size), or (b) accept the small,
enumerated live-write gap as a one-time loss for this non-critical-path
recovery. `pm_decisions_pending` id=1 left as `status=open` (not resolved
-- recovery did not complete).

## Item 2 -- Item F / projexa-ai.com architecture decision

**Finding: the premise that a cutover still needs executing is false.** The
Wave 10 cutover (`projexa-ai.com` -> `veridian-compliance-ai`) was already
executed and independently verified on 2026-08-02T13:50 UTC under a
documented Owner decision (`UMR-20260802-134939-145d`), logged in
`compliance-tracker`'s `ai-os/boss/COMPLETED.yaml` (`WAVE-10-REDO`, PR #720,
already merged to `main`) and `ai-os/IMPLEMENTATION_MATRIX_2026-08-02.md`
item 12.

- [x] Independently re-verified live, right now (not trusting cached
      findings): `curl -I https://projexa-ai.com/` and
      `https://www.projexa-ai.com/` both -> HTTP 200/307->200 via Vercel,
      `x-vercel-id` region `sin1` (matches `compliance-tracker`'s
      `vercel.json` `regions: ["sin1"]`, not the standalone `projexa`
      project's `fra1`/`iad1`). `dig` shows Vercel's shared anycast IPs
      (`216.198.79.1`/`.65`), consistent with the documented Vercel-native
      DNS setup (no external registrar).
- [x] Confirmed no live domain is currently dual-bound (read-only checks
      only, no Vercel API/CLI writes performed).
- [x] Confirmed rollback path exists and is documented, not just asserted:
      `ai-os/boss/completed-work/wave10-dns-cutover.md` (compliance-tracker
      repo) has a full, tested rollback runbook
      (`vercel domains add projexa-ai.com projexa --force`), verified this
      file is real and already committed.
- [x] Checked PR #969 (`FChecklist/compliance-tracker`, still open,
      unmerged) -- an earlier task (`task-20260806-025037`) already added
      the `b12046eb` commit citation to both target files for this exact
      finding, and explicitly deferred the architecture question itself to
      the Owner ("out of scope").
- [x] Did **not** run `vercel domains add projexa-ai.com veridian-compliance-ai
      --force` -- there is nothing to cut over; the live binding already
      matches the target state. Running it anyway would be a redundant,
      unnecessary write against live production DNS/routing based on a
      stale premise, which the standing guidance (confirm before
      hard-to-reverse/outward-facing actions; verify SPECs independently
      before any write) counsels against.
- [x] Noted but did not act on (explicitly out of scope, flagged by the
      predecessor task): an orphaned, unmerged branch/commit `ca9e6432`
      logged a competing, *unattributed* `WAVE-201` re-cutover entry the
      same day as the real `WAVE-10-REDO` one. Doesn't change the live-state
      finding above; a separate cleanup question for whoever owns that
      branch.
- [ ] Documenting the new Owner architecture-decision framing
      (UMR-20260806-025638-cbea: VERIDIAN as one product sold through brand
      extensions) into `IMPLEMENTATION_MATRIX_2026-08-02.md` /
      `COMPLETED.yaml`, citing it explicitly, without claiming to have
      executed a cutover that was already live -- in progress.

- [x] Opened **PR #970**: https://github.com/FChecklist/compliance-tracker/pull/970
      -- adds the Owner architecture-decision citation + b12046eb citation to
      `IMPLEMENTATION_MATRIX_2026-08-02.md` and `COMPLETED.yaml`, documents
      the live-state re-verification, does not claim any cutover was
      executed.

## Remaining
- [ ] Item 1: Steps 5-6 blocked pending Owner decision on the live-write
      staleness gap (see above). Recovered artifacts left in place at
      `/opt/veridian/ai-os/memory/superboss-register.sqlite.recovered-20260806T025938Z`
      (and the intermediate `.recover-sql-*.sql`) for either a fresh
      Steps 1-3 redo or direct reuse once the Owner decides. Live file
      untouched throughout. `pm_decisions_pending` id=1 intentionally left
      `status=open` -- not resolved, and the live corrupted DB itself was
      not written to for any bookkeeping update (would touch the live path
      unnecessarily while corruption/recovery is still in flight).
- [ ] Item 2: PR #970 awaiting review/merge (compliance-tracker's own
      audit/CI gate). No live routing/DNS action needed or taken.
