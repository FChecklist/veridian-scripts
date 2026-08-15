#!/usr/bin/env python3
"""task-20260815-145619 (UMR-20260815-052932-e80b). Governing chain:
UMR-20260814-034424-ded4 (scope-aware matching, itself addendum to
UMR-20260813-102459-10c3 / UMR-20260813-084321-2962 / P1
UMR-20260806-171945-5767).

REAL INCIDENT this covers (2026-08-15): task-20260815-135327-d6ad ("Reject
invalid complexity_tier constant in pm_lifecycle so minted tasks pass schema
validation" -- UMR-20260815-135327-d6ad, a real, genuine edit of
pm_lifecycle.py) was rejected three times by find_target_identifier_
duplicate() as a false-positive duplicate of the already-queued
UMR-20260815-044235-a5e1 ("PM-in-Server: add real Part3+4 GTM-cert
completion tracking to pm-sentinel-tick.sh"), which does not touch
pm_lifecycle.py at all.

Root cause, confirmed by reading a5e1's real stored prompt text (verbatim
below): it mentions "pm_lifecycle.py" exactly twice --

    "check whether a real pm_lifecycle.py orchestrator run targeting these
    gaps is already in flight"
    "dispatch exactly one real pm_lifecycle.py run via this file's own
    dispatch_gap() ... mechanism"

-- both naming pm_lifecycle.py as something a5e1's own real target
(pm-sentinel-tick.sh) dispatches/runs, never as something a5e1 edits. a5e1's
prompt declares no TARGET:/SCOPE: section (this false-positive class was not
yet known when it was written), so the UMR-20260814-034424-ded4 scope-aware
restriction never engaged on its side: the whole text was scanned in
fallback mode and both "orchestrator run"/"run" citations were extracted
exactly like real edit targets, producing a false script:pm_lifecycle.py
overlap against d6ad's genuine, unrelated edit target -- the real,
one-directional gap this task fixes (today's scope-aware restriction only
ever narrowed the NEW dispatch's own identifiers via its own declared
TARGET:/SCOPE: section; a historical stored row that predates that
convention -- essentially every row in the table -- got no equivalent
narrowing at all).

Covers:
  1. The exact real-incident fixtures (d6ad, a5e1 -- verbatim real
     title/prompt text pulled from superboss-register.sqlite's umr_tasks
     rows) as pure-function extraction checks and as a real
     find_target_identifier_duplicate() call: proves the false positive is
     gone.
  2. The general TESTING-spec regression shape: a new dispatch with an
     explicit TARGET: section naming file A, whose EVIDENCE: aside mentions
     file B only via an invocation citation ("B orchestrator run"); a
     second, already-stored dispatch with NO TARGET: section at all that
     genuinely edits file B, mentioning file A only via the same invocation-
     citation shape -- must not collide.
  3. Real protection kept intact: two dispatches that both genuinely name
     the same file as their real edit target (no invocation-citation
     phrasing) still collide, both in unstructured fallback-mode text and
     inside explicit TARGET:/SCOPE: sections on both sides -- and a name
     that appears BOTH as a genuine edit mention and as an invocation
     citation elsewhere in the same text is still extracted (only the
     citation occurrence itself is excluded).
  4. Audit requirement: umr:/pr: extraction is provably unaffected by this
     new mechanism (an "orchestrator run" trailing a UMR id or a PR number
     still extracts normally).
"""
import importlib.util
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_invocation_citation_dedup_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_full_schema(path):
    sbr = _load_sbr()
    sbr.DB_PATH = path
    sbr.init_db()
    return sbr


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_full_schema(path)
        yield path


def _insert_row(path, umr_id, *, status, title, prompt, repo="veridian-scripts",
                 hours_ago=0.0, tier=2, task_identity=None):
    conn = sqlite3.connect(path)
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (umr_id, task_identity or (umr_id + "-identity"), ts, tier, status,
         "owner_dispatch_gateway", "veridian_task_create",
         json.dumps({"title": title, "prompt": prompt, "repo": repo}), "{}", "{}"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Real-incident fixtures: verbatim title/prompt text from
# superboss-register.sqlite's real umr_tasks rows for UMR-20260815-135327-d6ad
# and UMR-20260815-044235-a5e1.
# ---------------------------------------------------------------------------

REAL_D6AD_TITLE = (
    "Reject invalid complexity_tier constant in pm_lifecycle so minted "
    "tasks pass schema validation"
)
REAL_D6AD_PROMPT = (
    "ROOT CAUSE, already diagnosed with real evidence, do NOT re-discover "
    "it: /opt/veridian/scripts/pm_lifecycle.py sets complexity_tier to the "
    "string moderate in four places -- line 222 (function default), line "
    "539, line 575, and line 960 (the argparse --complexity-tier default). "
    "/opt/veridian/scripts/plan_generator.py line 56 defines VALID_TIERS as "
    "the list mechanical, integrative, judgment. The value moderate is not "
    "a member, so schema validation hard-rejects every task minted through "
    "pm_lifecycle.py with reason_code tight_task_schema_violation.\n\n"
    "SCOPE, exactly this and nothing more:\n"
    "1. In pm_lifecycle.py replace all four invalid complexity_tier values "
    "with judgment, a real member of VALID_TIERS.\n"
    "2. Import VALID_TIERS from plan_generator and add fail-fast validation."
)

REAL_A5E1_TITLE = (
    "PM-in-Server: add real Part3+4 GTM-cert completion tracking to "
    "pm-sentinel-tick.sh (min tokens, real audit, real cert)"
)
REAL_A5E1_PROMPT = (
    "REAL TASK: add one new deterministic check block to "
    "pm-sentinel-tick.sh, following the exact existing pattern already in "
    "the file.\n\n"
    "The new check must, each tick:\n"
    "1. Query gtm_certification_categories for rows where passed=0 or "
    "passed IS NULL.\n"
    "2. Before dispatching anything: check whether a real pm_lifecycle.py "
    "orchestrator run targeting these gaps is already in flight (query "
    "resource_governor.py --query-umr for any queued/running row).\n"
    "3. If zero rows currently in flight AND real gap rows > 0: dispatch "
    "exactly one real pm_lifecycle.py run via this file's own "
    "dispatch_gap()/DISPATCH_OWNER_TASK_SH mechanism."
)


def test_real_incident_a5e1_does_not_extract_pm_lifecycle_as_a_target():
    """a5e1's own real prompt mentions pm_lifecycle.py twice, both as an
    "orchestrator run"/"run" citation of something it dispatches -- neither
    occurrence names a5e1's own real edit target."""
    sbr = _load_sbr()
    ids = (set(sbr.extract_target_identifiers(REAL_A5E1_TITLE))
           | set(sbr.extract_target_identifiers(REAL_A5E1_PROMPT)))
    assert "script:pm_lifecycle.py" not in ids
    assert "script:pm-sentinel-tick.sh" in ids


def test_real_incident_baseline_pm_lifecycle_mention_really_is_in_the_raw_text():
    """Sanity check on the reconstruction: prove "pm_lifecycle.py" really
    is present in a5e1's raw prompt (so the false-positive this fixes was a
    real collision, not a strawman), and that the bare script-name regex
    really would match it directly (the higher-level function is what now
    excludes the citation occurrence, not an absence of the substring)."""
    sbr = _load_sbr()
    assert "pm_lifecycle.py" in REAL_A5E1_PROMPT
    assert sbr._TARGET_ID_SCRIPT_NAME_RE.search(REAL_A5E1_PROMPT)


def test_real_incident_d6ad_vs_a5e1_no_longer_false_positive_duplicate(scratch_db):
    """The exact real incident, end to end: d6ad's real dispatch attempt
    must no longer be refused as a duplicate of the already-queued a5e1."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-20260815-044235-a5e1", status="running",
        title=REAL_A5E1_TITLE, prompt=REAL_A5E1_PROMPT,
        repo="veridian-scripts", hours_ago=1.2,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, REAL_D6AD_TITLE, REAL_D6AD_PROMPT,
        repo="claude-control", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None, (
        "d6ad genuinely edits pm_lifecycle.py; a5e1 only cites "
        "pm_lifecycle.py as something it dispatches an orchestrator run "
        "of -- this must not be treated as a real duplicate")


# ---------------------------------------------------------------------------
# General TESTING-spec regression shape: TARGET: section on the new
# dispatch naming file A, EVIDENCE: aside citing file B only via an
# invocation citation; already-stored row with no TARGET: section at all,
# genuinely editing file B, citing file A only via the same invocation-
# citation shape.
# ---------------------------------------------------------------------------

def test_false_positive_new_dispatch_target_section_vs_unstructured_stored_row(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    # Already-stored dispatch: no TARGET:/SCOPE: section at all (predates
    # the convention), genuinely edits pm_lifecycle.py, and separately
    # mentions pm-sentinel-tick.sh only as something it triggers a run of.
    _insert_row(
        scratch_db, "UMR-TEST-e80b-old-row", status="running",
        title="Fix pm_lifecycle.py complexity_tier constant",
        prompt=(
            "pm_lifecycle.py sets complexity_tier to an invalid value in "
            "several places; correct it so minted tasks pass schema "
            "validation. Once landed, confirm the fix is visible on the "
            "next pm-sentinel-tick.sh orchestrator run -- this task does "
            "not modify that script."
        ),
        repo="veridian-scripts", hours_ago=0.5,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    # New dispatch: explicit TARGET: section naming pm-sentinel-tick.sh;
    # an EVIDENCE: aside cites pm_lifecycle.py only via the same
    # invocation-citation shape.
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Add GTM-cert completion check to pm-sentinel-tick.sh",
        (
            "TARGET: pm-sentinel-tick.sh -- add a new deterministic check "
            "block following the file's existing pattern.\n\n"
            "EVIDENCE: the new check will dispatch a pm_lifecycle.py "
            "orchestrator run when real gaps remain; this task does not "
            "edit pm_lifecycle.py itself."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None, (
        "neither side's pm_lifecycle.py/pm-sentinel-tick.sh mention is a "
        "real edit target on the other side -- both are invocation "
        "citations of a script the OTHER dispatch genuinely edits")


# ---------------------------------------------------------------------------
# Independent-audit regression (pre-merge review of this same UMR): the
# first version of _TARGET_ID_INVOCATION_CITATION_TRAILING_RE matched a
# BARE trailing "run" with no "orchestrator"/"via" qualifier, which fired
# on ordinary bug-report phrasing ("worker.py run() function", "X.py runs
# out of memory") and wrongly dropped a real target identifier -- since
# find_target_identifier_duplicate() returns None outright when my_ids is
# empty, this could have silently disabled the whole guard. It also fired
# unconditionally, even inside a text's own declared TARGET: section,
# overriding what should be an authoritative, exhaustive declaration.
# Narrowed to require "orchestrator" or an invocation preposition
# (via/using/through/by) after "run" -- both verbatim in the real a5e1
# incident text -- and restricted to fallback (no declared section)
# scanning only.
# ---------------------------------------------------------------------------

def test_bare_trailing_run_as_ordinary_prose_is_not_a_citation():
    """"run() function" / "run out of memory" / "run configuration" are
    ordinary bug-report phrasing, not invocation citations -- the
    identifier must still be extracted."""
    sbr = _load_sbr()
    assert sbr.extract_target_identifiers(
        "Fix the crash in worker.py run() function") == ["script:worker.py"]
    assert sbr.extract_target_identifiers(
        "edit scripts/deploy.py run configuration block") == [
        "path:scripts/deploy.py"]
    assert sbr.extract_target_identifiers(
        "fix pm_lifecycle.py run out of memory bug") == ["script:pm_lifecycle.py"]


def test_invocation_citation_exclusion_never_overrides_a_declared_target_section():
    """A bare trailing "run" inside a dispatcher's own explicit TARGET:
    section must never be excluded -- that section is this function's own
    exhaustive target declaration and must not be silently emptied by this
    heuristic (an empty my_ids makes find_target_identifier_duplicate()
    return None immediately, silently disabling the whole guard)."""
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "TARGET: pm_lifecycle.py run out of memory bug -- add streaming to "
        "fix it.")
    assert ids == ["script:pm_lifecycle.py"]


def test_orchestrator_run_still_excluded_even_though_bare_run_is_not():
    """Companion check: the real incident's own "orchestrator run"/"run
    via" phrasing is still excluded by the narrowed regex -- this is not a
    revert, only a narrowing of the trigger condition."""
    sbr = _load_sbr()
    assert sbr.extract_target_identifiers(
        "check whether a real pm_lifecycle.py orchestrator run targeting "
        "these gaps is already in flight") == []
    assert sbr.extract_target_identifiers(
        "dispatch exactly one real pm_lifecycle.py run via this file's "
        "own dispatch_gap() mechanism") == []


def test_unevidenced_run_prepositions_are_not_citations():
    """Second independent-audit finding, same UMR: a prior revision of
    this fix added "using"/"through"/"by" as siblings of "via" by analogy,
    with no real-incident evidence for any of the three -- and each fires
    on ordinary bug-report prose that is not an invocation citation at
    all. Only "via" is evidenced by the real a5e1 incident text; the
    identifier must still be extracted for these three."""
    sbr = _load_sbr()
    assert sbr.extract_target_identifiers(
        "worker.py run through 10 iterations before failing with a "
        "segfault -- add a real guard.") == ["script:worker.py"]
    assert sbr.extract_target_identifiers(
        "Noticed backup_rotate.sh run by cron every night leaves a stale "
        "lockfile behind. Please fix.") == ["script:backup_rotate.sh"]
    assert sbr.extract_target_identifiers(
        "pm_lifecycle.py run using the staging config accidentally wrote "
        "to the prod table. Add a real guard.") == ["script:pm_lifecycle.py"]


# ---------------------------------------------------------------------------
# Real protection kept intact: genuine same-target duplicates still refused.
# ---------------------------------------------------------------------------

def test_true_duplicate_unstructured_text_no_citation_phrasing_still_refused(scratch_db):
    """Both dispatches genuinely name the same file as their real edit
    target, in unstructured fallback-mode text with no invocation-citation
    phrasing anywhere -- must still collide exactly as before this fix."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-TEST-e80b-genuine-old", status="running",
        title="Fix a bug in pm_lifecycle.py",
        prompt="pm_lifecycle.py has an invalid complexity_tier constant; fix it.",
        repo="veridian-scripts", hours_ago=0.3,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, "Second attempt: fix pm_lifecycle.py",
        "Land the real fix for the pm_lifecycle.py constant, different approach.",
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-e80b-genuine-old"


def test_true_duplicate_both_sides_target_sections_same_file_still_refused(scratch_db):
    """Both dispatches declare explicit TARGET:/SCOPE: sections naming the
    same real edit target -- must still collide."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-TEST-e80b-genuine-scoped", status="running",
        title="Fix complexity_tier in pm_lifecycle.py",
        prompt=(
            "TARGET: pm_lifecycle.py -- correct the invalid complexity_tier "
            "constant so minted tasks pass schema validation.\n\n"
            "EVIDENCE: schema validation hard-rejects tasks with reason_code "
            "tight_task_schema_violation."
        ),
        repo="veridian-scripts", hours_ago=0.2,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Second attempt: fix complexity_tier constant",
        (
            "SCOPE: pm_lifecycle.py -- same real fix, redispatched after a "
            "suspected stall.\n\n"
            "EVIDENCE: no new diagnostic run, relying on the original."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-e80b-genuine-scoped"


def test_name_still_extracted_from_a_genuine_mention_elsewhere_in_same_text():
    """A name that appears BOTH as a genuine edit mention and as an
    invocation citation in the same text is still extracted -- only the
    specific citation occurrence is excluded, not the identifier as a
    whole (this mirrors _TARGET_ID_SCRIPT_NAME_BOILERPLATE_EXCLUDED's own
    "narrow, per-occurrence" precedent, but per-match rather than
    per-name)."""
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "Fix a real bug in pm_lifecycle.py's validate_tier function. "
        "Confirm the fix on the next pm_lifecycle.py orchestrator run."
    )
    assert "script:pm_lifecycle.py" in ids


def test_bare_run_word_preceding_a_name_is_unaffected():
    """The existing, already-covered "fix X and re-run Y" shape (the run
    word PRECEDES the name, not trailing it) must be extracted exactly as
    before -- this fix is narrowly trailing-only."""
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "fix scripts/resource_governor.py and re-run pm-sentinel-tick.sh")
    assert "path:scripts/resource_governor.py" in ids
    assert "script:pm-sentinel-tick.sh" in ids


# ---------------------------------------------------------------------------
# Audit requirement: umr:/pr: extraction is unaffected by this new
# mechanism -- it has its own separate real-incident history and must keep
# working exactly as before.
# ---------------------------------------------------------------------------

def test_umr_id_extraction_unaffected_by_trailing_run_word():
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "RCA: the UMR-20260807-151622-15cd orchestrator run failed -- "
        "read the real reason first")
    assert "umr:UMR-20260807-151622-15cd" in ids


def test_pr_id_extraction_unaffected_by_trailing_run_word():
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "the veridian-scripts#345 CI run failed, please investigate")
    assert "pr:veridian-scripts#345" in ids
    ids2 = sbr.extract_target_identifiers(
        "PR #345 orchestrator run failed", default_repo="veridian-scripts")
    assert "pr:veridian-scripts#345" in ids2
