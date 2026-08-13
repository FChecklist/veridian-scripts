#!/usr/bin/env python3
"""
superboss_gateway.py -- Owner-directed "one gate in, one gate out" for
superboss-register.sqlite (direct instruction 2026-08-07: single input gate,
single output gate, modern-tools pattern, zero duplication).

Real problem this fixes: 46 separate scripts each open their own raw
sqlite3.connect() to the live DB (confirmed via grep, 2026-08-07). No shared
validation, no shared retry/serialization discipline, no single place to fix
a query bug once.

Real pattern (single-writer gateway / data-access layer), confirmed via
research against Datasette execute_write() queue, Go qwr, python-sqlite-async
-- all serialize writes through ONE owner while allowing unlimited
concurrent reads, matching SQLite native WAL-mode concurrency (already live
on this DB: journal_mode=wal, busy_timeout=5000, confirmed via PRAGMA before
writing this).

Design:
  - ONE process owns the write connection; single in-process lock serializes
    writes now that this is the only process opening the file this way.
  - Reads use a separate mode=ro connection, run concurrently.
  - Exposed as a local HTTP JSON API (stdlib http.server only -- fastapi,
    flask, uvicorn are NOT installed on this box, confirmed before choosing
    this) on 127.0.0.1:8790.
  - Table access is allowlisted; column names are validated against the real
    PRAGMA table_info() before use; never raw arbitrary SQL from a caller.

Scope, honestly: this is a NEW, additive gate -- nothing existing depends on
it yet, zero risk to the live Phase 1-4 priority chain. Migrating the 46
existing scripts to use it instead of raw sqlite3.connect() is real, larger,
separate follow-up work, deliberately NOT done here -- track as its own UMR
before starting so it never competes with the live priority chain.

Endpoints:
  GET  /health
  POST /read   {"table": "...", "where": {...}, "limit": N}
  POST /write  {"op": "insert"|"update", "table": "...", "values": {...}, "where": {...}}
"""
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_gateway", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()


BIND = ("127.0.0.1", 8790)

ALLOWED_TABLES = {
    "umr_tasks", "wiring_registry", "capability_registry",
    "ai_agent_registry", "knowledge_engine", "owner_priority_sequence",
    "owner_priority_override",
}

_write_lock = threading.Lock()


def _ro_conn():
    conn = sqlite3.connect(f"file:{_resolve_db_path()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_conn():
    conn = sqlite3.connect(_resolve_db_path(), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _table_columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def handle_read(body):
    table = body.get("table")
    if table not in ALLOWED_TABLES:
        return 400, {"error": f"table not allowlisted: {table}"}
    where = body.get("where") or {}
    limit = int(body.get("limit") or 100)
    limit = min(limit, 1000)
    conn = _ro_conn()
    try:
        cols = _table_columns(conn, table)
        bad = [k for k in where if k not in cols]
        if bad:
            return 400, {"error": f"unknown column(s): {bad}"}
        clauses = " AND ".join(f"\"{k}\"=?" for k in where)
        sql = f"SELECT * FROM {table}"
        if clauses:
            sql += f" WHERE {clauses}"
        sql += f" LIMIT {limit}"
        rows = conn.execute(sql, list(where.values())).fetchall()
        return 200, {"count": len(rows), "rows": [dict(r) for r in rows]}
    finally:
        conn.close()


def handle_write(body):
    op = body.get("op")
    table = body.get("table")
    if table not in ALLOWED_TABLES:
        return 400, {"error": f"table not allowlisted: {table}"}
    if op not in ("insert", "update"):
        return 400, {"error": f"unsupported op: {op}"}
    values = body.get("values") or {}
    if not values:
        return 400, {"error": "values required"}
    with _write_lock:
        conn = _rw_conn()
        try:
            cols = _table_columns(conn, table)
            bad = [k for k in values if k not in cols]
            if bad:
                return 400, {"error": f"unknown column(s): {bad}"}
            if op == "insert":
                col_list = ", ".join(f"\"{k}\"" for k in values)
                placeholders = ", ".join("?" for _ in values)
                sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
                cur = conn.execute(sql, list(values.values()))
                conn.commit()
                return 200, {"ok": True, "rowid": cur.lastrowid}
            else:
                where = body.get("where") or {}
                if not where:
                    return 400, {"error": "update requires where"}
                wbad = [k for k in where if k not in cols]
                if wbad:
                    return 400, {"error": f"unknown where column(s): {wbad}"}
                set_clause = ", ".join(f"\"{k}\"=?" for k in values)
                where_clause = " AND ".join(f"\"{k}\"=?" for k in where)
                sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
                cur = conn.execute(sql, list(values.values()) + list(where.values()))
                conn.commit()
                return 200, {"ok": True, "rows_changed": cur.rowcount}
        finally:
            conn.close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            conn = _ro_conn()
            try:
                jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            finally:
                conn.close()
            self._send(200, {"ok": True, "db": _resolve_db_path(), "journal_mode": jm})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            self._send(400, {"error": "invalid json"})
            return
        if self.path == "/read":
            status, payload = handle_read(body)
        elif self.path == "/write":
            status, payload = handle_write(body)
        else:
            status, payload = 404, {"error": "not found"}
        self._send(status, payload)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(BIND, Handler)
    print(f"superboss_gateway listening on {BIND[0]}:{BIND[1]}")
    server.serve_forever()
