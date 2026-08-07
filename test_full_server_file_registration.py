"""Real tests for full_server_file_registration.py.

The module under test hardcodes several REAL production paths as either
module-level constants (REPOS_ROOT, SCRIPTS_DIR, VERIDIAN_ROOT, BACKUPS_DIR)
or literal strings inside scan_roots() ("/opt/veridian/ai-os",
"/opt/veridian/scripts", "/opt/veridian/shared") with zero CLI/env override
seam, and its write_lock() wrapper delegates to file_inventory.py's own
_write_lock(), which *also* hardcodes a live lock-file path
(/opt/veridian/ai-os/memory/superboss-register.sqlite.writelock) with no
override. To exercise the REAL run()/completion_check()/list_files() logic
without ever touching the live filesystem, live DB, or live lock file, these
tests:

  - load the real module via importlib (spec_from_file_location, same
    convention the module's own _load() helper and the house style in this
    test suite already use) and monkeypatch its module-level *attributes*
    (mod.REPOS_ROOT, mod.scan_roots, mod._finv, mod._sbr) at runtime -- this
    changes nothing on disk; the tracked .py file is never edited or
    overwritten;
  - point SUPERBOSS_REGISTER_DB at a fresh tmp_path sqlite file (bootstrapped
    with a minimal umr_tasks table first, since resolve_superboss_db_path()
    only honors the env override once the target path already exists and is
    non-empty -- same trap/pattern documented in test_ai_agent_registry.py)
    and verify mod.sbr().DB_PATH actually resolved to that tmp path before
    doing anything else, so a bug in this setup can never silently fall back
    to writing the live production DB;
  - replace mod._finv with a tiny fake object whose _write_lock() flocks a
    tmp_path file instead of file_inventory.py's hardcoded live path -- this
    is the "true external boundary" stub sanctioned by the task brief
    (avoiding a real flock() syscall against a live production lock file
    used by the real dispatch/cron system), not a stub of any of the
    module's own real scanning/hashing/dedup logic, all of which runs for
    real, against real tmp_path files, in every test below.

Never touches /opt/veridian/ai-os/memory/superboss-register.sqlite or any
other live path.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(HERE, "full_server_file_registration.py")
SUPERBOSS = os.path.join(HERE, "superboss-register.py")


def _bootstrap_and_point_env_at_tmp_db(db_path):
    """Same bootstrap trick test_ai_agent_registry.py's own docstring
    documents: resolve_superboss_db_path() only honors SUPERBOSS_REGISTER_DB
    once the named path already exists, is non-zero size, and already
    contains a real umr_tasks table -- a brand-new tempfile path fails all
    three, so without this stub bootstrap the module would silently fall
    back to the real, live production DB."""
    conn = sqlite3.connect(db_path)
    # Includes task_identity (referenced by a real CREATE INDEX in
    # _ensure_umr_table()'s slow path) and the literal 'completed_unmerged'
    # status value (checked via a raw sqlite_master.sql text search) so the
    # real _ensure_umr_table() fast path (all 5 gate columns present AND
    # status_migrated) returns immediately without running its slow-path
    # CREATE INDEX against a stub missing that column.
    conn.execute("""CREATE TABLE umr_tasks (
        umr_id TEXT PRIMARY KEY,
        task_identity TEXT,
        status TEXT DEFAULT 'queued' CHECK(status IN (
            'queued','dispatched','running','completed','completed_unmerged',
            'failed','rejected_duplicate','sigterm_sent','killed')),
        last_heartbeat TEXT,
        tenant_id TEXT,
        utm_source TEXT,
        external_agent_eligible INTEGER,
        ts_relay_attempted TEXT
    )""")
    conn.commit()
    conn.close()
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path


class _FakeFinv:
    """Stand-in for the real file_inventory.py module, used only to replace
    its hardcoded-live-path _write_lock() with one that flocks a tmp file.
    Nothing else in full_server_file_registration.py calls finv() for
    anything but write_lock()."""

    def __init__(self, lock_path):
        self._lock_path = lock_path

    @contextlib.contextmanager
    def _write_lock(self):
        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        with open(self._lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


@pytest.fixture
def sut(tmp_path, monkeypatch):
    """Loads a fresh copy of the real module per test, wired to a tmp DB and
    a tmp lock file. Runs a real `superboss-register.py init` subprocess
    first (against the same tmp DB) so every real table (instructions,
    wiring_registry, etc.) actually exists before init_db_silent()/run()
    touch it."""
    db_path = str(tmp_path / "test.sqlite")
    _bootstrap_and_point_env_at_tmp_db(db_path)

    env = dict(os.environ)
    proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"superboss-register.py init failed: {proc.stderr}"

    spec = importlib.util.spec_from_file_location("full_server_file_registration_test_mod", SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    resolved = mod.sbr().DB_PATH
    assert os.path.realpath(resolved) == os.path.realpath(db_path), (
        f"SUT resolved DB_PATH={resolved!r}, expected tmp db {db_path!r} -- refusing to "
        "proceed, would risk touching the live production DB"
    )

    mod._finv = _FakeFinv(str(tmp_path / "fake.writelock"))

    # GENUINE BUG WORKAROUND (see test_init_db_fresh_db_is_missing_vector_columns_bug
    # below for the regression test documenting this): a freshly `init`-ed DB is
    # missing wiring_registry.vector_json/vector_updated_ts even though
    # _migrate_schema() -> _migrate_wiring_registry_vector() is supposed to add
    # them, because _migrate_wiring_registry_entity_types() (called earlier in the
    # same _migrate_schema()) rebuilds wiring_registry via its own separate
    # CREATE TABLE that omits those two columns, and nothing re-runs the vector
    # migration after that rebuild. Calling the real migration function again
    # here (not raw SQL, not a script-under-test modification) mirrors what a
    # second real `init`/write-path call already does against a real
    # already-migrated production DB, and unblocks register_entity_row()'s own
    # unconditional vector_json UPDATE for every test below.
    conn = mod.sbr()._connect()
    mod.sbr()._migrate_wiring_registry_vector(conn)
    conn.commit()
    conn.close()

    yield mod, tmp_path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# content_hash_of() -- pure function, real files, real error handling
# ---------------------------------------------------------------------------

def test_content_hash_of_real_file_matches_real_sha256(sut, tmp_path):
    mod, _ = sut
    f = tmp_path / "hashme.txt"
    f.write_bytes(b"some real bytes to hash, not a fixture placeholder")
    assert mod.content_hash_of(str(f)) == _sha256(f.read_bytes())


def test_content_hash_of_nonexistent_file_returns_none(sut, tmp_path):
    mod, _ = sut
    missing = str(tmp_path / "does_not_exist.bin")
    assert mod.content_hash_of(missing) is None


def test_content_hash_of_permission_denied_returns_none(sut, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 does not deny root read access")
    mod, _ = sut
    f = tmp_path / "secret.bin"
    f.write_bytes(b"cannot read this")
    f.chmod(0o000)
    try:
        assert mod.content_hash_of(str(f)) is None
    finally:
        f.chmod(0o644)


# ---------------------------------------------------------------------------
# real_top_level_repos() -- real directory-listing logic, tmp REPOS_ROOT
# ---------------------------------------------------------------------------

def test_real_top_level_repos_filters_hidden_worktree_and_non_git_dirs(sut, tmp_path):
    mod, _ = sut
    repos_root = tmp_path / "repos"
    repos_root.mkdir()

    real_repo = repos_root / "veridian-scripts"
    real_repo.mkdir()
    (real_repo / ".git").mkdir()

    worktree_clone = repos_root / "veridian-scripts-loadstress-wt"
    worktree_clone.mkdir()
    (worktree_clone / ".git").write_text("gitdir: /somewhere/else\n")

    hidden = repos_root / ".hidden-repo"
    hidden.mkdir()
    (hidden / ".git").mkdir()

    not_a_repo = repos_root / "not_a_repo"
    not_a_repo.mkdir()
    (not_a_repo / "README.md").write_text("no .git here\n")

    a_plain_file = repos_root / "some_file.txt"
    a_plain_file.write_text("not even a directory\n")

    mod.REPOS_ROOT = str(repos_root)
    result = mod.real_top_level_repos()

    assert result == [str(real_repo)]


def test_real_top_level_repos_accepts_git_as_worktree_file_not_only_dir(sut, tmp_path):
    """A worktree checkout's own .git is a FILE (contains 'gitdir: ...'),
    not a directory -- the function's own docstring says this must still
    qualify as long as the dirname doesn't end in '-wt'."""
    mod, _ = sut
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    repo = repos_root / "some-worktree-checkout"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /opt/veridian/repos/some/.git/worktrees/x\n")

    mod.REPOS_ROOT = str(repos_root)
    assert mod.real_top_level_repos() == [str(repo)]


# ---------------------------------------------------------------------------
# make_entity_id() -- deterministic id + real collision widening
# ---------------------------------------------------------------------------

def test_make_entity_id_deterministic_for_same_path(sut):
    mod, _ = sut
    eid1 = mod.make_entity_id("/opt/veridian/scripts/foo.py", {})
    eid2 = mod.make_entity_id("/opt/veridian/scripts/foo.py", {})
    assert eid1 == eid2
    assert eid1 == f"file-{hashlib.sha1(b'/opt/veridian/scripts/foo.py').hexdigest()[:12]}"


def test_make_entity_id_reuses_id_when_existing_ids_has_same_path(sut):
    mod, _ = sut
    path = "/opt/veridian/scripts/foo.py"
    eid = mod.make_entity_id(path, {})
    existing = {eid: path}
    assert mod.make_entity_id(path, existing) == eid


def test_make_entity_id_widens_hash_on_real_collision(sut):
    """Simulates a genuine entity_id collision (same 12-hex prefix claimed by
    a DIFFERENT real path already in existing_ids) and proves the function
    deterministically widens to a longer, unique prefix rather than ever
    returning a colliding id."""
    mod, _ = sut
    path_a = "/opt/veridian/scripts/foo.py"
    path_b = "/some/totally/different/path.py"
    colliding_id = mod.make_entity_id(path_a, {})  # e.g. file-<12hex of path_a>
    existing = {colliding_id: path_b}  # claimed by a DIFFERENT path

    widened = mod.make_entity_id(path_a, existing)
    assert widened != colliding_id
    assert widened.startswith("file-")
    assert len(widened) == len("file-") + 16  # widened from 12 to 16 hex chars
    assert widened == f"file-{hashlib.sha1(path_a.encode()).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# repo_relationship_for()
# ---------------------------------------------------------------------------

def test_repo_relationship_for_file_inside_repo_links_to_known_github_repo_entity(sut, tmp_path):
    mod, _ = sut
    repos_root = tmp_path / "repos"
    mod.REPOS_ROOT = str(repos_root)
    abs_path = str(repos_root / "veridian-scripts" / "sub" / "file.py")

    rel = mod.repo_relationship_for(abs_path, {"veridian-scripts": "github_repo-abc123"})
    assert rel["relationship_type"] == "belongs_to_repo"
    assert rel["target_entity_id"] == "github_repo-abc123"
    assert rel["evidence"] == str(repos_root / "veridian-scripts")


def test_repo_relationship_for_file_inside_repo_unknown_github_entity_has_no_target(sut, tmp_path):
    mod, _ = sut
    repos_root = tmp_path / "repos"
    mod.REPOS_ROOT = str(repos_root)
    abs_path = str(repos_root / "unregistered-repo" / "file.py")

    rel = mod.repo_relationship_for(abs_path, {})
    assert rel["relationship_type"] == "belongs_to_repo"
    assert rel["target_entity_id"] is None
    assert rel["evidence"] == str(repos_root / "unregistered-repo")


def test_repo_relationship_for_file_outside_repos_root_uses_belongs_to_dir(sut, tmp_path):
    mod, _ = sut
    mod.REPOS_ROOT = str(tmp_path / "repos")
    abs_path = str(tmp_path / "ai-os" / "sub" / "file.py")

    rel = mod.repo_relationship_for(abs_path, {})
    assert rel["relationship_type"] == "belongs_to_dir"
    assert rel["target_entity_id"] is None
    assert rel["evidence"] == str(tmp_path / "ai-os" / "sub")


# ---------------------------------------------------------------------------
# list_files() -- real `find` subprocess, real pruning, tmp scan root
# ---------------------------------------------------------------------------

def test_list_files_real_find_prunes_node_modules_git_pycache_and_live_db_glob(sut, tmp_path, monkeypatch):
    mod, _ = sut
    root = tmp_path / "scan_root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "keep.py").write_text("print('keep')\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "ignored.js").write_text("ignored\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("ignored\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "cached.pyc").write_bytes(b"ignored")
    (root / "ai-os" / "memory").mkdir(parents=True)
    (root / "ai-os" / "memory" / "superboss-register.sqlite").write_bytes(b"ignored live db")

    monkeypatch.setattr(mod, "scan_roots", lambda: [str(root)])
    files = mod.list_files()

    assert str(root / "sub" / "keep.py") in files
    assert not any("node_modules" in f for f in files)
    assert not any("/.git/" in f for f in files)
    assert not any("__pycache__" in f for f in files)
    assert not any("superboss-register.sqlite" in f for f in files)
    assert files == sorted(set(files))  # sorted + deduped, per its own contract


# ---------------------------------------------------------------------------
# run() + completion_check() end to end, against real tmp files + real tmp DB
# ---------------------------------------------------------------------------

def test_run_registers_distinct_content_dedupes_and_links_repo_relationship(sut, tmp_path, monkeypatch):
    mod, _ = sut
    repos_root = tmp_path / "repos"
    (repos_root / "myrepo").mkdir(parents=True)
    (repos_root / "myrepo" / ".git").mkdir()
    (repos_root / "myrepo" / "app.py").write_text("print('app')\n")

    # Named to sort AFTER "repos" lexicographically -- list_files() returns a
    # sorted, deduped path list, and the FIRST-seen path for a given content
    # hash becomes the canonical registered entity (see run()'s own dedup
    # logic), so this ordering is required for app.py (inside the repo) to
    # be the canonical row and app_copy.py to be the recorded duplicate.
    other_root = tmp_path / "zz_other"
    other_root.mkdir()
    (other_root / "notes.txt").write_text("some real notes content\n")
    # duplicate CONTENT of app.py, at a completely different path/root
    (other_root / "app_copy.py").write_text("print('app')\n")

    mod.REPOS_ROOT = str(repos_root)
    monkeypatch.setattr(mod, "scan_roots", lambda: [str(repos_root), str(other_root)])

    result = mod.run(do_backup=False)

    assert result["files_found"] == 3
    assert result["newly_registered"] == 2  # app.py + notes.txt (distinct content)
    assert result["skipped_duplicate"] == 1  # app_copy.py, same content as app.py
    assert result["backfilled_legacy_content_hash"] == 0
    assert result["unreadable_errors"] == []

    conn = mod.sbr()._connect()
    rows = conn.execute(
        "SELECT entity_id, path, content_hash, relationships FROM wiring_registry WHERE entity_type='file'"
    ).fetchall()
    conn.close()
    assert len(rows) == 2

    by_path = {r["path"]: r for r in rows}
    app_row = by_path[str(repos_root / "myrepo" / "app.py")]
    assert app_row["content_hash"] == _sha256(b"print('app')\n")
    rels = json.loads(app_row["relationships"])
    assert rels[0]["relationship_type"] == "belongs_to_repo"
    assert rels[0]["evidence"] == str(repos_root / "myrepo")
    # the second physical path with identical content must be recorded as a
    # duplicate-content relationship on the SAME entity, not a new entity
    assert any(r.get("relationship_type") == "duplicate_content_at_path"
               and r.get("evidence") == str(other_root / "app_copy.py") for r in rels)

    notes_row = by_path[str(other_root / "notes.txt")]
    assert notes_row["content_hash"] == _sha256(b"some real notes content\n")


def test_run_is_idempotent_on_second_invocation(sut, tmp_path, monkeypatch):
    mod, _ = sut
    root = tmp_path / "scan"
    root.mkdir()
    (root / "a.py").write_text("content a\n")
    (root / "b.py").write_text("content b\n")
    mod.REPOS_ROOT = str(tmp_path / "repos")
    monkeypatch.setattr(mod, "scan_roots", lambda: [str(root)])

    first = mod.run(do_backup=False)
    assert first["newly_registered"] == 2
    assert first["skipped_duplicate"] == 0

    second = mod.run(do_backup=False)
    assert second["newly_registered"] == 0
    assert second["backfilled_legacy_content_hash"] == 0
    assert second["skipped_duplicate"] == 2

    conn = mod.sbr()._connect()
    count = conn.execute("SELECT COUNT(*) c FROM wiring_registry WHERE entity_type='file'").fetchone()["c"]
    conn.close()
    assert count == 2  # no duplicate entities created by the second run


def test_run_backfills_legacy_row_with_same_deterministic_entity_id_and_null_hash(sut, tmp_path, monkeypatch):
    """Simulates the documented legacy-row case: a wiring_registry file-entity
    row already exists at the SAME deterministic entity_id/path (e.g. created
    by an earlier generation of tooling) with content_hash still NULL. A real
    run() must backfill that row in place (content_hash moves from NULL to
    real) rather than creating a second entity_id for the same path."""
    mod, _ = sut
    root = tmp_path / "scan"
    root.mkdir()
    target = root / "legacy.py"
    target.write_text("legacy content\n")
    mod.REPOS_ROOT = str(tmp_path / "repos")
    monkeypatch.setattr(mod, "scan_roots", lambda: [str(root)])

    legacy_eid = mod.make_entity_id(str(target), {})
    conn = mod.sbr()._connect()
    mod.sbr()._ensure_wiring_registry_table(conn)
    mod.sbr().register_entity_row(conn, {
        "entity_id": legacy_eid,
        "entity_type": "file",
        "source_system": "server",
        "path": str(target),
        "relationships": [],
        "last_verified_ts": "2020-01-01T00:00:00+00:00",
        "verification_status": "UNVERIFIED",
        "source_ref": ["legacy_tool"],
        "content_hash": None,
    })
    conn.commit()
    conn.close()

    result = mod.run(do_backup=False)
    assert result["backfilled_legacy_content_hash"] == 1
    assert result["newly_registered"] == 0

    conn = mod.sbr()._connect()
    row = conn.execute(
        "SELECT entity_id, content_hash FROM wiring_registry WHERE path=?", (str(target),)
    ).fetchone()
    conn.close()
    assert row["entity_id"] == legacy_eid  # same entity_id, not a new one
    assert row["content_hash"] == _sha256(b"legacy content\n")


def test_run_records_unreadable_file_as_error_not_a_crash(sut, tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 does not deny root read access")
    mod, _ = sut
    root = tmp_path / "scan"
    root.mkdir()
    good = root / "good.py"
    good.write_text("good content\n")
    bad = root / "bad.py"
    bad.write_text("unreadable\n")
    bad.chmod(0o000)
    mod.REPOS_ROOT = str(tmp_path / "repos")
    monkeypatch.setattr(mod, "scan_roots", lambda: [str(root)])

    try:
        result = mod.run(do_backup=False)
    finally:
        bad.chmod(0o644)

    assert result["newly_registered"] == 1  # only good.py
    assert str(bad) in result["unreadable_errors"]


def test_completion_check_reports_real_breakdown_of_null_hash_remainder(sut, tmp_path, monkeypatch):
    mod, _ = sut
    root = tmp_path / "scan"
    root.mkdir()
    (root / "real.py").write_text("real content\n")
    mod.REPOS_ROOT = str(tmp_path / "repos")
    monkeypatch.setattr(mod, "scan_roots", lambda: [str(root)])
    mod.run(do_backup=False)

    conn = mod.sbr()._connect()
    mod.sbr().register_entity_row(conn, {
        "entity_id": "file-deadbeef0001",
        "entity_type": "file",
        "source_system": "server",
        "path": "/opt/veridian/repos/somerepo/.git/objects/ab/cdef",
        "relationships": [],
        "last_verified_ts": "2020-01-01T00:00:00+00:00",
        "verification_status": "UNVERIFIED",
        "source_ref": ["legacy"],
        "content_hash": None,
    })
    mod.sbr().register_entity_row(conn, {
        "entity_id": "file-deadbeef0002",
        "entity_type": "file",
        "source_system": "server",
        "path": "/opt/veridian/scripts/gone_missing.py",
        "relationships": [],
        "last_verified_ts": "2020-01-01T00:00:00+00:00",
        "verification_status": "PATH_MISSING",
        "source_ref": ["legacy"],
        "content_hash": None,
    })
    mod.sbr().register_entity_row(conn, {
        "entity_id": "file-deadbeef0003",
        "entity_type": "file",
        "source_system": "server",
        "path": "/home/rajat/.local/bin/somefile",
        "relationships": [],
        "last_verified_ts": "2020-01-01T00:00:00+00:00",
        "verification_status": "UNVERIFIED",
        "source_ref": ["legacy"],
        "content_hash": None,
    })
    conn.commit()
    conn.close()

    check = mod.completion_check()
    assert check["total_entity_type_file_rows"] == 4  # real.py + 3 injected legacy rows
    assert check["rows_with_null_or_empty_content_hash"] == 3
    assert check["remainder_breakdown"]["inside_git"] == 1
    assert check["remainder_breakdown"]["path_missing"] == 1
    assert check["remainder_breakdown"]["outside_scanned_roots"] == 1
    assert check["remainder_breakdown"]["other"] == 0


# ---------------------------------------------------------------------------
# CLI entry point -- --completion-check is safe to invoke as a REAL subprocess
# (does no scanning and takes no write_lock, unlike a plain/--backup-first
# run, so it never touches any hardcoded live path).
# ---------------------------------------------------------------------------

def test_init_db_fresh_db_is_missing_vector_columns_bug(tmp_path):
    """GENUINE BUG (in superboss-register.py, reused as a dependency of
    full_server_file_registration.py, not in either of this task's 3
    assigned scripts): a brand-new DB created via `superboss-register.py
    init` is left WITHOUT wiring_registry.vector_json/vector_updated_ts,
    even though init_db() -> _migrate_schema() -> _migrate_wiring_registry_vector()
    is supposed to guarantee both columns exist on every DB, fresh or old.
    Root cause: _migrate_schema() calls _migrate_wiring_registry_entity_types()
    BEFORE _migrate_wiring_registry_vector() (see superboss-register.py's own
    _migrate_schema(), lines ~741-754); entity_types migration rebuilds
    wiring_registry via its own separate CREATE TABLE (the
    `wiring_registry__migrate` rename-and-rebuild path) that never got updated
    to include vector_json/vector_updated_ts, and nothing re-checks for those
    two columns after that rebuild. Net effect: the FIRST real write any
    caller makes via register_entity_row() against a freshly-initialized DB
    raises `sqlite3.OperationalError: no such column: vector_json` (this is
    exactly what a bare `full_server_file_registration.py` run against a
    truly brand-new DB would hit). The real, long-lived production DB is
    unaffected only because it was already migrated in place before this
    entity_types-rebuild-vs-vector-migration ordering bug was introduced."""
    db_path = str(tmp_path / "bug_repro.sqlite")
    _bootstrap_and_point_env_at_tmp_db(db_path)
    env = dict(os.environ)

    init_proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True, env=env)
    assert init_proc.returncode == 0, init_proc.stderr

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
    conn.close()
    # Documents the real, currently-reproducible bug: this SHOULD be an empty
    # set (both columns present) on a real, correctly-migrated DB, but isn't.
    assert {"vector_json", "vector_updated_ts"} - cols == {"vector_json", "vector_updated_ts"}, (
        "if this assertion starts failing, the real bug has been fixed upstream "
        "in superboss-register.py and the fixture workaround above can be removed"
    )


def test_cli_completion_check_subprocess_prints_valid_json_against_tmp_db(tmp_path):
    db_path = str(tmp_path / "cli_test.sqlite")
    _bootstrap_and_point_env_at_tmp_db(db_path)
    env = dict(os.environ)

    init_proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True, env=env)
    assert init_proc.returncode == 0, init_proc.stderr

    proc = subprocess.run(
        ["python3", SUT_PATH, "--completion-check"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    parsed = json.loads(proc.stdout)
    assert parsed["total_entity_type_file_rows"] == 0
    assert parsed["rows_with_null_or_empty_content_hash"] == 0
    assert parsed["remainder_breakdown"] == {
        "inside_git": 0, "path_missing": 0, "outside_scanned_roots": 0, "other": 0,
    }
