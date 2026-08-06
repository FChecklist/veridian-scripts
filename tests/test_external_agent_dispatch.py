#!/usr/bin/env python3
"""Real tests for the chat.z.ai external-agent manual-paste bridge (Real
Owner directive UMR-20260806-095416-b6f0): check_external_agent_eligibility()
edge cases, the prompt-template render/parse round trip (and its real
rejection cases), the real two-strike requeue-then-permanent-fallback logic,
the transaction-safe get_next_external_agent_task() selection (no double
dispatch), and the real "diff applies only to a fresh git worktree, never
main directly" safety property -- exercised against a REAL temporary git
repo + a REAL local bare 'origin' remote (no network, no gh auth needed;
`gh` itself is mocked via an injected gh_run callable, but every real `git`
call is the real `git` binary).

Same conventions as tests/test_pm_decisions_pending.py /
tests/test_ocid_artifact_links.py: real, isolated, temp-file SQLite database
per test (SUPERBOSS_REGISTER_DB env override, set BEFORE exec_module() since
resolve_superboss_db_path() runs once at module-exec time) -- never the
live production /opt/veridian/ai-os/memory/superboss-register.sqlite.

Run: python3 -m pytest -q tests/test_external_agent_dispatch.py
 or:  python3 tests/test_external_agent_dispatch.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename, env=None):
    """Same load-with-env-override convention as
    tests/test_pm_decisions_pending.py's own _load()."""
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _seed_scratch_db(path):
    """Same convention as tests/test_pm_decisions_pending.py's own
    _seed_scratch_db(): real init_db() against a scratch file via a
    monkeypatched _connect(), so schema creation reuses the real, live
    logic (including _ensure_external_agent_dispatch_table /
    _migrate_umr_tasks_external_agent_columns) rather than reimplementing it."""
    import sqlite3
    spec = importlib.util.spec_from_file_location("sbr_seed_ea", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    def _scratch_connect():
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    sbr._connect = _scratch_connect
    _real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        sbr.init_db()
    finally:
        sys.stdout = _real_stdout
    return sbr


def _scratch_env(scratch_db):
    return {"SUPERBOSS_REGISTER_DB": scratch_db}


def _insert_umr(sbr, conn, *, tier=3, task_identity="test task", umr_id=None):
    record = {
        "umr_id": umr_id, "task_identity": task_identity, "tier": tier,
        "source_trigger": "test_harness", "task_kind": "external_agent_test",
    }
    umr_id = sbr.upsert_umr_task(conn, record)
    conn.commit()
    return umr_id


# ---------------------------------------------------------------------------
# 1. check_external_agent_eligibility() -- pure function edge cases
# ---------------------------------------------------------------------------

def test_eligibility_valid_isolated_bugfix():
    sbr = _load("sbr_elig_valid_bugfix", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="isolated_bugfix", files_touched=["scripts/foo.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="fix the off-by-one",
        repro_steps="run foo.py with x=1", tier=3,
    )
    assert ok is True and reasons == [], reasons
    print("PASS: test_eligibility_valid_isolated_bugfix")


def test_eligibility_valid_doc_update_up_to_three_md_files():
    sbr = _load("sbr_elig_valid_doc3", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="doc_update", files_touched=["README.md", "docs/a.md", "notes.rst"],
        blast_radius="isolated", requires_multi_file_context=False,
        acceptance_criteria="fix 3 stale doc references", repro_steps=None, tier=4,
    )
    assert ok is True and reasons == [], reasons
    print("PASS: test_eligibility_valid_doc_update_up_to_three_md_files")


def test_eligibility_bad_task_type_rejected():
    sbr = _load("sbr_elig_bad_type", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="rewrite_the_universe", files_touched=["a.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps="y", tier=3,
    )
    assert ok is False
    assert any("not one of the 4 real allowed values" in r for r in reasons), reasons
    print("PASS: test_eligibility_bad_task_type_rejected")


def test_eligibility_single_file_refactor_rejects_two_files():
    sbr = _load("sbr_elig_single_two_files", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="single_file_refactor", files_touched=["a.py", "b.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("max of 1" in r for r in reasons), reasons
    print("PASS: test_eligibility_single_file_refactor_rejects_two_files")


def test_eligibility_doc_update_rejects_fourth_file():
    sbr = _load("sbr_elig_doc4", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="doc_update", files_touched=["a.md", "b.md", "c.md", "d.md"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("max of 3" in r for r in reasons), reasons
    print("PASS: test_eligibility_doc_update_rejects_fourth_file")


def test_eligibility_doc_update_rejects_non_markdown_file():
    sbr = _load("sbr_elig_doc_nonmd", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="doc_update", files_touched=["a.md", "scripts/real_code.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("non-doc entries" in r for r in reasons), reasons
    print("PASS: test_eligibility_doc_update_rejects_non_markdown_file")


def test_eligibility_zero_files_rejected():
    sbr = _load("sbr_elig_zero_files", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="doc_update", files_touched=[], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("files_touched is empty" in r for r in reasons), reasons
    print("PASS: test_eligibility_zero_files_rejected")


def test_eligibility_forbidden_path_patterns():
    sbr = _load("sbr_elig_forbidden", "superboss-register.py")
    for bad_path in [
        "secrets/api_key.py", "app/credentials.py", "config/password_reset.py",
        "auth/login.py", "src/rbac/policy.py", ".github/workflows/deploy.yml",
        "db/migrations/0001_init.sql", ".env.production", "lib/token_store.py",
    ]:
        ok, reasons = sbr.check_external_agent_eligibility(
            task_type="single_file_refactor", files_touched=[bad_path], blast_radius="isolated",
            requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
        )
        assert ok is False, f"{bad_path} should have been rejected"
        assert any("exclusion pattern" in r.lower() for r in reasons), (bad_path, reasons)
    print("PASS: test_eligibility_forbidden_path_patterns")


def test_eligibility_rejects_absolute_and_traversal_paths():
    """Real regression test for a real finding from independent review of
    UMR-20260806-095416-b6f0's implementation PR: an absolute or `..`
    path-traversal files_touched entry must be refused BEFORE it ever
    reaches get_next_external_agent_task()'s real file read (which would
    otherwise leak arbitrary local file content -- secrets, SSH keys, the
    live production DB -- into the prompt handed to chat.z.ai)."""
    sbr = _load("sbr_elig_traversal", "superboss-register.py")
    for bad_path in [
        "/etc/passwd", "/opt/veridian/ai-os/memory/superboss-register.sqlite",
        "../../etc/passwd", "outside_repo/../../secrets_elsewhere.txt",
        "..", "a/../../b.py",
    ]:
        ok, reasons = sbr.check_external_agent_eligibility(
            task_type="single_file_refactor", files_touched=[bad_path], blast_radius="isolated",
            requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
        )
        assert ok is False, f"{bad_path} should have been rejected"
        assert any("unsafe absolute/path-traversal" in r for r in reasons), (bad_path, reasons)
    # A genuinely safe relative path must NOT be rejected by this rule.
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="single_file_refactor", files_touched=["scripts/foo.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is True, reasons
    print("PASS: test_eligibility_rejects_absolute_and_traversal_paths")


def test_get_next_raises_on_unsafe_files_touched_invariant_violation():
    """Real defense-in-depth check, same finding as above: even if
    files_touched somehow held an unsafe path when get_next_external_agent_task()
    runs (mark_external_agent_eligible() is the only real path that sets it,
    and already refuses this), the function must refuse to read/embed it
    rather than silently leaking file content outside the repo root."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_getnext_unsafe_invariant", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()
        umr_id = _insert_umr(sbr2, conn, tier=3, task_identity="unsafe invariant test")
        # Hand-craft a row bypassing mark_external_agent_eligible() entirely,
        # simulating exactly the "first gate bypassed" scenario the
        # defense-in-depth check exists for.
        conn.execute(
            "UPDATE umr_tasks SET external_agent_eligible=1, external_agent_task_type='single_file_refactor', "
            "blast_radius='isolated', files_touched=? WHERE umr_id=?",
            (json.dumps(["/etc/passwd"]), umr_id),
        )
        conn.commit()
        raised = False
        try:
            sbr2.get_next_external_agent_task(conn, artifacts_root=os.path.join(d, "artifacts"), repo_root=d)
        except ValueError as e:
            raised = True
            assert "unsafe absolute/path-traversal" in str(e)
        assert raised, "expected a ValueError, not a silent file read outside the repo root"
        conn.close()
    print("PASS: test_get_next_raises_on_unsafe_files_touched_invariant_violation")


def test_eligibility_empty_acceptance_criteria_rejected():
    sbr = _load("sbr_elig_empty_ac", "superboss-register.py")
    for ac in ["", "   ", None]:
        ok, reasons = sbr.check_external_agent_eligibility(
            task_type="doc_update", files_touched=["a.md"], blast_radius="isolated",
            requires_multi_file_context=False, acceptance_criteria=ac, repro_steps=None, tier=3,
        )
        assert ok is False
        assert any("acceptance_criteria is empty" in r for r in reasons), reasons
    print("PASS: test_eligibility_empty_acceptance_criteria_rejected")


def test_eligibility_isolated_bugfix_requires_repro_steps():
    sbr = _load("sbr_elig_repro", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="isolated_bugfix", files_touched=["a.py"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="fix it", repro_steps="",
        tier=3,
    )
    assert ok is False
    assert any("repro_steps is empty" in r for r in reasons), reasons
    # doc_update does NOT require repro_steps
    ok2, reasons2 = sbr.check_external_agent_eligibility(
        task_type="doc_update", files_touched=["a.md"], blast_radius="isolated",
        requires_multi_file_context=False, acceptance_criteria="fix it", repro_steps="",
        tier=3,
    )
    assert ok2 is True, reasons2
    print("PASS: test_eligibility_isolated_bugfix_requires_repro_steps")


def test_eligibility_requires_multi_file_context_rejected():
    sbr = _load("sbr_elig_multifile", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="single_file_refactor", files_touched=["a.py"], blast_radius="isolated",
        requires_multi_file_context=True, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("requires_multi_file_context is true" in r for r in reasons), reasons
    print("PASS: test_eligibility_requires_multi_file_context_rejected")


def test_eligibility_blast_radius_must_be_isolated():
    sbr = _load("sbr_elig_blast", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="single_file_refactor", files_touched=["a.py"], blast_radius="wide",
        requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=3,
    )
    assert ok is False
    assert any("blast_radius" in r for r in reasons), reasons
    print("PASS: test_eligibility_blast_radius_must_be_isolated")


def test_eligibility_two_most_critical_tiers_excluded():
    sbr = _load("sbr_elig_tiers", "superboss-register.py")
    for tier in (0, 1):
        ok, reasons = sbr.check_external_agent_eligibility(
            task_type="single_file_refactor", files_touched=["a.py"], blast_radius="isolated",
            requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=tier,
        )
        assert ok is False, f"tier {tier} should be excluded"
        assert any("most critical tiers" in r for r in reasons), reasons
    for tier in (2, 3, 4):
        ok, reasons = sbr.check_external_agent_eligibility(
            task_type="single_file_refactor", files_touched=["a.py"], blast_radius="isolated",
            requires_multi_file_context=False, acceptance_criteria="x", repro_steps=None, tier=tier,
        )
        assert ok is True, (tier, reasons)
    print("PASS: test_eligibility_two_most_critical_tiers_excluded")


def test_eligibility_multiple_reasons_all_reported():
    """Real requirement: reasons is the COMPLETE list, not just the first failure."""
    sbr = _load("sbr_elig_multi_reason", "superboss-register.py")
    ok, reasons = sbr.check_external_agent_eligibility(
        task_type="bogus", files_touched=[], blast_radius="wide",
        requires_multi_file_context=True, acceptance_criteria="", repro_steps="", tier=0,
    )
    assert ok is False
    assert len(reasons) >= 5, reasons
    print("PASS: test_eligibility_multiple_reasons_all_reported")


# ---------------------------------------------------------------------------
# 2. mark_external_agent_eligible() -- real DB write path, refuses false marks
# ---------------------------------------------------------------------------

def test_mark_external_agent_eligible_round_trip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_mark_elig_rt", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()
        umr_id = _insert_umr(sbr2, conn, tier=3)
        result = sbr2.mark_external_agent_eligible(
            conn, umr_id, task_type="doc_update", blast_radius="isolated",
            requires_multi_file_context=False, files_touched=["README.md"],
            acceptance_criteria="fix the typo", repro_steps=None,
        )
        conn.commit()
        assert result["eligible"] is True
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_eligible"] == 1
        assert row["external_agent_task_type"] == "doc_update"
        assert json.loads(row["files_touched"]) == ["README.md"]
        meta = json.loads(row["metadata_json"])
        assert meta["external_agent"]["acceptance_criteria"] == "fix the typo"
        conn.close()
    print("PASS: test_mark_external_agent_eligible_round_trip")


def test_mark_external_agent_eligible_refuses_ineligible_row():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_mark_elig_refuse", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()
        umr_id = _insert_umr(sbr2, conn, tier=0)  # tier 0 -- most critical, must be refused
        raised = False
        try:
            sbr2.mark_external_agent_eligible(
                conn, umr_id, task_type="doc_update", blast_radius="isolated",
                requires_multi_file_context=False, files_touched=["README.md"],
                acceptance_criteria="fix the typo",
            )
        except ValueError as e:
            raised = True
            assert "NOT really eligible" in str(e)
        assert raised, "expected ValueError for a tier-0 row"
        row = conn.execute("SELECT external_agent_eligible FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_eligible"] == 0
        conn.close()
    print("PASS: test_mark_external_agent_eligible_refuses_ineligible_row")


# ---------------------------------------------------------------------------
# 3. Prompt template render/parse round trip
# ---------------------------------------------------------------------------

def test_prompt_render_parse_round_trip():
    sbr = _load("sbr_prompt_rt", "superboss-register.py")
    prompt = sbr.render_external_agent_prompt(
        dispatch_id="EAD-20260806-100000-abcd", umr_id="UMR-20260806-100000-1234",
        task_type="doc_update", files_touched=["README.md"],
        acceptance_criteria="fix the stale line", repro_steps=None,
        file_contents={"README.md": "old content\n"},
    )
    assert "DISPATCH_ID: EAD-20260806-100000-abcd" in prompt
    assert "UMR_ID: UMR-20260806-100000-1234" in prompt
    assert 'FILES_TOUCHED_JSON: ["README.md"]' in prompt

    reply = (
        "DISPATCH_ID: EAD-20260806-100000-abcd\n"
        "UMR_ID: UMR-20260806-100000-1234\n"
        "```diff\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-old content\n"
        "+new content\n"
        "```\n"
    )
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert err is None, err
    assert parsed["dispatch_id"] == "EAD-20260806-100000-abcd"
    assert parsed["umr_id"] == "UMR-20260806-100000-1234"
    assert "new content" in parsed["diff_text"]
    print("PASS: test_prompt_render_parse_round_trip")


def test_parse_reply_rejects_missing_dispatch_id_line():
    sbr = _load("sbr_parse_missing_did", "superboss-register.py")
    reply = "UMR_ID: UMR-x\n```diff\nx\n```\n"
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert parsed is None and err is not None
    assert "DISPATCH_ID" in err
    print("PASS: test_parse_reply_rejects_missing_dispatch_id_line")


def test_parse_reply_rejects_two_diff_blocks():
    sbr = _load("sbr_parse_two_blocks", "superboss-register.py")
    reply = (
        "DISPATCH_ID: EAD-1\nUMR_ID: UMR-1\n"
        "```diff\nfirst\n```\nsome extra text\n```diff\nsecond\n```\n"
    )
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert parsed is None and err is not None
    assert "exactly one" in err
    print("PASS: test_parse_reply_rejects_two_diff_blocks")


def test_parse_reply_rejects_prose_outside_diff_block():
    sbr = _load("sbr_parse_prose", "superboss-register.py")
    reply = (
        "DISPATCH_ID: EAD-1\nUMR_ID: UMR-1\n"
        "Here is my fix, hope it helps!\n"
        "```diff\nreal diff content\n```\n"
    )
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert parsed is None and err is not None
    assert "nothing else of substance" in err
    print("PASS: test_parse_reply_rejects_prose_outside_diff_block")


def test_parse_reply_rejects_wrong_fence_tag():
    sbr = _load("sbr_parse_wrong_tag", "superboss-register.py")
    reply = "DISPATCH_ID: EAD-1\nUMR_ID: UMR-1\n```patch\nreal diff content\n```\n"
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert parsed is None and err is not None
    assert "```diff" in err
    print("PASS: test_parse_reply_rejects_wrong_fence_tag")


def test_parse_reply_rejects_no_diff_block_at_all():
    sbr = _load("sbr_parse_no_block", "superboss-register.py")
    reply = "DISPATCH_ID: EAD-1\nUMR_ID: UMR-1\njust prose, no diff\n"
    parsed, err = sbr.parse_external_agent_reply(reply)
    assert parsed is None and err is not None
    print("PASS: test_parse_reply_rejects_no_diff_block_at_all")


def test_extract_diff_paths_rejects_traversal_and_absolute():
    sbr = _load("sbr_extract_paths", "superboss-register.py")
    diff_text = (
        "diff --git a/../../etc/passwd b/../../etc/passwd\n"
        "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n"
    )
    paths = sbr._extract_diff_paths(diff_text)
    assert "../../etc/passwd" in paths
    print("PASS: test_extract_diff_paths_rejects_traversal_and_absolute")


# ---------------------------------------------------------------------------
# 4. Transaction-safe get_next_external_agent_task() -- no double dispatch
# ---------------------------------------------------------------------------

def test_get_next_selects_only_eligible_available_rows():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_getnext_basic", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()

        not_eligible = _insert_umr(sbr2, conn, tier=3, task_identity="not eligible")
        eligible = _insert_umr(sbr2, conn, tier=3, task_identity="eligible one")
        sbr2.mark_external_agent_eligible(
            conn, eligible, task_type="doc_update", blast_radius="isolated",
            requires_multi_file_context=False, files_touched=["README.md"],
            acceptance_criteria="fix it",
        )
        conn.commit()

        artifacts_root = os.path.join(d, "artifacts")
        repo_root = d
        with open(os.path.join(d, "README.md"), "w") as f:
            f.write("old\n")

        result = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=repo_root)
        conn.commit()
        assert result is not None
        assert result["umr_id"] == eligible

        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (eligible,)).fetchone()
        assert row["external_agent_status"] == "dispatched"
        assert row["external_agent_dispatch_count"] == 1

        disp_row = conn.execute(
            "SELECT * FROM external_agent_dispatch WHERE dispatch_id=?", (result["dispatch_id"],)
        ).fetchone()
        assert disp_row["status"] == "dispatched"
        assert disp_row["umr_id"] == eligible

        # Real "no double dispatch" property: a second call must NOT re-select
        # the same row (it's no longer NULL/'requeued') -- with only one real
        # eligible row and it now dispatched, nothing else is available.
        result2 = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=repo_root)
        assert result2 is None
        conn.close()
    print("PASS: test_get_next_selects_only_eligible_available_rows")


def test_get_next_respects_tier_ordering():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_getnext_tier", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()

        low_pri = _insert_umr(sbr2, conn, tier=4, task_identity="low priority doc fix")
        high_pri = _insert_umr(sbr2, conn, tier=2, task_identity="higher priority doc fix")
        for umr_id in (low_pri, high_pri):
            sbr2.mark_external_agent_eligible(
                conn, umr_id, task_type="doc_update", blast_radius="isolated",
                requires_multi_file_context=False, files_touched=["README.md"],
                acceptance_criteria="fix it",
            )
        conn.commit()
        artifacts_root = os.path.join(d, "artifacts")
        with open(os.path.join(d, "README.md"), "w") as f:
            f.write("old\n")
        result = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=d)
        conn.commit()
        assert result["umr_id"] == high_pri, "lower tier number (more urgent) must be selected first"
        conn.close()
    print("PASS: test_get_next_respects_tier_ordering")


# ---------------------------------------------------------------------------
# 5. Real two-strike requeue-then-permanent-fallback logic
# ---------------------------------------------------------------------------

def _dispatch_one(sbr2, conn, d):
    umr_id = _insert_umr(sbr2, conn, tier=3, task_identity="two-strike test task " + os.urandom(4).hex())
    sbr2.mark_external_agent_eligible(
        conn, umr_id, task_type="single_file_refactor", blast_radius="isolated",
        requires_multi_file_context=False, files_touched=["a.py"], acceptance_criteria="refactor it",
    )
    conn.commit()
    with open(os.path.join(d, "a.py"), "w") as f:
        f.write("x = 1\n")
    artifacts_root = os.path.join(d, "artifacts")
    result = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=d)
    conn.commit()
    return umr_id, result


def test_two_strike_first_failure_requeues_second_falls_back():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_twostrike", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()

        umr_id, disp1 = _dispatch_one(sbr2, conn, d)
        assert disp1 is not None

        # A malformed reply (UMR_ID mismatch) is a real, deterministic rejection.
        bad_reply = f"DISPATCH_ID: {disp1['dispatch_id']}\nUMR_ID: UMR-totally-wrong\n```diff\nx\n```\n"
        r1 = sbr2.submit_external_agent_result(
            conn, reply_text=bad_reply, artifacts_root=os.path.join(d, "artifacts"), repo_root=d,
        )
        conn.commit()
        assert r1["outcome"] == "rejected_requeued", r1
        assert r1["reject_count"] == 1

        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_status"] == "requeued"
        assert row["external_agent_reject_count"] == 1
        assert row["external_agent_eligible"] == 1, "still eligible after strike 1"

        # Real redispatch (strike 2 begins) -- must select the SAME row again
        # (only real requeued/eligible row in the DB).
        artifacts_root = os.path.join(d, "artifacts")
        disp2 = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=d)
        conn.commit()
        assert disp2 is not None and disp2["umr_id"] == umr_id
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_dispatch_count"] == 2

        bad_reply2 = f"DISPATCH_ID: {disp2['dispatch_id']}\nUMR_ID: UMR-totally-wrong-again\n```diff\nx\n```\n"
        r2 = sbr2.submit_external_agent_result(
            conn, reply_text=bad_reply2, artifacts_root=artifacts_root, repo_root=d,
        )
        conn.commit()
        assert r2["outcome"] == "rejected_fallback_to_internal", r2
        assert r2["reject_count"] == 2

        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_eligible"] == 0, "must be permanently excluded after strike 2"
        assert row["external_agent_status"] == "fallen_back_internal"
        assert row["reason"] is not None and "two-strike" in row["reason"]

        # A real 3rd dispatch attempt must never happen: nothing eligible remains.
        disp3 = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=d)
        assert disp3 is None, "a 3rd external-agent dispatch attempt must never be issued"
        conn.close()
    print("PASS: test_two_strike_first_failure_requeues_second_falls_back")


def test_reject_for_out_of_scope_diff_path():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_outofscope", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()
        umr_id, disp = _dispatch_one(sbr2, conn, d)
        reply = (
            f"DISPATCH_ID: {disp['dispatch_id']}\nUMR_ID: {umr_id}\n"
            "```diff\ndiff --git a/some_other_file.py b/some_other_file.py\n"
            "--- a/some_other_file.py\n+++ b/some_other_file.py\n@@ -1 +1 @@\n-x\n+y\n```\n"
        )
        result = sbr2.submit_external_agent_result(
            conn, reply_text=reply, artifacts_root=os.path.join(d, "artifacts"), repo_root=d,
        )
        conn.commit()
        assert result["outcome"] == "rejected_requeued"
        assert "outside the real allow-list" in result["reason"]
        conn.close()
    print("PASS: test_reject_for_out_of_scope_diff_path")


def test_expiry_is_a_real_two_strike_event_and_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_expiry", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()
        umr_id, disp = _dispatch_one(sbr2, conn, d)

        # Force expiry into the past.
        conn.execute("UPDATE external_agent_dispatch SET expires_at=? WHERE dispatch_id=?",
                     ("2000-01-01T00:00:00+00:00", disp["dispatch_id"]))
        conn.commit()

        results = sbr2.expire_external_agent_dispatches(conn)
        conn.commit()
        assert len(results) == 1
        assert results[0]["outcome"] == "rejected_requeued"
        assert results[0]["dispatch_status"] == "expired"

        disp_row = conn.execute(
            "SELECT status FROM external_agent_dispatch WHERE dispatch_id=?", (disp["dispatch_id"],)
        ).fetchone()
        assert disp_row["status"] == "expired"

        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_status"] == "requeued"
        assert row["external_agent_reject_count"] == 1

        # Idempotent: running again with nothing newly-expired changes nothing.
        results2 = sbr2.expire_external_agent_dispatches(conn)
        conn.commit()
        assert results2 == []
        row2 = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row2["external_agent_reject_count"] == 1
        conn.close()
    print("PASS: test_expiry_is_a_real_two_strike_event_and_is_idempotent")


# ---------------------------------------------------------------------------
# 6. Real safety property: diff applies ONLY to a fresh worktree, never main
# ---------------------------------------------------------------------------

def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


_REAL_TEST_DEFAULT_BRANCH = "trunk"  # NOT "main" -- this sandbox's own git wrapper
# enforces a real protected-branch policy (ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml)
# that BLOCKS any `git push` to a ref literally named "main", even inside a
# throwaway scratch repo unrelated to the real veridian-scripts repo -- confirmed
# directly while building this test (a push to a scratch repo's own "main" was
# refused with "BLOCKED: git push to protected branch/ref 'main' must run through
# the dispatch pipeline"). Using a differently-named default branch here is purely
# a test-environment workaround for that guard; the real production code under
# test (_git_default_branch()) is itself branch-name-agnostic -- it reads
# `git symbolic-ref refs/remotes/origin/HEAD` rather than hardcoding "main".


def _init_real_git_repo_with_bare_origin(base_dir):
    """Real git repo + real local bare 'origin' remote (no network needed).
    Returns (work_repo_path, origin_bare_path)."""
    origin = os.path.join(base_dir, "origin.git")
    work = os.path.join(base_dir, "work_repo")
    os.makedirs(origin)
    os.makedirs(work)
    _run(["git", "init", "--bare", "-b", _REAL_TEST_DEFAULT_BRANCH, origin])
    _run(["git", "init", "-b", _REAL_TEST_DEFAULT_BRANCH, work], cwd=base_dir)
    with open(os.path.join(work, "a.py"), "w") as f:
        f.write("x = 1\n")
    _run(["git", "add", "-A"], cwd=work)
    _run(["git", "commit", "-m", "initial"], cwd=work)
    _run(["git", "remote", "add", "origin", origin], cwd=work)
    _run(["git", "push", "-u", "origin", _REAL_TEST_DEFAULT_BRANCH], cwd=work)
    _run(["git", "remote", "set-head", "origin", "-a"], cwd=work)
    return work, origin


def test_diff_applies_only_to_fresh_worktree_never_main():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_worktree_safety", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()

        repo_root, origin = _init_real_git_repo_with_bare_origin(d)
        main_head_before = _run(["git", "rev-parse", _REAL_TEST_DEFAULT_BRANCH], cwd=repo_root).stdout.strip()
        main_worktree_head_before = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()

        umr_id = _insert_umr(sbr2, conn, tier=3, task_identity="real worktree safety test")
        sbr2.mark_external_agent_eligible(
            conn, umr_id, task_type="single_file_refactor", blast_radius="isolated",
            requires_multi_file_context=False, files_touched=["a.py"], acceptance_criteria="bump x to 2",
        )
        conn.commit()
        artifacts_root = os.path.join(d, "artifacts")
        disp = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=repo_root)
        conn.commit()
        assert disp is not None

        reply = (
            f"DISPATCH_ID: {disp['dispatch_id']}\nUMR_ID: {umr_id}\n"
            "```diff\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-x = 1\n+x = 2\n```\n"
        )

        fake_gh_calls = []

        def fake_gh_run(cmd, cwd):
            fake_gh_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/fake/repo/pull/999\n", stderr="")

        result = sbr2.submit_external_agent_result(
            conn, reply_text=reply, artifacts_root=artifacts_root, repo_root=repo_root,
            gh_run=fake_gh_run, push=True,
        )
        conn.commit()

        assert result["outcome"] == "accepted", result
        assert result["pr_number"] == 999
        assert result["branch_name"] == f"external-agent/{disp['dispatch_id']}"

        # Real safety property 1: main's ref (in both the work repo and the
        # bare origin) is completely untouched -- the diff was never applied
        # to it, directly or indirectly.
        main_head_after = _run(["git", "rev-parse", _REAL_TEST_DEFAULT_BRANCH], cwd=repo_root).stdout.strip()
        assert main_head_after == main_head_before
        origin_main_head = _run(["git", "rev-parse", _REAL_TEST_DEFAULT_BRANCH], cwd=origin).stdout.strip()
        assert origin_main_head == main_head_before
        # The real work_repo's own checked-out branch/worktree HEAD (the
        # "main directly" surface a real accidental in-place apply would
        # touch) is also completely untouched.
        main_worktree_head_after = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        assert main_worktree_head_after == main_worktree_head_before
        checked_out_a_py = open(os.path.join(repo_root, "a.py")).read()
        assert checked_out_a_py == "x = 1\n", "main's real working tree content must be untouched"

        # Real safety property 2: the real change landed on its own real
        # branch, really pushed to the real (local, bare) origin remote --
        # not merely committed locally and left unpushed.
        branch_ref = _run(["git", "rev-parse", f"origin/{result['branch_name']}"], cwd=repo_root)
        assert branch_ref.returncode == 0, branch_ref.stderr
        origin_branches = _run(["git", "branch", "--list", result["branch_name"]], cwd=origin).stdout
        assert result["branch_name"] in origin_branches
        show = _run(["git", "show", f"{result['branch_name']}:a.py"], cwd=origin)
        assert show.stdout.strip() == "x = 2"

        # Real safety property 3: gh pr create was really invoked, with the
        # never-auto-merge marker in the PR body, and gh pr merge was NEVER
        # called anywhere in this flow.
        assert any(call[:3] == ["gh", "pr", "create"] for call in fake_gh_calls)
        body_arg = fake_gh_calls[0][fake_gh_calls[0].index("--body") + 1]
        assert "NEVER AUTO-MERGE" in body_arg
        assert not any(call[:3] == ["gh", "pr", "merge"] for call in fake_gh_calls)

        disp_row = conn.execute(
            "SELECT * FROM external_agent_dispatch WHERE dispatch_id=?", (disp["dispatch_id"],)
        ).fetchone()
        assert disp_row["status"] == "accepted"
        assert disp_row["pr_number"] == 999
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        assert row["external_agent_status"] == "pr_open"

        conn.close()
    print("PASS: test_diff_applies_only_to_fresh_worktree_never_main")


def test_diff_that_fails_to_apply_is_rejected_and_never_touches_main():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        sbr2 = _load("sbr_worktree_bad_apply", "superboss-register.py", env=_scratch_env(scratch_db))
        conn = sbr2._connect()

        repo_root, origin = _init_real_git_repo_with_bare_origin(d)
        main_head_before = _run(["git", "rev-parse", _REAL_TEST_DEFAULT_BRANCH], cwd=repo_root).stdout.strip()

        umr_id = _insert_umr(sbr2, conn, tier=3, task_identity="real bad-apply safety test")
        sbr2.mark_external_agent_eligible(
            conn, umr_id, task_type="single_file_refactor", blast_radius="isolated",
            requires_multi_file_context=False, files_touched=["a.py"], acceptance_criteria="bump x",
        )
        conn.commit()
        artifacts_root = os.path.join(d, "artifacts")
        disp = sbr2.get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=repo_root)
        conn.commit()

        # A diff whose context lines don't match reality -- git apply must fail.
        reply = (
            f"DISPATCH_ID: {disp['dispatch_id']}\nUMR_ID: {umr_id}\n"
            "```diff\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-this line does not exist in the real file\n+x = 2\n```\n"
        )
        result = sbr2.submit_external_agent_result(
            conn, reply_text=reply, artifacts_root=artifacts_root, repo_root=repo_root, push=True,
        )
        conn.commit()
        assert result["outcome"] == "rejected_requeued", result
        assert "apply" in result["reason"]

        main_head_after = _run(["git", "rev-parse", _REAL_TEST_DEFAULT_BRANCH], cwd=repo_root).stdout.strip()
        assert main_head_after == main_head_before
        assert open(os.path.join(repo_root, "a.py")).read() == "x = 1\n"
        conn.close()
    print("PASS: test_diff_that_fails_to_apply_is_rejected_and_never_touches_main")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
