"""Real tests for ddl_authorization_check.py's dispatch-time DDL gate.

Covers: DDL/DCL keyword + MCP-tool-name detection, Category A citation
validation (placeholder rejection, dated-note length floor, real KE-id /
OWNER_DECISIONS_NEEDED existence checks), the full check_ddl_authorization()
allow/deny flow, and Category B deterministic-recovery evidence checking
(built against a real, throwaway git repo under tmp_path -- never the live
ai-os/ tree or any live DB) including the prompt<->sql_file binding check.

Module-level constants (AI_OS_DIR, REPOS_BASE_DIR) are monkeypatched to
tmp_path locations for isolation -- the script itself is never modified.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ddl_authorization_check as ddl  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ddl_authorization_check.py")


def _run_git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


# ---------------------------------------------------------------------------
# find_ddl_references
# ---------------------------------------------------------------------------

def test_find_ddl_references_detects_multiple_real_keywords():
    text = "Please run:\nCREATE TABLE foo (id int);\nGRANT SELECT ON foo TO bar;\n"
    hits = ddl.find_ddl_references(text)
    assert "CREATE TABLE" in hits
    assert "GRANT" in hits


def test_find_ddl_references_case_insensitive_and_multiline():
    text = "create   table\nfoo (id int);"
    hits = ddl.find_ddl_references(text)
    assert "CREATE TABLE" in hits


def test_find_ddl_references_detects_supabase_mcp_tool_name():
    text = "Call mcp__Supabase__apply_migration with this query."
    hits = ddl.find_ddl_references(text)
    assert "apply_migration" in hits


def test_find_ddl_references_empty_for_plain_select():
    text = "SELECT * FROM users WHERE id = 1;"
    assert ddl.find_ddl_references(text) == []


def test_find_ddl_references_empty_for_prose_with_no_sql():
    text = "This task creates a new dashboard component in React and tests it."
    assert ddl.find_ddl_references(text) == []


# ---------------------------------------------------------------------------
# is_real_reference / find_pre_approval (Category A)
# ---------------------------------------------------------------------------

def test_placeholder_reference_yes_is_rejected():
    assert ddl.is_real_reference("yes") is False


def test_placeholder_reference_tbd_is_rejected():
    assert ddl.is_real_reference("TBD") is False


def test_empty_reference_is_rejected():
    assert ddl.is_real_reference("") is False


def test_dated_note_below_min_length_is_rejected():
    short_note = "ok 2026-07-25"
    assert len(short_note) < ddl.MIN_DATED_NOTE_LENGTH
    assert ddl.is_real_reference(short_note) is False


def test_dated_note_at_min_length_is_accepted():
    note = "Owner approved via Slack DM on 2026-07-25, see #ops-approvals thread"
    assert len(note) >= ddl.MIN_DATED_NOTE_LENGTH
    assert ddl.is_real_reference(note) is True


def test_fabricated_ke_id_well_formed_but_nonexistent_is_rejected(tmp_path, monkeypatch):
    """Round-2 gap this module explicitly closed: a well-formed KE-id that
    was never actually recorded anywhere must be rejected, not accepted on
    string shape alone."""
    ai_os = tmp_path / "ai-os"
    ai_os.mkdir()
    (ai_os / "some_record.md").write_text("nothing relevant here")
    monkeypatch.setattr(ddl, "AI_OS_DIR", str(ai_os))
    assert ddl.is_real_reference("KE-20260726-999999-dead") is False


def test_real_ke_id_present_on_disk_is_accepted(tmp_path, monkeypatch):
    ai_os = tmp_path / "ai-os"
    ai_os.mkdir()
    (ai_os / "decision_log.md").write_text("Approved: KE-20260726-101112-ab12 for real")
    monkeypatch.setattr(ddl, "AI_OS_DIR", str(ai_os))
    assert ddl.is_real_reference("see KE-20260726-101112-ab12") is True


def test_owner_decisions_file_reference_must_exist_on_disk(tmp_path, monkeypatch):
    ai_os = tmp_path / "ai-os"
    ai_os.mkdir()
    monkeypatch.setattr(ddl, "AI_OS_DIR", str(ai_os))
    # Not written to disk -> rejected.
    assert ddl.is_real_reference("OWNER_DECISIONS_NEEDED_2026-07-23.yaml#ddl-1") is False
    (ai_os / "OWNER_DECISIONS_NEEDED_2026-07-23.yaml").write_text("ddl-1: approved")
    # Now it's real -> accepted.
    assert ddl.is_real_reference("OWNER_DECISIONS_NEEDED_2026-07-23.yaml#ddl-1") is True


def test_find_pre_approval_skips_placeholder_and_returns_first_valid():
    text = (
        "PRE-APPROVED-LIVE-DDL: yes\n"
        "PRE-APPROVED-LIVE-DDL: Owner approved via Slack DM on 2026-07-25, see #ops-approvals thread\n"
    )
    ref = ddl.find_pre_approval(text)
    assert ref == "Owner approved via Slack DM on 2026-07-25, see #ops-approvals thread"


def test_find_pre_approval_returns_none_when_no_line_present():
    assert ddl.find_pre_approval("just a normal prompt with no approval line") is None


# ---------------------------------------------------------------------------
# check_ddl_authorization -- full flow (Category A)
# ---------------------------------------------------------------------------

def test_check_ddl_authorization_allows_when_no_ddl_present():
    result = ddl.check_ddl_authorization("Please refactor the dashboard component.")
    assert result["valid"] is True
    assert result["policy_decision"]["decision"] == "allow"
    assert result["policy_decision"]["reason_code"] == "no_ddl_language_found"


def test_check_ddl_authorization_denies_ddl_with_no_approval():
    text = "## SCOPE\nRun: DROP TABLE legacy_users;\n"
    result = ddl.check_ddl_authorization(text)
    assert result["valid"] is False
    assert "ddl_authorization_required" in result["reason"]
    assert "DROP TABLE" in result["ddl_references_found"]
    assert result["policy_decision"]["decision"] == "deny"
    assert "guidance" in result and "PRE-APPROVED-LIVE-DDL" in result["guidance"]


def test_check_ddl_authorization_allows_ddl_with_valid_dated_note():
    text = (
        "## SCOPE\nRun: ALTER TABLE foo ADD COLUMN bar int;\n"
        "PRE-APPROVED-LIVE-DDL: Owner approved via Slack DM on 2026-07-25, see #ops-approvals thread\n"
    )
    result = ddl.check_ddl_authorization(text)
    assert result["valid"] is True
    assert result["pre_approved_reference"].startswith("Owner approved via Slack DM")
    assert result["policy_decision"]["decision"] == "allow"
    assert result["policy_decision"]["reason_code"] == "ddl_pre_approved"


def test_check_ddl_authorization_rejects_ddl_with_bare_placeholder_approval():
    text = "## SCOPE\nRun: TRUNCATE audit_log;\nPRE-APPROVED-LIVE-DDL: yes\n"
    result = ddl.check_ddl_authorization(text)
    assert result["valid"] is False
    assert "TRUNCATE" in result["ddl_references_found"]


# ---------------------------------------------------------------------------
# Category B: deterministic recovery, built against a real throwaway git repo
# ---------------------------------------------------------------------------

@pytest.fixture
def category_b_repo(tmp_path):
    """A real git repo (under tmp_path, never touching the live ai-os/ tree)
    with a merged, idempotent SQL file and a COMPLETED.yaml carrying a scoped
    YAML entry with all the evidence phrases Category B's 10 conditions look
    for."""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    _run_git(repo_dir, "init", "-q")
    _run_git(repo_dir, "config", "user.email", "test@example.com")
    _run_git(repo_dir, "config", "user.name", "Test")

    migrations = repo_dir / "migrations"
    migrations.mkdir()
    sql_path = migrations / "0001_fix.sql"
    sql_path.write_text("CREATE TABLE IF NOT EXISTS foo (id int);\n")

    ai_os = repo_dir / "ai-os"
    ai_os.mkdir()
    completed = ai_os / "COMPLETED.yaml"
    completed.write_text(
        "queue:\n"
        "  - id: MIGRATION-DRIFT-TEST\n"
        "    governing_umr: UMR-20260803-025317-0c64\n"
        "    detail: |\n"
        "      real Sev1 outage confirmed\n"
        "      root cause independently verified\n"
        "      independent audit confirms exact match\n"
        "      before and after evidence captured\n"
        "      rollback path documented\n"
        "      canonical artifact updated after execution\n"
        "  - id: UNRELATED-ENTRY\n"
        "    detail: |\n"
        "      rollback path documented for a totally different migration\n"
    )

    (repo_dir / "README.md").write_text("test repo\n")
    _run_git(repo_dir, "add", ".")
    _run_git(repo_dir, "commit", "-q", "-m", "init")
    # Fake a remote-tracking ref (no real network origin needed) so the
    # script's real origin/main-based git log / cat-file lookups succeed.
    _run_git(repo_dir, "update-ref", "refs/remotes/origin/main", "HEAD")

    return repo_dir


def _valid_evidence(repo_name="myrepo"):
    entry = "ai-os/COMPLETED.yaml#MIGRATION-DRIFT-TEST"
    return {
        "repo": repo_name,
        "sql_file": "migrations/0001_fix.sql",
        "governing_umr": "UMR-20260803-025317-0c64",
        "outage_evidence": f"{entry}::real Sev1 outage confirmed",
        "root_cause_evidence": f"{entry}::root cause independently verified",
        "audit_match_evidence": f"{entry}::independent audit confirms exact match",
        "before_after_evidence": f"{entry}::before and after evidence captured",
        "rollback_path": f"{entry}::rollback path documented",
        "canonical_artifact": f"{entry}::canonical artifact updated after execution",
    }


def test_category_b_all_conditions_pass_for_real_evidence(category_b_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    result = ddl.check_category_b_recovery(_valid_evidence())
    failed = [c for c in result["conditions"] if not c["passed"]]
    assert result["category_b_valid"] is True, f"unexpectedly failed conditions: {failed}"
    assert len(result["conditions"]) == 12  # 0_evidence_complete, 0_repo_resolved, 1..10


def test_category_b_missing_field_fails_closed(category_b_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence()
    del evidence["rollback_path"]
    result = ddl.check_category_b_recovery(evidence)
    assert result["category_b_valid"] is False
    assert result["conditions"][0]["id"] == "0_evidence_complete"
    assert result["conditions"][0]["passed"] is False
    assert "rollback_path" in result["conditions"][0]["detail"]


def test_category_b_unresolvable_repo_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence(repo_name="does-not-exist-anywhere")
    result = ddl.check_category_b_recovery(evidence)
    assert result["category_b_valid"] is False
    repo_cond = [c for c in result["conditions"] if c["id"] == "0_repo_resolved"][0]
    assert repo_cond["passed"] is False


def test_category_b_bare_file_citation_with_no_anchor_rejected(category_b_repo, monkeypatch, tmp_path):
    """Real gap this module closed in independent review: a bare existing
    file path (no #anchor) must NOT satisfy a claim-substantiating
    condition like outage_evidence."""
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence()
    evidence["outage_evidence"] = "README.md"  # exists, but no anchor
    result = ddl.check_category_b_recovery(evidence)
    assert result["category_b_valid"] is False
    outage_cond = [c for c in result["conditions"] if c["id"] == "4_real_outage"][0]
    assert outage_cond["passed"] is False
    assert "no #anchor" in outage_cond["detail"]


def test_category_b_unmerged_sql_file_fails_condition_2(tmp_path, monkeypatch):
    """SQL present in the working tree only (never committed to
    origin/main or origin/master) must fail the 'previously reviewed and
    merged' condition."""
    repo_dir = tmp_path / "unmerged_repo"
    repo_dir.mkdir()
    _run_git(repo_dir, "init", "-q")
    _run_git(repo_dir, "config", "user.email", "t@t.com")
    _run_git(repo_dir, "config", "user.name", "T")
    (repo_dir / "migrations").mkdir()
    (repo_dir / "migrations" / "new.sql").write_text("CREATE TABLE IF NOT EXISTS bar (id int);\n")
    _run_git(repo_dir, "add", ".")
    _run_git(repo_dir, "commit", "-q", "-m", "wip, not merged/pushed to origin")
    # Deliberately do NOT create refs/remotes/origin/main.

    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence(repo_name="unmerged_repo")
    evidence["sql_file"] = "migrations/new.sql"
    result = ddl.check_category_b_recovery(evidence)
    assert result["category_b_valid"] is False
    merged_cond = [c for c in result["conditions"] if c["id"] == "2_previously_merged"][0]
    assert merged_cond["passed"] is False
    # _read_repo_file() falls back to the real working-tree file when no
    # origin/* ref resolves, so condition 1 (mere existence) still passes --
    # it's specifically condition 2 (merged history) that must fail here.
    sql_cond = [c for c in result["conditions"] if c["id"] == "1_sql_exists"][0]
    assert sql_cond["passed"] is True


def test_is_idempotent_sql_flags_unguarded_create_table():
    ok, detail = ddl._is_idempotent_sql("CREATE TABLE foo (id int);")
    assert ok is False
    assert "unguarded" in detail


def test_is_idempotent_sql_accepts_guarded_create_table():
    ok, _detail = ddl._is_idempotent_sql("CREATE TABLE IF NOT EXISTS foo (id int);")
    assert ok is True


def test_is_idempotent_sql_accepts_do_block_with_real_exception_guard():
    sql = (
        "DO $$ BEGIN\n"
        "  CREATE POLICY foo_policy ON foo USING (true);\n"
        "EXCEPTION WHEN duplicate_object THEN NULL;\n"
        "END $$;"
    )
    ok, _detail = ddl._is_idempotent_sql(sql)
    assert ok is True


def test_is_idempotent_sql_rejects_do_block_without_exception_guard():
    """A DO $$ block with no duplicate_object handler provides no real
    rerun-safety and must not be exempted."""
    sql = "DO $$ BEGIN\n  CREATE POLICY foo_policy ON foo USING (true);\nEND $$;"
    ok, detail = ddl._is_idempotent_sql(sql)
    assert ok is False
    assert "unguarded" in detail


def test_is_idempotent_sql_comment_cannot_fake_a_guard():
    """A comment merely containing the words 'IF EXISTS' must not make a
    genuinely unguarded statement pass."""
    sql = "-- TODO: add IF EXISTS here\nDROP TABLE foo;"
    ok, _detail = ddl._is_idempotent_sql(sql)
    assert ok is False


def test_is_idempotent_sql_row_level_security_alter_is_inherently_idempotent():
    sql = "ALTER TABLE foo ENABLE ROW LEVEL SECURITY;"
    ok, _detail = ddl._is_idempotent_sql(sql)
    assert ok is True


def test_scoped_yaml_entry_block_does_not_leak_into_unrelated_entry(category_b_repo, monkeypatch, tmp_path):
    """Real gap this module closed: a citation for MIGRATION-DRIFT-TEST must
    not be satisfied by text that only appears in the unrelated,
    differently-scoped UNRELATED-ENTRY block."""
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    ok, detail = ddl._citation_exists(
        category_b_repo,
        "ai-os/COMPLETED.yaml#MIGRATION-DRIFT-TEST::rollback path documented for a totally different migration",
        require_anchor=True,
    )
    assert ok is False, detail


# ---------------------------------------------------------------------------
# _prompt_scope_matches_cited_sql -- the SCOPE<->evidence binding check
# ---------------------------------------------------------------------------

def test_binding_check_passes_when_inline_ddl_matches_cited_sql_file(category_b_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence()
    prompt = "## SCOPE\nCREATE TABLE IF NOT EXISTS foo (id int);\n"
    ok, detail = ddl._prompt_scope_matches_cited_sql(prompt, evidence)
    assert ok is True, detail


def test_binding_check_fails_when_inline_ddl_does_not_match_cited_sql_file(category_b_repo, monkeypatch, tmp_path):
    """The critical gap this binding check closed: citing a safe sql_file
    while SCOPE actually inlines different, unreviewed DDL must be rejected."""
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence()
    prompt = "## SCOPE\nDROP TABLE users;\n"
    ok, detail = ddl._prompt_scope_matches_cited_sql(prompt, evidence)
    assert ok is False
    assert "do not appear in the cited sql_file" in detail


def test_binding_check_passes_when_scope_only_names_the_file_path(category_b_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    evidence = _valid_evidence()
    prompt = "## SCOPE\nReapply migrations/0001_fix.sql to fix the production drift.\n"
    ok, detail = ddl._prompt_scope_matches_cited_sql(prompt, evidence)
    assert ok is True, detail


def test_check_ddl_authorization_full_category_b_success(category_b_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    entry = "ai-os/COMPLETED.yaml#MIGRATION-DRIFT-TEST"
    prompt = (
        "## SCOPE\n"
        "CREATE TABLE IF NOT EXISTS foo (id int);\n\n"
        "CATEGORY-B-DETERMINISTIC-RECOVERY:\n"
        "  repo: myrepo\n"
        "  sql_file: migrations/0001_fix.sql\n"
        "  governing_umr: UMR-20260803-025317-0c64\n"
        f"  outage_evidence: {entry}::real Sev1 outage confirmed\n"
        f"  root_cause_evidence: {entry}::root cause independently verified\n"
        f"  audit_match_evidence: {entry}::independent audit confirms exact match\n"
        f"  before_after_evidence: {entry}::before and after evidence captured\n"
        f"  rollback_path: {entry}::rollback path documented\n"
        f"  canonical_artifact: {entry}::canonical artifact updated after execution\n"
    )
    result = ddl.check_ddl_authorization(prompt)
    assert result["valid"] is True, result
    assert result["category"] == "B"
    assert result["policy_decision"]["reason_code"] == "category_b_deterministic_recovery"


def test_check_ddl_authorization_category_b_blocked_by_binding_mismatch(category_b_repo, monkeypatch, tmp_path):
    """All 10 numbered conditions pass (evidence is real), but SCOPE's real
    inline DDL doesn't match the cited sql_file -- must still be denied."""
    monkeypatch.setattr(ddl, "REPOS_BASE_DIR", str(tmp_path))
    entry = "ai-os/COMPLETED.yaml#MIGRATION-DRIFT-TEST"
    prompt = (
        "## SCOPE\n"
        "DROP TABLE users;\n\n"
        "CATEGORY-B-DETERMINISTIC-RECOVERY:\n"
        "  repo: myrepo\n"
        "  sql_file: migrations/0001_fix.sql\n"
        "  governing_umr: UMR-20260803-025317-0c64\n"
        f"  outage_evidence: {entry}::real Sev1 outage confirmed\n"
        f"  root_cause_evidence: {entry}::root cause independently verified\n"
        f"  audit_match_evidence: {entry}::independent audit confirms exact match\n"
        f"  before_after_evidence: {entry}::before and after evidence captured\n"
        f"  rollback_path: {entry}::rollback path documented\n"
        f"  canonical_artifact: {entry}::canonical artifact updated after execution\n"
    )
    result = ddl.check_ddl_authorization(prompt)
    assert result["valid"] is False
    binding_cond = [c for c in result["category_b_conditions"] if c["id"] == "11_scope_matches_cited_sql"][0]
    assert binding_cond["passed"] is False


# ---------------------------------------------------------------------------
# CLI (subprocess) -- exercises the real __main__ entry point, exit codes
# ---------------------------------------------------------------------------

def test_cli_no_args_prints_usage_note_and_exits_zero():
    proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["valid"] is True
    assert "usage" in payload["note"]


def test_cli_prompt_file_with_no_ddl_exits_zero(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Refactor the login form component.")
    proc = subprocess.run([sys.executable, SCRIPT, str(prompt_file)], capture_output=True, text=True)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["valid"] is True


def test_cli_prompt_file_with_ddl_and_no_approval_exits_one(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("## SCOPE\nDROP TABLE users;\n")
    proc = subprocess.run([sys.executable, SCRIPT, str(prompt_file)], capture_output=True, text=True)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["valid"] is False
    assert "DROP TABLE" in payload["ddl_references_found"]


def test_cli_prompt_file_with_dated_note_approval_exits_zero(tmp_path):
    """This acceptance path needs no real ai-os/ existence check (dated
    free-text notes are format-only), so it's exercisable via a real
    subprocess run without touching any live directory."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(
        "## SCOPE\nTRUNCATE staging_table;\n"
        "PRE-APPROVED-LIVE-DDL: Owner approved via Slack DM on 2026-07-25, see #ops-approvals thread\n"
    )
    proc = subprocess.run([sys.executable, SCRIPT, str(prompt_file)], capture_output=True, text=True)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["valid"] is True


def test_cli_category_b_evidence_flag_with_incomplete_evidence_exits_one(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"repo": "myrepo"}))
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--category-b-evidence", str(evidence_file)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["category_b_valid"] is False
