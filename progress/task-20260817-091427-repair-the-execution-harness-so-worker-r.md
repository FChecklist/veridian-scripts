# PROGRESS -- task-20260817-091427-repair-the-execution-harness-so-worker-r

## Completed
- [x] Verified all 4 named PRs exist (not fabricated): ai-os#6, ai-os#7, ai-os#9, scripts#422, scripts#401, scripts#416
- [x] **FALSE PREMISE FOUND on item 1 (ai-os PR#6)**: SPEC claims PR#6 (+857 lines) is
      "THE ROOT CAUSE" fix and must land first. Verified via `gh pr diff 6 --patch`:
      the diff touches ONLY `PROGRESS.md` (106 lines) and a JSON evidence file
      (751 lines) -- zero code. The PR's own body even contains a prior
      "Superboss review" note stating "this diff adds only PROGRESS.md and a JSON
      results file to this repo -- it contains zero of the act[ual fix]...".
      Further: the REAL fix for this exact bug (worker exit status never bridged
      to umr_tasks, rows stuck at "running") is **already merged to veridian-scripts
      main**, NOT ai-os: commits `ce64927` ("bridge worker exit status to umr_tasks,
      never leave rows stuck at running"), `b9522a4` (ExecStopPost hardening), and
      PR **veridian-scripts#425** (merged 2026-08-16T09:32:42Z, before this task's
      SPEC was even issued) which propagates the real preflight denial reason into
      the register row. Per the SPEC's own method step (a) and prohibition (6):
      documentation-only diffs must not be merged. **PR ai-os#6 will NOT be merged**;
      it is stale/superseded diagnostic work from 2026-08-07. This is the
      `[[veridian-task-prompt-false-premise-pattern]]` recurring again.
- [x] Confirmed ai-os#7 (re-adjudicate 320 rows) and ai-os#9 (backup/retention) are
      out of scope per SPEC -- left untouched, not merged, not adjudicated.
- [x] Confirmed scripts#422, #401, #416 all contain real code+test diffs (not docs-only).

## Remaining
- [ ] Item 2: scripts PR#422 (fast-exit fix) -- rebase, real tests, real independent
      audit (with head commit hash), merge, deploy live, prove via file read.
- [ ] Item 3: scripts PR#401 (completion gate false-rejects) -- verify prose/prohibition
      coverage; extend + add 3 regression tests if missing; rebase, tests, audit, merge, deploy.
- [ ] Item 4: scripts PR#416 (stale-skip gate) -- rebase, real tests, real independent
      audit, merge, deploy live, prove via file read.
- [ ] End-to-end proof: dispatch one trivial real one-line-change task, let it run to
      completion, assert register row reaches terminal state unaided and matches
      actual outcome; assert a framework-name-in-prose prompt does not create a
      completion target.
- [ ] Final DoD report: (a)-(f) per SPEC.

## Notes
- Live checkout is `/opt/veridian/scripts` (working copy of veridian-scripts) and
  `/opt/veridian/ai-os` presumably tracks veridian-ai-os -- must verify and
  fast-forward + read back file contents per SPEC step (e), not just merge on GitHub.
- Owner-reserved, untouched: ai-os#7, ai-os#9, dispatch_core.py (standing stop-work
  order, see `[[veridian-dispatch-core-py-frozen-stop-work-order]]`), CI workflow
  definitions, Owner PAT.
