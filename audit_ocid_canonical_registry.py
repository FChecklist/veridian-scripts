#!/usr/bin/env python3
"""audit_ocid_canonical_registry.py -- OCID-068 Phase 2 real, standalone,
independently re-runnable, deterministic audit driver (Owner urgent
correction UMR-20260805-092408-4f97, extending UMR-20260805-090549-9710 /
UMR-20260805-091934-86a2, citing the canonical OCID-068 UMR
UMR-20260804-170055-a069).

Real citation honesty note (this module's own real safety-rule revision
below, task-20260805-161157-close-a-real-fabrication-loophole--not-a): a
real, direct query of the live `umr_tasks` table found no row whose
`task_identity` matches this exact task -- no real UMR ID with a hash
suffix could be independently verified for it as of this writing. Rather
than write an unverified, plausible-looking `UMR-YYYYMMDD-HHMMSS-hash`
citation into this module or into any real database row (exactly the kind
of unverifiable claim this whole task exists to close off), this task is
cited below and in `_THIS_TASK_CITATION` by its real, literal task-
directory identifier instead, with this same honest caveat.

Zero AI judgment inside this script: its only logic is (1) iterate over a
real list of OCID numbers, (2) call the already-merged, already-locked
resolve_ocid_canonical() (UMR-20260805-042152-e559) for each -- the one
real, existing, fully mechanical, zero-AI-judgment 6-method search this
codebase already runs, in this exact order:
  (a) real umr_tasks.task_identity substring match, multiple casings
  (b) real full dump + grep of every umr_tasks text column
  (c) real `gh pr list --search "<OCID> in:title,body"` across
      compliance-tracker/veridian-scripts/projexa, --state all
  (d) real `git log --all --oneline -i --grep=<OCID>` across the same 3
      repos, cross-check only
  (e) real UMR ID extraction (regex) from matched PR body/title text
  (f) real MASTER-TRACKER.yaml/ACTIVE-CLAIMS.yaml grep, last resort only
(see resolve_ocid_canonical()'s own docstring in superboss-register.py for
the authoritative description -- this script does not reimplement or
duplicate any of it), and (3) decide, by a single fixed, deterministic rule
applied identically to every OCID (never a per-row judgment call), whether
to overwrite the existing row's canonical_umr_id/status/all_umr_ids/
pr_number/pr_repo/duplicate_reason with this run's fresh result, or
preserve the existing values while still recording the fresh raw evidence.

Fixed merge rule (identical for every OCID, no per-row exception --
REVISED this task (see the module docstring's citation-honesty note),
after this script's own first real
run against live production data proved the original rule below unsafe;
see plan_for_ocid()'s own docstring for the full real live-data proof):
  - `audit_raw_output` is ALWAYS overwritten with this run's fresh, bounded,
    verbatim result -- per UMR-20260805-092408-4f97, its real provenance
    must always trace to the latest real mechanical run, never to a stale
    or hand-typed value. This is the one real mechanism that structurally
    closes the `not_applicable_confirmed` fabrication loophole this whole
    task exists for.
  - canonical_umr_id/status/all_umr_ids/pr_number/pr_repo/not_found are
    ALWAYS PRESERVED exactly as already recorded on the existing row, for
    every real existing OCID row, with no exception -- this script never
    silently overwrites a real, already-reasoned canonical choice, ever,
    regardless of what the fresh mechanical search finds. (Superseded
    behavior, kept here only as a documented historical note: the original
    version of this rule preserved only when the fresh run still
    corroborated the existing choice, and otherwise silently substituted
    the fresh result in full. That was proven live-data-unsafe this task --
    a fully mechanical full-text search over a corpus that increasingly
    contains real meta-discussion ABOUT OCIDs, not just genuine completion
    evidence FOR them, produces real false "corrections" that would have
    overwritten real, carefully-reasoned historical judgments, e.g.
    OCID-001's `rejected_duplicate (historical, ...)` status.)
  - Any real disagreement between the fresh search and the existing record
    is appended (never replacing the original reasoning) to
    `duplicate_reason` as an explicit, honest NEEDS HUMAN REVIEW note.
  - Only when no existing row exists at all for an OCID number (not
    currently possible for OCID-001..069; kept for real forward-
    compatibility) is this run's fresh result used to populate the row.

Bounded-storage rule (this task -- see the module docstring's citation-
honesty note; identical for every OCID, never a per-row judgment call): before
`audit_raw_output`/`evidence` are written, every individual string leaf
value in the fresh evidence dict is passed through `_bounded_for_storage()`
-- a fixed 5000-char cap applied identically regardless of OCID, with the
real original length disclosed whenever a value is capped. This is not
interpretation and not a narrative summary -- every real command actually
run and its real result is still recorded, and every genuine gh/git/grep
result (naturally well under the cap) is untouched byte-for-byte. It exists
solely because real production `umr_tasks` rows were found this task to
carry multi-megabyte `metadata_json` values (a real, separate, pre-existing
data-quality issue in this codebase's task-dedup engine, independently
flagged, not fixed here) that resolve_ocid_canonical()'s method (b) (full
umr_tasks grep) legitimately matches for most real OCID numbers -- storing
those verbatim in full would have added an estimated 1-2+ GB to the live
production database in a single --apply run. See _bounded_for_storage()'s
own docstring below for the full real accounting.

Default is a dry run (prints the real proposed per-OCID plan as JSON,
writes nothing). Pass --apply to actually write, via the real, already-
merged upsert_ocid_canonical_registry() (never raw SQL), inside a real
_write_lock(). --ocid-number limits to a single OCID (debugging / targeted
re-run); default is the full real OCID-001..OCID-069 range.
"""
import argparse
import importlib.util
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ALL_OCID_NUMBERS = [f"OCID-{n:03d}" for n in range(1, 70)]  # OCID-001..OCID-069

# Real operational-safety cap, discovered this task (see the module
# docstring's citation-honesty note; extending UMR-20260805-092408-4f97 /
# -091934-86a2 / -090549-9710): running this script for real against the live production
# `umr_tasks` table (never exercised against real production data before --
# every prior run of this exact script was either --dry-run or against a
# scratch test DB) surfaced that a handful of real umr_tasks rows (this same
# session's own OCID-068 Phase-2 dispatch/reuse-check rows among them) carry
# a `metadata_json.reuse_check_result` field 6+ MB in size, because the
# real, separate, already-existing task-dedup engine embeds full candidate
# intent text, not just match scores, in that field (a genuine, real,
# pre-existing data-quality issue in that OTHER subsystem, independently
# flagged for Owner awareness below -- out of scope to fix here). Method (b)
# of resolve_ocid_canonical() (real full-table grep, UMR-20260805-... gap
# fix) legitimately matches those rows for a majority of real OCID numbers
# (many of those dispatch texts literally enumerate ranges like "OCID-001
# through OCID-069"), so storing every matched row's FULL text verbatim in
# `audit_raw_output` would have added an estimated 1-2+ GB to the live
# 1.4 GB production superboss-register.sqlite in a single --apply run --
# a real, hard-to-reverse, outward-facing operational risk, not merely a
# cosmetic one.
#
# The fix applied here is a fixed, deterministic, identically-applied-to-
# every-OCID byte cap on individual string leaf values inside the evidence
# dict before it is stored -- NOT interpretation of what the evidence means,
# NOT a narrative summary, NOT a per-OCID judgment call. Every real command
# actually run and its real result are still recorded; only pathologically
# oversized individual matched-row-text values (never the genuine gh/git/
# grep command outputs themselves, which are naturally small) are capped,
# with the real original length disclosed alongside the cap, and the exact
# same real search fully re-runnable on demand (via this same script, or
# directly via resolve_ocid_canonical()) to recover the untruncated value.
_AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP = 5000
_TRUNCATION_MARKER_SENTINEL = "REAL VERBATIM VALUE TRUNCATED FOR STORAGE"
_NEEDS_HUMAN_REVIEW_SENTINEL = "NEEDS HUMAN REVIEW"
# See the module docstring's "Real citation honesty note": no real umr_tasks
# row could be found for this exact task, so it is cited by its real
# task-directory identifier rather than an unverified UMR ID.
_THIS_TASK_CITATION = (
    "task-20260805-161157-close-a-real-fabrication-loophole--not-a "
    "(no matching umr_tasks row found for this task_identity as of this "
    "writing; cited by its real task-directory identifier, not an "
    "unverified UMR ID)"
)


def _bounded_for_storage(value, max_chars=_AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP):
    """Pure, deterministic, zero-AI-judgment recursive cap on string leaf
    values -- same fixed limit applied identically to every OCID, every
    field, every run. Dicts/lists are walked and rebuilt with every string
    value over `max_chars` replaced by a real, disclosed-truncation marker
    naming the real original length; everything else (including every
    string at or under the cap, i.e. every genuine real gh/git/grep command
    result in ordinary practice) passes through byte-for-byte unchanged.
    Deterministic and idempotent: calling this twice on the same input (or
    on its own output) yields the same result, which is what
    tests/test_audit_ocid_canonical_registry.py's determinism proof
    requires."""
    if isinstance(value, str):
        if len(value) <= max_chars or _TRUNCATION_MARKER_SENTINEL in value:
            # Already at/under the cap, OR already carries this function's
            # own truncation marker (re-applying the cap to its own prior
            # output is a real, deliberate no-op -- required for
            # idempotence/determinism across repeated real runs; without
            # this check, the marker text itself would push a
            # freshly-truncated value back over max_chars and get truncated
            # again, differently, each time it is re-bounded).
            return value
        return (
            value[:max_chars]
            + f"...[REAL VERBATIM VALUE TRUNCATED FOR STORAGE: {len(value)} total real chars, "
              f"fixed {max_chars}-char cap applied identically to every OCID by "
              f"audit_ocid_canonical_registry.py's own _bounded_for_storage(); re-run this same "
              f"script, or call resolve_ocid_canonical() directly, for the full untruncated real value]"
        )
    if isinstance(value, dict):
        return {k: _bounded_for_storage(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_bounded_for_storage(v, max_chars) for v in value]
    return value


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_audit_ocid", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def plan_for_ocid(sbr, conn, ocid_number, existing_by_ocid, **resolve_kwargs):
    """Pure (given a real resolve_ocid_canonical() call and the real
    existing row snapshot) decision function -- the one fixed merge rule
    documented above, applied identically to every OCID. Kept separate from
    main() so real tests can call it directly with an injected fake
    `_runner`/`conn`, no live network or live DB required.

    Real safety rule (this task -- see the module docstring's citation-honesty
    note -- replacing
    this function's own original "overwrite when no longer corroborated"
    fallback -- live-data-proven unsafe, not merely theorized): this
    script's very first real run against the live production database (this
    same task) found that resolve_ocid_canonical()'s method (b) (full-table
    `umr_tasks` grep) legitimately, mechanically, but WRONGLY matches
    unrelated meta-dispatch UMRs whose own real prompt text enumerates
    broad OCID ranges ("populate OCID-001 through OCID-068", "the real
    not_found rows OCID-007 through OCID-014") as if those UMRs were real
    completion evidence for every individual OCID number they merely
    mention. Concretely, live-verified this task: OCID-001's real, careful,
    historical `canonical_umr_id=UMR-20260802-034545-3388` /
    `status=rejected_duplicate (historical, pre-OCID-numbering, no
    implementation authorized)` -- a deliberate, reasoned prior judgment --
    would have been silently overwritten by this function's OLD fallback
    with a spurious match against the unrelated batch-registration dispatch
    UMR, and OCID-007/OCID-011 (real, honestly-confirmed `not_found` rows)
    would have been silently flipped to a false "found" status by matching
    THIS VERY TASK's own meta-dispatch text discussing them.

    A fully mechanical, zero-AI-judgment full-text search over an
    ever-growing corpus that increasingly contains real meta-discussion
    ABOUT OCIDs (not just genuine completion evidence FOR them) is
    therefore not a safe unattended authority to silently overwrite an
    existing, already-reasoned canonical_umr_id/status/all_umr_ids/
    duplicate_reason/not_found -- so this function now NEVER does that.
    canonical_umr_id/status/all_umr_ids/pr_number/pr_repo/not_found are
    ALWAYS preserved exactly as already recorded, for every real existing
    row, no per-OCID exception. This run's real, fresh, bounded, verbatim
    evidence is still always captured in `audit_raw_output` (the actual
    real fix this whole task exists to deliver: structurally gating
    `not_applicable_confirmed` on real, re-runnable, stored evidence rather
    than a hand-typed claim -- see the module docstring), and any real
    disagreement between the fresh search and the existing record is
    appended (never silently dropped, never replacing the original
    reasoning) to `duplicate_reason` as an explicit NEEDS HUMAN REVIEW note
    for the Owner. Only when no existing row exists at all (not currently
    possible for OCID-001..069, all 69 of which already have a row; kept
    for real forward-compatibility if a new OCID number is ever added) is
    this run's fresh result used to populate the row, since there is then
    nothing real to protect from being overwritten."""
    fresh = sbr.resolve_ocid_canonical(ocid_number, conn, **resolve_kwargs)
    existing = existing_by_ocid.get(ocid_number)
    bounded_fresh_evidence = _bounded_for_storage(fresh["evidence"])

    if existing is not None:
        if existing.get("not_found"):
            corroborated = bool(fresh["not_found"])
        else:
            corroborated = bool(
                existing.get("canonical_umr_id")
                and existing["canonical_umr_id"] in fresh["all_umr_ids"]
            )

        note = existing.get("duplicate_reason")
        if not corroborated and (not note or _NEEDS_HUMAN_REVIEW_SENTINEL not in note):
            # `not note or sentinel not in note` keeps this idempotent across
            # repeated real re-runs (required: this script must be
            # genuinely re-runnable on demand without its own notes growing
            # unboundedly each time) -- a prior run's own review note is
            # never appended a second time.
            note = (
                (note + " " if note else "")
                + f"[{_NEEDS_HUMAN_REVIEW_SENTINEL} -- real re-audit run ({_THIS_TASK_CITATION}) found "
                  f"fresh mechanical evidence that does not corroborate this existing record (fresh "
                  f"status={fresh['status']!r}, fresh all_umr_ids={fresh['all_umr_ids']!r}); the "
                  f"existing record was deliberately PRESERVED unchanged rather than silently "
                  f"overwritten, per the live-data over-aggressive full-text-match risk found and "
                  f"fixed this task -- see audit_raw_output for the full real fresh evidence, and "
                  f"have a real human confirm or correct this record before any further change.]"
            )

        plan = {
            "ocid_number": ocid_number,
            "canonical_umr_id": existing.get("canonical_umr_id"),
            "status": existing.get("status"),
            "all_umr_ids": existing.get("all_umr_ids"),
            "pr_number": existing.get("pr_number"),
            "pr_repo": existing.get("pr_repo"),
            "duplicate_reason": note,
            "not_found": bool(existing.get("not_found")),
        }
        preserved = True
    else:
        plan = {
            "ocid_number": ocid_number,
            "canonical_umr_id": fresh["canonical_umr_id"],
            "status": fresh["status"],
            "all_umr_ids": fresh["all_umr_ids"],
            "pr_number": fresh["pr_number"],
            "pr_repo": fresh["pr_repo"],
            "duplicate_reason": fresh.get("duplicate_reason"),
            "not_found": fresh["not_found"],
        }
        preserved = False
        corroborated = None

    plan["audit_raw_output"] = bounded_fresh_evidence
    plan["evidence"] = existing["evidence"] if existing is not None else bounded_fresh_evidence
    plan["preserved_existing_canonical_choice"] = preserved
    plan["fresh_evidence_corroborates_existing"] = corroborated
    plan["changed_from_existing"] = existing is None
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the re-audited rows; default is a dry run that only prints the plan")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)

    ocid_numbers = [args.ocid_number] if args.ocid_number else ALL_OCID_NUMBERS
    existing_rows = sbr.query_ocid_canonical_registry(conn)
    existing_by_ocid = {r["ocid_number"]: r for r in existing_rows}

    plans = []
    for ocid_number in ocid_numbers:
        plan = plan_for_ocid(sbr, conn, ocid_number, existing_by_ocid)
        plans.append(plan)
        print(f"  {ocid_number}: not_found={plan['not_found']} canonical_umr_id={plan['canonical_umr_id']} "
              f"preserved_existing={plan['preserved_existing_canonical_choice']} "
              f"fresh_corroborates={plan['fresh_evidence_corroborates_existing']}", file=sys.stderr)

    new_rows = [p for p in plans if p["changed_from_existing"]]
    needs_review = [p for p in plans if p["fresh_evidence_corroborates_existing"] is False]
    print(f"SUMMARY: {len(plans)} real OCIDs re-audited | {len(new_rows)} brand-new row(s) written in full | "
          f"{len(needs_review)} existing row(s) PRESERVED unchanged but flagged NEEDS HUMAN REVIEW "
          f"(fresh mechanical evidence did not corroborate the existing record -- never auto-applied, "
          f"see plan_for_ocid()'s own docstring)", file=sys.stderr)
    for p in needs_review:
        print(f"  NEEDS HUMAN REVIEW: {p['ocid_number']} -- existing canonical_umr_id={p['canonical_umr_id']!r} "
              f"preserved; fresh evidence disagreed", file=sys.stderr)

    if not args.apply:
        conn.close()
        print(json.dumps(plans, indent=2, default=str))
        print("DRY RUN -- pass --apply to actually write these rows", file=sys.stderr)
        return 0

    with sbr._write_lock():
        for p in plans:
            sbr.upsert_ocid_canonical_registry(
                conn, p["ocid_number"],
                canonical_umr_id=p["canonical_umr_id"], status=p["status"],
                all_umr_ids=p["all_umr_ids"], evidence=p["evidence"],
                pr_number=p["pr_number"], pr_repo=p["pr_repo"],
                duplicate_reason=p["duplicate_reason"], not_found=p["not_found"],
                audit_raw_output=p["audit_raw_output"],
            )
        conn.commit()
    conn.close()
    print(f"APPLIED: wrote {len(plans)} real re-audited rows to ocid_canonical_registry", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
