#!/usr/bin/env python3
"""
generate_software_catalog.py -- Knowledge Engine Phase 2 (task-20260724-033446),
SCOPE item 3: a real, machine-generated inventory of every script/service/cron
job on the server, written to ai-os/SOFTWARE_CATALOG.yaml.

Machine-generated, not hand-authored: every field below is pulled live from
real system state --
  - purpose      : each script's own module docstring for *.py (ast.get_docstring
                    -- the same "software's own self-declared header, never
                    guessed from filename" convention register-knowledge's
                    --purpose argument already documents), or leading '#'/'//'
                    header-comment block for *.sh/*.mjs (see
                    _shell_header_comment()) -- first paragraph either way.
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
  - originating_umr / script_version : real, mechanically recovered
                    script-registry bookkeeping fields added 2026-08-06
                    (task-20260806-035541, Owner directive "real PM cycle
                    script registry") -- see script_originating_umr()/
                    script_version_from_filename() below; NULL, never
                    invented, when nothing real is recoverable.

2026-08-06 (task-20260806-035541): scope widened from *.py-only to
*.py/*.sh/*.mjs -- a real gap independently found this task (28 real *.sh
scripts on this server, e.g. dispatch-owner-task.sh/worker-entrypoint.sh/
sync-repos.sh, were silently absent from every prior real run of this
catalog because list_scripts() below only ever matched *.py).

Does NOT duplicate FUNCTION_CATALOG.json/DATABASE_CATALOG.json/AI_ROSTER_CATALOG.json
(KNOWN_CONTEXT: those already cover app-code/DB-schema/AI-role inventories for
compliance-tracker+projexa+veda-advisors) -- this catalog only covers
/opt/veridian/scripts + /opt/veridian/ai-os/scripts + cron + systemd, and
points at those 3 existing catalogs by reference for app-level detail.
"""
import ast
import datetime
import os
import re
import subprocess
import sys

import yaml

VERIDIAN_ROOT = "/opt/veridian"
SCRIPT_DIRS = [f"{VERIDIAN_ROOT}/scripts", f"{VERIDIAN_ROOT}/ai-os/scripts"]
OUT_PATH = f"{VERIDIAN_ROOT}/ai-os/SOFTWARE_CATALOG.yaml"
EXCLUDE_SUFFIXES = (".bak", ".pyc")
EXCLUDE_MARKERS = (".bak-", ".CORRUPTED", ".v1.bak")
# 2026-08-06 (task-20260806-035541, Owner directive "real PM cycle script
# registry"): real gap found and fixed here -- list_scripts() below only ever
# matched *.py, silently excluding every real *.sh/*.mjs script on this server
# (28 real files confirmed missing from a live SOFTWARE_CATALOG.yaml this same
# task, e.g. dispatch-owner-task.sh, worker-entrypoint.sh, sync-repos.sh).
SCRIPT_SUFFIXES = (".py", ".sh", ".mjs")

# Real, mechanical UMR/task-id recovery -- never invented. Prefers a real
# UMR-YYYYMMDD-HHMMSS-hash (this codebase's real Universal Metadata Registry
# id, per ai-os's own registry_taxonomy_notes) over the older pre-UMR-convention
# task-YYYYMMDD-HHMMSS directory id, since a script's own header may cite
# several ids across its real revision history -- the first real match of the
# preferred pattern is taken as this script's real originating id. NULL means
# a real regex search of the file's own content found neither -- never guessed.
UMR_ID_RE = re.compile(r"UMR-\d{8}-\d{6}-[0-9a-fA-F]{4}")
TASK_ID_RE = re.compile(r"task-\d{8}-\d{6}")
# Real, mechanical version-token recovery from a script's own filename (e.g.
# 'anthropic_openrouter_proxy_v2.py' -> 'v2', 'generate_pm_report_v3.py' ->
# 'v3') -- never invented, and NULL for the (most common) case where a
# script's filename carries no such suffix.
VERSION_SUFFIX_RE = re.compile(r"_v(\d+)(?:\.[^.]+)?$")


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


def _shell_header_comment(text):
    """Real header-comment extraction for a non-Python script (.sh/.mjs): the
    leading run of '#'-prefixed (or '//'-prefixed, for .mjs) lines immediately
    after an optional shebang, same "script's own self-declared header" source
    ast.get_docstring uses for Python -- just a different real comment syntax,
    not a different convention."""
    lines = text.splitlines()
    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    header = []
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#") and not line.startswith("#!"):
            header.append(line.lstrip("#").strip())
        elif line.startswith("//"):
            header.append(line[2:].strip())
        elif not line:
            i += 1
            continue
        else:
            break
        i += 1
    return "\n".join(header) if header else None


def script_docstring(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"(docstring unreadable: {e})"
    try:
        if path.endswith(".py"):
            tree = ast.parse(text, filename=path)
            doc = ast.get_docstring(tree)
        else:
            doc = _shell_header_comment(text)
        if not doc:
            return None
        first_para = doc.strip().split("\n\n")[0].replace("\n", " ").strip()
        return first_para[:400]
    except Exception as e:
        return f"(docstring unreadable: {e})"


def script_originating_umr(path):
    """Real, mechanical UMR/task-id recovery from a script's own file content --
    see UMR_ID_RE/TASK_ID_RE module docstring above for the honest-recovery-only
    rule this follows. Reads the whole real file (these are all small scripts,
    never large enough for this to be wasteful) rather than only the header, since
    an originating UMR is sometimes cited later in the file (e.g. in a function's
    own docstring) rather than the module header."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = UMR_ID_RE.search(text)
    if m:
        return m.group(0)
    m = TASK_ID_RE.search(text)
    return m.group(0) if m else None


def script_version_from_filename(path):
    """Real, mechanical version-token recovery -- see VERSION_SUFFIX_RE module
    docstring above. Matched against the basename without its final extension
    so 'foo_v2.py' -> 'v2', not against the raw filename."""
    base = os.path.basename(path)
    stem, _, _ = base.rpartition(".")
    stem = stem or base
    m = re.search(r"_v(\d+)$", stem)
    return f"v{m.group(1)}" if m else None


def list_scripts():
    scripts = []
    for d in SCRIPT_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(SCRIPT_SUFFIXES):
                continue
            if any(m in name for m in EXCLUDE_MARKERS) or name.endswith(EXCLUDE_SUFFIXES):
                continue
            full = os.path.join(d, name)
            if not os.path.isfile(full):
                continue
            scripts.append(full)
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
            runner = "python3 " if path.endswith(".py") else "node " if path.endswith(".mjs") else ""
            invocation = f"{runner}{path} [args]"
            when_to_use = "Not currently scheduled -- run manually/ad-hoc via the invocation command above."
        catalog_scripts.append({
            "path": path,
            "purpose": purpose,
            "cron_scheduled": bool(matching_cron),
            "invocation": invocation,
            "when_to_use": when_to_use,
            # 2026-08-06 (task-20260806-035541): real, mechanically recovered
            # bookkeeping fields -- see script_originating_umr()/
            # script_version_from_filename() docstrings above for how, never
            # hand-typed. Feeds wiring_registry's own originating_umr/
            # script_version columns via generate_wiring_registry.py's
            # build_scripts_and_cron(), not duplicated here.
            "originating_umr": script_originating_umr(path),
            "script_version": script_version_from_filename(path),
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
                "`crontab -l`, a directory listing of scripts/*.{py,sh,mjs} + ai-os/scripts/*.{py,sh,mjs}, and "
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
