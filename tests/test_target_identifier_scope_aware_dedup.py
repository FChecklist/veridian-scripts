#!/usr/bin/env python3
"""UMR-20260814-034424-ded4 (task-20260814-034453-duplicate-guard-false-
refuses-legitimate). Governing chain: P1 UMR-20260806-171945-5767.

REAL INCIDENT this covers: first-hand reproduction by the PM Sentinel,
2026-08-14T03:38-03:42Z UTC, three consecutive real refusals of legitimate,
non-duplicate P0 dispatches -- all against the same already-running
duplicate_umr_id (a disk-fix task). extract_target_identifiers() scanned the
whole title+prompt as one undifferentiated blob, with no way to tell an
identifier that names the dispatch's real work TARGET from one merely CITED
AS EVIDENCE or explicitly marked OUT OF SCOPE / PRIOR CONTEXT:

  1. a live-checkout-drift dispatch refused because its prompt cited the
     disk-fix UMR id purely to say the disk cause was already owned
     elsewhere;
  2. the same dispatch, with that UMR id removed, refused AGAIN -- this
     time on the shared worker task-directory name both prompts referenced
     only as the location of evidence files;
  3. an invocation-accounting dispatch refused because it named the worker
     entrypoint script, which the disk-fix prompt had mentioned only as the
     file a line number of evidence lived in (the disk task never modifies
     that script).

Each case below is reconstructed from the real prompt text described in the
incident (see this task's own progress/ file and the SPEC that dispatched
it) using the new scope-aware mechanisms this fix adds:
  - scenario 1 exercises the fallback PRIOR CONTEXT: exclusion (no explicit
    TARGET: section -- the "at minimum" bar).
  - scenario 2 exercises the explicit TARGET:/SCOPE: section restriction.
  - scenario 3 exercises the inline `[EVIDENCE-ONLY: ...]` escape hatch.

test_true_duplicate_still_refused proves none of this weakens the guard's
real protection: two dispatches that both genuinely target the same PR are
still refused, both in the original unstructured-prompt shape (the real
2026-08-13 incident this guard exists for) and in the new explicit
TARGET:/SCOPE:-section shape.
"""
import importlib.util
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_scope_aware_dedup_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
    import json
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


# The real duplicate_umr_id from all three refusals in the incident.
DISK_FIX_UMR_ID = "UMR-20260814-033442-c885"


# ---------------------------------------------------------------------------
# Scenario 1: disk-fix UMR id cited only as PRIOR CONTEXT (fallback
# exclusion, no explicit TARGET: section on either side).
# ---------------------------------------------------------------------------

def test_scenario1_umr_cited_as_prior_context_now_allowed(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, DISK_FIX_UMR_ID, status="running",
        title="Fix root disk full condition on worker hosts",
        prompt=(
            f"This task's own UMR id is {DISK_FIX_UMR_ID}. Root filesystem hit "
            "100% on several worker hosts; reclaim space and add retention so it "
            "does not recur."
        ),
        repo="veridian-scripts", hours_ago=0.3,
    )

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    # Reconstructed real prompt: cites the disk-fix UMR purely to say its
    # cause is owned elsewhere -- explicitly labeled PRIOR CONTEXT:, no
    # TARGET:/SCOPE: header at all (the "at minimum" fallback path).
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Reconcile live-checkout git drift across worker task directories",
        (
            "Several running worker task directories have a checked-out commit "
            "that no longer matches what was dispatched; reconcile the checkout "
            "so it matches the real dispatched ref again.\n\n"
            "PRIOR CONTEXT: the root-disk-full condition that caused some of "
            f"these checkouts to fail mid-clone is already owned by {DISK_FIX_UMR_ID} "
            "(disk-fix), not by this task -- that disk cause is out of scope "
            "here, this task only reconciles the resulting checkout drift."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None, (
        "citing the disk-fix UMR id inside an explicit PRIOR CONTEXT: section "
        "must not be treated as this dispatch's own target")


def test_scenario1_without_the_fix_would_have_been_refused_baseline():
    """Sanity check on the reconstruction itself: prove the disk-fix UMR id
    really is present in the raw prompt text (so this "false positive"
    reconstruction is a real collision on the old full-text-scan behavior,
    not a strawman), and that a plain scan with no PRIOR CONTEXT: label at
    all (the pre-fix shape) really does extract it as a target identifier."""
    raw_prompt = (
        "the root-disk-full condition that caused some of these checkouts "
        f"to fail mid-clone is already owned by {DISK_FIX_UMR_ID} (disk-fix), "
        "not by this task."
    )
    sbr = _load_sbr()
    assert f"umr:{DISK_FIX_UMR_ID}" in sbr.extract_target_identifiers(raw_prompt)


# ---------------------------------------------------------------------------
# Scenario 2: shared worker task-directory evidence-file path, cited outside
# an explicit TARGET: section (section-restriction path).
# ---------------------------------------------------------------------------

SHARED_EVIDENCE_PATH = (
    "ai-os/tasks/task-20260814-020115-disk-full-worker-hosts/workspace/"
    "evidence/df_before.txt"
)


def test_scenario2_shared_evidence_path_outside_target_section_now_allowed(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, DISK_FIX_UMR_ID, status="running",
        title="Fix root disk full condition on worker hosts",
        prompt=(
            f"This task's own UMR id is {DISK_FIX_UMR_ID}. See "
            f"{SHARED_EVIDENCE_PATH} for the `df -h` snapshot taken before "
            "remediation."
        ),
        repo="veridian-scripts", hours_ago=0.3,
    )

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    # Reconstructed real prompt: declares its real target explicitly via
    # TARGET:, and cites the shared evidence-file path only in a separate
    # EVIDENCE: section -- the path must not count even though it is the
    # exact same literal string the disk-fix row's prompt also contains.
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Reconcile live-checkout git drift across worker task directories",
        (
            "TARGET: reconcile live-checkout git drift -- the on-disk checkout "
            "no longer matches the dispatched commit for several running "
            "tasks; re-fetch and hard-reset each affected worker checkout to "
            "its real dispatched ref.\n\n"
            "EVIDENCE: the drift was first observed while investigating the "
            f"disk-full incident; {SHARED_EVIDENCE_PATH} shows the disk was "
            "already at 100% when several of these checkouts silently "
            "truncated mid-clone, which is what led to noticing the drift."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None, (
        "a shared evidence-file path cited outside the declared TARGET: "
        "section must not be treated as this dispatch's own target")


# ---------------------------------------------------------------------------
# Scenario 3: worker entrypoint script cited only for a line number of
# evidence (inline `[EVIDENCE-ONLY: ...]` escape hatch, on the STORED row's
# side -- the new dispatch's own target legitimately includes that script).
# ---------------------------------------------------------------------------

def test_scenario3_script_cited_only_for_a_line_number_now_allowed(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, DISK_FIX_UMR_ID, status="running",
        title="Fix root disk full condition on worker hosts",
        prompt=(
            f"This task's own UMR id is {DISK_FIX_UMR_ID}. Root filesystem hit "
            "100% on several worker hosts; reclaim space and add retention. "
            "For context on how the disk usage was captured during dispatch, "
            "see [EVIDENCE-ONLY: dispatch-owner-task.sh line 87 logs `du -sh` "
            "output inline] -- this task does not modify that script."
        ),
        repo="veridian-scripts", hours_ago=0.3,
    )

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    # The invocation-accounting dispatch's own real target legitimately IS
    # dispatch-owner-task.sh (it adds accounting to that script) -- this is
    # not excluded on the new dispatch's side, only on the stored row's side
    # (which marked its own citation as evidence-only).
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Add invocation accounting to dispatch-owner-task.sh",
        "TARGET: dispatch-owner-task.sh -- record a counter row for every "
        "real invocation of this script so double-dispatch volume is "
        "measurable.",
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None, (
        "a script name the stored row marked `[EVIDENCE-ONLY: ...]` must not "
        "collide with a genuinely new dispatch that really does target that "
        "script")


def test_scenario3_escape_hatch_is_stripped_as_a_pure_function():
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "See [EVIDENCE-ONLY: dispatch-owner-task.sh line 87] for the du -sh "
        "output.")
    assert ids == []


# ---------------------------------------------------------------------------
# Real protection kept intact: genuine same-target duplicates still refused.
# ---------------------------------------------------------------------------

def test_true_duplicate_still_refused_unstructured_prompt(scratch_db):
    """The exact real 2026-08-13 incident shape this guard exists for
    (two same-PR dispatches, different wording, no TARGET:/SCOPE:
    structure at all) must still be caught by the fallback full-text scan."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-TEST-a248", status="running",
        title="Desktop sentinel: RCA for PR #131",
        prompt="Real audit of PR #131 -- confirm CI status and review comments.",
        repo="veridian-scripts", hours_ago=0.1,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, "Desktop session: land fix for PR #131",
        "Desktop session: please land the real fix that resolves PR #131 now.",
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-a248"


def test_true_duplicate_still_refused_with_explicit_target_sections(scratch_db):
    """Same real-target collision, but this time BOTH dispatches use the new
    explicit TARGET:/SCOPE: structure -- the scope-restriction mechanism
    must still let a genuine shared target through as a real match, not
    accidentally hide it."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, DISK_FIX_UMR_ID, status="running",
        title="Fix root disk full condition on worker hosts",
        prompt=(
            "TARGET: land veridian-scripts#345, the real disk-retention fix "
            "for root filesystem 100% full on worker hosts.\n\n"
            "EVIDENCE: df -h showed 100% used on 3 hosts at 2026-08-14T03:10Z."
        ),
        repo="veridian-scripts", hours_ago=0.2,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Second attempt: fix worker disk full condition",
        (
            "SCOPE: land the same veridian-scripts#345 disk-retention fix, "
            "this is a redispatch after a suspected stall.\n\n"
            "EVIDENCE: no new df -h snapshot taken, relying on the original."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None, (
        "two dispatches that both genuinely target the same real work "
        "(here, sharing the same PR number inside their own TARGET:/SCOPE: "
        "sections) must still be refused as a real duplicate")
    assert dup["umr_id"] == DISK_FIX_UMR_ID


def test_title_always_counts_even_when_prompt_has_a_target_section_elsewhere(scratch_db):
    """The title is always the field that declares the target -- it must
    never be silently dropped just because the prompt happens to declare
    its own TARGET: section (which, in scoped mode, drops all other prose,
    including what would otherwise be the pre-header prompt text -- but
    never the title, since title and prompt are extracted independently)."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-TEST-title-only", status="running",
        title="Fix PR #777 merge conflict", prompt="TARGET: land the real fix.",
        repo="veridian-scripts", hours_ago=0.1,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, "Re-fix PR #777 merge conflict",
        "TARGET: same real conflict, different approach this time.",
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-title-only"


# ---------------------------------------------------------------------------
# Independent-audit regression: an unclosed fallback-mode excluded-section
# label (no TARGET:/SCOPE: header anywhere, no further recognized header
# after it either) must not swallow real target-naming prose that follows
# it -- only the immediately-cited paragraph is really "the citation".
# ---------------------------------------------------------------------------

def test_unclosed_excluded_section_does_not_swallow_trailing_real_target(scratch_db):
    """Real bug found on independent audit of PR #350: `PRIOR CONTEXT:`
    (etc.) with no closing header gave `_split_labeled_sections()` an
    unbounded span running to end-of-text, so a genuine target named in
    ordinary prose *after* the cited-as-evidence paragraph -- separated
    only by a blank line, not a new header -- was silently dropped,
    letting a real duplicate dispatch through undetected."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(
        scratch_db, "UMR-TEST-unclosed-prior-context", status="running",
        title="Rebase and land veridian-scripts#500",
        prompt="Resolving the merge conflict in superboss-register.py.",
        repo="veridian-scripts", hours_ago=0.1,
    )
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn,
        "Second dispatch, different title",
        (
            "PRIOR CONTEXT: the flaky CI runner issue for "
            "UMR-20260101-000000-feed is already known and unrelated to "
            "this dispatch.\n\n"
            "Now, the actual work: rebase and land veridian-scripts#500, "
            "resolving the merge conflict in superboss-register.py."
        ),
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None, (
        "PR #500, named in real prose after an unclosed PRIOR CONTEXT: "
        "paragraph break, must still be extracted as this dispatch's own "
        "target and match the real prior duplicate")
    assert dup["umr_id"] == "UMR-TEST-unclosed-prior-context"


def test_unclosed_excluded_section_still_excludes_the_cited_paragraph_itself():
    """Companion pure-function check: the excluded citation itself (before
    the blank line) must still be excluded -- this is a scope fix, not a
    removal of the exclusion mechanism."""
    sbr = _load_sbr()
    prompt = (
        "PRIOR CONTEXT: the flaky CI runner issue for "
        "UMR-20260101-000000-feed is already known and unrelated to this "
        "dispatch.\n\n"
        "Now, the actual work: rebase and land veridian-scripts#500, "
        "resolving the merge conflict in superboss-register.py."
    )
    ids = sbr.extract_target_identifiers(prompt, default_repo="veridian-scripts")
    assert "pr:veridian-scripts#500" in ids
    assert "umr:UMR-20260101-000000-feed" not in ids
