#!/usr/bin/env python3
"""
test_ai_agent_registry.py -- standalone (no pytest required) proof that
ai_agent_registry.py's real one-UMR-equals-one-agent_id scoping (the
UMR-20260806-121332-6ba4 correction to UMR-20260806-121252-3207) actually
holds: same umr_id called twice mints exactly one agent_id (idempotent, DB
UNIQUE(umr_id) enforced), record-work appends to that same agent's own
memory file across multiple calls (never a second file, never another
agent's file), a different umr_id gets a different agent_id, and
check-before-dispatch's new_work_justified flips from true to false once an
agent already exists for that exact umr_id.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override -- same mechanism superboss-register.py's own DB_PATH already
supports, same convention test_dedup_constraints_2026-07-31.py already
uses) -- never touches the live
/opt/veridian/ai-os/memory/superboss-register.sqlite. Memory files are
written under a throwaway AI_AGENT_REGISTRY_MEMORY_DIR override so this test
never touches the live /opt/veridian/ai-os/memory/agents/ directory either.

Run: python3 test_ai_agent_registry.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS = os.path.join(SCRIPTS, "superboss-register.py")
AGENT_REGISTRY = os.path.join(SCRIPTS, "ai_agent_registry.py")


def run(cmd, env):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main():
    tmpdir = tempfile.mkdtemp(prefix="ai_agent_registry_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")
    memory_dir = os.path.join(tmpdir, "agents")

    # resolve_superboss_db_path() (superboss-register.py) only honors
    # SUPERBOSS_REGISTER_DB when the path it names ALREADY exists on disk,
    # is non-zero size, and already contains a real umr_tasks table --
    # otherwise it silently falls back to the real, live, production
    # /opt/veridian/ai-os/memory/superboss-register.sqlite (no error raised).
    # A brand-new tempfile.mkdtemp() path does not exist yet, so it must be
    # bootstrapped with a minimal stub umr_tasks table BEFORE the env var is
    # set and BEFORE any subprocess/in-process call resolves DB_PATH -- this
    # is exactly the trap test_dedup_constraints_2026-07-31.py's own
    # identical-looking setup does NOT bootstrap against, which this test
    # discovered (empirically, via 3 real rows landing in the live db) while
    # this file was first being written. The stub only needs the table to
    # exist by name (the resolver checks existence, not columns) -- every
    # table this test actually touches (capability_registry, ai_agent_registry)
    # is created fresh via its own real CREATE TABLE IF NOT EXISTS regardless.
    # _ensure_umr_table()'s own fast path (superboss-register.py) returns
    # immediately, without running any further CREATE/ALTER/migration
    # statement, once umr_id plus these 5 columns are all present -- so the
    # stub only needs to satisfy that exact set to make _migrate_schema()
    # (invoked by every real command via init_db()/init_db_silent()) a
    # pure no-op against this table, without needing to replicate its full
    # real column set.
    import sqlite3 as _sqlite3
    _bootstrap = _sqlite3.connect(db_path)
    _bootstrap.execute("""CREATE TABLE umr_tasks (
        umr_id TEXT PRIMARY KEY,
        last_heartbeat TEXT,
        tenant_id TEXT,
        utm_source TEXT,
        external_agent_eligible INTEGER,
        ts_relay_attempted TEXT
    )""")
    _bootstrap.commit()
    _bootstrap.close()

    # Set in the real process environment (not just a subprocess env copy)
    # BEFORE anything imports/execs superboss-register.py in-process below --
    # that module computes its own DB_PATH once, at exec time, via
    # resolve_superboss_db_path() reading os.environ directly, so this must
    # be a real env var, not merely a dict passed to subprocess.run().
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    env = dict(os.environ)
    # ai_agent_registry.py hardcodes AGENT_MEMORY_DIR (matching the fixed,
    # real deployment path convention every other script in this file uses
    # for its own DB_PATH/paths) -- this test patches it in-process via a
    # module attribute override instead of adding a second env-var seam
    # this module's production callers would never use. Every subprocess
    # call below only ever hits umr_ids that were already minted in-process
    # first (against the patched memory_dir), so record-work/check-before-
    # dispatch's own subprocess-side AGENT_MEMORY_DIR (the real, hardcoded
    # one) is never actually exercised to create a file -- see the ordering
    # note at each subprocess call site below.

    failures = []

    proc = run(["python3", SUPERBOSS, "init"], env)
    if proc.returncode != 0:
        failures.append(f"init failed: {proc.stderr}")
        print("FAIL (setup):\n" + "\n".join(failures))
        return 1

    umr_a = "UMR-20260806-121332-6ba4"
    umr_b = "UMR-20260806-121252-3207"

    import importlib.util as ilu
    spec = ilu.spec_from_file_location("ai_agent_registry_test_mod", AGENT_REGISTRY)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.AGENT_MEMORY_DIR = memory_dir

    # Hard safety check: confirm the module actually resolved to OUR temp
    # db, not a silent fallback to the live production db, before running
    # anything that writes. Abort loudly rather than ever repeat the
    # live-db-pollution near-miss the comment above describes.
    resolved_db_path = mod._superboss_register().DB_PATH
    if os.path.realpath(resolved_db_path) != os.path.realpath(db_path):
        print(f"FAIL (setup): resolved DB_PATH {resolved_db_path!r} is NOT the temp db "
              f"{db_path!r} -- refusing to proceed (would risk writing to the live db).")
        return 1

    # 1. First ensure-agent for umr_a mints a new agent.
    row1, already1 = mod.ensure_agent(umr_a, "correction implementer")
    if already1:
        failures.append("first ensure-agent for umr_a reported already_existed=True")
    expected_agent_id = "AGENT-20260806-121332-6ba4"
    if row1["agent_id"] != expected_agent_id:
        failures.append(f"agent_id mismatch: got {row1['agent_id']!r}, expected {expected_agent_id!r}")
    if row1["umr_id"] != umr_a:
        failures.append(f"umr_id mismatch: got {row1['umr_id']!r}")
    if not os.path.isfile(row1["memory_file_path"]):
        failures.append(f"memory file not created at {row1['memory_file_path']}")

    # 2. Second ensure-agent for the SAME umr_a is idempotent -- same
    #    agent_id, already_existed=True, not a second row.
    row2, already2 = mod.ensure_agent(umr_a, "different label, should be ignored")
    if not already2:
        failures.append("second ensure-agent for umr_a did not report already_existed=True")
    if row2["agent_id"] != row1["agent_id"]:
        failures.append(f"second ensure-agent for umr_a minted a DIFFERENT agent_id: {row2['agent_id']!r} != {row1['agent_id']!r}")

    # 3. A DIFFERENT umr_id gets a DIFFERENT agent_id -- never shared
    #    across UMRs.
    row3, already3 = mod.ensure_agent(umr_b, "another role")
    if already3:
        failures.append("first ensure-agent for umr_b reported already_existed=True")
    if row3["agent_id"] == row1["agent_id"]:
        failures.append("umr_a and umr_b were assigned the SAME agent_id -- scoping violated")

    # 4. record-work appends to the SAME memory file across multiple calls
    #    under the same umr_id, and bumps total_tasks_handled.
    proc = run(["python3", AGENT_REGISTRY, "record-work", "--umr-id", umr_a,
                "--entry-text", "first real work entry"], env)
    if proc.returncode != 0:
        failures.append(f"record-work call 1 failed: {proc.stderr}")
    proc2 = run(["python3", AGENT_REGISTRY, "record-work", "--umr-id", umr_a,
                 "--entry-text", "second real work entry, a retry"], env)
    if proc2.returncode != 0:
        failures.append(f"record-work call 2 failed: {proc2.stderr}")
    else:
        result2 = json.loads(proc2.stdout)
        if result2["agent_id"] != row1["agent_id"]:
            failures.append("record-work's second call routed to a different agent_id than the first")
        if result2["total_tasks_handled"] != 2:
            failures.append(f"total_tasks_handled expected 2, got {result2.get('total_tasks_handled')}")

    with open(row1["memory_file_path"], encoding="utf-8") as f:
        memory_content = f.read()
    if "first real work entry" not in memory_content or "second real work entry" not in memory_content:
        failures.append("both real work entries were not both found in the same agent memory file")

    # 5. row3's own (umr_b) memory file must NOT contain umr_a's entries --
    #    never a shared file, never overwriting another agent's memory.
    with open(row3["memory_file_path"], encoding="utf-8") as f:
        row3_memory_content = f.read()
    if "first real work entry" in row3_memory_content:
        failures.append("umr_a's work entry leaked into umr_b's own agent memory file")

    # 6. check-before-dispatch: new_work_justified is True for a brand-new
    #    umr with no existing capability/agent match, then False once an
    #    agent already exists for that exact umr_id.
    umr_c = "UMR-20260806-999999-abcd"
    proc = run(["python3", AGENT_REGISTRY, "check-before-dispatch", "--umr-id", umr_c,
                "--intent-text", "some brand new never-before-seen intent xyzzy123"], env)
    if proc.returncode != 0:
        failures.append(f"check-before-dispatch (pre) failed: {proc.stderr}")
    else:
        pre = json.loads(proc.stdout)
        if pre["new_work_justified"] is not True:
            failures.append(f"check-before-dispatch pre-mint expected new_work_justified=True, got {pre}")

    mod.ensure_agent(umr_c, "just minted")
    proc = run(["python3", AGENT_REGISTRY, "check-before-dispatch", "--umr-id", umr_c,
                "--intent-text", "some brand new never-before-seen intent xyzzy123"], env)
    if proc.returncode != 0:
        failures.append(f"check-before-dispatch (post) failed: {proc.stderr}")
    else:
        post = json.loads(proc.stdout)
        if post["new_work_justified"] is not False:
            failures.append(f"check-before-dispatch post-mint expected new_work_justified=False, got {post}")
        if not post["existing_agent_for_this_umr"] or post["existing_agent_for_this_umr"]["umr_id"] != umr_c:
            failures.append(f"check-before-dispatch post-mint did not surface the existing agent: {post}")

    # 7. Malformed umr_id is rejected, never silently minted.
    try:
        mod.ensure_agent("not-a-real-umr-id", "role")
        failures.append("ensure_agent did not raise ValueError for a malformed umr_id")
    except ValueError:
        pass

    if failures:
        print("FAIL:\n" + "\n".join(f"  - {f}" for f in failures))
        return 1

    print("PASS: all ai_agent_registry.py checks held (idempotent 1:1 UMR scoping, "
          "per-agent memory file isolation, deterministic id derivation, "
          "check-before-dispatch two-step gate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
