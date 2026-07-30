#!/usr/bin/env python3
"""SAP module/report mapping metadata store. This is the durable, queryable
knowledge base z.ai's research fills in -- distinct from the generic
external_ai_state_machine.py raw-file safety net (which still receives every
chunk verbatim first, so nothing is ever lost even if a chunk fails to
parse into this structured schema).

Design: z.ai outputs one YAML document per SAP module (via the chunk
protocol from external_ai_state_machine.py). This script's ingest_chunk()
parses that YAML and upserts real rows into sap_modules/sap_reports.
Malformed YAML never crashes silently -- the raw chunk is already safely on
disk via save_chunk() regardless, and ingest failures are logged, not
swallowed.
"""
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = "/opt/veridian/ai-os/memory/sap_mapping.sqlite"

VALID_STUDY_STATUS = {"PENDING", "IN_PROGRESS", "DONE"}
VALID_MAPPING_STATUS = {"NOT_MAPPED", "PARTIALLY_MAPPED", "FULLY_MAPPED", "NOT_APPLICABLE"}
VALID_PRIORITY = {"HIGH", "MEDIUM", "LOW"}


class IngestError(Exception):
    pass


def get_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sap_modules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            veridian_equivalent_area TEXT,
            study_status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(study_status IN ('PENDING','IN_PROGRESS','DONE')),
            source_chunk_file TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sap_reports (
            id TEXT PRIMARY KEY,
            module_id TEXT NOT NULL REFERENCES sap_modules(id) ON DELETE CASCADE,
            report_name TEXT NOT NULL,
            sap_transaction_code TEXT,
            business_purpose TEXT,
            calculation_logic TEXT,
            input_data_required TEXT,
            output_format TEXT,
            source_urls TEXT,
            veridian_mapping_status TEXT NOT NULL DEFAULT 'NOT_MAPPED'
                CHECK(veridian_mapping_status IN ('NOT_MAPPED','PARTIALLY_MAPPED','FULLY_MAPPED','NOT_APPLICABLE')),
            veridian_existing_equivalent TEXT,
            veridian_gap_notes TEXT,
            implementation_notes TEXT,
            priority TEXT NOT NULL DEFAULT 'MEDIUM' CHECK(priority IN ('HIGH','MEDIUM','LOW')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reports_module ON sap_reports(module_id);

        CREATE TABLE IF NOT EXISTS ingest_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            status TEXT NOT NULL CHECK(status IN ('OK','PARSE_ERROR','VALIDATION_ERROR')),
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "db_path": DB_PATH}))


def _log_ingest(source_file, status, detail):
    conn = get_db()
    conn.execute(
        "INSERT INTO ingest_log (source_file, status, detail) VALUES (?,?,?)",
        (source_file, status, detail),
    )
    conn.commit()
    conn.close()


def ingest_chunk(chunk_file_path: str) -> dict:
    """Parses one z.ai-produced YAML chunk (one module + its reports) and
    upserts into the structured store. The raw chunk file itself is NEVER
    touched or deleted here -- it was already saved verbatim by
    external_ai_state_machine.py's save_chunk() before this ever runs, so a
    parse failure here loses nothing; it just means the structured DB isn't
    updated yet and needs a human/re-prompt to fix the format."""
    import yaml

    try:
        with open(chunk_file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        _log_ingest(chunk_file_path, "PARSE_ERROR", str(e))
        raise IngestError(f"Could not parse {chunk_file_path} as YAML: {e}")

    if not isinstance(data, dict) or "module" not in data:
        _log_ingest(chunk_file_path, "VALIDATION_ERROR", "missing top-level 'module' key")
        raise IngestError(f"{chunk_file_path}: expected a top-level 'module:' key, got {type(data)}")

    mod = data["module"]
    for required in ("id", "name"):
        if required not in mod:
            _log_ingest(chunk_file_path, "VALIDATION_ERROR", f"module missing '{required}'")
            raise IngestError(f"{chunk_file_path}: module.{required} is required")

    conn = get_db()
    conn.execute(
        """INSERT INTO sap_modules (id, name, description, veridian_equivalent_area, study_status, source_chunk_file, updated_at)
           VALUES (?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, description=excluded.description,
             veridian_equivalent_area=excluded.veridian_equivalent_area,
             study_status=excluded.study_status, source_chunk_file=excluded.source_chunk_file,
             updated_at=datetime('now')""",
        (
            mod["id"], mod["name"], mod.get("description"),
            mod.get("veridian_equivalent_area"),
            mod.get("study_status", "DONE") if mod.get("study_status") in VALID_STUDY_STATUS else "DONE",
            chunk_file_path,
        ),
    )

    reports = data.get("reports", [])
    saved_report_ids = []
    for r in reports:
        if "id" not in r or "report_name" not in r:
            continue  # skip malformed individual entries rather than failing the whole module
        mapping_status = r.get("veridian_mapping_status", "NOT_MAPPED")
        if mapping_status not in VALID_MAPPING_STATUS:
            mapping_status = "NOT_MAPPED"
        priority = r.get("priority", "MEDIUM")
        if priority not in VALID_PRIORITY:
            priority = "MEDIUM"

        conn.execute(
            """INSERT INTO sap_reports
               (id, module_id, report_name, sap_transaction_code, business_purpose,
                calculation_logic, input_data_required, output_format, source_urls,
                veridian_mapping_status, veridian_existing_equivalent, veridian_gap_notes,
                implementation_notes, priority, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 report_name=excluded.report_name, sap_transaction_code=excluded.sap_transaction_code,
                 business_purpose=excluded.business_purpose, calculation_logic=excluded.calculation_logic,
                 input_data_required=excluded.input_data_required, output_format=excluded.output_format,
                 source_urls=excluded.source_urls, veridian_mapping_status=excluded.veridian_mapping_status,
                 veridian_existing_equivalent=excluded.veridian_existing_equivalent,
                 veridian_gap_notes=excluded.veridian_gap_notes, implementation_notes=excluded.implementation_notes,
                 priority=excluded.priority, updated_at=datetime('now')""",
            (
                r["id"], mod["id"], r["report_name"], r.get("sap_transaction_code"),
                r.get("business_purpose"), r.get("calculation_logic"), r.get("input_data_required"),
                r.get("output_format"), json.dumps(r.get("source_urls", [])),
                mapping_status, r.get("veridian_existing_equivalent"), r.get("veridian_gap_notes"),
                r.get("implementation_notes"), priority,
            ),
        )
        saved_report_ids.append(r["id"])

    conn.commit()
    conn.close()
    _log_ingest(chunk_file_path, "OK", f"module={mod['id']} reports={len(saved_report_ids)}")

    result = {"module_id": mod["id"], "reports_ingested": saved_report_ids}
    print(json.dumps(result, indent=2))
    return result


def resume_snapshot() -> str:
    """Builds the real, current state of the mapping effort as plain text,
    meant to be embedded fresh into the resume prompt every time. This is
    what makes 'paste the same prompt again after a break' actually work --
    z.ai is told exactly what's already mapped and what's left, derived
    from real DB state, not from its own memory (which it doesn't have
    across sessions)."""
    conn = get_db()
    modules = conn.execute("SELECT * FROM sap_modules ORDER BY id").fetchall()
    lines = []
    if not modules:
        lines.append("No modules mapped yet. This is a fresh start.")
    else:
        for m in modules:
            report_count = conn.execute(
                "SELECT COUNT(*) as c FROM sap_reports WHERE module_id = ?", (m["id"],)
            ).fetchone()["c"]
            lines.append(f"- {m['id']} ({m['name']}): {m['study_status']}, {report_count} reports mapped so far.")
    conn.close()
    result = "\n".join(lines)
    print(result)
    return result


def status_report() -> dict:
    conn = get_db()
    modules = conn.execute("SELECT * FROM sap_modules").fetchall()
    reports = conn.execute("SELECT * FROM sap_reports").fetchall()
    by_priority = {}
    by_mapping_status = {}
    for r in reports:
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
        by_mapping_status[r["veridian_mapping_status"]] = by_mapping_status.get(r["veridian_mapping_status"], 0) + 1
    conn.close()
    result = {
        "total_modules": len(modules),
        "total_reports": len(reports),
        "by_priority": by_priority,
        "by_mapping_status": by_mapping_status,
        "high_priority_not_mapped": [
            {"id": r["id"], "name": r["report_name"], "module": r["module_id"]}
            for r in reports if r["priority"] == "HIGH" and r["veridian_mapping_status"] == "NOT_MAPPED"
        ],
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    ic = sub.add_parser("ingest-chunk")
    ic.add_argument("--file", required=True)
    sub.add_parser("resume-snapshot")
    sub.add_parser("status-report")

    args = p.parse_args()
    try:
        if args.command == "init-db":
            init_db()
        elif args.command == "ingest-chunk":
            ingest_chunk(args.file)
        elif args.command == "resume-snapshot":
            resume_snapshot()
        elif args.command == "status-report":
            status_report()
    except IngestError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
