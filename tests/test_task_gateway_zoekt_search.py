#!/usr/bin/env python3
"""Real tests for UMR171945-0017 (governing chain UMR-20260806-171945-5767):
task-gateway.py's cmd_submit search step now folds in a real Zoekt code-
search hit alongside its existing check-duplicate/search/query-knowledge
calls (which query superboss-register.sqlite's own FTS5 tables -- a
structurally different, DB-row-text-only corpus that never ingests actual
source-file contents).

Real, live-triggered boolean test (per UMR171945-0017's own explicit
requirement): submits a real instruction whose keywords match real content
only findable via Zoekt's own index (a distinctive identifier that
genuinely exists in resource_governor.py, confirmed live via direct curl
against http://127.0.0.1:6070 before writing this test), against a freshly
seeded, empty scratch superboss-register.sqlite (so check-duplicate/search/
query-knowledge are guaranteed to find nothing there -- proving the Zoekt
hit is real, additive coverage, not overlap). This hits the REAL, already-
running veridian-zoekt-webserver.service -- not mocked -- because the whole
point of this test is proving the real service is really wired in.

run_zoekt_search()'s own fail-open behavior (a separate, real requirement --
a Zoekt outage must never block or crash cmd_submit) is tested directly
against a deliberately unreachable URL, no live service dependency there.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_GATEWAY = os.path.join(SCRIPTS_DIR, "task-gateway.py")
SUPERBOSS = os.path.join(SCRIPTS_DIR, "superboss-register.py")

# Confirmed live, 2026-08-08, via direct curl against the real Zoekt
# webserver: this identifier is a real, distinctive substring of
# resource_governor.py (indexed under multiple real repos: scripts,
# veridian-scripts, compliance-tracker, claude-control) and returns real
# FileMatches -- chosen specifically because it is unlikely to ever appear
# as literal text in any superboss-register.sqlite FTS5-indexed row
# (instruction/capability/knowledge text), especially not in a freshly
# seeded, empty scratch DB.
REAL_ZOEKT_ONLY_TERM = "EMERGENCY_CONSECUTIVE_TICKS_SHED"


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_zoekt_seed", SUPERBOSS)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()


def test_run_zoekt_search_fails_open_on_unreachable_service():
    """A Zoekt outage/misconfiguration must never crash or block cmd_submit
    -- honest ok=False, empty hits, real error message, no exception
    propagated."""
    spec = importlib.util.spec_from_file_location("tg_zoekt_fail_open", TASK_GATEWAY)
    tg = importlib.util.module_from_spec(spec)
    old = os.environ.get("ZOEKT_URL")
    os.environ["ZOEKT_URL"] = "http://127.0.0.1:1"  # nothing real listens here
    try:
        spec.loader.exec_module(tg)
        result = tg.run_zoekt_search("anything", limit=5)
    finally:
        if old is None:
            os.environ.pop("ZOEKT_URL", None)
        else:
            os.environ["ZOEKT_URL"] = old
    assert result["ok"] is False, result
    assert result["hits"] == [], result
    assert "error" in result and result["error"], result


def test_run_zoekt_search_empty_query_is_a_real_no_op():
    spec = importlib.util.spec_from_file_location("tg_zoekt_empty", TASK_GATEWAY)
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    result = tg.run_zoekt_search("   ", limit=5)
    assert result["ok"] is False, result
    assert result["hits"] == [], result


def test_run_zoekt_search_finds_a_real_hit_against_the_live_service():
    """Direct real call (no subprocess) against the real, already-running
    Zoekt webserver -- confirms run_zoekt_search() itself, independent of
    cmd_submit's wiring."""
    spec = importlib.util.spec_from_file_location("tg_zoekt_live", TASK_GATEWAY)
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    result = tg.run_zoekt_search(REAL_ZOEKT_ONLY_TERM, limit=5)
    assert result["ok"] is True, result
    assert len(result["hits"]) > 0, "expected at least one real Zoekt hit, found none -- is the live service down?"
    hit = result["hits"][0]
    assert hit["repo"], hit
    assert hit["file"], hit


def test_cmd_submit_folds_in_a_real_zoekt_hit_alongside_empty_fts5_results():
    """The real, live-triggered UMR171945-0017 boolean test: a real
    cmd_submit() run whose output includes a real Zoekt hit for a term that
    the (freshly seeded, empty) sqlite FTS5 side of the same search step
    genuinely cannot find -- proving this is real, additive coverage."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
        proc = subprocess.run(
            [sys.executable, TASK_GATEWAY, "submit",
             "--text", f"investigate {REAL_ZOEKT_ONLY_TERM} behavior",
             "--source", "ai_agent", "--session-id", "test-zoekt-wiring"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)

        # The FTS5 side of the same search step: genuinely nothing in a
        # freshly seeded, empty scratch DB.
        assert result["duplicate_found"] is False, result
        assert result["knowledge_matches"].get("found") in (0, None, False), result

        # The real Zoekt side: a genuine hit from the real, live service.
        zoekt = result["zoekt_matches"]
        assert zoekt["ok"] is True, zoekt
        assert len(zoekt["hits"]) > 0, (
            "cmd_submit's real Zoekt integration returned zero hits for a term confirmed "
            "live to exist in the real index -- either the wiring or the live service regressed"
        )


if __name__ == "__main__":
    test_run_zoekt_search_fails_open_on_unreachable_service()
    print("PASS: test_run_zoekt_search_fails_open_on_unreachable_service")
    test_run_zoekt_search_empty_query_is_a_real_no_op()
    print("PASS: test_run_zoekt_search_empty_query_is_a_real_no_op")
    test_run_zoekt_search_finds_a_real_hit_against_the_live_service()
    print("PASS: test_run_zoekt_search_finds_a_real_hit_against_the_live_service")
    test_cmd_submit_folds_in_a_real_zoekt_hit_alongside_empty_fts5_results()
    print("PASS: test_cmd_submit_folds_in_a_real_zoekt_hit_alongside_empty_fts5_results")
    print("ALL PASS")
