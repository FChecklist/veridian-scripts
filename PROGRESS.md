# PROGRESS -- task-20260806-050102-owner-standing-directive--register-umr-f

SPEC: Real Owner standing directive. This dispatch itself mints the real
permanent UMR ID for the whole nine-part directive covering UMR/UTR global
registry consolidation and the Mini VERIDIAN browser-first local execution
architecture. Hard constraint: **analysis only** -- investigation and
written notes only, nothing built, nothing implemented, until PM reviews
findings and gives explicit build authorization.

In scope for this task (parts 1,2,3,4,7,8 only -- parts 5,6,9 are PM-level
conceptual framing, PM handling those directly):
1/2. Confirm whether UTR=umr_tasks / UMR=broader metadata taxonomy
     (UMR-20260805-093630-29d1) already covers what the Owner described --
     check `registry_taxonomy_notes` table content directly, don't assume.
3. Confirm superboss-register.py is genuinely the one script AI agents use;
   measure real search time + memory use on a representative query.
4. Full inventory: every table in superboss-register.sqlite, every script
   in live /opt/veridian/scripts, one line each.
7. Two lists: global server-side tables/functions/scripts, and honest
   client-side inventory for PROJEXA + veridian-compliance-ai frontends
   (localStorage/IndexedDB/PWA manifest/offline support) -- report what's
   real, invent nothing.
8. Honest yes/no on whether a server<->browser sync mechanism exists today,
   with evidence.

## Standing practice applied (per memory: prior urgent PM SPECs in this
## repo have not always matched live state -- verify independently first)
All of the above is independent verification against live system state
before any conclusion is written up. No file writes to live systems, no DB
writes, no code changes -- this task only produces a findings document to
deposit into the UMR row.

## Completed
- [x] Located live system paths: DB = /opt/veridian/ai-os/memory/superboss-register.sqlite
      (has WAL); canonical script = /opt/veridian/scripts/superboss-register.py;
      candidate frontends = /opt/veridian/repos/projexa,
      /opt/veridian/repos/compliance-tracker.

- [x] Confirmed "this UMR row" = `UMR-20260806-050055-d145`
      (`task_identity owner-task-20260806-050053-1500765`, status
      `running`) via direct read-only query -- matched its `inputs_json`
      verbatim against the Owner's SPEC text rather than assuming.
- [x] Part 1/2: read `registry_taxonomy_notes` table content directly +
      UMR-20260805-093630-29d1 record verbatim -- verdict PARTIAL. Taxonomy
      naming (UTR=umr_tasks, UMR=broader layer) is real and merged; zero
      evidence anywhere of "consolidation" work or "Mini VERIDIAN
      browser-first" architecture under that or any related UMR.
- [x] Part 3: confirmed superboss-register.py is the real one script via
      independent wiring grep (worker-entrypoint.sh, task-gateway.py,
      prompt_gateway/gateway.py, etc, not just its own docstring). Measured
      real numbers on a representative `search` query (mirroring the real
      gateway_persistence.py call site) via read-only-connection harness:
      ~0.04-0.05s wall clock, ~19.7MB peak RSS, both runs, against a live
      1.6GB DB.
- [x] Part 4: full inventory -- 36 real DB tables (+ FTS shadows) and all
      124 live top-level scripts in /opt/veridian/scripts, one line each,
      170 backup/dead files excluded and counted separately, 7-item
      possible-duplication callout list (incl. 3 scripts still live
      despite being marked superseded-by-consolidation).
- [x] Part 7: server-side list (36 tables + 124 scripts) + honest
      client-side inventory for PROJEXA and compliance-tracker (confirmed
      as the real veridian-compliance-ai repo by its own docs, not
      assumed). PROJEXA has a real working service worker + IndexedDB
      offline queue. compliance-tracker has a real PWA manifest + real
      IndexedDB (incl. an existing browser-execution ML model-cache tier
      directly relevant to "Mini VERIDIAN") but zero service
      worker/offline support.
- [x] Part 8: verdict PARTIAL/leaning-NO -- only plain HTTP polling exists
      client<->each app's own server; zero server-push; no frontend calls
      /opt/veridian/scripts or ai-os at all; one real delta-sync module
      (compliance-tracker's sync-engine.ts) exists but is unwired,
      imported only by its own test file. Corroborated against (not just
      trusted) an existing internal doc reaching the same conclusion.
- [x] Compiled structured findings into
      `UMR-20260806-050055-d145_MINI_VERIDIAN_UMR_UTR_ANALYSIS_2026-08-06.md`
      and deposited it in this task's branch, citing the real UMR row.
- [x] Explicitly stated in that document: nothing was built or changed --
      all five investigation threads were read-only (DB opened
      `-readonly` throughout, mtime confirmed unchanged; no source files
      modified in scripts or either frontend repo; no
      install/build/deploy commands run).

## Remaining
- [ ] None -- analysis-only scope (parts 1,2,3,4,7,8) delivered. Awaiting
      PM review of findings + explicit build authorization before any
      implementation work (and before parts 5,6,9, which are PM-level
      conceptual framing handled directly by the PM).

## Post-delivery checkpoint (invocation 2/20)
- [x] PR #125 was open but `mergeable: CONFLICTING` against `main`
      (another task's PR, #126, had merged in between and also touched the
      repo-root `PROGRESS.md` scratch file -- expected per-task scratch
      collision, not a real content conflict). Merged `origin/main` into
      this branch with `-X ours` on `PROGRESS.md` (kept this task's own
      progress notes; the findings doc had no conflict) and pushed
      (`7f140fc`). PR #125 is now `state: OPEN`, `mergeable: MERGEABLE`.
- [ ] Nothing further for this worker to do -- PR ready to merge, still
      pending PM review/authorization per the analysis-only constraint.
