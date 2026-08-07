#!/usr/bin/env python3
"""
test_agent_work_briefing.py -- standalone (no pytest required) proof that
agent_work_briefing.py's real deterministic pre-work briefing and
post-work write-back actually behave as the SPEC (direct correction/
extension to UMR-20260806-121332-6ba4) requires:

  assemble-briefing:
    - a brand-new UMR/scope with nothing on record gets an honest
      all-False, all-empty briefing (never a fabricated match).
    - a real wiring_registry row matching a scope term via `path` (existing
      lookup_entity() coverage) IS surfaced.
    - a real wiring_registry row matching a scope term ONLY via
      `relationships` (NOT FTS-indexed -- this module's own LIKE-scan
      extension) IS surfaced too.
    - a real capability_registry row matching intent_text IS surfaced.
    - this exact UMR's own prior ai_agent_registry history IS surfaced once
      it exists, and is absent before it does.

  record-completion:
    - writes one real ai_agent_registry memory entry + bumps
      total_tasks_handled (via ai_agent_registry.py's own record-work).
    - writes a real terminal status onto the umr_tasks row (via
      superboss-register.py's own mark-umr-terminal).
    - a genuinely new wiring_registry entity IS written.
    - calling it again with the SAME entity_id is a real no-op (search-first
      dedup) -- never a second row.
    - a gtm_certification_categories result IS written when
      --gtm-category-index is given, via the existing canonical writer.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override), same convention test_ai_agent_registry.py already established --
never touches the live /opt/veridian/ai-os/memory/superboss-register.sqlite.

Run: python3 test_agent_work_briefing.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure(s) otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS = os.path.join(SCRIPTS, "superboss-register.py")
BRIEFING = os.path.join(SCRIPTS, "agent_work_briefing.py")


def main():
    tmpdir = tempfile.mkdtemp(prefix="agent_work_briefing_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")
    memory_dir = os.path.join(tmpdir, "agents")

    # Same resolve_superboss_db_path() existence trap test_ai_agent_registry.py
    # already discovered and works around: bootstrap a stub umr_tasks table
    # BEFORE the env var is set, so the resolver accepts this temp path
    # instead of silently falling back to the live production db. Unlike
    # test_ai_agent_registry.py's own minimal 6-column stub, this uses the
    # REAL, full live column set verbatim: _ensure_umr_table()'s own fast
    # path short-circuits (skips every further ALTER/migration statement)
    # once umr_id plus 5 specific columns are present, which would otherwise
    # leave `status`/`ts_completed`/etc. permanently missing -- this test
    # asserts against those columns directly, so it cannot rely on that
    # fast path ever firing.
    import sqlite3 as _sqlite3
    _bootstrap = _sqlite3.connect(db_path)
    _bootstrap.execute("""CREATE TABLE umr_tasks (
        umr_id TEXT PRIMARY KEY,
        task_identity TEXT NOT NULL,
        ts_submitted TEXT NOT NULL,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 4),
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','dispatched','running','completed','completed_unmerged','failed','rejected_duplicate','sigterm_sent','killed')),
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
        metadata_json TEXT NOT NULL DEFAULT '{}',
        last_heartbeat TEXT, tenant_id TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT, external_agent_eligible INTEGER NOT NULL DEFAULT 0,
        external_agent_task_type TEXT, blast_radius TEXT, requires_multi_file_context INTEGER NOT NULL DEFAULT 0,
        files_touched TEXT NOT NULL DEFAULT '[]', external_agent_status TEXT,
        external_agent_reject_count INTEGER NOT NULL DEFAULT 0, external_agent_dispatch_count INTEGER NOT NULL DEFAULT 0,
        ts_relay_attempted TEXT, relay_outcome TEXT, relay_detail TEXT
    )""")
    _bootstrap.commit()
    _bootstrap.close()

    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    env = dict(os.environ)

    failures = []

    proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        print(f"FAIL (setup): init failed: {proc.stderr}")
        return 1

    # gtm_certification_categories is not part of init_db()'s own executescript
    # (a separate additive migration in production) -- bootstrap the real
    # live schema directly here, same convention as the umr_tasks stub above.
    conn = _sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS gtm_certification_categories (
        category_index INTEGER PRIMARY KEY,
        category_name TEXT NOT NULL,
        ocid_number TEXT NOT NULL,
        parent_umr_id TEXT NOT NULL,
        child_umr_id TEXT,
        passed INTEGER,
        evidence_summary TEXT,
        evidence_json TEXT,
        fix_commit TEXT,
        fix_file_path TEXT,
        fix_pr_number INTEGER,
        validated_at TEXT,
        created_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL
    )""")
    conn.execute(
        "INSERT INTO gtm_certification_categories "
        "(category_index, category_name, ocid_number, parent_umr_id, created_at, last_updated_at) "
        "VALUES (99, 'test_category', 'OCID-999', 'UMR-20260101-000000-0000', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    import importlib.util as ilu
    spec = ilu.spec_from_file_location("agent_work_briefing_test_mod", BRIEFING)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    air = mod._ai_agent_registry()
    air.AGENT_MEMORY_DIR = memory_dir
    sbr = mod._superboss_register()

    # Hard safety check, same convention test_ai_agent_registry.py uses:
    # confirm we're really pointed at the temp db before writing anything.
    if os.path.realpath(sbr.DB_PATH) != os.path.realpath(db_path):
        print(f"FAIL (setup): resolved DB_PATH {sbr.DB_PATH!r} is NOT the temp db "
              f"{db_path!r} -- refusing to proceed.")
        return 1

    umr_id = "UMR-20260806-165903-aaaa"

    # A real dispatched umr_tasks row must already exist for mark-umr-terminal
    # (via record-completion's --umr-status) to have anything real to UPDATE --
    # same as every real worker's row, created at dispatch time before any
    # AI agent's own completion write-back runs.
    conn = sbr._connect()
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger) "
        "VALUES (?, 'test-task-identity', ?, 1, 'running', 'test')",
        (umr_id, sbr._now_iso()),
    )
    conn.commit()
    conn.close()

    # 1. Brand-new UMR/scope: honest, all-empty briefing.
    briefing0 = mod.assemble_briefing(umr_id, ["totally_unknown_scope_term_xyzzy"], "totally unknown intent xyzzy")
    if briefing0["wiring_registry"]["has_matches"] is not False:
        failures.append("pre-registration briefing: wiring_registry.has_matches should be False")
    if briefing0["capability_registry"]["has_match"] is not False:
        failures.append("pre-registration briefing: capability_registry.has_match should be False")
    if briefing0["ai_agent_registry"]["has_prior_history"] is not False:
        failures.append("pre-registration briefing: ai_agent_registry.has_prior_history should be False")
    if not briefing0["close_ended_facts"] or len(briefing0["close_ended_facts"]) != 3:
        failures.append(f"pre-registration briefing: expected 3 close_ended_facts, got {briefing0.get('close_ended_facts')}")

    # 2. Register one real wiring_registry row matching via `path`, one
    #    matching ONLY via `relationships` (not FTS-indexed).
    conn = sbr._connect()
    sbr._ensure_wiring_registry_table(conn)
    sbr.register_entity_row(conn, {
        "entity_id": "test-entity-path-match", "entity_type": "script", "source_system": "server",
        "path": "scripts/scope_marker_alpha.py", "relationships": [], "last_verified_ts": sbr._now_iso(),
        "verification_status": "VERIFIED_MATCH", "source_ref": ["scripts/scope_marker_alpha.py"],
    })
    sbr.register_entity_row(conn, {
        "entity_id": "test-entity-relationships-match", "entity_type": "function", "source_system": "server",
        "path": None, "relationships": ["calls scope_marker_beta_relationship_only"],
        "last_verified_ts": sbr._now_iso(), "verification_status": "VERIFIED_MATCH", "source_ref": [],
    })
    conn.commit()
    conn.close()

    briefing1 = mod.assemble_briefing(
        umr_id, ["scope_marker_alpha", "scope_marker_beta_relationship_only"], "totally unknown intent xyzzy")
    found_ids = {m["entity_id"] for m in briefing1["wiring_registry"]["matches"]}
    if "test-entity-path-match" not in found_ids:
        failures.append("path-matching wiring_registry row was not surfaced by assemble_briefing")
    if "test-entity-relationships-match" not in found_ids:
        failures.append("relationships-only-matching wiring_registry row was not surfaced -- LIKE-scan extension broken")
    if briefing1["wiring_registry"]["has_matches"] is not True:
        failures.append("briefing1: wiring_registry.has_matches should be True")
    if "scripts/scope_marker_alpha.py" not in briefing1["wiring_registry"]["relevant_file_paths"]:
        failures.append("briefing1: relevant_file_paths should include the path-matched entity's real path")

    # 3. Register a real capability_registry row matching intent_text.
    conn = sbr._connect()
    sbr._ensure_capability_registry_table(conn)
    conn.execute(
        "INSERT INTO capability_registry (capability_id, ts, capability_name, permissions, owner) "
        "VALUES ('CAP-TEST-0001', ?, 'unique_capability_marker_qwerty', '[]', 'test')",
        (sbr._now_iso(),),
    )
    conn.commit()
    conn.close()
    briefing2 = mod.assemble_briefing(umr_id, [], "unique_capability_marker_qwerty")
    if briefing2["capability_registry"]["has_match"] is not True:
        failures.append("briefing2: capability_registry.has_match should be True after registering a matching row")

    # 4. record-completion writes ai_agent_registry + umr_tasks; verify via
    #    the CLI (matches how a real caller would invoke this).
    #    --umr-status completed now requires real structured completion
    #    evidence (superboss-register.py's mark-umr-terminal gate,
    #    UMR-20260806-130914-e7f1) -- a real --umr-file-path that exists on
    #    disk (this test script's own real path) satisfies it without needing
    #    a real git ancestor-of-main check.
    proc = subprocess.run(
        ["python3", BRIEFING, "record-completion", "--umr-id", umr_id,
         "--entry-text", "did the real work", "--role-label", "test agent",
         "--umr-status", "completed", "--umr-reason", "test run complete",
         "--umr-file-path", os.path.abspath(__file__)],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        failures.append(f"record-completion (basic) failed: {proc.stderr}")
    else:
        rc_result = json.loads(proc.stdout)
        if rc_result["ai_agent_registry"].get("total_tasks_handled") != 1:
            failures.append(f"record-completion: expected total_tasks_handled=1, got {rc_result['ai_agent_registry']}")
        if rc_result.get("umr_tasks", {}).get("status") != "completed":
            failures.append(f"record-completion: umr_tasks status not set to completed: {rc_result.get('umr_tasks')}")

    conn = sbr._connect()
    row = conn.execute("SELECT status, ts_completed FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    conn.close()
    if row is None or row["status"] != "completed" or not row["ts_completed"]:
        failures.append(f"umr_tasks row not actually updated in the db: {dict(row) if row else None}")

    # 5. A second assemble-briefing for the same UMR now shows real prior history.
    briefing3 = mod.assemble_briefing(umr_id, [], "totally unknown intent xyzzy")
    if briefing3["ai_agent_registry"]["has_prior_history"] is not True:
        failures.append("briefing3: ai_agent_registry.has_prior_history should be True after record-completion")
    if briefing3["ai_agent_registry"]["agent"]["total_tasks_handled"] != 1:
        failures.append(f"briefing3: expected total_tasks_handled=1, got {briefing3['ai_agent_registry']['agent']}")

    # 6. A genuinely new wiring_registry entity gets written.
    entity_file = os.path.join(tmpdir, "new_entity.json")
    with open(entity_file, "w", encoding="utf-8") as f:
        json.dump({
            "entity_id": "test-new-genuinely-new-entity", "entity_type": "script", "source_system": "server",
            "path": "scripts/agent_work_briefing.py", "relationships": [], "last_verified_ts": sbr._now_iso(),
            "verification_status": "VERIFIED_MATCH", "source_ref": ["scripts/agent_work_briefing.py"],
        }, f)
    proc = subprocess.run(
        ["python3", BRIEFING, "record-completion", "--umr-id", umr_id, "--entry-text", "registered a new script",
         "--new-entity-record-file", entity_file],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        failures.append(f"record-completion (new entity) failed: {proc.stderr}")
    else:
        r = json.loads(proc.stdout)
        if r.get("wiring_registry", {}).get("written") is not True:
            failures.append(f"record-completion: new entity was not written: {r.get('wiring_registry')}")

    # 7. Calling it again with the SAME entity_id is a real no-op (search-first
    #    dedup), never a second row.
    proc = subprocess.run(
        ["python3", BRIEFING, "record-completion", "--umr-id", umr_id, "--entry-text", "tried to re-register",
         "--new-entity-record-file", entity_file],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        failures.append(f"record-completion (dup entity) failed: {proc.stderr}")
    else:
        r = json.loads(proc.stdout)
        if r.get("wiring_registry", {}).get("written") is not False:
            failures.append(f"record-completion: duplicate entity_id should NOT be written twice: {r.get('wiring_registry')}")

    conn = sbr._connect()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM wiring_registry WHERE entity_id=?", ("test-new-genuinely-new-entity",)
    ).fetchone()["c"]
    conn.close()
    if n != 1:
        failures.append(f"wiring_registry has {n} rows for the same entity_id -- search-first dedup violated")

    # 8. gtm_certification_categories write-back, only when given.
    proc = subprocess.run(
        ["python3", BRIEFING, "record-completion", "--umr-id", umr_id, "--entry-text", "gtm result recorded",
         "--gtm-category-index", "99", "--gtm-result", "pass",
         "--gtm-script-path", "scripts/agent_work_briefing.py",
         "--gtm-evidence-summary", "test evidence", "--gtm-evidence-json", '{"real": true}'],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        failures.append(f"record-completion (gtm) failed: {proc.stderr}")
    else:
        r = json.loads(proc.stdout)
        gtm = r.get("gtm_certification_categories", {})
        if gtm.get("category_index") != 99 or gtm.get("result") != "pass":
            failures.append(f"record-completion: gtm_certification_categories write-back malformed: {gtm}")

    conn = sbr._connect()
    grow = conn.execute("SELECT passed, evidence_summary FROM gtm_certification_categories WHERE category_index=99").fetchone()
    conn.close()
    if grow is None or grow["passed"] != 1 or grow["evidence_summary"] != "test evidence":
        failures.append(f"gtm_certification_categories row not actually updated: {dict(grow) if grow else None}")

    if failures:
        print("FAIL:\n" + "\n".join(f"  - {f}" for f in failures))
        return 1

    print("PASS: all agent_work_briefing.py checks held (honest empty briefing, path + relationships "
          "wiring_registry matching, capability_registry matching, prior-history surfacing, "
          "ai_agent_registry/umr_tasks/gtm_certification_categories write-back, search-first "
          "wiring_registry dedup).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
