# PROGRESS -- task-20260805-161241-document-the-real-utr--umr--and-single-s

## Completed
- [x] Located the real DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite`) and confirmed
      this task's own UMR (`UMR-20260805-093630-29d1`, `owner-task-20260805-093629-1256404`) is the
      live `umr_tasks` row for this exact SPEC.
- [x] Independently re-verified the real, permanent explanatory taxonomy row already exists at the
      real source: `registry_taxonomy_notes` table (`superboss-register.py`
      `_ensure_registry_taxonomy_notes_table`/`_seed_registry_taxonomy_notes`/
      `record_registry_taxonomy_note`), live row `note_key='utr_umr_single_source_of_truth_taxonomy'`
      -- content matches this SPEC's required taxonomy exactly, citing this task's own UMR.
- [x] Independently re-verified the same taxonomy is already in the OCID-068 real addendum document
      (`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`, section 7).
- [x] Independently re-verified this went through a real pre-merge audit review: PR #57 (merged,
      commit `c8f40eb`/`768fd6e`) carries a real, structured `AUDIT: PASS` review comment (posted
      under this repo's systemic automated-audit convention -- disclosed, not glossed over, that
      it shares its GitHub account with the PR author, same as every other merged PR checked).
- [x] Got a real independent review of this task's own new work (a fresh subagent, no shared
      context) before committing -- it found the doc's claims all held up against fresh live
      evidence, flagged one overclaim (the PR #57 audit comment's "independence" needed the
      same-account caveat above), which was fixed before commit.
- [x] Took a real, sha256-verified live-DB backup, then ran the canonical
      `superboss-register.py reconcile-umr-status --umr-id UMR-20260805-093630-29d1 --apply` to
      correct stale bookkeeping (`running` -> `completed`, matching PR #57's real merge evidence).
- [x] Wrote `OCID_068_UTR_UMR_TAXONOMY_INDEPENDENT_VERIFICATION_2026-08-05.md`, the new additive
      OCID-068 addendum documenting this duplicate-check finding and the bookkeeping correction.
- [ ] Commit + push this task's own real changes (the new addendum doc, this PROGRESS.md).
- [ ] Open PR, get real independent review, merge.

## Remaining
- [ ] Commit + push.
- [ ] Open PR against `main`.
- [ ] Confirm real independent review (the repo's automated pre-merge audit) before merging.
- [ ] Merge, record the PR link here.
