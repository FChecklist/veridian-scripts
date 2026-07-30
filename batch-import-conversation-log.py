#!/usr/bin/env python3
"""
Batch-imports queued conversation-log events (UserPromptSubmit /
PostToolUse(Bash) / Stop hook payloads, written locally by Claude Code
hooks on the laptop, uploaded as one NDJSON file) into the Superboss
Register -- inserted directly via sqlite3 (bypassing
superboss-register.py's one-row-per-CLI-invocation design) so a whole
session's worth of hook events sync in ONE remote script run instead of
N separate SSH round-trips. Reuses the register's own schema/ID
conventions (INS-/ACT- prefixes, UTM columns) exactly -- not a parallel
logging path.

Owner directive 2026-07-20: manual, discipline-dependent logging is the
root cause of "you not logging every discussion." This makes logging
happen by construction (Claude Code's own hook system fires
unconditionally on every prompt/tool-call/response), not by an AI
agent remembering to call log-instruction.

Usage: batch-import-conversation-log.py <ndjson_path>
"""
import json
import sys
import os
import secrets
import sqlite3
import contextlib
import fcntl
from datetime import datetime, timezone

DB_PATH = os.environ.get("SUPERBOSS_REGISTER_DB", "/opt/veridian/ai-os/memory/superboss-register.sqlite")
_WRITE_LOCK_PATH = DB_PATH + ".writelock"


@contextlib.contextmanager
def _write_lock():
    """Same proven flock convention as scripts/superboss-register.py's
    _write_lock() (root-caused 2026-07-23 for this DB's repeated corruption:
    concurrent unlocked writers + short outer timeouts killing a writer
    mid-transaction leaves partial b-tree pages). Applied here 2026-07-24 --
    this importer is an especially high-risk unlocked writer: it holds ONE
    open transaction across hundreds of INSERTs (a full NDJSON batch) with
    no coordination against any other process writing the same DB."""
    os.makedirs(os.path.dirname(_WRITE_LOCK_PATH), exist_ok=True)
    with open(_WRITE_LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


_id_counter = 0


def _new_id(prefix):
    """Collision-proof even under tight-loop batch generation: the CLI
    version of this function (superboss-register.py) only ever generates
    one ID per process invocation, so a timestamp + 4-hex-random suffix
    was safe there. This batch importer generates hundreds of IDs in a
    single process within the same wall-clock second -- confirmed a real
    UNIQUE constraint collision on the first backfill run (395 rows into
    a 65536-value random space is a real birthday-paradox hit, not a
    freak accident). Fixed with a monotonic per-process counter appended,
    which guarantees uniqueness regardless of how fast IDs are minted."""
    global _id_counter
    _id_counter += 1
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(2)
    return f"{prefix}-{ts}-{rand}-{_id_counter:06d}"


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: batch-import-conversation-log.py <ndjson_path>"}))
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(json.dumps({"error": f"file not found: {path}"}))
        sys.exit(1)

    counts = {"instructions": 0, "actions": 0, "skipped": 0, "malformed_lines": 0}

    with _write_lock():
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    counts["malformed_lines"] += 1
                    continue
                etype = ev.get("type")
                session_id = ev.get("session_id", "") or ""
                ts = ev.get("ts") or _now_iso()
                is_backfill = bool(ev.get("backfill"))
                campaign = "conversation-log-backfill" if is_backfill else "conversation-log-auto"

                if etype == "user_prompt":
                    iid = _new_id("INS")
                    conn.execute(
                        "INSERT INTO instructions (instruction_id, ts, session_id, utm_source, utm_medium, "
                        "utm_campaign, utm_content, utm_term, raw_text, metadata_json, response_summary) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (iid, ts, session_id, "owner", "claude_code_cli_hook",
                         campaign, "user_prompt", "hook_auto,user_prompt" if not is_backfill else "backfill,user_prompt",
                         ev.get("text", ""), json.dumps({"prompt_id": ev.get("prompt_id", "")}), None),
                    )
                    counts["instructions"] += 1

                elif etype == "tool_use":
                    aid = _new_id("ACT")
                    cmd = (ev.get("command") or "")[:300]
                    content = f"tool:{ev.get('tool_name', '')} cmd:{cmd}"
                    result = ev.get("output_excerpt", "")
                    conn.execute(
                        "INSERT INTO actions (action_id, ts, work_item_id, instruction_id, utm_source, utm_medium, "
                        "utm_campaign, utm_content, utm_term, result, metadata_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, ts, None, None, "ai_agent", "claude_code_cli_hook",
                         campaign, content, "hook_auto,tool_use,bash" if not is_backfill else "backfill,tool_use,bash",
                         result,
                         json.dumps({"prompt_id": ev.get("prompt_id", ""), "tool_name": ev.get("tool_name", "")})),
                    )
                    counts["actions"] += 1

                elif etype == "turn_end":
                    aid = _new_id("ACT")
                    conn.execute(
                        "INSERT INTO actions (action_id, ts, work_item_id, instruction_id, utm_source, utm_medium, "
                        "utm_campaign, utm_content, utm_term, result, metadata_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, ts, None, None, "ai_agent", "claude_code_cli_hook",
                         campaign, "assistant_response",
                         "hook_auto,turn_end,response" if not is_backfill else "backfill,turn_end,response",
                         ev.get("assistant_message_excerpt", ""),
                         json.dumps({"prompt_id": ev.get("prompt_id", "")})),
                    )
                    counts["actions"] += 1

                else:
                    counts["skipped"] += 1

        conn.commit()
        conn.close()

    print(json.dumps(counts))


if __name__ == "__main__":
    main()
