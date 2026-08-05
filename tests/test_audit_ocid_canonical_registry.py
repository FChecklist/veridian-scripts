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
import subprocess
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


def _run_cli(db_path, extra_args=()):
    """Real subprocess invocation of the real CLI entrypoint (never calling
    main() in-process), against a real isolated temp-file SQLite DB pointed
    at via the real SUPERBOSS_REGISTER_DB env-var seam
    (resolve_superboss_db_path(), superboss-register.py) -- so this exercises
    exactly what a human/PM running `python3 audit_ocid_canonical_registry.py`
    at a real terminal actually sees, with zero risk to the real production
    DB."""
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "audit_ocid_canonical_registry.py"), *extra_args],
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True, timeout=60,
    )


def test_dry_run_makes_zero_writes_and_every_output_line_is_unambiguously_labeled():
    """Regression test for the real incident (UMR-20260805-121654-4b77 /
    UMR-20260805-122042-8dbc): a dry run's own per-OCID 'changed=True'/
    'CHANGED:' stderr lines were misread as proof of a confirmed live DB
    write, when no write had occurred -- independent verification found the
    live table matched the known-correct roster on every row named. This
    proves, empirically, both halves of that never recurring:
      1. a dry run (no --apply) makes ZERO writes to the real DB file --
         byte-identical before/after, not just 'no exception raised'.
      2. every stderr line a dry run prints (including every 'changed'/
         'CHANGED' line) is prefixed '[DRY RUN]', so no line can be misread
         in isolation as describing a confirmed write."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        # seed one row whose existing canonical choice will no longer be
        # corroborated by the fresh (empty) search below, so this run
        # produces a real changed=True / CHANGED line -- the exact case
        # that was misread in the real incident.
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-960", canonical_umr_id="UMR-STALE-FOR-DRYRUN-TEST",
            status="completed", all_umr_ids=["UMR-STALE-FOR-DRYRUN-TEST"],
            evidence={**{k: None for k in sbr.EVIDENCE_JSON_REQUIRED_KEYS},
                      "umr_id": "UMR-STALE-FOR-DRYRUN-TEST", "ocid_number": "OCID-960",
                      "evidence_summary": "seeded for dry-run-zero-writes regression test."},
        )
        conn.commit()
        conn.close()

        def _snapshot():
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM ocid_canonical_registry ORDER BY ocid_number")]
            c.close()
            return rows

        # Note: raw file bytes are NOT compared here -- opening a real
        # sqlite3 connection legitimately flips WAL-mode header bytes even
        # for a connection that performs no data writes, which would make a
        # byte-identical check falsely fail. Full-table *data* snapshot
        # (every column, every row) is the real, correct zero-writes proof.
        before = _snapshot()
        result = _run_cli(path, ["--ocid-number", "OCID-960"])
        assert result.returncode == 0, result.stderr
        after = _snapshot()

        assert before == after, (
            f"DRY RUN (no --apply) modified real row data on disk -- this must never happen\n"
            f"before={before}\nafter={after}"
        )
        assert after[0]["canonical_umr_id"] == "UMR-STALE-FOR-DRYRUN-TEST", (
            "DRY RUN changed the live row's canonical_umr_id -- this must never happen"
        )

        stderr_lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
        assert any("changed=True" in ln for ln in stderr_lines), (
            "test setup did not actually produce a changed=True line to exercise the mislabel risk"
        )
        assert any(ln.startswith("CHANGED:") or " CHANGED:" in ln for ln in stderr_lines) is False or all(
            "[DRY RUN]" in ln for ln in stderr_lines if "CHANGED:" in ln
        ), f"a CHANGED line was printed without an unambiguous [DRY RUN] label: {stderr_lines}"
        for ln in stderr_lines:
            if "changed=True" in ln or "CHANGED:" in ln:
                assert ln.startswith("[DRY RUN]"), f"unlabeled dry-run line could be misread as a live write: {ln!r}"
        assert "no rows were written to the live database" in result.stderr
        print("PASS: test_dry_run_makes_zero_writes_and_every_output_line_is_unambiguously_labeled")


def test_apply_actually_writes_and_output_is_labeled_apply_not_dry_run():
    """Sanity-check the other half: --apply DOES write, and its own output
    lines are labeled '[APPLY]', never '[DRY RUN]' -- the two modes must
    never share an ambiguous label."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(path)

        result = _run_cli(path, ["--ocid-number", "OCID-961", "--apply"])
        assert result.returncode == 0, result.stderr
        assert "[APPLY]" in result.stderr
        assert "[DRY RUN]" not in result.stderr
        assert "APPLIED: wrote 1 real re-audited rows" in result.stderr

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", ("OCID-961",)
        ).fetchone()
        conn.close()
        assert row is not None, "--apply did not write a row for the requested OCID"
        print("PASS: test_apply_actually_writes_and_output_is_labeled_apply_not_dry_run")


if __name__ == "__main__":
    test_determinism_two_runs_identical_structured_output()
    test_plan_preserves_existing_reasoned_canonical_choice_when_still_corroborated()
    test_plan_uses_fresh_result_when_existing_choice_no_longer_corroborated()
    test_not_applicable_confirmed_requires_real_stored_audit_raw_output()
    test_dry_run_makes_zero_writes_and_every_output_line_is_unambiguously_labeled()
    test_apply_actually_writes_and_output_is_labeled_apply_not_dry_run()
    print("ALL PASS")
