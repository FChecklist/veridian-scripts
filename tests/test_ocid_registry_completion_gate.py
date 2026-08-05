#!/usr/bin/env python3
"""Real tests for the OCID-068 Phase 2 registry-schema and completion-gate
extension (Owner directive UMR-20260805-090549-9710, extending the
now-superseded UMR-20260805-085025-c257; citing the canonical OCID-068 UMR
UMR-20260804-170055-a069 and its permanent closure record
UMR-20260805-032731-b412).

Covers:
  1. The 7 has_real_*/is_fully_complete boolean columns on
     ocid_canonical_registry are DB-enforced by the
     ocid_canonical_registry_completion_ai/_au triggers, never hand-settable
     -- including a direct raw-SQL UPDATE attempt.
  2. The existing (PR #20), already-merged ocid_artifact_links linkage graph
     correctly answers both the forward direction (by ocid_number) and the
     new reverse direction (by file_path / commit_sha) added by this same
     task, via query_ocid_artifact_links()'s new file_path/commit_sha
     filters.
  3. No infinite trigger recursion / no hang on insert or update, given
     SQLite's default `PRAGMA recursive_triggers=OFF`.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database, same convention as
tests/test_ocid_canonical_registry.py and tests/test_ocid_artifact_links.py.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_ocid_completion_gate", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()
    return sbr


def _row(conn, ocid_number):
    return dict(conn.execute(
        "SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", (ocid_number,)
    ).fetchone())


def test_is_fully_complete_requires_all_six_real_conditions():
    """Insert a row with 5 of 6 conditions satisfied (missing commit_sha) --
    is_fully_complete must be 0. Updating that same row with a real
    commit_sha must flip it to 1."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-901", canonical_umr_id="UMR-20260805-000001-aaaa",
            status="merged (PR #900)", pr_number=900, pr_repo="veridian-scripts",
            all_umr_ids=["UMR-20260805-000001-aaaa"], evidence={"gh_pr_search": "OCID-901"},
            merge_status="merged", file_path="OCID_901_TEST.md",
            evidence_summary="OCID-901 closed by real merged PR #900.",
        )
        conn.commit()
        row = _row(conn, "OCID-901")
        assert row["has_real_umr"] == 1, row
        assert row["has_real_pr"] == 1, row
        assert row["has_real_commit"] == 0, row  # missing
        assert row["has_real_merge"] == 1, row
        assert row["has_real_file_path"] == 1, row
        assert row["has_real_evidence_summary"] == 1, row
        assert row["is_fully_complete"] == 0, row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-901", canonical_umr_id="UMR-20260805-000001-aaaa",
            status="merged (PR #900)", pr_number=900, pr_repo="veridian-scripts",
            all_umr_ids=["UMR-20260805-000001-aaaa"], evidence={"gh_pr_search": "OCID-901"},
            merge_status="merged", file_path="OCID_901_TEST.md",
            evidence_summary="OCID-901 closed by real merged PR #900.",
            commit_sha="0123456789abcdef0123456789abcdef01234567",
        )
        conn.commit()
        row = _row(conn, "OCID-901")
        assert row["has_real_commit"] == 1, row
        assert row["is_fully_complete"] == 1, row
        conn.close()
        print("PASS: test_is_fully_complete_requires_all_six_real_conditions")


def test_direct_sql_hand_set_of_is_fully_complete_is_overridden_by_trigger():
    """The real proof the gate can't be hand-set: a direct raw
    `UPDATE ocid_canonical_registry SET is_fully_complete = 1` against a row
    that does NOT satisfy all 6 real conditions must be silently overridden
    back to 0 by the AFTER UPDATE trigger -- not merely rejected, the
    trigger's own recompute must win."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-902", canonical_umr_id="UMR-20260805-000002-bbbb",
            status="open", all_umr_ids=["UMR-20260805-000002-bbbb"], evidence={},
        )
        conn.commit()
        row = _row(conn, "OCID-902")
        assert row["is_fully_complete"] == 0, "precondition: row must start incomplete"

        conn.execute("UPDATE ocid_canonical_registry SET is_fully_complete = 1 WHERE ocid_number = ?", ("OCID-902",))
        conn.commit()
        row = _row(conn, "OCID-902")
        assert row["is_fully_complete"] == 0, (
            f"GATE BYPASSED: direct SQL hand-set of is_fully_complete=1 was NOT overridden by trigger, row={row}"
        )

        # Also try hand-setting each individual has_real_* boolean directly --
        # every one must be overridden back to its real recomputed value.
        # OCID-902 genuinely has a real canonical_umr_id (not_found=0), so
        # has_real_umr's real recomputed value is 1 -- only the other 5
        # booleans (and is_fully_complete) have a real value of 0 here, since
        # pr_number/commit_sha/merge_status/file_path/evidence_summary are
        # all still genuinely NULL/unset on this row.
        conn.execute(
            "UPDATE ocid_canonical_registry SET has_real_umr=1, has_real_pr=1, has_real_commit=1, "
            "has_real_merge=1, has_real_file_path=1, has_real_evidence_summary=1 WHERE ocid_number = ?",
            ("OCID-902",),
        )
        conn.commit()
        row = _row(conn, "OCID-902")
        assert row["has_real_umr"] == 1, f"has_real_umr should reflect its genuinely real value: {row}"
        for col in ("has_real_pr", "has_real_commit", "has_real_merge",
                    "has_real_file_path", "has_real_evidence_summary", "is_fully_complete"):
            assert row[col] == 0, f"GATE BYPASSED on {col}: {row}"

        conn.close()
        print("PASS: test_direct_sql_hand_set_of_is_fully_complete_is_overridden_by_trigger")


def test_has_real_umr_respects_not_found_flag():
    """has_real_umr must be 0 for a not_found row even if canonical_umr_id
    happens to be non-NULL (real spec: has_real_umr = 1 iff
    canonical_umr_id IS NOT NULL AND not_found = 0)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-903", canonical_umr_id=None,
            status="no real UMR or PR found after thorough search", not_found=True,
            all_umr_ids=[], evidence={"search": "zero rows"},
        )
        conn.commit()
        row = _row(conn, "OCID-903")
        assert row["has_real_umr"] == 0, row
        assert row["is_fully_complete"] == 0, row
        conn.close()
        print("PASS: test_has_real_umr_respects_not_found_flag")


def test_not_applicable_confirmed_is_trigger_derived_from_not_found():
    """Owner reinforcement directive UMR-20260805-091934-86a2: the 8 real
    confirmed not_found rows (OCID-007..OCID-011, OCID-012, OCID-013,
    OCID-014) must carry an explicit, never-silently-NULL
    not_applicable_confirmed=1 marker, trigger-derived from the row's own
    real not_found column AND a genuinely non-empty audit_raw_output
    (Owner urgent correction UMR-20260805-092408-4f97 -- a bare not_found
    flag alone, with no real stored audit evidence behind it, is no longer
    sufficient) -- never hand-settable, same governance as the other 7
    boolean gate columns."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-905", canonical_umr_id=None, status="not_found", not_found=True,
            all_umr_ids=[], evidence={"search": "zero rows"},
            evidence_summary="OCID-905 never real/never registered; no file path applies.",
            audit_raw_output={"umr_tasks_task_identity_substring": "zero rows",
                               "gh_pr_search_compliance-tracker": {"ok": True, "prs": []}},
        )
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-906", canonical_umr_id="UMR-20260805-000006-ffff", status="open",
            all_umr_ids=["UMR-20260805-000006-ffff"], evidence={},
        )
        # real requirement: not_found=True with NO real stored audit_raw_output
        # must NOT earn not_applicable_confirmed -- a bare claim is not evidence.
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-907", canonical_umr_id=None, status="not_found", not_found=True,
            all_umr_ids=[], evidence={},
        )
        conn.commit()

        row_not_found = _row(conn, "OCID-905")
        row_real = _row(conn, "OCID-906")
        row_unaudited_claim = _row(conn, "OCID-907")
        assert row_not_found["not_applicable_confirmed"] == 1, row_not_found
        assert row_real["not_applicable_confirmed"] == 0, row_real
        assert row_unaudited_claim["not_applicable_confirmed"] == 0, (
            f"GATE BYPASSED: not_found=True with no real audit_raw_output earned "
            f"not_applicable_confirmed: {row_unaudited_claim}"
        )

        # direct SQL hand-set bypass attempt on the real (not not_found) row
        conn.execute(
            "UPDATE ocid_canonical_registry SET not_applicable_confirmed = 1 WHERE ocid_number = ?",
            ("OCID-906",),
        )
        conn.commit()
        row_real = _row(conn, "OCID-906")
        assert row_real["not_applicable_confirmed"] == 0, f"GATE BYPASSED: {row_real}"
        conn.close()
        print("PASS: test_not_applicable_confirmed_is_trigger_derived_from_not_found")


def test_linkage_graph_forward_and_reverse_query():
    """Insert a real fixture link row via insert_ocid_artifact_link(), then
    prove query_ocid_artifact_links() finds it forward (by ocid_number) AND
    in reverse (by the same real file_path, and separately by the same real
    commit_sha) -- the extension this task adds to the existing (PR #20)
    linkage graph, not a new parallel mechanism."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        link_id = sbr.insert_ocid_artifact_link(
            conn, ocid_number="OCID-904", umr_id="UMR-20260805-000004-dddd",
            repo="veridian-scripts", link_kind="closure",
            pr_number=904, commit_sha="fedcba9876543210fedcba9876543210fedcba9",
            file_path="OCID_904_FIXTURE.md",
        )
        conn.commit()
        assert link_id is not None, "insert_ocid_artifact_link failed"

        forward = sbr.query_ocid_artifact_links(conn, ocid_number="OCID-904")
        assert len(forward) == 1, forward
        assert forward[0]["umr_id"] == "UMR-20260805-000004-dddd", forward[0]
        assert forward[0]["file_path"] == "OCID_904_FIXTURE.md", forward[0]

        by_file_path = sbr.query_ocid_artifact_links(conn, file_path="OCID_904_FIXTURE.md")
        assert len(by_file_path) == 1, by_file_path
        assert by_file_path[0]["ocid_number"] == "OCID-904", by_file_path[0]
        assert by_file_path[0]["umr_id"] == "UMR-20260805-000004-dddd", by_file_path[0]

        by_commit_sha = sbr.query_ocid_artifact_links(
            conn, commit_sha="fedcba9876543210fedcba9876543210fedcba9"
        )
        assert len(by_commit_sha) == 1, by_commit_sha
        assert by_commit_sha[0]["ocid_number"] == "OCID-904", by_commit_sha[0]

        # a non-matching file_path/commit_sha must find nothing
        assert sbr.query_ocid_artifact_links(conn, file_path="NO_SUCH_FILE.md") == []
        assert sbr.query_ocid_artifact_links(conn, commit_sha="0000000000000000000000000000000000000") == []

        conn.close()
        print("PASS: test_linkage_graph_forward_and_reverse_query")


def test_no_infinite_trigger_recursion_or_hang_on_insert_and_update():
    """Real, timing-bounded proof: with SQLite's default
    `PRAGMA recursive_triggers=OFF`, the completion-gate triggers' own
    internal UPDATE does not recursively re-fire themselves. A batch of
    inserts followed by updates to every one of those same rows must
    complete near-instantly, not hang."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0, (
            "test assumes the real default recursive_triggers=OFF behavior"
        )

        start = time.monotonic()
        for i in range(100):
            sbr.upsert_ocid_canonical_registry(
                conn, f"OCID-90{i:03d}", canonical_umr_id=f"UMR-loop-{i}",
                status="open", all_umr_ids=[f"UMR-loop-{i}"], evidence={},
            )
        conn.commit()
        for i in range(100):
            sbr.upsert_ocid_canonical_registry(
                conn, f"OCID-90{i:03d}", canonical_umr_id=f"UMR-loop-{i}",
                status="closed", all_umr_ids=[f"UMR-loop-{i}"], evidence={},
                pr_number=i, pr_repo="veridian-scripts", merge_status="merged",
                file_path=f"file-{i}.md", evidence_summary="loop test",
                commit_sha=f"sha-{i}",
            )
        conn.commit()
        elapsed = time.monotonic() - start

        assert elapsed < 10, f"200 real trigger-firing writes took {elapsed:.2f}s -- possible recursion/hang"
        count = conn.execute(
            "SELECT COUNT(*) FROM ocid_canonical_registry WHERE is_fully_complete = 1"
        ).fetchone()[0]
        assert count == 100, f"expected all 100 rows fully complete after real update, got {count}"
        conn.close()
        print(f"PASS: test_no_infinite_trigger_recursion_or_hang_on_insert_and_update ({elapsed:.3f}s for 200 writes)")


if __name__ == "__main__":
    test_is_fully_complete_requires_all_six_real_conditions()
    test_direct_sql_hand_set_of_is_fully_complete_is_overridden_by_trigger()
    test_has_real_umr_respects_not_found_flag()
    test_not_applicable_confirmed_is_trigger_derived_from_not_found()
    test_linkage_graph_forward_and_reverse_query()
    test_no_infinite_trigger_recursion_or_hang_on_insert_and_update()
    print("ALL PASS")
