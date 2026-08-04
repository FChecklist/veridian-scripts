#!/usr/bin/env python3
"""Real tests for resolve_superboss_db_path() (OCID-068 real requirement,
Owner directive UMR-20260804-180210-9e2c, following UMR-20260804-170055-a069,
itself under OCID-068's own UMR-20260804-164106-3fb8). Covers the three
required real failure paths (missing file, zero-byte file, file missing the
umr_tasks table) plus the real success path against the actual live
database. Imports superboss-register.py via the same importlib-by-file-path
pattern resource_governor.py's own _superboss_register() uses (the filename
has a hyphen, so it cannot be a plain `import`).

Failure-path tests use the function's own `default_path` testability
parameter (see its docstring) rather than SUPERBOSS_REGISTER_DB, because the
real 5-step algorithm's own step 2 pre-check (env path must exist AND be
non-zero size) means an env override pointing at a missing/empty file
silently falls through to the real, valid default and succeeds -- that is
correct, real behavior (a bad override never bricks the system), not a
failure path. Exercising steps 4/5's actual verification failures requires
injecting the bad candidate directly, which is exactly what default_path is
for -- this never touches the real, live production database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "superboss_register_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_file():
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        missing_path = os.path.join(d, "does-not-exist.sqlite")
        try:
            sbr.resolve_superboss_db_path(default_path=missing_path)
            assert False, "expected SuperbossDbPathError for a missing file"
        except sbr.SuperbossDbPathError as e:
            msg = str(e)
            assert "file exists" in msg, msg
            assert missing_path in msg, msg
            print("PASS: test_missing_file ->", msg)


def test_zero_byte_file():
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        zero_path = os.path.join(d, "zero-byte.sqlite")
        open(zero_path, "wb").close()
        assert os.path.getsize(zero_path) == 0
        try:
            sbr.resolve_superboss_db_path(default_path=zero_path)
            assert False, "expected SuperbossDbPathError for a zero-byte file"
        except sbr.SuperbossDbPathError as e:
            msg = str(e)
            assert "non-zero size" in msg, msg
            assert "size_found=0" in msg, msg
            print("PASS: test_zero_byte_file ->", msg)


def test_real_stale_decoy_file_still_fails():
    """Independently re-confirms the specific real, known decoy path the
    Owner directive named stays rejected -- not a synthetic fixture, the
    real file on this server, injected via default_path (not
    SUPERBOSS_REGISTER_DB, since the env-override pre-check would just skip
    a zero-byte override and fall through to the good default instead --
    this directly exercises step 4's own rejection of that exact real path,
    matching the directive's literal "must keep failing against [this path]"
    requirement)."""
    real_decoy = "/opt/veridian/ai-os/superboss-register.sqlite"
    if not os.path.exists(real_decoy):
        print("SKIP: test_real_stale_decoy_file_still_fails -> real decoy path not present on this host")
        return
    assert os.path.getsize(real_decoy) == 0, f"expected the known decoy to still be 0 bytes, got {os.path.getsize(real_decoy)}"
    sbr = _load_module()
    try:
        sbr.resolve_superboss_db_path(default_path=real_decoy)
        assert False, "expected SuperbossDbPathError for the real known stale decoy file"
    except sbr.SuperbossDbPathError as e:
        msg = str(e)
        assert "non-zero size" in msg, msg
        assert real_decoy in msg, msg
        print("PASS: test_real_stale_decoy_file_still_fails ->", msg)


def test_missing_umr_tasks_table():
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        wrong_db_path = os.path.join(d, "wrong-schema.sqlite")
        conn = sqlite3.connect(wrong_db_path)
        conn.execute("CREATE TABLE some_other_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        assert os.path.getsize(wrong_db_path) > 0
        with open(wrong_db_path, "rb") as f:
            assert f.read(16) == b"SQLite format 3\x00"
        try:
            sbr.resolve_superboss_db_path(default_path=wrong_db_path)
            assert False, "expected SuperbossDbPathError for a real SQLite file with no umr_tasks table"
        except sbr.SuperbossDbPathError as e:
            msg = str(e)
            assert "umr_tasks table" in msg, msg
            print("PASS: test_missing_umr_tasks_table ->", msg)


def test_non_sqlite_file_rejected():
    """Real check (c) from the spec: a non-SQLite file must be rejected by
    its header, not just accidentally caught by the table check."""
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        fake_path = os.path.join(d, "not-a-database.sqlite")
        with open(fake_path, "wb") as f:
            f.write(b"this is definitely not a real sqlite file, just plain bytes")
        try:
            sbr.resolve_superboss_db_path(default_path=fake_path)
            assert False, "expected SuperbossDbPathError for a non-SQLite file"
        except sbr.SuperbossDbPathError as e:
            msg = str(e)
            assert "SQLite file header magic bytes" in msg, msg
            print("PASS: test_non_sqlite_file_rejected ->", msg)


def test_env_override_success_path():
    """Real success path via step 2: a valid SUPERBOSS_REGISTER_DB override
    pointing at a real, non-empty, correctly-schemad database is accepted in
    preference to the (unrelated, also-valid) default_path."""
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        valid_path = os.path.join(d, "valid-override.sqlite")
        # _connect() always targets module-level DB_PATH, not an arbitrary
        # path, so build the fixture directly with the real row_factory
        # convention _connect() itself uses instead of calling it.
        conn = sqlite3.connect(valid_path)
        conn.row_factory = sqlite3.Row
        sbr._ensure_umr_table(conn)
        conn.close()
        os.environ["SUPERBOSS_REGISTER_DB"] = valid_path
        try:
            resolved = sbr.resolve_superboss_db_path()
            assert resolved == valid_path, resolved
            print("PASS: test_env_override_success_path ->", resolved)
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]


def test_env_override_missing_falls_back_to_default():
    """Real, correct step-2/3 behavior: an env override pointing at a
    missing file does NOT raise -- it silently falls back to a valid
    default_path candidate instead (a bad override must never brick the
    system when a good default is available)."""
    sbr = _load_module()
    with tempfile.TemporaryDirectory() as d:
        missing_override = os.path.join(d, "nope.sqlite")
        good_default = os.path.join(d, "good-default.sqlite")
        conn = sqlite3.connect(good_default)
        conn.row_factory = sqlite3.Row
        sbr._ensure_umr_table(conn)
        conn.close()
        os.environ["SUPERBOSS_REGISTER_DB"] = missing_override
        try:
            resolved = sbr.resolve_superboss_db_path(default_path=good_default)
            assert resolved == good_default, resolved
            print("PASS: test_env_override_missing_falls_back_to_default ->", resolved)
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]


def test_real_success_path_against_live_database():
    """The real success path: no SUPERBOSS_REGISTER_DB override, resolves
    to the real, live, actual production database on this server, and every
    verification check genuinely passes against it (not a fixture)."""
    if "SUPERBOSS_REGISTER_DB" in os.environ:
        del os.environ["SUPERBOSS_REGISTER_DB"]
    sbr = _load_module()
    resolved = sbr.resolve_superboss_db_path()
    assert resolved == "/opt/veridian/ai-os/memory/superboss-register.sqlite", resolved
    assert os.path.exists(resolved)
    assert os.path.getsize(resolved) > 0
    conn = sqlite3.connect(resolved)
    row = conn.execute("SELECT count(*) FROM umr_tasks").fetchone()
    conn.close()
    assert row[0] > 0, "expected real, non-empty umr_tasks table"
    assert sbr.DB_PATH == resolved, "module-level DB_PATH must equal the resolved path"
    print(f"PASS: test_real_success_path_against_live_database -> resolved={resolved}, real umr_tasks row count={row[0]}")


if __name__ == "__main__":
    tests = [
        test_missing_file,
        test_zero_byte_file,
        test_real_stale_decoy_file_still_fails,
        test_missing_umr_tasks_table,
        test_non_sqlite_file_rejected,
        test_env_override_success_path,
        test_env_override_missing_falls_back_to_default,
        test_real_success_path_against_live_database,
    ]
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
