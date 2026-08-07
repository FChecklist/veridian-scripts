#!/usr/bin/env python3
"""Real tests for metadata_reconciliation.py -- the Phase 5 mechanism that
back-references every MASTER_INDEX.yaml registries[] file path into
knowledge_engine as a registry:<id>-tagged row. SPEC: owner absolute
stop-work order, task-20260806-165921, first real test batch for the
~101-script untested gap, UMR-20260806-175915-8f47.

Covers the real, pure path-token-extraction/resolution logic
(extract_file_tokens/resolve_abs -- the actual regex + filesystem-check code
this script runs, exercised against real files, not a reimplementation) and
the real sqlite read helpers (row_exists/all_tagged_paths), monkeypatched
only at the module-level DB_PATH constant to point at a real, isolated,
temp-file SQLite database -- never the live production database.
main()/reconcile(write=True) themselves call out to the live superboss-
register.py CLI as a subprocess and read the live MASTER_INDEX_PATH, so
they are intentionally NOT exercised end-to-end here (that would mutate
production state); the exact real functions that do the file-existence and
path-resolution work are.
"""
import importlib.util
import os
import sqlite3

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# extract_file_tokens' own real regex (PATH_TOKEN_RE) only matches tokens
# rooted at /opt/veridian/... (or bare ai-os/scripts/repos/ prefixes) -- a
# worktree checkout of this repo can legitimately live outside /opt/veridian
# (this task's own coordination note requires it), so these tests use a
# real file guaranteed to exist at its real, fixed, production path
# regardless of which checkout is running the test, never a reimplemented
# stand-in path.
REAL_EXISTING_FILE = "/opt/veridian/scripts/superboss-register.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "metadata_reconciliation_test", os.path.join(SCRIPTS_DIR, "metadata_reconciliation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_abs_passes_through_already_absolute_paths():
    mod = _load()
    assert mod.resolve_abs("/opt/veridian/scripts/foo.py") == "/opt/veridian/scripts/foo.py"


def test_resolve_abs_prefixes_relative_tokens():
    mod = _load()
    assert mod.resolve_abs("scripts/foo.py") == "/opt/veridian/scripts/foo.py"
    assert mod.resolve_abs("ai-os/MASTER_INDEX.yaml") == "/opt/veridian/ai-os/MASTER_INDEX.yaml"


def test_extract_file_tokens_finds_a_real_existing_file():
    mod = _load()
    path_field = f"See {REAL_EXISTING_FILE} for details."
    file_hits, non_file = mod.extract_file_tokens(path_field)
    resolved_paths = [h["resolved"] for h in file_hits]
    assert REAL_EXISTING_FILE in resolved_paths
    assert non_file == []


def test_extract_file_tokens_reports_not_found_on_disk():
    mod = _load()
    path_field = "/opt/veridian/scripts/definitely_does_not_exist_metadata_reconciliation_test.py"
    file_hits, non_file = mod.extract_file_tokens(path_field)
    assert file_hits == []
    assert len(non_file) == 1
    assert non_file[0]["reason"] == "not_found_on_disk"
    assert non_file[0]["resolved"] == path_field


def test_extract_file_tokens_reports_directory_not_registered():
    mod = _load()
    path_field = "/opt/veridian/scripts"
    file_hits, non_file = mod.extract_file_tokens(path_field)
    assert file_hits == []
    assert len(non_file) == 1
    assert non_file[0]["reason"] == "directory_not_registered"


def test_extract_file_tokens_empty_path_field_returns_empty():
    mod = _load()
    assert mod.extract_file_tokens(None) == ([], [])
    assert mod.extract_file_tokens("") == ([], [])


def test_extract_file_tokens_handles_sqlite_table_suffix():
    mod = _load()
    path_field = f"{REAL_EXISTING_FILE}#some_table"
    file_hits, non_file = mod.extract_file_tokens(path_field)
    assert len(file_hits) == 1
    assert file_hits[0]["resolved"] == REAL_EXISTING_FILE
    assert file_hits[0]["sqlite_table"] == "some_table"


def test_row_exists_and_all_tagged_paths_against_real_isolated_db(tmp_path):
    mod = _load()
    db_path = str(tmp_path / "scratch.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE knowledge_engine (
            artifact_id TEXT PRIMARY KEY, ts TEXT, artifact_path TEXT NOT NULL,
            content_hash TEXT, artifact_type TEXT, secondary_path TEXT,
            exists_on_disk INTEGER DEFAULT 1, purpose TEXT, tags TEXT,
            entity_relationships TEXT DEFAULT '[]', last_verified_ts TEXT,
            verification_status TEXT DEFAULT 'UNVERIFIED', metadata_json TEXT DEFAULT '{}'
        )"""
    )
    conn.execute(
        "INSERT INTO knowledge_engine (artifact_id, ts, artifact_path, purpose, tags, last_verified_ts) "
        "VALUES ('a1', '2026-08-06T00:00:00Z', '/opt/veridian/scripts/tagged.py', 'p', "
        "'[\"registry:foo\"]', '2026-08-06T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO knowledge_engine (artifact_id, ts, artifact_path, purpose, tags, last_verified_ts) "
        "VALUES ('a2', '2026-08-06T00:00:00Z', '/opt/veridian/scripts/orphan.py', 'p', "
        "'[]', '2026-08-06T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    mod.DB_PATH = db_path
    assert mod.row_exists("/opt/veridian/scripts/tagged.py") is True
    assert mod.row_exists("/opt/veridian/scripts/never-registered.py") is False

    rows = mod.all_tagged_paths()
    paths = {r[0] for r in rows}
    assert paths == {"/opt/veridian/scripts/tagged.py", "/opt/veridian/scripts/orphan.py"}


def test_row_exists_false_when_db_file_absent(tmp_path):
    mod = _load()
    mod.DB_PATH = str(tmp_path / "does-not-exist.sqlite")
    assert mod.row_exists("/opt/veridian/scripts/anything.py") is False
    assert mod.all_tagged_paths() == []


if __name__ == "__main__":
    test_resolve_abs_passes_through_already_absolute_paths()
    test_resolve_abs_prefixes_relative_tokens()
    test_extract_file_tokens_finds_a_real_existing_file()
    test_extract_file_tokens_reports_not_found_on_disk()
    test_extract_file_tokens_reports_directory_not_registered()
    test_extract_file_tokens_empty_path_field_returns_empty()
    test_extract_file_tokens_handles_sqlite_table_suffix()
    print("PASS: core metadata_reconciliation.py smoke tests (run pytest for full tmp_path-based coverage)")
