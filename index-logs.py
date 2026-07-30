#!/usr/bin/env python3
"""
VERIDIAN index-logs: governance item 52 (searchable_indexed_logs). Scans
ai-os/logs/*.log and ai-os/logs/*.jsonl and indexes each real (non-blank)
line into superboss-register.sqlite's log_index table + log_index_fts,
making them searchable via `superboss-register.py search` alongside
instructions/work_items/actions/system_index -- reuses that script's own
DB-connection/write-lock/id-generation primitives directly (imported, not
reimplemented) rather than a second sqlite writer, and its FTS5 CREATE
VIRTUAL TABLE + AFTER INSERT trigger convention (see init_db() there).

Idempotent via a per-file <file>.indexed_line state file -- same pattern as
superboss-register.py's own index-transcript command.

Cron (not added to crontab unilaterally -- AI_ENGINEERING_POLICY.yaml's
standing exception requires Owner confirmation before any crontab change):
    */30 * * * * /opt/veridian/scripts/run-logged.sh "index-logs" \
        /usr/bin/python3 /opt/veridian/scripts/index-logs.py \
        >> /opt/veridian/ai-os/logs/index-logs-cron.log 2>&1
"""
import glob
import hashlib
import importlib.util
import json
import os

LOG_DIR = "/opt/veridian/ai-os/logs"

_spec = importlib.util.spec_from_file_location("superboss_register", "/opt/veridian/scripts/superboss-register.py")
sbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbr)


def _extract_ts(content):
    """Best-effort only -- log_index.ts is nullable and used for display/
    filtering convenience, never blocks indexing a line."""
    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            d = json.loads(stripped)
            for key in ("ts", "timestamp"):
                if isinstance(d.get(key), str):
                    return d[key]
        except Exception:
            pass
    return None


def index_file(path, conn):
    state_path = path + ".indexed_line"
    last = 0
    if os.path.isfile(state_path):
        try:
            last = int(open(state_path).read().strip())
        except Exception:
            last = 0

    indexed = 0
    line_no = last
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line_no, raw_line in enumerate(f, start=1):
            if line_no <= last:
                continue
            content = raw_line.rstrip("\n")
            if not content.strip():
                continue
            ts = _extract_ts(content)
            # deterministic per (log_file, line_no), not sbr._new_id()'s
            # second-resolution-timestamp + 2-byte-random scheme -- that
            # collides at this call volume (many thousands of rows/run,
            # routinely several per wall-clock second).
            file_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
            lid = f"LOGIDX-{file_hash}-{line_no}"
            cur = conn.execute(
                "INSERT INTO log_index (log_index_id, log_file, line_no, ts, content) VALUES (?,?,?,?,?) "
                "ON CONFLICT(log_file, line_no) DO NOTHING",
                (lid, path, line_no, ts, content),
            )
            if cur.rowcount > 0:
                indexed += 1

    with open(state_path, "w") as f:
        f.write(str(line_no))

    return indexed, line_no


def main():
    sbr.init_db_silent()
    files = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")) + glob.glob(os.path.join(LOG_DIR, "*.jsonl")))
    results = {}
    total_indexed = 0
    with sbr._write_lock():
        conn = sbr._connect()
        for path in files:
            indexed, through_line = index_file(path, conn)
            conn.commit()
            total_indexed += indexed
            results[path] = {"indexed": indexed, "through_line": through_line}
        conn.close()
    print(json.dumps({"total_indexed": total_indexed, "files": results}))


if __name__ == "__main__":
    main()
