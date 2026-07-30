#!/usr/bin/env python3
"""
VERIDIAN Prompt Gateway - Real Persistence Helpers (task-20260730-owner-engine-layer0)
========================================================================================
Closes the real gap this task's OBJECTIVE names directly: gateway.py's own
classification runs previously left no trace beyond an ephemeral --output
path (e.g. /tmp/gateway_output_20260730.json) plus the per-chat JSON file
under CHATS_DIR (real, but not in the same queryable, FTS-backed store the
rest of the system audits through).

Every function here calls a REAL, ALREADY-EXISTING CLI entrypoint via
subprocess rather than opening superboss-register.sqlite directly:
  - insert_instruction()   -> superboss-register.py log-instruction
  - append_pending_review()-> the exact same PENDING_OWNER_REVIEW.md file/
                               format directive_engine.py's own
                               note_needs_review() already writes (reused,
                               not reinvented).
  - lookup_capability_reuse() -> superboss-register.py lookup-capability
                               (the exact same real primitive
                               plan_generator.py's own _lookup_capability()
                               calls; plan_generator.py has zero real
                               callers today per a direct grep of
                               /opt/veridian -- see this task's own report
                               for why plan_generator.py's CLI itself,
                               which creates a whole plans/plan_steps DB
                               row per call and requires a globally-unique
                               --plan-name, is the wrong shape for a
                               single-instruction reuse check).
  - search_status()        -> superboss-register.py search (instructions_fts
                               + work_items_fts, already covers the
                               "instructions" table this task named) PLUS
                               one direct, read-only SELECT against
                               umr_tasks_fts (search() does not cover that
                               table today; this is a disclosed, additive,
                               SELECT-only exception, not a rewrite of
                               search()).

This is "software calling software", the same convention
dispatch_to_task_lifecycle() and directive_engine.py's run_check_duplicate_
battery() already use for calling task-gateway.py -- one real writer/reader
per table, not a second ad-hoc SQL path growing alongside it.
"""
import json
import os
import sqlite3
import subprocess

VERIDIAN_ROOT = os.environ.get("VERIDIAN_BASE", "/opt/veridian")
SCRIPTS_ROOT_DIR = os.path.join(VERIDIAN_ROOT, "scripts")
SUPERBOSS = os.path.join(SCRIPTS_ROOT_DIR, "superboss-register.py")
DB_PATH = os.path.join(VERIDIAN_ROOT, "ai-os", "memory", "superboss-register.sqlite")
PENDING_REVIEW_FILE = os.path.join(VERIDIAN_ROOT, "ai-os", "PENDING_OWNER_REVIEW.md")

_SUBPROCESS_TIMEOUT_S = 30


def _run_superboss(args):
    """Real invocation of the one real writer/reader for the instructions/
    work_items/system_index/etc. tables. Never raises -- every caller in
    this module treats a failure here as 'could not persist', logs it into
    the return value, and continues; a broken/unreachable DB must never
    crash gateway.py's own classification pipeline (same fail-open
    philosophy documented in directive_engine.py's find_in_flight_
    duplicate()/run_check_duplicate_battery())."""
    try:
        proc = subprocess.run(
            ["python3", SUPERBOSS] + args,
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        try:
            parsed = json.loads(proc.stdout) if proc.stdout else None
        except json.JSONDecodeError:
            parsed = None
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "parsed": parsed}
    except Exception as e:
        return {"returncode": None, "stdout": None, "stderr": str(e), "parsed": None}


def insert_instruction(text, source, medium, campaign="", content="", term="",
                        session_id="", metadata=None, response_summary=""):
    """Real, persisted row in the `instructions` table via superboss-register.py
    log-instruction -- the exact real CLI this task's own step 8 uses for its
    own log_instruction() call, reused here so gateway.py's decision history
    lands in the SAME real, queryable, FTS-backed store as every other
    Owner/AI instruction in the system, not a second parallel log.
    Returns {"instruction_id": ...} on success, {"error": ...} on failure --
    never raises."""
    args = [
        "log-instruction", "--text", text, "--source", source, "--medium", medium,
        "--campaign", campaign, "--content", content, "--term", term,
        "--session-id", session_id or "",
        "--metadata", json.dumps(metadata or {}),
        "--response-summary", response_summary,
    ]
    result = _run_superboss(args)
    if result["parsed"] and "instruction_id" in result["parsed"]:
        return result["parsed"]
    return {"error": "log-instruction failed", "detail": result["stderr"] or result["stdout"]}


def append_pending_review(identity, reason):
    """Reuses directive_engine.py's own note_needs_review() file + line
    format verbatim (same PENDING_OWNER_REVIEW.md, same '- {identity}:
    {reason} (logged {iso ts})' shape) so gateway.py's low-confidence/
    governance-directive flags land in the SAME real place an Owner already
    checks for anything needing their judgment, instead of a second,
    gateway-specific review file."""
    import time
    try:
        with open(PENDING_REVIEW_FILE, "a") as f:
            f.write(f"- {identity}: {reason} (logged {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})\n")
        return True
    except Exception:
        return False


def lookup_capability_reuse(intent_text):
    """The real reuse-check primitive (superboss-register.py lookup-capability)
    -- identical to what plan_generator.py's own _lookup_capability() and
    task-gateway.py's cmd_submit already call. Returns the parsed
    {"found": bool, "matches": [...]} dict, or {"found": False, "matches": [],
    "error": ...} if the call failed for any reason (fail-open: a broken
    reuse-check must never block a real, legitimate task-dispatch)."""
    result = _run_superboss(["lookup-capability", "--intent-text", intent_text])
    if result["parsed"] is not None:
        return result["parsed"]
    return {"found": False, "matches": [], "error": result["stderr"] or result["stdout"]}


def _umr_tasks_fts_search(query_text, limit=5):
    """Disclosed, additive exception: superboss-register.py's own `search`
    subcommand queries instructions_fts/work_items_fts/actions_fts/
    system_index_fts/log_index_fts but NOT umr_tasks_fts, even though the
    umr_tasks table (systemctl-execution-tier tasks) already has a real FTS5
    index built (umr_tasks_fts, confirmed via direct schema read of
    superboss-register.sqlite). This is a read-only SELECT against that
    existing FTS table -- no new table, no write -- to close that specific
    coverage gap for STATUS_QUERY lookups without modifying search() itself
    or duplicating its five-table loop for one additional table."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        q = " ".join(w for w in query_text.split() if w.isalnum() or "-" in w) or query_text
        rows = conn.execute(
            "SELECT t.umr_id, t.task_identity, t.status, t.ts_submitted, t.ts_completed "
            "FROM umr_tasks_fts f JOIN umr_tasks t ON t.rowid = f.rowid "
            "WHERE umr_tasks_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]


def search_status(query_text, limit=5):
    """Zero-AI-cost, read-only status/completion lookup: the real
    entrypoint Layer 0's STATUS_QUERY action calls instead of doing
    nothing. Covers instructions/work_items (via the existing
    superboss-register.py search command) and umr_tasks (via the
    disclosed direct FTS SELECT above) -- the three real stores this
    task named."""
    search_result = _run_superboss(["search", query_text, "--limit", str(limit)])
    instructions_matches = []
    work_items_matches = []
    if search_result["parsed"]:
        instructions_matches = search_result["parsed"].get("instructions", [])
        work_items_matches = search_result["parsed"].get("work_items", [])
    umr_matches = _umr_tasks_fts_search(query_text, limit)
    return {
        "instructions": instructions_matches,
        "work_items": work_items_matches,
        "umr_tasks": umr_matches,
    }
