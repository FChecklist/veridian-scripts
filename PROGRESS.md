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
- [ ] Step 4: independent verification of the recovered file (integrity_check
      ok, row-count baselines, `file_inventory` queryable) -- in progress.
- [ ] Step 5: only if Step 4 passes fully -- final fresh live backup, then
      atomic `mv` swap of the recovered file onto the live path.
- [ ] Step 6: post-swap re-verification + resolver/write-through check,
      then mark `pm_decisions_pending` id=1 resolved.

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

## Remaining
- [ ] Item 1: finish Steps 4-6, report per-step outcome.
- [ ] Item 2: land the architecture-decision citation, open PR, report
      outcome.
