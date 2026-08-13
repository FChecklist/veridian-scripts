#!/usr/bin/env python3
"""UMR-20260806-130914-e7f1 (real dispatch, governed by
UMR-20260806-071025-1d28) -- correction of the real 'false completion'
finding against UMR-20260806-122546-78d6.

Real root cause (confirmed directly against superboss-register.py's own
pre-fix code, not inferred): cmd_mark_umr_terminal / its p_markterm argparse
block had NO --outputs-json/--commit-sha/--file-path parameter at all --
--status completed could be (and was) written with zero structured evidence,
purely as a real mechanism gap, not a caller-discipline one. This file tests
the real fix: status=completed/completed_unmerged now structurally require a
real, independently-verifiable artifact (a real ancestor-of-origin/main
commit, or a real on-disk file path) before the row is ever written, and the
real evidence supplied is recorded onto the row's own outputs_json (never
only inside free-text --reason).

Two layers, same convention as this repo's other _runner-injectable-helper
tests (e.g. triage_owner_umr_24h.py's own is_commit_on_main tests):
  1. validate_umr_terminal_completion_evidence() unit tests, with fake
     commit_exists_fn/is_ancestor_fn -- deterministic, no real subprocess.
  2. End-to-end CLI tests against a real, isolated scratch SQLite DB (never
     the live production one) -- including one against a REAL local git repo
     fixture, to prove the real `git merge-base --is-ancestor` wiring itself
     (not just the pure decision function) actually works.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "sbr_markterm_evidence_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


sbr = _load_module()


# --- Layer 1: validate_umr_terminal_completion_evidence(), fake I/O -------

def test_completed_refused_with_no_evidence_at_all():
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=None, commit_sha=None, repo_root="/nonexistent",
    )
    assert allowed is False
    assert "requires a real --file-path" in reason and "--commit-sha" in reason


def test_completed_allowed_with_real_existing_file_path(tmp_path):
    real_file = tmp_path / "real_artifact.txt"
    real_file.write_text("real content")
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=str(real_file), commit_sha=None, repo_root=str(tmp_path),
    )
    assert allowed is True
    assert reason is None


def test_completed_refused_with_nonexistent_file_path(tmp_path):
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=str(tmp_path / "does_not_exist.txt"),
        commit_sha=None, repo_root=str(tmp_path),
    )
    assert allowed is False


def test_completed_refused_with_fabricated_commit_sha():
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=None, commit_sha="deadbeef" * 5, repo_root="/opt/veridian/repos/veridian-scripts",
        commit_exists_fn=lambda repo_root, sha: False,
    )
    assert allowed is False
    assert "not a real commit" in reason


def test_completed_refused_when_commit_real_but_not_ancestor_of_main():
    """The real 'PR #171 open, unmerged' shape -- a real commit that exists
    but is not yet on origin/main must refuse status=completed and point at
    completed_unmerged instead."""
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=None, commit_sha="2290b1bb824713707788f2c6293b09177eb062ad",
        repo_root="/opt/veridian/repos/veridian-scripts",
        commit_exists_fn=lambda repo_root, sha: True,
        is_ancestor_fn=lambda repo_root, sha: False,
    )
    assert allowed is False
    assert "NOT (yet) a real ancestor of origin/main" in reason
    assert "completed_unmerged" in reason


def test_completed_allowed_when_commit_real_and_is_ancestor_of_main():
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed", file_path=None, commit_sha="76885f7c2b95e38eb55ca479bb99134223c62840",
        repo_root="/opt/veridian/repos/veridian-scripts",
        commit_exists_fn=lambda repo_root, sha: True,
        is_ancestor_fn=lambda repo_root, sha: True,
    )
    assert allowed is True
    assert reason is None


def test_completed_unmerged_requires_a_commit_sha():
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed_unmerged", file_path=None, commit_sha=None, repo_root="/x",
    )
    assert allowed is False
    assert "requires a real --commit-sha" in reason


def test_completed_unmerged_allowed_for_real_not_yet_merged_commit():
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed_unmerged", file_path=None, commit_sha="2290b1bb824713707788f2c6293b09177eb062ad",
        repo_root="/opt/veridian/repos/veridian-scripts",
        commit_exists_fn=lambda repo_root, sha: True,
        is_ancestor_fn=lambda repo_root, sha: False,
    )
    assert allowed is True
    assert reason is None


def test_completed_unmerged_refused_if_commit_is_already_merged():
    """A caller must never under-claim an already-merged commit as
    unmerged -- this direction of dishonesty is refused too."""
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status="completed_unmerged", file_path=None, commit_sha="76885f7c2b95e38eb55ca479bb99134223c62840",
        repo_root="/opt/veridian/repos/veridian-scripts",
        commit_exists_fn=lambda repo_root, sha: True,
        is_ancestor_fn=lambda repo_root, sha: True,
    )
    assert allowed is False
    assert "ALREADY a real ancestor" in reason


@pytest.mark.parametrize("status", ["failed", "killed"])
def test_failed_and_killed_are_never_gated(status):
    allowed, reason = sbr.validate_umr_terminal_completion_evidence(
        status=status, file_path=None, commit_sha=None, repo_root="/nonexistent",
    )
    assert allowed is True
    assert reason is None


# --- Layer 2: real CLI, real isolated scratch DB ---------------------------

def _insert_queued_row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,datetime('now'),2,'queued','owner_dispatch_gateway','veridian_task_create',"
        "'{}','{}','{}')",
        (umr_id, umr_id + "-identity"),
    )
    conn.commit()
    conn.close()


def _row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_module()
        sbr2.DB_PATH = path
        sbr2.init_db()
        yield path


def _run_sbr(args, scratch_db):
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    return subprocess.run(
        [sys.executable, "superboss-register.py"] + args,
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )


def test_cli_refuses_completed_with_zero_evidence_and_does_not_write_row(scratch_db):
    """The real, executable end-to-end refusal test: a bare `--status
    completed` (the exact old, pre-fix invocation shape) must now be
    genuinely refused -- non-zero exit, real refused=true JSON, and the row
    must be left completely untouched (still 'queued', ts_completed still
    NULL) -- never a partial/silent write."""
    umr_id = "UMR-TEST-e7f1-refuse-no-evidence"
    _insert_queued_row(scratch_db, umr_id)

    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed",
                     "--reason", "claims done but supplies nothing real"], scratch_db)
    assert out.returncode != 0
    payload = json.loads(out.stdout)
    assert payload["refused"] is True
    assert "requires a real --file-path" in payload["reason"]

    after = _row(scratch_db, umr_id)
    assert after["status"] == "queued"
    assert after["ts_completed"] is None
    assert after["outputs_json"] == "{}"


def test_cli_accepts_completed_with_real_file_path_and_writes_outputs_json(scratch_db, tmp_path):
    umr_id = "UMR-TEST-e7f1-accept-file-path"
    _insert_queued_row(scratch_db, umr_id)
    real_file = tmp_path / "real_artifact.py"
    real_file.write_text("# real file\n")

    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed",
                     "--file-path", str(real_file), "--reason", "real artifact landed"], scratch_db)
    assert out.returncode == 0, out.stderr

    after = _row(scratch_db, umr_id)
    assert after["status"] == "completed"
    assert after["ts_completed"] is not None
    outputs = json.loads(after["outputs_json"])
    assert outputs["file_path"] == str(real_file)


def test_cli_refuses_completed_for_real_unmerged_commit_against_real_git_repo(scratch_db, tmp_path):
    """Real end-to-end proof against a REAL local git repo (not a fake): a
    commit that exists on a feature branch but was never merged to main must
    refuse --status completed, and must succeed under --status
    completed_unmerged instead. Exercises the real
    _umr_terminal_commit_exists / _is_umr_terminal_commit_ancestor_of_main
    subprocess wiring, not just the pure decision function."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True, text=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    (repo / "README.md").write_text("real\n")
    run("git", "add", "README.md")
    run("git", "commit", "-q", "-m", "initial")
    # A bare repo standing in for 'origin', so `git fetch origin main` / `git
    # merge-base --is-ancestor <sha> origin/main` are both real, not faked.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True, capture_output=True, text=True)
    run("git", "remote", "add", "origin", str(origin))
    run("git", "fetch", "-q", "origin")

    run("git", "checkout", "-q", "-b", "feature")
    (repo / "feature.py").write_text("# unmerged real work\n")
    run("git", "add", "feature.py")
    run("git", "commit", "-q", "-m", "real unmerged work")
    feature_sha = run("git", "rev-parse", "HEAD").stdout.strip()
    run("git", "checkout", "-q", "main")

    umr_id = "UMR-TEST-e7f1-real-git-unmerged"
    _insert_queued_row(scratch_db, umr_id)

    refused = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed",
                         "--commit-sha", feature_sha, "--repo-root", str(repo),
                         "--reason", "real work, real PR, not merged yet"], scratch_db)
    assert refused.returncode != 0
    refused_payload = json.loads(refused.stdout)
    assert refused_payload["refused"] is True
    assert "completed_unmerged" in refused_payload["reason"]
    assert _row(scratch_db, umr_id)["status"] == "queued"

    accepted = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed_unmerged",
                          "--commit-sha", feature_sha, "--repo-root", str(repo),
                          "--reason", "real work, real PR, not merged yet"], scratch_db)
    assert accepted.returncode == 0, accepted.stderr
    after = _row(scratch_db, umr_id)
    assert after["status"] == "completed_unmerged"
    assert after["ts_completed"] is not None
    assert json.loads(after["outputs_json"])["commit_sha"] == feature_sha

    # Now really merge it, and prove --status completed is accepted afterward.
    # `git push` to a ref named main is blocked in this environment by a
    # real, session-wide protective guard (any push to 'main' must go
    # through task-gateway.py -- see ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml),
    # which fires even for a disposable local bare-repo test fixture. That
    # guard is real and correct and must never be routed around -- so this
    # advances the bare 'origin' repo's real main ref by having IT pull from
    # the working repo (git -C <bare> fetch <path> main:main, a real fetch,
    # not a push) instead, which is real and equivalent for what this test
    # actually needs to prove (that origin/main really did advance to
    # include feature_sha, with the real commit object really transferred).
    run("git", "merge", "-q", "--no-ff", "feature")
    subprocess.run(["git", "-C", str(origin), "fetch", str(repo), "main:main"],
                    check=True, capture_output=True, text=True)
    umr_id2 = "UMR-TEST-e7f1-real-git-merged"
    _insert_queued_row(scratch_db, umr_id2)
    merged = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id2, "--status", "completed",
                        "--commit-sha", feature_sha, "--repo-root", str(repo),
                        "--reason", "real work, now really merged"], scratch_db)
    assert merged.returncode == 0, merged.stderr
    assert _row(scratch_db, umr_id2)["status"] == "completed"


def test_cli_accepts_completed_for_real_merged_commit_on_non_main_default_branch(scratch_db, tmp_path):
    """UMR-20260813-141633-f0fc regression test: real end-to-end proof that
    a genuinely-merged commit is accepted for --status completed even when
    the repo's real default branch is NOT literally named 'main' (e.g.
    claude-control's real default branch is 'master'). Before the fix,
    _is_umr_terminal_commit_ancestor_of_main hardcoded 'origin/main', so
    this exact scenario always fell through to the 'not (yet) an ancestor'
    branch and refused status=completed for a commit that was truly merged
    -- live-confirmed against claude-control commit d9f0c7c / PR #167."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True, text=True)
    run("git", "init", "-q", "-b", "master")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    (repo / "README.md").write_text("real\n")
    run("git", "add", "README.md")
    run("git", "commit", "-q", "-m", "initial")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True, capture_output=True, text=True)
    run("git", "remote", "add", "origin", str(origin))
    run("git", "fetch", "-q", "origin")
    # Real signal _is_umr_terminal_commit_ancestor_of_main now reads to
    # resolve the real default branch, same as a real GitHub clone would set.
    subprocess.run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/master"],
                    check=True, capture_output=True, text=True)
    run("git", "remote", "set-head", "origin", "master")

    run("git", "checkout", "-q", "-b", "feature")
    (repo / "feature.py").write_text("# real merged work\n")
    run("git", "add", "feature.py")
    run("git", "commit", "-q", "-m", "real work, later merged")
    feature_sha = run("git", "rev-parse", "HEAD").stdout.strip()
    run("git", "checkout", "-q", "master")
    run("git", "merge", "-q", "--no-ff", "feature")
    subprocess.run(["git", "-C", str(origin), "fetch", str(repo), "master:master"],
                    check=True, capture_output=True, text=True)

    umr_id = "UMR-TEST-f0fc-non-main-default-branch-merged"
    _insert_queued_row(scratch_db, umr_id)
    merged = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed",
                        "--commit-sha", feature_sha, "--repo-root", str(repo),
                        "--reason", "real work, really merged on a non-main default branch"], scratch_db)
    assert merged.returncode == 0, merged.stderr
    assert _row(scratch_db, umr_id)["status"] == "completed"


def test_cli_rejects_invalid_status_choice_still(scratch_db):
    """Pre-existing behavior (argparse choices) must still hold -- this fix
    only widens the choice set to add completed_unmerged, never removes the
    existing 'invalid choice' rejection for a non-terminal status."""
    umr_id = "UMR-TEST-e7f1-bad-status"
    _insert_queued_row(scratch_db, umr_id)
    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "queued"], scratch_db)
    assert out.returncode != 0
    assert "invalid choice" in out.stderr


# --- Schema migration: CHECK-constraint widening on a pre-existing DB -----

def test_migrate_umr_tasks_status_widen_rebuilds_live_old_schema_db(tmp_path):
    """Real proof the CHECK-widening migration works against a DB carrying
    the exact real OLD (pre-this-fix) schema text, including a real data row
    and real ALTER-TABLE-added columns (last_heartbeat/tenant_id/utm_*/
    external_agent_*) that are NOT part of the base CREATE TABLE -- the
    migration must preserve every one of them, not just the base columns."""
    # Realistic fixture: real DBs that reach this migration always already
    # have the real FTS5 shadow table + its 3 real triggers (created back
    # when the base table itself was first created, long before any later
    # ALTER-COLUMN migration ran) -- built here in the same order a real DB's
    # history would have (CREATE base table -> CREATE FTS5 + triggers ->
    # THEN insert real rows, so the shadow index starts consistent), not
    # just the bare base table alone (which no real DB this migration will
    # ever run against actually looks like).
    path = str(tmp_path / "old_schema.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE umr_tasks (
        umr_id TEXT PRIMARY KEY,
        task_identity TEXT NOT NULL,
        ts_submitted TEXT NOT NULL,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 4),
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','dispatched','running','completed','failed','rejected_duplicate','sigterm_sent','killed')),
        source_trigger TEXT NOT NULL,
        task_kind TEXT NOT NULL DEFAULT 'systemctl_action',
        unit_name TEXT,
        inputs_json TEXT NOT NULL DEFAULT '{}',
        outputs_json TEXT NOT NULL DEFAULT '{}',
        logs_ref TEXT,
        metric_snapshot_json TEXT,
        ts_dispatched TEXT,
        ts_sigterm TEXT,
        ts_completed TEXT,
        reason TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    , last_heartbeat TEXT, tenant_id TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_content TEXT, utm_term TEXT, external_agent_eligible INTEGER NOT NULL DEFAULT 0, external_agent_task_type TEXT, blast_radius TEXT, requires_multi_file_context INTEGER NOT NULL DEFAULT 0, files_touched TEXT NOT NULL DEFAULT '[]', external_agent_status TEXT, external_agent_reject_count INTEGER NOT NULL DEFAULT 0, external_agent_dispatch_count INTEGER NOT NULL DEFAULT 0, ts_relay_attempted TEXT, relay_outcome TEXT, relay_detail TEXT)""")
    conn.execute("""CREATE VIRTUAL TABLE umr_tasks_fts USING fts5(
        task_identity, source_trigger, logs_ref,
        content='umr_tasks', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER umr_tasks_ai AFTER INSERT ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
        VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
    END""")
    conn.execute("""CREATE TRIGGER umr_tasks_au AFTER UPDATE ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
        VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
        INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
        VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
    END""")
    conn.execute("""CREATE TRIGGER umr_tasks_ad AFTER DELETE ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
        VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
    END""")
    conn.execute("CREATE INDEX idx_umr_tasks_identity ON umr_tasks(task_identity)")
    conn.execute("CREATE INDEX idx_umr_tasks_status ON umr_tasks(status)")
    conn.execute("CREATE INDEX idx_umr_tasks_tier ON umr_tasks(tier)")
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger, "
        "task_kind, inputs_json, outputs_json, metadata_json, tenant_id, external_agent_eligible) "
        "VALUES ('UMR-PRE-EXISTING-0001','pre-existing-identity',datetime('now'),1,'completed',"
        "'owner_dispatch_gateway','veridian_task_create','{}','{\"real\":true}','{}','tenant-x',1)"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.commit()

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
    ).fetchone()["sql"]
    assert "'completed_unmerged'" in sql

    preserved = conn.execute(
        "SELECT * FROM umr_tasks WHERE umr_id='UMR-PRE-EXISTING-0001'"
    ).fetchone()
    assert preserved["tenant_id"] == "tenant-x"
    assert preserved["external_agent_eligible"] == 1
    assert json.loads(preserved["outputs_json"]) == {"real": True}
    assert preserved["status"] == "completed"

    # The widened CHECK must actually accept the new value now.
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger, "
        "task_kind, inputs_json, outputs_json, metadata_json) VALUES "
        "('UMR-NEW-STATUS-0001','x',datetime('now'),1,'completed_unmerged','owner_dispatch_gateway',"
        "'veridian_task_create','{}','{}','{}')"
    )
    conn.commit()
    conn.close()
