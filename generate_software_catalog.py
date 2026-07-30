#!/usr/bin/env python3
"""
generate_software_catalog.py -- Knowledge Engine Phase 2 (task-20260724-033446),
SCOPE item 3: a real, machine-generated inventory of every script/service/cron
job on the server, written to ai-os/SOFTWARE_CATALOG.yaml.

Machine-generated, not hand-authored: every field below is pulled live from
real system state --
  - purpose      : each script's own module docstring (ast.get_docstring --
                    the same "software's own self-declared header, never
                    guessed from filename" convention register-knowledge's
                    --purpose argument already documents), first paragraph.
  - invocation    : the real `crontab -l` line that runs it (schedule +
                    wrapped command via run-logged.sh), or "not_currently_scheduled"
                    if no crontab entry references it -- never invented.
  - when_to_use   : mechanically derived from invocation (scheduled jobs:
                     "runs automatically on this schedule, no manual
                     invocation needed"; unscheduled scripts: the literal
                     `python3 <path> [args]` command, so a reader can run it
                     ad-hoc) -- not prose judgment about intent.
  - systemd units : systemctl --user list-units --all output, Description=
                    field pulled live via `systemctl --user show`.

Does NOT duplicate FUNCTION_CATALOG.json/DATABASE_CATALOG.json/AI_ROSTER_CATALOG.json
(KNOWN_CONTEXT: those already cover app-code/DB-schema/AI-role inventories for
compliance-tracker+projexa+veda-advisors) -- this catalog only covers
/opt/veridian/scripts + /opt/veridian/ai-os/scripts + cron + systemd, and
points at those 3 existing catalogs by reference for app-level detail.
"""
import ast
import datetime
import os
import subprocess
import sys

import yaml

VERIDIAN_ROOT = "/opt/veridian"
SCRIPT_DIRS = [f"{VERIDIAN_ROOT}/scripts", f"{VERIDIAN_ROOT}/ai-os/scripts"]
OUT_PATH = f"{VERIDIAN_ROOT}/ai-os/SOFTWARE_CATALOG.yaml"
EXCLUDE_SUFFIXES = (".bak", ".pyc")
EXCLUDE_MARKERS = (".bak-", ".CORRUPTED", ".v1.bak")


def run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_crontab():
    proc = run(["crontab", "-l"])
    entries = []
    if proc.returncode != 0:
        return entries
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]
        entries.append({"schedule": schedule, "command": command, "raw": line})
    return entries


def script_docstring(path):
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        doc = ast.get_docstring(tree)
        if not doc:
            return None
        first_para = doc.strip().split("\n\n")[0].replace("\n", " ").strip()
        return first_para[:400]
    except Exception as e:
        return f"(docstring unreadable: {e})"


def list_scripts():
    scripts = []
    for d in SCRIPT_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            if any(m in name for m in EXCLUDE_MARKERS) or name.endswith(EXCLUDE_SUFFIXES):
                continue
            scripts.append(os.path.join(d, name))
    return scripts


def systemd_units():
    proc = run(["systemctl", "--user", "list-units", "--all", "--no-legend", "--plain"])
    units = []
    if proc.returncode != 0:
        return units
    for line in proc.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit_name = parts[0]
        load, active, sub = parts[1], parts[2], parts[3]
        description = parts[4] if len(parts) > 4 else ""
        units.append({
            "unit": unit_name, "load": load, "active": active, "sub": sub, "description": description,
        })
    # also real .service/.timer files installed, cross-referenced against systemd's own unit-files list
    proc2 = run(["systemctl", "--user", "list-unit-files", "--no-legend", "--plain"])
    installed = []
    if proc2.returncode == 0:
        for line in proc2.stdout.splitlines():
            parts = line.split()
            if parts:
                installed.append(parts[0])
    return units, installed


def main():
    cron_entries = parse_crontab()
    scripts = list_scripts()
    units, installed_unit_files = systemd_units()

    catalog_scripts = []
    for path in scripts:
        matching_cron = [e for e in cron_entries if path in e["command"]]
        purpose = script_docstring(path)
        if matching_cron:
            invocation = matching_cron[0]["raw"]
            when_to_use = f"Runs automatically on cron schedule '{matching_cron[0]['schedule']}' -- no manual invocation needed."
        else:
            invocation = f"python3 {path} [args]"
            when_to_use = "Not currently scheduled -- run manually/ad-hoc via the invocation command above."
        catalog_scripts.append({
            "path": path,
            "purpose": purpose,
            "cron_scheduled": bool(matching_cron),
            "invocation": invocation,
            "when_to_use": when_to_use,
        })

    veridian_units = [u for u in units if "veridian" in u["unit"].lower()]

    doc = {
        "meta": {
            "id": "SOFTWARE-CATALOG",
            "generated_ts": now(),
            "generated_by": "ai-os/scripts/generate_software_catalog.py (re-run this to refresh -- do not hand-edit)",
            "purpose": (
                "Real, machine-generated inventory of every script/cron job/systemd unit on this server, "
                "built for Knowledge Engine Phase 2 (task-20260724-033446) SCOPE item 3. Sourced live from "
                "`crontab -l`, a directory listing of scripts/*.py + ai-os/scripts/*.py, and "
                "`systemctl --user list-units`/`list-unit-files` -- not hand-typed."
            ),
            "related_catalogs_referenced_not_duplicated": [
                "ai-os/FUNCTION_CATALOG.json (app-code function inventory for compliance-tracker/projexa/veda-advisors)",
                "ai-os/DATABASE_CATALOG.json (DB schema inventory)",
                "ai-os/AI_ROSTER_CATALOG.json (AI-role inventory)",
            ],
        },
        "cron_jobs": cron_entries,
        "scripts": catalog_scripts,
        "systemd_units_veridian": veridian_units,
        "systemd_unit_files_installed": installed_unit_files,
        "coverage_summary": {
            "cron_entries_total": len(cron_entries),
            "scripts_total": len(catalog_scripts),
            "scripts_cron_scheduled": sum(1 for s in catalog_scripts if s["cron_scheduled"]),
            "scripts_not_scheduled": sum(1 for s in catalog_scripts if not s["cron_scheduled"]),
            "veridian_systemd_units": len(veridian_units),
        },
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=110)

    print(f"SOFTWARE_CATALOG.yaml written: {OUT_PATH}")
    print(f"  cron_entries={len(cron_entries)} scripts={len(catalog_scripts)} "
          f"scheduled={doc['coverage_summary']['scripts_cron_scheduled']} "
          f"unscheduled={doc['coverage_summary']['scripts_not_scheduled']} "
          f"veridian_units={len(veridian_units)}")


if __name__ == "__main__":
    sys.exit(main())
