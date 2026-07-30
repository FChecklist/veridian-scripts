#!/usr/bin/env python3
"""wiring_query.py -- task-20260727-025248 (knowledge-engine/wiring-registry
integration), SCOPE item 4: one query helper answering "what rows exist for X"
across BOTH wiring_registry and knowledge_engine in a single sub-second call,
backed by each table's own ALREADY-EXISTING FTS5 index (wiring_registry_fts /
knowledge_engine_fts -- neither is new; this module is the first thing that
queries both together instead of one at a time via superboss-register.py's own
separate lookup-entity/query-knowledge CLI subcommands).

Two-stage resolution per table, the same convention lookup_entity()/
query_knowledge() in superboss-register.py already use: exact match first
(entity_id/artifact_id via PRIMARY KEY, or a real `path`/`artifact_path` string,
both O(1) via an index) falls through to a forgiving OR-based FTS5 keyword match
(reuses superboss-register.py's own _fts_query(), never reimplemented). Real,
live index only -- no new table, no cache that could itself go stale.

Usage:
    python3 scripts/wiring_query.py "CONSTITUTION.yaml"
    python3 scripts/wiring_query.py engine-05
    python3 scripts/wiring_query.py --entity-id engine-05
"""
import argparse
import importlib.util as _ilu
import json
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)


def _connect(db_path=None):
    conn = sqlite3.connect(f"file:{db_path or _sbr.DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _query_table(conn, term, table, fts_table, id_col, path_col):
    """Shared exact-then-FTS resolution for one table. Degrades to an empty,
    stage='none' result (never raises) if the table/FTS index doesn't exist yet --
    same "best-effort read" posture the rest of this DB's read paths already use."""
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (term,)).fetchone()
        if row:
            return [dict(row)], "exact_id_match"
        rows = conn.execute(f"SELECT * FROM {table} WHERE {path_col} = ?", (term,)).fetchall()
        if rows:
            return [dict(r) for r in rows], "exact_path_match"
    except sqlite3.OperationalError:
        return [], "none"

    q = _sbr._fts_query(term)
    try:
        rows = conn.execute(
            f"SELECT t.* FROM {fts_table} f JOIN {table} t ON t.rowid = f.rowid "
            f"WHERE {fts_table} MATCH ? ORDER BY rank",
            (q,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    return [dict(r) for r in rows], ("keyword_match" if rows else "none")


def query(term, db_path=None):
    """Real, sub-second combined lookup across wiring_registry + knowledge_engine for
    one term -- an entity_id/artifact_id, a real path, or a free-text keyword. Returns
    a dict with both tables' matches + which resolution stage found each; never raises
    on a miss (empty lists + stage='none') or on a DB/table that doesn't exist yet."""
    conn = _connect(db_path)
    try:
        wr_matches, wr_stage = _query_table(
            conn, term, "wiring_registry", "wiring_registry_fts", "entity_id", "path")
        ke_matches, ke_stage = _query_table(
            conn, term, "knowledge_engine", "knowledge_engine_fts", "artifact_id", "artifact_path")
    finally:
        conn.close()
    return {
        "term": term,
        "wiring_registry": {"stage": wr_stage, "count": len(wr_matches), "matches": wr_matches},
        "knowledge_engine": {"stage": ke_stage, "count": len(ke_matches), "matches": ke_matches},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("term", nargs="?", help="entity_id/artifact_id, real path, or free-text keyword")
    parser.add_argument("--entity-id", dest="entity_id", help="alias for `term`, exact entity_id/artifact_id lookup")
    args = parser.parse_args()
    term = args.entity_id or args.term
    if not term:
        parser.error("a search term (positional) or --entity-id is required")
    print(json.dumps(query(term), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
