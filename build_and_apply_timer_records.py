#!/usr/bin/env python3
"""task-20260807-053617: live systemctl --user is-enabled/is-active re-verify
for every real veridian-* .timer unit on this server, then upsert (reuse,
never duplicate) the matching cron_job row in wiring_registry via
superboss-register.py's own register_entity_row() -- same ON CONFLICT(entity_id)
DO UPDATE path the CLI uses, called in-process here so all 24 rows share one
connection/commit instead of 24 subprocess round-trips.

Governing chain UMR-20260806-124055-bc80 (confirmed present in umr_tasks,
status=completed) is recorded as a relationship on every row touched.

launchpadlib-cache-clean.timer and systemd-tmpfiles-clean.timer are
deliberately OUT of scope: SPEC calls launchpadlib global-scope/unrelated,
neither has ever had a wiring_registry cron_job row (confirmed by query
before this script ran), and both are non-veridian system defaults, not part
of the veridian priority-chain timer set this UMR governs.
"""
import importlib.util as _ilu
import json
import os
import subprocess
import sys

SCRIPTS_DIR = "/opt/veridian/scripts"
GOVERNING_UMR = "UMR-20260806-124055-bc80"
SOURCE_REF_PRIOR = "manual-systemd-sweep-2026-08-06"
SOURCE_REF_NEW = "task-20260807-053617-live-systemctl-reverify"

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)


def list_veridian_user_timers():
    out = subprocess.run(
        ["systemctl", "--user", "list-unit-files", "--type=timer", "--no-pager", "--no-legend"],
        capture_output=True, text=True, check=True,
    ).stdout
    units = [line.split()[0] for line in out.splitlines() if line.strip()]
    return sorted(u for u in units if u.startswith("veridian-"))


def systemctl_state(unit, verb):
    r = subprocess.run(["systemctl", "--user", verb, unit], capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def main():
    units = list_veridian_user_timers()
    now = _sbr._now_iso()

    conn = _sbr._connect()
    _sbr._ensure_wiring_registry_table(conn)

    before_count = conn.execute(
        "SELECT COUNT(*) AS c FROM wiring_registry WHERE entity_type='cron_job'"
    ).fetchone()["c"]

    results = []
    for unit in units:
        entity_id = f"cron_job-{unit}"
        existing = conn.execute(
            "SELECT entity_id FROM wiring_registry WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if not existing:
            print(f"WARNING: no pre-existing wiring_registry row for {entity_id}; skipping to avoid an unreviewed new row", file=sys.stderr)
            continue

        enabled_state = systemctl_state(unit, "is-enabled")
        active_state = systemctl_state(unit, "is-active")
        path = f"/home/rajat/.config/systemd/user/{unit}"
        path_exists = os.path.isfile(path)

        entity = {
            "entity_id": entity_id,
            "entity_type": "cron_job",
            "source_system": "server",
            "path": path,
            "relationships": [
                {
                    "target_entity_id": GOVERNING_UMR,
                    "relationship_type": "governed_by",
                    "evidence": "SPEC governing chain UMR-20260806-124055-bc80 (task-20260807-053617, real cron/systemd timer disposition registration)",
                }
            ],
            "last_verified_ts": now,
            "verification_status": "VERIFIED_MATCH" if path_exists else "PATH_MISSING",
            "source_ref": [SOURCE_REF_PRIOR, SOURCE_REF_NEW],
            "metadata": {
                "unit_name": unit,
                "unit_type": "timer",
                "is_enabled": enabled_state,
                "is_active": active_state,
                "checked_via": "systemctl --user is-enabled / is-active",
                "checked_ts": now,
                "governing_umr": GOVERNING_UMR,
            },
        }
        _sbr.register_entity_row(conn, entity)
        results.append({"unit": unit, "entity_id": entity_id, "is_enabled": enabled_state, "is_active": active_state})

    conn.commit()

    after_count = conn.execute(
        "SELECT COUNT(*) AS c FROM wiring_registry WHERE entity_type='cron_job'"
    ).fetchone()["c"]
    dup_check = conn.execute(
        "SELECT entity_id, COUNT(*) AS c FROM wiring_registry WHERE entity_type='cron_job' GROUP BY entity_id HAVING c > 1"
    ).fetchall()
    conn.close()

    print(json.dumps({
        "before_count": before_count,
        "after_count": after_count,
        "rows_updated": len(results),
        "duplicate_entity_ids": [dict(r) for r in dup_check],
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
