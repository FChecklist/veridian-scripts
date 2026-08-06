# PROGRESS -- task-20260806-035541-owner-directive--build-a-real-pm-cycle-s

SPEC: extend the zero-manual-searching principle from the 10-minute PM report to the rest of the real
PM cycle. See `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md` for the independent verification of every
concrete claim in the SPEC (two did not match live state, documented there plainly).

## Completed

- [x] Item 1 (script registry check): independently verified `capability_registry` already has a
      `version` field (nothing to add there) but is the wrong table for generic script bookkeeping
      (business-capability schema, no `path` column). The genuinely correct existing mechanism is
      `wiring_registry`'s `entity_type='script'` rows (`register-entity`/`lookup-entity`/`list-entities`).
      Extended it (not `capability_registry`, not a new table) with two new nullable columns,
      `originating_umr` and `script_version`, via an additive idempotent migration
      (`_migrate_wiring_registry_umr_and_version()` in `superboss-register.py`), wired into
      `_migrate_schema()` and into `_migrate_wiring_registry_entity_types()`'s own rebuild path (so a
      future entity-type-widening rebuild doesn't silently drop the new columns).
      `register_entity_row()` now accepts both as optional fields.
- [x] Item 2 (backfill): found and fixed the real root cause of an existing catalog gap --
      `generate_software_catalog.py`'s `list_scripts()` only ever matched `*.py`, silently excluding 28
      real `*.sh`/`*.mjs` scripts. Fixed scope to `.py`/`.sh`/`.mjs`, added a shell-header-comment
      purpose extractor, and added real, mechanically-recovered (never invented) `originating_umr` /
      `script_version` fields per script. `generate_wiring_registry.py`'s `build_scripts_and_cron()`
      passes both through into `wiring_registry`. Verified zero `gtm_check_*.py` files exist on this
      server (SPEC's premise was false -- they exist only on unmerged feature branches); backfill covers
      every real script that actually exists, invents nothing for ones that don't.
      **Real backfill executed against the live production DB**: see "Real live evidence" below.
- [x] Item 3 (new script): `pm_cycle_precheck.py` -- one read-only invocation covering server health
      (reuses `generate_pm_report_v3.py`'s own functions directly, zero duplication), dispatch-tick
      results since the last real PM cycle (`pm_report_snapshots`), a zero-duplication precheck over
      queued/dispatched/running `umr_tasks` plus the existing `check_duplicate()` capability search,
      tracked PR state checks (`gh pr view`), and the three OCID-068 regression checks (resolver
      present, `ocid_canonical_registry` row count vs. the real 69-row baseline, seven guardrail PRs
      still ancestors of `origin/main`). Only write is its own bookkeeping log append
      (`--no-bookkeeping-write` to skip).
- [x] Item 4 (self-registration): every touched/new script (`superboss-register.py`,
      `generate_software_catalog.py`, `generate_wiring_registry.py`, `pm_cycle_precheck.py`)
      self-registered into `wiring_registry` via `register-entity` -- see real invocation output below.
- [x] Test coverage: `tests/test_wiring_registry_umr_and_version.py` (5 tests), `test_generate_software_catalog.py`
      (9 tests), `test_pm_cycle_precheck.py` (7 tests) -- all against real isolated temp-file DBs, never
      the live production DB. Full repo suite: 198 passed (4 pre-existing, unrelated `vt`-fixture errors
      in `test_ocid063_handoff_envelope.py`, already tracked on branch
      `fix/ocid063-handoff-envelope-pytest-vt-fixture`, not introduced by this task).
- [x] Independent verification doc: `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md`.

## Real live evidence (PR https://github.com/FChecklist/veridian-scripts/pull/114)

- **Bonus real bug found and fixed**: `generate_wiring_registry.py`'s `SOFTWARE_CATALOG` constant was
  wrongly using `resolve_doc_path()` (meant for hand-maintained planning docs, prefers the git mirror)
  instead of the hardcoded-real-path convention its 3 sibling machine-generated catalogs use right
  above it. Since `generate_software_catalog.py` always writes straight to the real
  `/opt/veridian/ai-os/SOFTWARE_CATALOG.yaml` and never to the mirror, the mirror copy
  (`/opt/veridian/repos/claude-control/ai-os/SOFTWARE_CATALOG.yaml`) was silently stuck at **40
  scripts, dated 2026-07-24** -- over 2 weeks stale -- and every prior real
  `generate_wiring_registry.py` run on this server (which never runs from inside a `veridian-scripts`
  git checkout) silently backfilled `wiring_registry` from that stale copy instead of live reality.
  Fixed by hardcoding the real path, matching its siblings.
- Ran `generate_software_catalog.py` for real against the live server: **101 -> 122 real scripts**
  cataloged (confirms the `.sh`/`.mjs` fix), **56/122 got a real `originating_umr` tag**, 3/122 got a
  real `script_version` tag, all mechanically recovered, none invented.
- Ran the narrow, scoped backfill (`build_scripts_and_cron()` + `upsert_live_wiring_registry()` only --
  not the full `generate()` pipeline, to avoid touching the other ~8,300 unrelated live rows) for real
  against the live production DB: **124 real `entity_type='script'` rows**, e.g.
  `script-superboss_register_py` -> `originating_umr='UMR-20260806-031211-64de'`,
  `script-generate_wiring_registry_py` -> `originating_umr='task-20260725-032718'`,
  `script-worker_entrypoint_sh` -> `originating_umr='UMR-20260801-153900-9100'` (real per-file regex
  recovery, live-verified).
- Self-registered `pm_cycle_precheck.py` (not yet deployed to `/opt/veridian/scripts`, so the catalog
  scan above correctly couldn't see it) via `register-entity` directly:
  `verification_status='PATH_MISSING'`, `originating_umr='task-20260806-035541'`,
  `metadata.not_yet_deployed_pending_pr=114` -- honest, not fabricated as already-deployed.
- **Real sample invocation** of `pm_cycle_precheck.py` against the live production DB:
  `python3 pm_cycle_precheck.py --search-term "pm_cycle_precheck" --pr-numbers "114,109"` --
  correctly found PR #114 `[OPEN]` / PR #109 `[MERGED]` via live `gh pr view`, correctly flagged the
  zero-dup precheck (`found=1 verdict=STOP`, matching its own just-created registry row), and passed
  all 3 real OCID-068 regression checks (resolver present, 69/69 canonical rows, all 7 guardrail PRs
  still ancestors of `origin/main`) in one invocation, one SSH round trip. Full output in this task's
  own transcript.# PROGRESS -- task-20260806-050102-owner-standing-directive--register-umr-f# PROGRESS -- task-20260806-033142-real-correction--both-pr-98-and-pr-100-g
Real PM correction, relates to UMR-20260806-032912-9088. SPEC claimed both PR
#98 and PR #100 raced each other closed, leaving the worker-entrypoint 429
hard-stop fix with zero open PRs, and asked me to reopen #100 specifically,
re-verify its diff contains both the primary 429 fix and a secondary
circuit-breaker fix, confirm tests pass, get it reviewed, and merge it.

## Completed

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
      install/build/deploy commands run).- [x] Independently verified live PR state before touching anything (per
      this session's own memory note on veridian-scripts SPECs not matching
      live state -- same pattern here). **SPEC's premise was already stale
      by the time this task was dispatched**: PR #100 was NOT closed. It had
      already been reopened at 2026-08-06T03:30:24Z by the same task thread
      that originally raced #98 (see PR #100's own comment thread) --
      *before* this task even started. Nothing to reopen.
- [x] Confirmed PR #98 correctly stays closed (real duplicate, superseded by
      #100, per its own closing comment).
- [x] Independently re-verified PR #100's actual diff (`gh pr diff 100`,
      cross-checked byte-for-byte against the copy embedded in the automated
      supervisor's own review-agent log -- two independent sources, not just
      my own fetch):
  - Primary 429/weekly-usage-limit hard-stop in `worker-entrypoint.sh`:
    **present, correct**. `bash -n` syntax check passed. Functionally
    smoke-tested the new detection one-liner against the real failed task's
    own `.claude-out-main.json` (task-20260805-193951) -- correctly prints
    `1`; against a synthetic ordinary-success JSON -- correctly prints `0`.
  - Fleet-wide-scope claim ("27 other tasks hit the identical error"):
    independently corroborated -- a direct grep across
    `/opt/veridian/ai-os/tasks/*/.claude-out-main.json` for
    `api_error_status: 429` found 29 tasks total (including this incident's
    own), consistent with the claim (small remainder from an unrelated
    2026-07-23 burst window).
  - **Secondary circuit-breaker fix: claimed but NOT actually present.**
    PR #100's own PROGRESS.md says "real secondary bug found **and fixed**"
    (circuit breaker's `record_failure_signature()` hashes `worker.log`'s
    last 400 chars, which always contains a per-invocation random
    `action_id`/`session_id`, so repeated identical 429s produce different
    signatures and never trip the 2-consecutive-identical breaker). The
    diagnosis is real and well-evidenced. But the diff **only touches
    `worker-entrypoint.sh`'s 429-detection block and `PROGRESS.md`** --
    no hunk touches `record_failure_signature()` or `preflight-guard.py`.
    Confirmed the function is unchanged: read it directly out of the
    working tree (`sed -n '340,400p' worker-entrypoint.sh`), still hashes
    the raw log tail with no normalization. **This part of the SPEC's claim
    does not hold** -- the PR documents the secondary bug but does not code
    a fix for it.
- [x] PR #100 already went through real independent review and merged --
      **not by my hand**: the pre-existing automated `veridian-supervisor`
      pipeline (task `task-20260806-checkpoint-pr100-adoption`, tier1) ran a
      fresh Claude review against the actual diff (confirmed via its own
      supervisor.log: read `SUPERBOSS_DISPATCH_PROMPT.md`, reviewed the real
      diff, verdict `approve`), and autonomously merged it per its
      documented tier1 policy while I was still mid-verification. Confirmed
      independently via `gh pr view 100` (`state: MERGED`,
      `mergedAt: 2026-08-06T03:34:10Z`) and `git log origin/main`
      (merge commit `9730b1e74a8e6b92a4f6f7a566bfdbee118f20c7`). The
      automated reviewer's own approval also did not catch that the
      secondary fix was undelivered -- its 3 non-blocking issues were about
      scope gaps elsewhere (quality-gate auto-fix loop not covered by the
      429 check; the text-fallback match being broader than the one
      confirmed string), not this.
## Remaining

- [ ] Get PR #114 merged.
- [ ] Full-catalog `generate_wiring_registry.py` rebuild (all entity types, not just scripts) is
      deliberately OUT of scope for this task -- it touches ~8,300 unrelated rows and risks racing
      concurrent sibling tasks writing to the same shared production DB. Left for its own routine cron
      run; this task only ran the narrow, scoped script-only backfill described above.## Post-delivery checkpoint (invocation 2/20)
- [x] PR #125 was open but `mergeable: CONFLICTING` against `main`
      (another task's PR, #126, had merged in between and also touched the
      repo-root `PROGRESS.md` scratch file -- expected per-task scratch
      collision, not a real content conflict). Merged `origin/main` into
      this branch with `-X ours` on `PROGRESS.md` (kept this task's own
      progress notes; the findings doc had no conflict) and pushed
      (`7f140fc`). PR #125 is now `state: OPEN`, `mergeable: MERGEABLE`.
- [ ] Nothing further for this worker to do -- PR ready to merge, still
      pending PM review/authorization per the analysis-only constraint.- [ ] Real residual gap, not closed by this task: the circuit-breaker
      signature-hashing bug (`record_failure_signature()` in
      `worker-entrypoint.sh`) is genuinely still unfixed in code on `main`.
      Deliberately did **not** patch this myself -- it's shared retry-safety
      logic for every `veridian-worker@*` unit in the fleet, the SPEC asked
      me to *verify* the existing diff rather than author a new fix for a
      gap in someone else's already-merged PR, and this session's own
      memory note counsels caution before unrequested writes to
      infrastructure this central. Recommending a scoped fast-follow task
      instead of silently expanding this one's scope.

## Environment note (not a real finding, recorded so it isn't rediscovered)

`git show <ref>:<path> > file` intermittently produced truncated content
(cutting off at an arbitrary line with a literal placeholder string) for
`worker-entrypoint.sh` specifically, while `git diff --stat HEAD` (whole
repo) and plain `diff` against the real working-tree file stayed reliable
and consistent with each other and with GitHub's own state throughout. Did
not chase further since the two independent, trustworthy sources (my own
`gh pr diff` and the supervisor's own logged diff text) already fully
answered the actual question. Treat single-file `git show` redirection with
suspicion in this environment; prefer `gh pr diff`, `git diff --stat`, or
reading the working tree directly.