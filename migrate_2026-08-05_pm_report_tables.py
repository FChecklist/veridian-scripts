#!/usr/bin/env python3
"""
Real, additive-only migration: creates pm_report_snapshots and
pm_decisions_pending in superboss-register.sqlite, for the
generate_pm_report_v3.py deterministic PM report mechanism
(UMR-20260805-181636-32f2).

Same safety discipline as migrate_2026-08-05_gtm_certification_categories.py:
CREATE TABLE IF NOT EXISTS only, reuses superboss-register.py's own
_connect()/_write_lock(), never touches any existing table (including the
held-corrupted file_inventory table -- this script never references it).
"""
import importlib.util
import json
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location(
    "superboss_register", "/opt/veridian/scripts/superboss-register.py"
)
sbr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbr)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    conn = sbr._connect()
    with sbr._write_lock():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_report_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                gtm_pass_count INTEGER,
                gtm_fail_count INTEGER,
                gtm_blocked_count INTEGER,
                gtm_pending_count INTEGER,
                mem_available_mb INTEGER,
                swap_free_pct REAL,
                load_1min REAL,
                load_5min REAL,
                load_15min REAL,
                dispatch_tick_active INTEGER,
                parallel_worker_count INTEGER,
                stuck_task_count INTEGER,
                tmux_session_alive INTEGER,
                emergency_stop_present INTEGER,
                db_integrity_ok INTEGER,
                umr_tasks_total INTEGER,
                ocid_canonical_registry_total INTEGER,
                report_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pm_decisions_pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_ts TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                options_json TEXT,
                recommended_option TEXT,
                related_umr TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                closed_ts TEXT,
                closed_by TEXT,
                closed_note TEXT
            )
            """
        )
        conn.commit()

    # Backfill the one currently-real open decision: the superboss-register.sqlite
    # file_inventory corruption recovery choice, held under Hard Rule 8.
    existing = conn.execute(
        "SELECT id FROM pm_decisions_pending WHERE title = ? AND status='open'",
        ("superboss-register.sqlite file_inventory corruption recovery",),
    ).fetchone()
    if existing is None:
        options = [
            {
                "option": "sqlite3 .recover",
                "detail": (
                    "Run the sqlite3 CLI's .recover command (now installed, v3.45.1, "
                    "non-root install at ~/.local/bin/sqlite3) against the live DB -- "
                    "the same method that successfully recovered this exact database "
                    "from a similar corruption incident on 2026-07-23. RECOMMENDED: "
                    "matches known-working precedent on this exact file."
                ),
                "recommended": True,
            },
            {
                "option": "Drop and let file_inventory regenerate",
                "detail": (
                    "file_inventory is a pure filesystem-inventory cache "
                    "(path/size/mtime/hash), refreshed every ~20min by the already-"
                    "active veridian-cron-file-inventory.service/.timer. A prior "
                    "attempt to DROP TABLE it directly failed with the same "
                    "'database disk image is malformed' error, so this option may "
                    "require .recover or a lower-level tool first regardless."
                ),
                "recommended": False,
            },
            {
                "option": "Restore from the last known-good backup",
                "detail": (
                    "Last real full backup predating the corruption: "
                    "superboss-register.sqlite.20260803.bak (2026-08-03). Would lose "
                    "all umr_tasks/gtm_certification_categories/ocid_canonical_registry "
                    "writes from 08-04 and 08-05 -- a real, large amount of this "
                    "session's own work. Least preferred given the other two options "
                    "don't require any data loss."
                ),
                "recommended": False,
            },
        ]
        with sbr._write_lock():
            conn.execute(
                """
                INSERT INTO pm_decisions_pending
                (opened_ts, title, detail, options_json, recommended_option, related_umr, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    now_iso(),
                    "superboss-register.sqlite file_inventory corruption recovery",
                    (
                        "Real, confirmed, contained corruption: exactly 1 of 88 tables "
                        "(file_inventory, a non-load-bearing filesystem cache) fails "
                        "PRAGMA integrity_check and even a plain DROP TABLE. All 87 other "
                        "tables, including everything this session's OCID-020 GTM "
                        "certification work depends on, read/write normally. Held under "
                        "Hard Rule 8 since 2026-08-05T16:2x. sqlite3 CLI now installed "
                        "and ready (non-root, v3.45.1) but not yet run against the live DB."
                    ),
                    json.dumps(options),
                    "sqlite3 .recover",
                    "UMR-20260805-163026-14f1",
                ),
            )
            conn.commit()
        print("backfilled 1 row into pm_decisions_pending")
    else:
        print("pm_decisions_pending row already exists, not duplicating")

    snap_count = conn.execute("SELECT COUNT(*) FROM pm_report_snapshots").fetchone()[0]
    dec_count = conn.execute("SELECT COUNT(*) FROM pm_decisions_pending").fetchone()[0]
    print(json.dumps({"pm_report_snapshots_rows": snap_count, "pm_decisions_pending_rows": dec_count}))


if __name__ == "__main__":
    main()
