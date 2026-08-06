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

## Remaining
- [ ] Part 1/2: read registry_taxonomy_notes table content + UMR-20260805-093630-29d1 doc
- [ ] Part 3: confirm superboss-register.py is the one script; measure real search time/memory
- [ ] Part 4: full table inventory (sqlite) + full script inventory (/opt/veridian/scripts)
- [ ] Part 7: server-side list + honest client-side (PROJEXA, veridian-compliance-ai) inventory
- [ ] Part 8: honest yes/no on server<->browser sync mechanism, with evidence
- [ ] Compile structured findings report, deposit into UMR row (docs only)
- [ ] State explicitly: nothing built or changed
