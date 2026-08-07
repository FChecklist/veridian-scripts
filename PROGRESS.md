# PROGRESS -- task-20260807-142924-register-the-real-965-issue-resolution-m

## Completed

- [x] Independently verified `/opt/veridian/ai-os/UMR_5767_ISSUE_RESOLUTION_MATRIX.json`
      is real, well-formed JSON (`python3 json.load()`, 1,263,697 bytes,
      sha256 `d633dec8488550895927c793d3ccd55bb68427aba97d57ae77c86cefcfacf4ca`).
      `real_checklist_item_count`=70 matches `len(checklist_resolution_table)`
      exactly. **Found a discrepancy with the dispatch SPEC**: the SPEC
      claimed `real issue_count 965`, but the file's own `real_issue_count`
      field and `len(issues)` both independently read **977**, with
      `issue_number` running 1..977, no duplicates. The file's own
      `phase2_subphase1_tool_selection` block cites an internal sub-range
      `"covers_issue_range": "916-965"` -- almost certainly the source of the
      SPEC's 965 figure (a range boundary misread as the document total).
      Registered using the real 977 count, not the SPEC's unverified 965.
- [x] Confirmed real `DB_PATH` resolution: superboss-register.py's live DB is
      `/opt/veridian/ai-os/memory/superboss-register.sqlite`. A stale-decoy
      `/opt/veridian/scripts/superboss-register.sqlite` (0 bytes) exists on
      disk and was **not** touched -- same wrong-DB-file trap flagged before
      in this chain.
- [x] Deduped first: `lookup-capability --capability-name
      umr_5767_issue_resolution_matrix` returned `found=false` before
      registering (24 existing capability rows, none matching).
- [x] Registered via `superboss-register.py register-capability`
      (never a raw INSERT) with the real file path in `documents`, citing
      `originating_umr: UMR-20260806-171945-5767`. Result:
      **`capability_id = CAP-20260807-143709-d29a`**.
- [x] Independently re-confirmed persistence in a fresh process two ways:
      (1) `lookup-capability --capability-name ...` in a fresh `python3`
      subprocess, `found=true`; (2) a direct `sqlite3` CLI query (a
      completely separate tool, not the Python script) against
      `capability_registry` -- both show the real row.
- [x] Called `agent_work_briefing.py record-completion` for the SPEC's
      governing-chain UMR (`UMR-20260806-171945-5767`), citing the real
      evidence above (no `--umr-status` passed -- that UMR is already
      terminal (`status='killed'`), left untouched).
- [x] Called `agent_work_briefing.py record-completion` for this task's own
      dispatch UMR (`UMR-20260807-104456-2e64`, per the deterministic
      briefing block), citing the same real evidence.

## Remaining
- [ ] None.
