#!/usr/bin/env python3
"""Real tests for audit_ocid_canonical_registry.py (Owner urgent correction
UMR-20260805-092408-4f97, extending UMR-20260805-090549-9710 /
UMR-20260805-091934-86a2). Every test uses a real, isolated, temp-file
SQLite database seeded with the real schema, and a real injected fake
`_runner` (never a live subprocess/network call) -- same convention as
tests/test_ocid_master_standard_phase1.py, whose `_make_gh_pr_list_runner`
helper this file reuses the shape of.

Covers:
  1. Determinism: resolve_ocid_canonical() (the real, already-merged,
     zero-AI-judgment 6-method search this script's plan_for_ocid() calls,
     never reimplemented here) produces byte-identical structured JSON
     output across two separate real runs against unchanged real data.
  2. plan_for_ocid()'s one fixed merge rule: preserve a real, existing,
     still-corroborated canonical choice; use the fresh result in full when
     no longer corroborated; always refresh not_found/audit_raw_output.
  3. not_applicable_confirmed cannot be earned through any path that
     bypasses real, genuinely-stored audit_raw_output -- re-confirms the
     DB-trigger enforcement this script's own real writes rely on.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_audit_ocid", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _load_audit_script():
    spec = importlib.util.spec_from_file_location(
        "audit_ocid_canonical_registry_test", os.path.join(SCRIPTS_DIR, "audit_ocid_canonical_registry.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    sbr = _load_sbr()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    conn.close()
    return sbr


def _insert_umr_task(conn, umr_id, task_identity, status, ts_completed=None, tier=1):
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, ts_completed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (umr_id, task_identity, "2026-08-04T00:00:00+00:00", tier, status, "owner", ts_completed),
    )


class _FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_runner(prs_by_repo_and_ocid, git_log_by_repo=None):
    """Deterministic fake _runner -- same dispatch shape as
    tests/test_ocid_master_standard_phase1.py's own _make_gh_pr_list_runner,
    reused here rather than reinvented."""
    git_log_by_repo = git_log_by_repo or {}

    def _runner(cmd, cwd=None):
        if cmd[:3] == ["gh", "pr", "list"]:
            repo = cmd[cmd.index("--repo") + 1].split("/", 1)[1]
            search = cmd[cmd.index("--search") + 1]
            ocid = search.split(" ")[0]
            prs = prs_by_repo_and_ocid.get((repo, ocid), [])
            return _FakeCompletedProcess(stdout=json.dumps(prs), returncode=0)
        if cmd[:2] == ["git", "log"]:
            repo = os.path.basename(cwd) if cwd else None
            lines = git_log_by_repo.get(repo, [])
            return _FakeCompletedProcess(stdout="\n".join(lines), returncode=0)
        if cmd[:1] == ["grep"]:
            return _FakeCompletedProcess(stdout="", returncode=1)  # honest zero matches
        raise AssertionError(f"unexpected real subprocess call in test: {cmd}")

    return _runner


def test_determinism_two_runs_identical_structured_output():
    """The real audit's own mechanical search (resolve_ocid_canonical(),
    already-merged UMR-20260805-042152-e559) must produce byte-identical
    structured JSON across two separate real runs against unchanged real
    data -- real, empirical proof, not an assumption."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _insert_umr_task(conn, "UMR-20260804-090000-aaaa", "owner-task-ocid-950-real-work", "completed",
                          ts_completed="2026-08-04T10:00:00+00:00")
        conn.commit()

        runner = _make_runner({
            ("compliance-tracker", "OCID-950"): [
                {"number": 950, "title": "OCID-950 closure", "state": "MERGED",
                 "body": "Closes OCID-950. Dispatch instruction: UMR-20260804-080000-bbbb.",
                 "mergedAt": "2026-08-04T09:00:00Z"},
            ],
            ("veridian-scripts", "OCID-950"): [],
            ("projexa", "OCID-950"): [],
        })

        result1 = sbr.resolve_ocid_canonical("OCID-950", conn, _runner=runner)
        result2 = sbr.resolve_ocid_canonical("OCID-950", conn, _runner=runner)
        conn.close()

        json1 = json.dumps(result1, sort_keys=True, default=str)
        json2 = json.dumps(result2, sort_keys=True, default=str)
        assert json1 == json2, "real audit run is NOT deterministic across two identical runs"
        assert result1["canonical_umr_id"] == "UMR-20260804-080000-bbbb"
        print("PASS: test_determinism_two_runs_identical_structured_output")


def test_plan_preserves_existing_reasoned_canonical_choice_when_still_corroborated():
    """A real, existing, carefully-reasoned canonical_umr_id that the fresh
    audit run still finds among its own real all_umr_ids must be PRESERVED
    -- never silently downgraded to resolve_ocid_canonical()'s own cruder
    'earliest UMR wins' automatic default."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        audit = _load_audit_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        # two real UMRs exist; a human/AI previously reasoned the LATER one
        # (UMR-...-bbbb) is canonical (e.g. the earlier one was a real
        # duplicate dispatch, correctly rejected) -- NOT the naive
        # chronologically-earliest default.
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-951", canonical_umr_id="UMR-20260804-090000-bbbb",
            status="completed", all_umr_ids=["UMR-20260804-080000-aaaa", "UMR-20260804-090000-bbbb"],
            duplicate_reason="UMR-...-aaaa correctly rejected as duplicate; -bbbb is canonical.",
            evidence={
                **{k: None for k in sbr.EVIDENCE_JSON_REQUIRED_KEYS},
                "umr_id": "UMR-20260804-090000-bbbb", "ocid_number": "OCID-951",
                "evidence_summary": "seeded pre-existing curated canonical choice for this test.",
                "legacy_evidence": {"prior": "curated by original 5-agent methodology run"},
            },
        )
        conn.commit()

        _insert_umr_task(conn, "UMR-20260804-080000-aaaa", "owner-task-ocid-951-dup", "rejected_duplicate")
        _insert_umr_task(conn, "UMR-20260804-090000-bbbb", "owner-task-ocid-951-real", "completed",
                          ts_completed="2026-08-04T10:00:00+00:00")
        conn.commit()

        existing_by_ocid = {r["ocid_number"]: r for r in sbr.query_ocid_canonical_registry(conn)}
        runner = _make_runner({
            ("compliance-tracker", "OCID-951"): [], ("veridian-scripts", "OCID-951"): [],
            ("projexa", "OCID-951"): [],
        })
        plan = audit.plan_for_ocid(sbr, conn, "OCID-951", existing_by_ocid, _runner=runner)
        conn.close()

        assert plan["preserved_existing_canonical_choice"] is True
        assert plan["canonical_umr_id"] == "UMR-20260804-090000-bbbb"
        assert "correctly rejected as duplicate" in plan["duplicate_reason"]
        # not_found and audit_raw_output ALWAYS come from the fresh run
        assert plan["not_found"] is False
        assert plan["audit_raw_output"] != {"prior": "curated by original 5-agent methodology run"}
        print("PASS: test_plan_preserves_existing_reasoned_canonical_choice_when_still_corroborated")


def test_plan_uses_fresh_result_when_existing_choice_no_longer_corroborated():
    """When the fresh real audit run no longer finds the existing
    canonical_umr_id anywhere in its own real evidence, the plan must use
    the fresh result in full, with a real, non-silent explanatory note --
    never keep citing evidence that no longer exists."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        audit = _load_audit_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-952", canonical_umr_id="UMR-STALE-NO-LONGER-REAL",
            status="completed", all_umr_ids=["UMR-STALE-NO-LONGER-REAL"],
            evidence={
                **{k: None for k in sbr.EVIDENCE_JSON_REQUIRED_KEYS},
                "umr_id": "UMR-STALE-NO-LONGER-REAL", "ocid_number": "OCID-952",
                "evidence_summary": "seeded pre-existing (now stale) canonical choice for this test.",
                "legacy_evidence": {"prior": "stale"},
            },
        )
        conn.commit()

        # fresh real search finds a DIFFERENT, real UMR -- the stale one is
        # nowhere in umr_tasks or any PR body this run
        _insert_umr_task(conn, "UMR-20260805-000000-cccc", "owner-task-ocid-952-real", "completed",
                          ts_completed="2026-08-05T10:00:00+00:00")
        conn.commit()

        existing_by_ocid = {r["ocid_number"]: r for r in sbr.query_ocid_canonical_registry(conn)}
        runner = _make_runner({
            ("compliance-tracker", "OCID-952"): [], ("veridian-scripts", "OCID-952"): [],
            ("projexa", "OCID-952"): [],
        })
        plan = audit.plan_for_ocid(sbr, conn, "OCID-952", existing_by_ocid, _runner=runner)
        conn.close()

        assert plan["preserved_existing_canonical_choice"] is False
        assert plan["canonical_umr_id"] == "UMR-20260805-000000-cccc"
        assert "no longer corroborates" in plan["duplicate_reason"]
        assert "UMR-STALE-NO-LONGER-REAL" in plan["duplicate_reason"]
        print("PASS: test_plan_uses_fresh_result_when_existing_choice_no_longer_corroborated")


def test_not_applicable_confirmed_requires_real_stored_audit_raw_output():
    """Re-confirms (from this script's own real write path) the DB-trigger
    enforcement: not_applicable_confirmed can only ever read true when a
    genuinely non-empty audit_raw_output was actually written alongside
    not_found=1 -- exactly what plan_for_ocid()'s always-fresh
    audit_raw_output guarantees, and exactly what a hand-set bypass
    (upserting not_found=True with no audit_raw_output) cannot achieve."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        audit = _load_audit_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        runner = _make_runner({
            ("compliance-tracker", "OCID-953"): [], ("veridian-scripts", "OCID-953"): [],
            ("projexa", "OCID-953"): [],
        })
        plan = audit.plan_for_ocid(sbr, conn, "OCID-953", {}, _runner=runner)
        assert plan["not_found"] is True

        sbr.upsert_ocid_canonical_registry(
            conn, plan["ocid_number"], canonical_umr_id=plan["canonical_umr_id"], status=plan["status"],
            all_umr_ids=plan["all_umr_ids"], evidence=plan["evidence"], pr_number=plan["pr_number"],
            pr_repo=plan["pr_repo"], duplicate_reason=plan["duplicate_reason"], not_found=plan["not_found"],
            audit_raw_output=plan["audit_raw_output"],
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", ("OCID-953",)).fetchone())
        assert row["not_applicable_confirmed"] == 1, row
        assert row["audit_raw_output"] is not None and len(row["audit_raw_output"]) > 0

        # bypass attempt: a caller claiming not_found=True with NO real audit_raw_output
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-954", canonical_umr_id=None, status="not_found", not_found=True,
            all_umr_ids=[], evidence={},
        )
        conn.commit()
        row2 = dict(conn.execute("SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", ("OCID-954",)).fetchone())
        assert row2["not_applicable_confirmed"] == 0, f"GATE BYPASSED without real audit_raw_output: {row2}"
        conn.close()
        print("PASS: test_not_applicable_confirmed_requires_real_stored_audit_raw_output")


def test_bounded_for_storage_caps_oversized_leaf_values_deterministically():
    """Real regression proof (Owner urgent correction UMR-20260805-161157):
    this task's own first real --apply attempt against real (not test-
    fixture) production data found real umr_tasks rows with multi-megabyte
    metadata_json values, which resolve_ocid_canonical()'s real method (b)
    (full-table grep) legitimately matches for most real OCID numbers --
    storing those verbatim would have added an estimated 1-2+ GB to the
    live database in a single run. _bounded_for_storage() must cap any
    individual string leaf value over the fixed 5000-char limit, leave every
    value at or under it untouched byte-for-byte (so genuine, naturally-
    small real gh/git/grep command output is never altered), and disclose
    the real original length whenever it truncates -- and must be
    deterministic/idempotent (same input -> same output, every time,
    applying it twice is a no-op), the same determinism guarantee this
    file's own two-runs proof requires of the search itself."""
    audit = _load_audit_script()

    small = "a real, small, genuine gh/git/grep result"
    assert audit._bounded_for_storage(small) == small, "must not alter values at/under the cap"

    exactly_at_cap = "x" * audit._AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP
    assert audit._bounded_for_storage(exactly_at_cap) == exactly_at_cap, "must not alter a value exactly at the cap"

    oversized = "y" * (audit._AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP * 3)
    bounded_once = audit._bounded_for_storage(oversized)
    assert len(bounded_once) < len(oversized), "oversized value must actually be shortened"
    assert bounded_once.startswith("y" * audit._AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP), "prefix must be real, untouched original bytes"
    assert str(len(oversized)) in bounded_once, "real original length must be disclosed, not silently dropped"
    assert "TRUNCATED" in bounded_once

    # idempotent: running the cap again on its own already-bounded output is a no-op
    bounded_twice = audit._bounded_for_storage(bounded_once)
    assert bounded_twice == bounded_once, "cap must be idempotent/deterministic across repeated application"

    # real, nested evidence shape (dicts/lists) must be walked and rebuilt, not skipped
    nested = {
        "umr_tasks_full_dump_grep": {
            "UMR-real-small": "short real match",
            "UMR-real-huge": oversized,
        },
        "gh_pr_search_veridian-scripts": {"ok": True, "prs": [{"title": "short", "body": oversized}]},
    }
    bounded_nested = audit._bounded_for_storage(nested)
    assert bounded_nested["umr_tasks_full_dump_grep"]["UMR-real-small"] == "short real match"
    assert len(bounded_nested["umr_tasks_full_dump_grep"]["UMR-real-huge"]) < len(oversized)
    assert len(bounded_nested["gh_pr_search_veridian-scripts"]["prs"][0]["body"]) < len(oversized)
    print("PASS: test_bounded_for_storage_caps_oversized_leaf_values_deterministically")


def test_apply_style_write_of_pathologically_large_real_evidence_stays_bounded():
    """End-to-end proof through plan_for_ocid() -> upsert_ocid_canonical_registry()
    -> the live row: a real umr_tasks row with a multi-megabyte matching
    field must not result in a multi-megabyte audit_raw_output on the
    written row, and not_applicable_confirmed must still correctly derive
    from the (now-bounded, but still genuinely non-empty and genuinely
    real) stored evidence."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        audit = _load_audit_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        # real-shaped pathological row: a huge task_identity match for OCID-960,
        # same real-world shape as this task's own production discovery.
        huge_identity = "owner-task mentions OCID-960 " + ("z" * 500_000)
        _insert_umr_task(conn, "UMR-20260805-000000-9999", huge_identity, "completed",
                          ts_completed="2026-08-05T10:00:00+00:00")
        conn.commit()

        runner = _make_runner({
            ("compliance-tracker", "OCID-960"): [], ("veridian-scripts", "OCID-960"): [],
            ("projexa", "OCID-960"): [],
        })
        plan = audit.plan_for_ocid(sbr, conn, "OCID-960", {}, _runner=runner)
        assert len(json.dumps(plan["audit_raw_output"])) < 50_000, (
            f"real audit_raw_output was not bounded: {len(json.dumps(plan['audit_raw_output']))} chars"
        )

        with sbr._write_lock():
            sbr.upsert_ocid_canonical_registry(
                conn, plan["ocid_number"], canonical_umr_id=plan["canonical_umr_id"], status=plan["status"],
                all_umr_ids=plan["all_umr_ids"], evidence=plan["evidence"], pr_number=plan["pr_number"],
                pr_repo=plan["pr_repo"], duplicate_reason=plan["duplicate_reason"], not_found=plan["not_found"],
                audit_raw_output=plan["audit_raw_output"],
            )
            conn.commit()
        row = dict(conn.execute("SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", ("OCID-960",)).fetchone())
        assert len(row["audit_raw_output"]) < 50_000, f"real stored row audit_raw_output was not bounded: {len(row['audit_raw_output'])} chars"
        assert "UMR-20260805-000000-9999" in row["audit_raw_output"]
        conn.close()
        print("PASS: test_apply_style_write_of_pathologically_large_real_evidence_stays_bounded")


if __name__ == "__main__":
    test_determinism_two_runs_identical_structured_output()
    test_plan_preserves_existing_reasoned_canonical_choice_when_still_corroborated()
    test_plan_uses_fresh_result_when_existing_choice_no_longer_corroborated()
    test_not_applicable_confirmed_requires_real_stored_audit_raw_output()
    test_bounded_for_storage_caps_oversized_leaf_values_deterministically()
    test_apply_style_write_of_pathologically_large_real_evidence_stays_bounded()
    print("ALL PASS")
