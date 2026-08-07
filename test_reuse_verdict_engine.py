#!/usr/bin/env python3
"""
test_reuse_verdict_engine.py -- standalone (no pytest required) proof that
reuse_verdict_engine.py's real three-tier create/reuse/duplicate-block verdict
(UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767) actually
works across all four real wiring_registry sources plus capability_registry,
that vector_json is kept current by the real on-write hooks in
superboss-register.py's register_entity_row()/register_capability() (not a
separate backfill-only path), and that idempotency (same intent text -> cached
verdict, no recompute) really holds.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override), same bootstrap-stub convention test_ai_agent_registry.py already
established -- never touches the live
/opt/veridian/ai-os/memory/superboss-register.sqlite.

Run: python3 test_reuse_verdict_engine.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure otherwise.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS = os.path.join(SCRIPTS, "superboss-register.py")
ENGINE = os.path.join(SCRIPTS, "reuse_verdict_engine.py")

FAILURES = []


def check(label, cond, detail=""):
    if not cond:
        FAILURES.append(f"{label}: {detail}")
        print(f"FAIL: {label} {detail}")
    else:
        print(f"ok: {label}")


def run(cmd, env):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def register_entity(env, entity):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(entity, f)
        path = f.name
    try:
        p = run(["python3", SUPERBOSS, "register-entity", "--record-file", path], env)
        assert p.returncode == 0, p.stderr
    finally:
        os.unlink(path)


def register_capability(env, record):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(record, f)
        path = f.name
    try:
        p = run(["python3", SUPERBOSS, "register-capability", "--record-file", path], env)
        assert p.returncode == 0, p.stderr
    finally:
        os.unlink(path)


def assess(env, intent_text, no_cache=True):
    cmd = ["python3", ENGINE, "assess", "--intent-text", intent_text]
    if no_cache:
        cmd.append("--no-cache")
    p = run(cmd, env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def main():
    tmpdir = tempfile.mkdtemp(prefix="reuse_verdict_engine_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")

    # Same minimal-stub bootstrap trick test_ai_agent_registry.py uses: the DB
    # must already exist with a real umr_tasks table before SUPERBOSS_REGISTER_DB
    # is honored, else resolve_superboss_db_path() silently falls back to the
    # live production DB.
    _bootstrap = sqlite3.connect(db_path)
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

    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path

    p = run(["python3", SUPERBOSS, "init"], env)
    check("init succeeds against throwaway db", p.returncode == 0, p.stderr)

    # --- Seed one real row per source, plus a capability -----------------
    register_entity(env, {
        "entity_id": "script-alpha_report_generator_py",
        "entity_type": "script", "source_system": "server",
        "path": "/opt/veridian/scripts/alpha_report_generator.py",
        "relationships": [], "last_verified_ts": "2026-08-07T00:00:00+00:00",
        "verification_status": "VERIFIED_MATCH", "source_ref": [], "metadata": {},
    })
    register_entity(env, {
        "entity_id": "file-beta-config-hash1",
        "entity_type": "file", "source_system": "server",
        "path": "/opt/veridian/config/beta-widget-settings.json",
        "relationships": [], "last_verified_ts": "2026-08-07T00:00:00+00:00",
        "verification_status": "VERIFIED_MATCH", "source_ref": [], "metadata": {},
    })
    register_entity(env, {
        "entity_id": "github_repo-acme__gamma_service",
        "entity_type": "github_repo", "source_system": "github",
        "path": "repos/gamma-service",
        "relationships": [], "last_verified_ts": "2026-08-07T00:00:00+00:00",
        "verification_status": "VERIFIED_MATCH", "source_ref": ["census"], "metadata": {},
    })
    register_entity(env, {
        "entity_id": "vercel_project-delta_dashboard",
        "entity_type": "vercel_project", "source_system": "vercel",
        "path": None,
        "relationships": [], "last_verified_ts": "2026-08-07T00:00:00+00:00",
        "verification_status": "VERIFIED_MATCH", "source_ref": ["census"], "metadata": {},
    })
    register_entity(env, {
        "entity_id": "supabase_table-billing__epsilon_invoices",
        "entity_type": "supabase_table", "source_system": "supabase",
        "path": "billing.epsilon_invoices",
        "relationships": [], "last_verified_ts": "2026-08-07T00:00:00+00:00",
        "verification_status": "VERIFIED_MATCH", "source_ref": [], "metadata": {},
    })
    register_capability(env, {
        "capability_name": "zeta_invoice_export", "inputs": [], "business_rules": [],
        "apis": ["/opt/veridian/scripts/zeta_invoice_export.py"],
        "permissions": "internal", "ai_required": False, "confidence": 1.0,
        "version": "1.0", "owner": "test", "workflow": "zeta invoice export capability",
    })

    # --- vector_json populated automatically on write, not just backfill --
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for eid in ("script-alpha_report_generator_py", "file-beta-config-hash1",
                "github_repo-acme__gamma_service", "vercel_project-delta_dashboard",
                "supabase_table-billing__epsilon_invoices"):
        row = conn.execute("SELECT vector_json FROM wiring_registry WHERE entity_id=?", (eid,)).fetchone()
        check(f"vector_json auto-populated on write: {eid}", bool(row and row["vector_json"] and row["vector_json"] != "{}"),
              row["vector_json"] if row else "row missing")
    cap_row = conn.execute("SELECT vector_json FROM capability_registry WHERE capability_name='zeta_invoice_export'").fetchone()
    check("capability vector_json auto-populated on write", bool(cap_row and cap_row["vector_json"] and cap_row["vector_json"] != "{}"),
          cap_row["vector_json"] if cap_row else "row missing")
    conn.close()

    # --- Three-tier verdict across all four wiring sources + capability ---
    r = assess(env, "alpha report generator script")
    check("script source: near-exact match -> duplication_blocked", r["verdict"] == "duplication_blocked", r)
    check("script source: best match is the real script row", r["best_match"]["id"] == "script-alpha_report_generator_py", r)

    r = assess(env, "billing epsilon invoices supabase table")
    check("supabase_table source: near-exact match -> duplication_blocked", r["verdict"] == "duplication_blocked", r)
    check("supabase_table source: best match is the real table row", r["best_match"]["id"] == "supabase_table-billing__epsilon_invoices", r)

    r = assess(env, "zeta invoice export capability")
    check("capability_registry source: near-exact match -> duplication_blocked", r["verdict"] == "duplication_blocked", r)
    check("capability_registry source: best match is the real capability", r["best_match"]["source"] == "capability_registry", r)

    r = assess(env, "gamma service repo acme")
    check("github_repo source considered as a real candidate", r["candidates_considered"] >= 6, r)
    check("github_repo source: real relationship found (not create_authorized)", r["verdict"] in ("reuse_mandated_extend", "duplication_blocked"), r)

    r = assess(env, "delta dashboard vercel project")
    check("vercel_project source considered as a real candidate", r["candidates_considered"] >= 6, r)
    check("vercel_project source: real relationship found (not create_authorized)", r["verdict"] in ("reuse_mandated_extend", "duplication_blocked"), r)

    r = assess(env, "beta widget settings config")
    check("file source: real relationship found", r["verdict"] in ("reuse_mandated_extend", "duplication_blocked"), r)

    r = assess(env, "zzqxvwkpjnmbf totally unrelated gibberish query nine four two")
    check("no real relationship -> create_authorized", r["verdict"] == "create_authorized", r)
    check("no real relationship -> score 0.0", r["score"] == 0.0, r)

    # --- Idempotency: same intent text hits the recent-history cache ------
    # (a never-before-asked intent text -- every prior assess() call in this
    # test already wrote its own real cache record regardless of --no-cache,
    # since --no-cache only skips the READ side; this checks a genuinely
    # first-time query.)
    r1 = assess(env, "omega never seen before scheduling automation query", no_cache=False)
    check("first call (fresh cache) is not a cache hit", r1["cache_hit"] is False, r1)
    r2 = assess(env, "omega never seen before scheduling automation query", no_cache=False)
    check("second identical call is a real cache hit", r2["cache_hit"] is True, r2)
    check("cached verdict matches the real original verdict", r2["verdict"] == r1["verdict"] and r2["score"] == r1["score"], (r1, r2))

    # --- register_entity_row was the only write path (never a raw INSERT) -
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT verification_status, metadata_json FROM wiring_registry WHERE entity_type='dispatch_event' "
        "AND entity_id LIKE 'search_intent-%' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    check("idempotency record written via register_entity_row (dispatch_event row exists)", row is not None)
    if row:
        check("idempotency record has real verdict metadata", "verdict_result" in json.loads(row["metadata_json"]))
    conn.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f" - {f}")
        sys.exit(1)
    print("\nPASS")


if __name__ == "__main__":
    main()
