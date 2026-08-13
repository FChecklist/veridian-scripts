#!/usr/bin/env python3
"""
generate_wiring_registry.py -- Wiring Engine Phase 0 (task-20260725-032718-
wiring-engine-phase0-schema-registry), SCOPE item 2: mechanically populates
ai-os/WIRING_ENGINE_SCHEMA_2026-07-25.yaml's entity_record_schema FROM 8 real,
already-existing data sources -- never hand-authored:

  DATABASE_CATALOG.json          -> entity_type=table
  FUNCTION_CATALOG.json          -> entity_type=function (+ implements_engine
                                     cross-reference against engine_inventory)
  AI_ROSTER_CATALOG.json         -> entity_type=ai_role
  20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml
                                  -> entity_type=engine, entity_type=gateway
                                     (+ shares_implementation_with when two
                                     engines/gateways cite the same real file)
  ROUTE_REGISTRY_SCHEMA_2026-07-24.yaml
                                  -> entity_type=route
  SOFTWARE_CATALOG.yaml          -> entity_type=script, entity_type=cron_job
  knowledge_engine (live sqlite table, scripts/superboss-register.py)
                                  -> entity_type=file, one per existing row
                                     (verification_status/last_verified_ts
                                     reused verbatim, not recomputed)
  capability_registry (live sqlite table, same DB)
                                  -> no new entity_type (not in this schema's
                                     closed enum) -- cross-checked against
                                     route entities' capability_name

Every "file" entity referenced by more than one source (engine/gateway
exists_as, route source/destination, table/function/ai_role defining file,
script path, cron_job command, knowledge_engine artifact_path) collapses into
ONE entity keyed by its real, live-verified absolute path -- not duplicated
per source. Idempotent: rewrites ai-os/WIRING_ENGINE_REGISTRY_2026-07-25.json
from scratch every run (a full snapshot; see WIRING_ENGINE_SCHEMA's own
generation.idempotent note for why Phase 0 does not need incremental diffing).

Usage: python3 scripts/generate_wiring_registry.py [--out PATH]
Exit code 0 on success. Prints a JSON summary (counts per entity_type +
relationship_type + total) to stdout.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import yaml

VERIDIAN_ROOT = "/opt/veridian"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
REPO_AI_OS = os.path.join(REPO_ROOT, "ai-os")
# os.path.exists (not isdir) -- a git WORKTREE checkout (like this task's own
# workspace) has .git as a FILE ("gitdir: ..."), not a directory. The isdir-only
# check this used to be silently treated every worktree as a non-git deployment
# and fell through to MIRROR_AI_OS below, so local, not-yet-merged edits made in
# a worktree (e.g. this same phase's own ROUTE_REGISTRY_SCHEMA changes) were
# invisible to this script even when run directly against that worktree -- a
# real bug found and fixed by WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml phase_1.
IS_GIT_CHECKOUT = os.path.exists(os.path.join(REPO_ROOT, ".git"))
# The real, sync-repos.sh-kept-current (every 2h cron) git mirror -- same
# second location scripts/auto_phase_continuation.py's own PLAN_DIRS already
# reads. /opt/veridian/ai-os/ itself is NOT git-tracked and was confirmed this
# run to drift from it (its 20_ENGINES_10_GATEWAYS_PHASE_PLAN copy still lacks
# the gateway_inventory block a later, already-merged phase added).
MIRROR_AI_OS = f"{VERIDIAN_ROOT}/repos/claude-control/ai-os"


def resolve_doc_path(filename):
    """When run from an actual git checkout (this task's own workspace,
    possibly with not-yet-merged local changes), that checkout's own ai-os/
    is authoritative. When run from a flat, non-git deployment (this
    script's own live cron copy at /opt/veridian/scripts/), prefer the real
    git mirror over the drifted, non-git-tracked /opt/veridian/ai-os/."""
    if IS_GIT_CHECKOUT:
        return os.path.join(REPO_AI_OS, filename)
    mirror_path = os.path.join(MIRROR_AI_OS, filename)
    if os.path.isfile(mirror_path):
        return mirror_path
    return os.path.join(REPO_AI_OS, filename)


# The 3 huge catalogs are machine-generated live artifacts, never committed to
# git (too large, regenerated from live app code) -- only reachable at their
# real absolute /opt/veridian path. The knowledge_engine/capability_registry
# tables are likewise only real as the one live SQLite DB.
DATABASE_CATALOG = f"{VERIDIAN_ROOT}/ai-os/DATABASE_CATALOG.json"
FUNCTION_CATALOG = f"{VERIDIAN_ROOT}/ai-os/FUNCTION_CATALOG.json"
AI_ROSTER_CATALOG = f"{VERIDIAN_ROOT}/ai-os/AI_ROSTER_CATALOG.json"
ENGINES_GATEWAYS_PLAN = resolve_doc_path("20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml")
ROUTE_REGISTRY_SCHEMA = resolve_doc_path("ROUTE_REGISTRY_SCHEMA_2026-07-24.yaml")
# 2026-08-06 (task-20260806-035541, real bug found and fixed this task):
# SOFTWARE_CATALOG.yaml was wrongly grouped with the hand-maintained planning
# docs above (resolve_doc_path(), which prefers the git mirror when this
# script isn't run from inside veridian-scripts' own checkout) even though
# it belongs with the 3 machine-generated catalogs in the comment above --
# generate_software_catalog.py ALWAYS writes it straight to the real
# {VERIDIAN_ROOT}/ai-os/SOFTWARE_CATALOG.yaml path (hardcoded OUT_PATH),
# never to the git mirror, so the mirror copy is never refreshed and was
# independently confirmed this task to be 40 scripts/33KB/dated 2026-07-24 --
# over 2 weeks stale against the real, live 101+-script catalog. Every real
# script-entity backfill before this fix (including this task's own
# generate_wiring_registry.py runs on a non-veridian-scripts-checkout host,
# which is the live server's own normal case) silently used that stale copy.
SOFTWARE_CATALOG = f"{VERIDIAN_ROOT}/ai-os/SOFTWARE_CATALOG.yaml"
# vercel_project/github_repo source (task-20260726-162252-extend-wiring-engine-to-full-system--ser,
# full-system extension) -- the real, fresh `vercel`/`gh` CLI census is an inline registries[]
# entry in MASTER_INDEX.yaml itself (no separate tracking file, per that census's own path note),
# so this generator reads it the same resolve_doc_path()-aware way as every other doc source above.
MASTER_INDEX = resolve_doc_path("MASTER_INDEX.yaml")
DB_PATH = f"{VERIDIAN_ROOT}/ai-os/memory/superboss-register.sqlite"

DEFAULT_OUT = os.path.join(REPO_AI_OS, "WIRING_ENGINE_REGISTRY_2026-07-25.json")

VALID_VERIFICATION = {"VERIFIED_MATCH", "HASH_DRIFTED", "PATH_MISSING", "UNVERIFIED"}

# task-20260727-025248 (knowledge-engine/wiring-registry integration): the tags
# build_governance_docs() below treats as "this knowledge_engine row is a
# governance/constitution doc" -- real, already-applied tags from
# knowledge_registry_multisource.py's own SERVER-source seeding, not a new taxonomy.
GOVERNANCE_TAGS = {"governance", "constitution"}

# Phase 3 live-wiring (ai-os/WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml
# phase_3_wiring_registry_live_wiring): reuse scripts/superboss-register.py's own
# wiring_registry table DDL + upsert logic as the single source of truth (never
# redefine the schema here) -- loaded via importlib since its filename has a
# hyphen and can't be a normal `import` target.
import importlib.util as _ilu

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def slug(s):
    return "".join(c if c.isalnum() else "_" for c in s).strip("_").lower()


def normalize_path(p):
    if not p:
        return p
    return p if p.startswith("/") else os.path.join(VERIDIAN_ROOT, p)


def source_system_for_path(p):
    return "github" if ("repos/" in p or p.startswith(f"{VERIDIAN_ROOT}/repos/")) else "server"


def path_exists(p):
    return os.path.exists(normalize_path(p))


def parse_ke_tags(tags):
    """knowledge_engine.tags is stored as a JSON-encoded list (register-knowledge's own
    --tags help text: "stored as a JSON-encoded list") -- e.g. '["governance",
    "constitution", "source:SERVER"]', never a bare comma-separated string. Real,
    live-verified 2026-07-27 (task-20260727-025248): all 343 current rows parse as a
    JSON list; this was a real pre-existing bug in this file (both here and in
    build_from_knowledge_engine below used to do `(tags or "").split(",")` directly on
    that JSON string, which can never match a plain tag like "governance" or
    "source:VERCEL" against the bracketed/quoted fragments .split(",") produces -- so
    source_system for every VERCEL/SUPABASE/GITHUB-tagged knowledge_engine row silently
    fell through to the "server" default, and build_governance_docs() below found zero
    governance-tagged rows instead of the real 9). Falls back to comma-split only for a
    malformed/non-JSON value, so a future caller that ever does pass a bare CSV string
    degrades instead of crashing."""
    if not tags:
        return []
    try:
        parsed = json.loads(tags)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [t.strip() for t in tags.split(",") if t.strip()]


def _hash_file_bytes(abs_path):
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_content_hash(raw_paths):
    """Real sha256 over live file content for one or more real paths -- multi-path
    engine/gateway entities (whose own `path` field is already "; ".join(exists_as))
    pass every exists_as entry here, combined into ONE hash. Lets a re-run of this
    generator tell "content changed" apart from "path still exists": verification_status
    alone only ever checked existence (VERIFIED_MATCH/PATH_MISSING/UNVERIFIED), never
    content, for anything except knowledge_engine's own separate content_hash column
    and route's dependency_validation_status. Directories (some engine exists_as
    entries are directories, e.g. `services/doc-processing`) are walked recursively in
    sorted order for determinism. Each real path/file's own path string is folded into
    the hash alongside its bytes, so renaming a file changes the hash even when its
    content is byte-identical to some other file. Returns None only when NONE of the
    given paths resolve to anything real on disk -- an entity with nothing real to hash
    (verification_status already carries PATH_MISSING for that case)."""
    h = hashlib.sha256()
    found_any = False
    for raw in sorted(p for p in raw_paths if p):
        abs_path = normalize_path(raw)
        if os.path.isfile(abs_path):
            found_any = True
            h.update(abs_path.encode())
            h.update(_hash_file_bytes(abs_path).encode())
        elif os.path.isdir(abs_path):
            for root, dirs, files in os.walk(abs_path):
                dirs.sort()
                for fname in sorted(files):
                    fpath = os.path.join(root, fname)
                    found_any = True
                    h.update(fpath.encode())
                    h.update(_hash_file_bytes(fpath).encode())
    return h.hexdigest() if found_any else None


class Registry:
    """Accumulates entities and de-duplicates file entities by their real
    normalized absolute path, so a path cited by N sources becomes ONE
    entity carrying N source_ref entries, not N rows."""

    def __init__(self):
        self.entities = []
        self.path_to_id = {}

    def add(self, entity):
        self.entities.append(entity)
        return entity["entity_id"]

    def get_or_create_file(self, raw_path, source_ref):
        abs_path = normalize_path(raw_path)
        if abs_path in self.path_to_id:
            eid = self.path_to_id[abs_path]
            for e in self.entities:
                if e["entity_id"] == eid:
                    if source_ref not in e["source_ref"]:
                        e["source_ref"].append(source_ref)
                    return eid
        eid = f"file-{hashlib.sha1(abs_path.encode()).hexdigest()[:12]}"
        self.path_to_id[abs_path] = eid
        self.add({
            "entity_id": eid,
            "entity_type": "file",
            "source_system": source_system_for_path(abs_path),
            "path": abs_path,
            "relationships": [],
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if os.path.exists(abs_path) else "PATH_MISSING",
            "source_ref": [source_ref],
            "metadata": None,
        })
        return eid

    def find_by_id(self, eid):
        for e in self.entities:
            if e["entity_id"] == eid:
                return e
        return None


def load_engines_gateways():
    with open(ENGINES_GATEWAYS_PLAN) as f:
        return yaml.safe_load(f)


def load_route_registry():
    with open(ROUTE_REGISTRY_SCHEMA) as f:
        return yaml.safe_load(f)


def load_software_catalog():
    with open(SOFTWARE_CATALOG) as f:
        return yaml.safe_load(f)


def load_master_index():
    with open(MASTER_INDEX) as f:
        return yaml.safe_load(f)


def build_engines_and_gateways(reg, doc):
    """entity_type=engine, entity_type=gateway + shares_implementation_with
    whenever two engine/gateway entities cite the exact same real file."""
    path_owners = {}  # abs_path -> [ (entity_id, kind) ]
    engine_ids_by_no = {}
    gateway_ids_by_id = {}

    for row in doc.get("engine_inventory", []):
        eid = f"engine-{row['engine_no']:02d}"
        engine_ids_by_no[row["engine_no"]] = eid
        rels = []
        for p in row.get("exists_as", []):
            fid = reg.get_or_create_file(p, "engine_inventory")
            rels.append({"target_entity_id": fid, "relationship_type": "implemented_by", "evidence": p})
            path_owners.setdefault(normalize_path(p), []).append((eid, row["engine_name"]))
        reg.add({
            "entity_id": eid,
            "entity_type": "engine",
            "source_system": "server",
            "path": "; ".join(row.get("exists_as", [])) or None,
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if row.get("verified_on_disk") else "UNVERIFIED",
            "source_ref": ["engine_inventory"],
            "content_hash": compute_content_hash(row.get("exists_as", [])),
            "metadata": {"engine_no": row["engine_no"], "engine_name": row["engine_name"],
                         "purpose": row.get("purpose"), "coverage": row.get("coverage")},
        })

    for row in doc.get("gateway_inventory", []):
        gid = f"gateway-{row['gateway_id']}"
        gateway_ids_by_id[row["gateway_id"]] = gid
        rels = []
        for p in row.get("exists_as", []):
            fid = reg.get_or_create_file(p, "gateway_inventory")
            rels.append({"target_entity_id": fid, "relationship_type": "implemented_by", "evidence": p})
            path_owners.setdefault(normalize_path(p), []).append((gid, row["gateway_name"]))
        reg.add({
            "entity_id": gid,
            "entity_type": "gateway",
            "source_system": "server",
            "path": "; ".join(row.get("exists_as", [])) or None,
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if row.get("verified_on_disk") else "UNVERIFIED",
            "source_ref": ["gateway_inventory"],
            "content_hash": compute_content_hash(row.get("exists_as", [])),
            "metadata": {"gateway_no": row["gateway_no"], "gateway_id": row["gateway_id"],
                         "gateway_name": row["gateway_name"], "purpose": row.get("purpose"),
                         "coverage": row.get("coverage")},
        })

    # shares_implementation_with: two distinct engine/gateway entities citing
    # the exact same real path (a fact, never an inferred call relationship --
    # see WIRING_ENGINE_SCHEMA meta.honest_limitation).
    shared_count = 0
    for abs_path, owners in path_owners.items():
        distinct_ids = sorted(set(o[0] for o in owners))
        if len(distinct_ids) < 2:
            continue
        for i in range(len(distinct_ids)):
            for j in range(len(distinct_ids)):
                if i == j:
                    continue
                e = reg.find_by_id(distinct_ids[i])
                e["relationships"].append({
                    "target_entity_id": distinct_ids[j],
                    "relationship_type": "shares_implementation_with",
                    "evidence": f"both cite {abs_path}",
                })
        shared_count += 1

    return engine_ids_by_no, gateway_ids_by_id


def build_tables(reg):
    """entity_type=supabase_table (renamed from Phase 0's plain `table` by this
    task's full-system extension -- every row this function has ever emitted
    already carried source_system=supabase, so a separate supabase_table type
    sourced from this same catalog would have duplicated an already-modeled
    entity; see WIRING_ENGINE_SCHEMA_2026-07-25.yaml meta.full_system_extension_2026_07_26)."""
    if not os.path.isfile(DATABASE_CATALOG):
        print(f"  ! {DATABASE_CATALOG} not found, skipping supabase_table entities", file=sys.stderr)
        return 0
    with open(DATABASE_CATALOG) as f:
        cat = json.load(f)
    source_file = cat.get("source_file")
    file_id = reg.get_or_create_file(source_file, "database_catalog") if source_file else None
    count = 0
    for t in cat.get("tables", []):
        eid = f"supabase_table-{slug(t.get('schema', 'public'))}__{slug(t['table_name'])}"
        rels = []
        if file_id:
            rels.append({"target_entity_id": file_id, "relationship_type": "defined_in", "evidence": source_file})
        reg.add({
            "entity_id": eid,
            "entity_type": "supabase_table",
            "source_system": "supabase",
            "path": f"{t.get('schema', 'public')}.{t['table_name']}",
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if (file_id and path_exists(source_file)) else "UNVERIFIED",
            "source_ref": ["database_catalog"],
            "metadata": {"export_name": t.get("export_name"), "column_count": t.get("column_count")},
        })
        count += 1
    return count


def build_functions(reg, engine_ids_by_no, engine_inventory):
    if not os.path.isfile(FUNCTION_CATALOG):
        print(f"  ! {FUNCTION_CATALOG} not found, skipping function entities", file=sys.stderr)
        return 0
    with open(FUNCTION_CATALOG) as f:
        cat = json.load(f)
    # source_root e.g. "src/ (compliance-tracker)" -- every function's own
    # "file" field is relative to repos/compliance-tracker.
    repo_prefix = "repos/compliance-tracker"

    # Pre-build (engine_no, real_exists_as_path) pairs for prefix matching --
    # cheap mechanical cross-reference, not a guess.
    engine_paths = [(row["engine_no"], p) for row in engine_inventory for p in row.get("exists_as", [])]

    count = 0
    for fn in cat.get("functions", []):
        rel_path = f"{repo_prefix}/{fn['file']}"
        abs_path = normalize_path(rel_path)
        fn_key = f"{rel_path}:{fn.get('line')}:{fn['name']}"
        eid = f"function-{hashlib.sha1(fn_key.encode()).hexdigest()[:12]}"
        file_id = reg.get_or_create_file(rel_path, "function_catalog")
        rels = [{"target_entity_id": file_id, "relationship_type": "defined_in", "evidence": rel_path}]
        for engine_no, ep in engine_paths:
            ep_abs = normalize_path(ep)
            if abs_path == ep_abs or abs_path.startswith(ep_abs.rstrip("/") + "/"):
                rels.append({
                    "target_entity_id": engine_ids_by_no.get(engine_no),
                    "relationship_type": "implements_engine",
                    "evidence": f"{rel_path} matches engine exists_as {ep}",
                })
        reg.add({
            "entity_id": eid,
            "entity_type": "function",
            "source_system": "github",
            "path": rel_path,
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if os.path.exists(abs_path) else "PATH_MISSING",
            "source_ref": ["function_catalog"],
            "metadata": {"name": fn["name"], "kind": fn.get("kind"), "exported": fn.get("exported"),
                         "async": fn.get("async"), "line": fn.get("line")},
        })
        count += 1
    return count


def build_ai_roles(reg):
    if not os.path.isfile(AI_ROSTER_CATALOG):
        print(f"  ! {AI_ROSTER_CATALOG} not found, skipping ai_role entities", file=sys.stderr)
        return 0
    with open(AI_ROSTER_CATALOG) as f:
        cat = json.load(f)
    source_file = cat.get("source_file")
    file_id = reg.get_or_create_file(source_file, "ai_roster_catalog") if source_file else None
    count = 0
    for r in cat.get("roles", []):
        eid = f"ai_role-{slug(r['roleKey'])}"
        rels = []
        if file_id:
            rels.append({"target_entity_id": file_id, "relationship_type": "defined_in", "evidence": source_file})
        reg.add({
            "entity_id": eid,
            "entity_type": "ai_role",
            "source_system": "github",
            "path": r["roleKey"],
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if (file_id and path_exists(source_file)) else "UNVERIFIED",
            "source_ref": ["ai_roster_catalog"],
            "metadata": {"team": r.get("team"), "title": r.get("title"), "model": r.get("model"),
                         "isHuman": r.get("isHuman"), "escalationLevel": r.get("escalationLevel")},
        })
        count += 1
    return count


def build_routes(reg, engine_ids_by_no, gateway_ids_by_id, cap_by_name):
    if not os.path.isfile(ROUTE_REGISTRY_SCHEMA):
        print(f"  ! {ROUTE_REGISTRY_SCHEMA} not found, skipping route entities", file=sys.stderr)
        return 0
    doc = load_route_registry()
    count = 0
    for r in doc.get("populated_routes", []):
        eid = f"route-{r['route_id']}"
        src_id = reg.get_or_create_file(r["source"], "route_registry_schema")
        dst_id = reg.get_or_create_file(r["destination"], "route_registry_schema")
        rels = [
            {"target_entity_id": src_id, "relationship_type": "originates_at", "evidence": r["source"]},
            {"target_entity_id": dst_id, "relationship_type": "terminates_at", "evidence": r["destination"]},
        ]
        for hop in r.get("expected_path", []):
            if hop.get("engine_no"):
                rels.append({
                    "target_entity_id": engine_ids_by_no.get(hop["engine_no"]),
                    "relationship_type": "hops_through",
                    "evidence": f"hop {hop['hop_no']}: {hop['hop_name']} ({hop['live_or_planned']}) via {hop['mechanism_path']}",
                })
            elif hop.get("gateway_id"):
                # Same real, evidence-only convention as the engine_no branch above --
                # previously MISSING entirely (a hop_type: gateway entry was silently
                # dropped no matter what expected_path said), the literal reason this
                # registry's hops_through relationships have never once targeted a
                # gateway entity. See WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml phase_1.
                rels.append({
                    "target_entity_id": gateway_ids_by_id.get(hop["gateway_id"]),
                    "relationship_type": "hops_through",
                    "evidence": f"hop {hop['hop_no']}: {hop['hop_name']} ({hop['live_or_planned']}) via {hop['mechanism_path']}",
                })
        both_paths_real = path_exists(r["source"]) and path_exists(r["destination"])
        cap_row = cap_by_name.get(r["capability_name"])
        if cap_row:
            rels.append({
                "target_entity_id": None,
                "relationship_type": "registered_capability_match",
                "evidence": f"capability_registry row found live (confidence={cap_row[0]}, ai_required={bool(cap_row[1])})",
            })
        dvs = r.get("dependency_validation_status")
        if not both_paths_real:
            status = "PATH_MISSING"
        elif dvs == "validated_match":
            status = "VERIFIED_MATCH"
        elif dvs == "validated_mismatch":
            status = "HASH_DRIFTED"
        else:
            status = "UNVERIFIED"
        reg.add({
            "entity_id": eid,
            "entity_type": "route",
            "source_system": "github",
            "path": f"{r['source']} -> {r['destination']}",
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": status,
            "source_ref": ["route_registry_schema"] + (["capability_registry"] if cap_row else []),
            "metadata": {"capability_name": r["capability_name"], "test_status": r.get("test_status"),
                         "trace_verification_status": r.get("trace_verification_status")},
        })
        count += 1
    return count


def build_scripts_and_cron(reg):
    if not os.path.isfile(SOFTWARE_CATALOG):
        print(f"  ! {SOFTWARE_CATALOG} not found, skipping script/cron_job entities", file=sys.stderr)
        return 0, 0
    doc = load_software_catalog()

    script_ids_by_path = {}
    script_count = 0
    for s in doc.get("scripts", []):
        eid = f"script-{slug(os.path.basename(s['path']))}"
        reg.add({
            "entity_id": eid,
            "entity_type": "script",
            "source_system": source_system_for_path(s["path"]),
            "path": s["path"],
            "relationships": [],
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if os.path.exists(s["path"]) else "PATH_MISSING",
            "source_ref": ["software_catalog"],
            "metadata": {"purpose": s.get("purpose"), "cron_scheduled": s.get("cron_scheduled")},
            # 2026-08-06 (task-20260806-035541, Owner directive "real PM cycle
            # script registry"): pass through SOFTWARE_CATALOG.yaml's own real,
            # mechanically recovered originating_umr/script_version -- computed
            # once in generate_software_catalog.py, not re-derived here.
            "originating_umr": s.get("originating_umr"),
            "script_version": s.get("script_version"),
        })
        script_ids_by_path[s["path"]] = eid
        script_count += 1

    cron_count = 0
    for i, c in enumerate(doc.get("cron_jobs", [])):
        eid = f"cron_job-{i:03d}"
        command = c.get("raw", c.get("command", ""))
        rels = []
        for spath, sid in script_ids_by_path.items():
            if spath in command:
                rels.append({"target_entity_id": sid, "relationship_type": "triggers", "evidence": command})
                script_entity = reg.find_by_id(sid)
                script_entity["relationships"].append({
                    "target_entity_id": eid, "relationship_type": "triggered_by", "evidence": command,
                })
        reg.add({
            "entity_id": eid,
            "entity_type": "cron_job",
            "source_system": "server",
            "path": command,
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if rels else "UNVERIFIED",
            "source_ref": ["software_catalog"],
            "metadata": {"schedule": c.get("schedule")},
        })
        cron_count += 1

    return script_count, cron_count


def load_capability_registry():
    """Live read-only query -- never mutates the DB. Returns
    {capability_name: (confidence, ai_required)}."""
    if not os.path.isfile(DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        rows = conn.execute("SELECT capability_name, confidence, ai_required FROM capability_registry").fetchall()
        conn.close()
        return {r[0]: (r[1], r[2]) for r in rows}
    except sqlite3.Error as e:
        print(f"  ! capability_registry read failed: {e}", file=sys.stderr)
        return {}


def _fetch_governance_tagged_knowledge_engine_rows():
    """Real, live read of every knowledge_engine row tagged governance/constitution --
    single source of truth for what "the governance/constitution doc set" means, shared
    by build_governance_docs() and coverage_delta() below so the two can never disagree
    about which rows count (a real risk if each ran its own separate tag-filter query)."""
    if not os.path.isfile(DB_PATH):
        print(f"  ! {DB_PATH} not found, skipping governance_doc entities", file=sys.stderr)
        return []
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        rows = conn.execute(
            "SELECT artifact_id, artifact_path, verification_status, tags FROM knowledge_engine"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"  ! knowledge_engine read failed (governance_doc pass): {e}", file=sys.stderr)
        return []
    out = []
    for artifact_id, artifact_path, verification_status, tags in rows:
        tag_list = parse_ke_tags(tags)
        if GOVERNANCE_TAGS & set(tag_list):
            out.append((artifact_id, artifact_path, verification_status, tag_list))
    return out


def build_governance_docs(reg):
    """entity_type=governance_doc -- task-20260727-025248 (knowledge-engine/wiring-registry
    integration). Reads the SAME live knowledge_engine rows build_from_knowledge_engine()
    below already merges into the wiring graph, but for the subset already tagged
    governance/constitution there, creates a FIRST-CLASS governance_doc entity (not the
    generic 'file' bucket every other knowledge_engine row falls into) with a real sha256
    content_hash of the live file bytes -- the same "first-class type, not a bucket"
    pattern build_scripts_and_cron already established for entity_type='script'.

    MUST run before build_from_knowledge_engine() in main()/generate() so that function's
    own `if abs_path in reg.path_to_id` branch enriches these entities (adding
    source_ref=knowledge_engine, refreshing verification_status/last_verified_ts) instead
    of creating a second, duplicate 'file' entity for the exact same real doc -- the
    Registry class's whole "collapse into ONE entity" contract (see its own docstring)
    depends on the more specific type claiming the path first. If some OTHER source
    already claimed this exact path first (e.g. it is also cited as an engine's exists_as
    or a script's own path), this function does not steal or duplicate it -- that entity
    keeps its own, more specific type, unchanged.

    content_hash is computed fresh from the live file bytes here (not reused verbatim
    from knowledge_engine.content_hash) so a doc edited after its last knowledge_engine
    verify-knowledge run shows real drift immediately on this run, rather than waiting for
    that separate table's own next verify pass. Fragment-style artifact_paths (e.g.
    "ai-os/MASTER_INDEX.yaml#registries.function_catalog", a sub-key inside a real file,
    not a standalone file of its own) never resolve to a real path, so content_hash is
    None for those -- same "no real file to hash" case compute_content_hash() already
    returns None for."""
    count = 0
    for artifact_id, artifact_path, verification_status, tag_list in _fetch_governance_tagged_knowledge_engine_rows():
        abs_path = normalize_path(artifact_path)
        if abs_path in reg.path_to_id:
            continue  # another, more specific source already claimed this exact path
        eid = f"governance_doc-{artifact_id}"
        reg.path_to_id[abs_path] = eid
        vstatus = verification_status if verification_status in VALID_VERIFICATION else "UNVERIFIED"
        if not os.path.exists(abs_path):
            vstatus = "PATH_MISSING"
        reg.add({
            "entity_id": eid,
            "entity_type": "governance_doc",
            "source_system": source_system_for_path(abs_path),
            "path": abs_path,
            "relationships": [],
            "last_verified_ts": now_iso(),
            "verification_status": vstatus,
            "source_ref": ["governance_tag"],
            "content_hash": compute_content_hash([artifact_path]),
            "metadata": {"knowledge_engine_artifact_id": artifact_id, "tags": tag_list},
        })
        count += 1
    return count


def coverage_delta():
    """Real, re-runnable coverage-delta report (SCOPE item 1): compares the current
    engine_inventory (source of truth for the 20-engine registry) and the current live
    governance/constitution knowledge_engine rows against what's ACTUALLY live in
    wiring_registry right now, using the exact same entity_id/path conventions
    build_engines_and_gateways()/build_governance_docs() use -- so this can never silently
    drift from what a real generate() run would produce. Read-only; safe to call anytime,
    not just right after a generate() run, which is the whole point (it answers "is the
    live index still in sync" as its own question, not just "was it in sync when this
    process last ran generate()")."""
    ge_doc = load_engines_gateways()
    expected_engine_ids = {f"engine-{row['engine_no']:02d}" for row in ge_doc.get("engine_inventory", [])}
    governance_rows = _fetch_governance_tagged_knowledge_engine_rows()
    expected_governance_abs_paths = {normalize_path(r[1]) for r in governance_rows}

    wr_engine_ids = set()
    wr_governance_paths = set()
    wr_ke_backed_paths = set()
    if os.path.isfile(DB_PATH):
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        for (eid,) in conn.execute("SELECT entity_id FROM wiring_registry WHERE entity_type='engine'"):
            wr_engine_ids.add(eid)
        for (path,) in conn.execute("SELECT path FROM wiring_registry WHERE entity_type='governance_doc'"):
            wr_governance_paths.add(path)
        for entity_id, path, source_ref in conn.execute("SELECT entity_id, path, source_ref FROM wiring_registry"):
            if "knowledge_engine" in json.loads(source_ref or "[]"):
                wr_ke_backed_paths.add(path)
        conn.close()

    governance_covered_paths = expected_governance_abs_paths & (wr_governance_paths | wr_ke_backed_paths)
    return {
        "engines_expected": len(expected_engine_ids),
        "engines_covered": len(expected_engine_ids & wr_engine_ids),
        "engines_missing": sorted(expected_engine_ids - wr_engine_ids),
        "governance_docs_expected": len(expected_governance_abs_paths),
        "governance_docs_covered": len(governance_covered_paths),
        "governance_docs_missing": sorted(expected_governance_abs_paths - governance_covered_paths),
    }


def build_from_knowledge_engine(reg):
    """entity_type=file, one per existing knowledge_engine row.
    verification_status/last_verified_ts reused VERBATIM (not recomputed --
    avoids a duplicate verification pass over an artifact already tracked
    there). entity_relationships passed through as ke:<type>, target
    resolved against this run's own path->entity_id map when possible."""
    if not os.path.isfile(DB_PATH):
        print(f"  ! {DB_PATH} not found, skipping knowledge_engine rows", file=sys.stderr)
        return 0
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        rows = conn.execute(
            "SELECT artifact_id, artifact_path, verification_status, last_verified_ts, tags, entity_relationships "
            "FROM knowledge_engine"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"  ! knowledge_engine read failed: {e}", file=sys.stderr)
        return 0

    count = 0
    for artifact_id, artifact_path, verification_status, last_verified_ts, tags, entity_relationships in rows:
        abs_path = normalize_path(artifact_path)
        tag_list = parse_ke_tags(tags)
        if "source:VERCEL" in tag_list:
            src_sys = "vercel"
        elif "source:SUPABASE" in tag_list:
            src_sys = "supabase"
        elif "source:GITHUB" in tag_list:
            src_sys = "github"
        else:
            src_sys = "server"  # SERVER / LOCAL both live on this box

        vstatus = verification_status if verification_status in VALID_VERIFICATION else "UNVERIFIED"

        rels = []
        try:
            ke_rels = json.loads(entity_relationships) if entity_relationships else []
        except json.JSONDecodeError:
            ke_rels = []
        for kr in ke_rels:
            target_path = kr.get("path")
            target_id = reg.path_to_id.get(normalize_path(target_path)) if target_path else None
            rels.append({
                "target_entity_id": target_id,
                "relationship_type": f"ke:{kr.get('relationship_type', 'related_to')}",
                "evidence": kr.get("evidence") or target_path or "",
            })

        if abs_path in reg.path_to_id:
            # Already emitted as a file entity by another source (e.g. an
            # engine/gateway exists_as path) -- enrich, don't duplicate.
            eid = reg.path_to_id[abs_path]
            e = reg.find_by_id(eid)
            e["source_ref"].append("knowledge_engine")
            e["relationships"].extend(rels)
            e["verification_status"] = vstatus
            e["last_verified_ts"] = last_verified_ts or e["last_verified_ts"]
            e["metadata"] = e.get("metadata") or {}
            e["metadata"]["knowledge_engine_artifact_id"] = artifact_id
        else:
            eid = f"file-ke-{artifact_id}"
            reg.path_to_id[abs_path] = eid
            reg.add({
                "entity_id": eid,
                "entity_type": "file",
                "source_system": src_sys,
                "path": abs_path,
                "relationships": rels,
                "last_verified_ts": last_verified_ts or now_iso(),
                "verification_status": vstatus,
                "source_ref": ["knowledge_engine"],
                "metadata": {"knowledge_engine_artifact_id": artifact_id},
            })
        count += 1
    return count


BROWSER_COMPONENT_BASENAMES = {
    "VeriComposer.tsx", "ChainSelector.tsx", "IntentCommandPalette.tsx", "browser-intent-cache.ts",
}


def build_browser_components(reg, engine_inventory, engine_ids_by_no):
    """entity_type=browser_component -- full-system extension
    (task-20260726-162252-extend-wiring-engine-to-full-system--ser). A new logical
    layer above the existing `file` entities engine_inventory's own exists_as lists
    already produce for these exact 4 real paths (never a duplicate file entity --
    see WIRING_ENGINE_SCHEMA_2026-07-25.yaml relationship_type_vocabulary.defined_in).
    Restricted to whichever engine_inventory rows actually cite one of these 4 real
    basenames -- a live match against engine_inventory data, not the 3 engine_no's
    (1/2/17) this task's own SPEC mentioned (engine_no 2/Context Engine's exists_as
    was checked and does not cite any of them)."""
    seen = set()
    count = 0
    for row in engine_inventory:
        engine_no = row["engine_no"]
        for p in row.get("exists_as", []):
            base = os.path.basename(p)
            if base not in BROWSER_COMPONENT_BASENAMES:
                continue
            eid = f"browser_component-{slug(os.path.splitext(base)[0])}"
            file_id = reg.path_to_id.get(normalize_path(p))
            implements_rel = {
                "target_entity_id": engine_ids_by_no.get(engine_no),
                "relationship_type": "implements_engine",
                "evidence": f"engine_inventory engine_no={engine_no} ({row['engine_name']}) exists_as cites {p}",
            }
            if eid not in seen:
                seen.add(eid)
                reg.add({
                    "entity_id": eid,
                    "entity_type": "browser_component",
                    "source_system": "github",
                    "path": p,
                    "relationships": [
                        {"target_entity_id": file_id, "relationship_type": "defined_in", "evidence": p},
                        implements_rel,
                    ],
                    "last_verified_ts": now_iso(),
                    "verification_status": "VERIFIED_MATCH" if path_exists(p) else "PATH_MISSING",
                    "source_ref": ["engines_gateways_phase_plan_browser_components"],
                    "metadata": {"basename": base},
                })
                count += 1
            else:
                reg.find_by_id(eid)["relationships"].append(implements_rel)
    return count


def _merge_census_entries(master_index_doc):
    """Merges every registries[] entry whose id starts with
    'vercel_github_state_census_' (UMR-20260806-140841-46d1, real gh-repo-list-vs-wiring_registry
    drift found: the original 2026_07_26 entry undercounted real GitHub repos 7-vs-15 and had
    drifted open_pr_count values). Entries are applied in id-sort order (2026_07_26 base, then
    dated refresh entries like 2026_08_06_refresh layered on top), field-by-field per repo/project
    key so a refresh entry only needs to carry the fields that actually changed -- it does not have
    to repeat a whole repo's required_status_checks_contexts/active_workflows just to update its
    open_pr_count. New repo/project keys a refresh entry introduces are added as-is. Returns None if
    no census entry exists at all (never invents one)."""
    entries = sorted(
        (r for r in master_index_doc.get("registries", []) if r.get("id", "").startswith("vercel_github_state_census_")),
        key=lambda r: r["id"],
    )
    if not entries:
        return None
    merged_repos, merged_projects, source_ids = {}, {}, []
    for e in entries:
        source_ids.append(e["id"])
        for name, row in (e.get("github", {}).get("repos", {}) or {}).items():
            merged_repos.setdefault(name, {}).update(row)
        for name, row in (e.get("vercel", {}).get("projects", {}) or {}).items():
            merged_projects.setdefault(name, {}).update(row)
    merged = dict(entries[0])
    merged["github"] = dict(merged.get("github", {}))
    merged["github"]["repos"] = merged_repos
    merged["vercel"] = dict(merged.get("vercel", {}))
    merged["vercel"]["projects"] = merged_projects
    merged["_source_census_ids"] = source_ids
    return merged


def build_vercel_and_github(reg, master_index_doc):
    """entity_type=vercel_project, entity_type=github_repo -- full-system extension
    (task-20260726-162252-extend-wiring-engine-to-full-system--ser). Sourced from the
    real, fresh vercel/gh CLI census already committed at
    ai-os/MASTER_INDEX.yaml registries[id=vercel_github_state_census_2026_07_26], merged
    with any later dated refresh entries via _merge_census_entries() (UMR-20260806-140841-46d1)
    -- never a separate hand-typed catalog. Also emits the real `contains` relationship
    from each github_repo to any file/engine/gateway entity this run already
    collected whose own path is a real prefix match under that repo's local mirror
    directory (repos/<repo>/...) -- run LAST in main() so path_to_id is maximally
    populated first."""
    entry = _merge_census_entries(master_index_doc)
    if not entry:
        print("  ! no vercel_github_state_census_* entry found in MASTER_INDEX.yaml, "
              "skipping vercel_project/github_repo entities", file=sys.stderr)
        return 0, 0

    master_index_rel = "ai-os/MASTER_INDEX.yaml"
    master_index_file_id = reg.get_or_create_file(master_index_rel, "vercel_github_state_census")
    master_index_exists = path_exists(master_index_rel)
    census_ids_note = "+".join(entry.get("_source_census_ids", ["vercel_github_state_census_2026_07_26"]))

    github_repos = entry.get("github", {}).get("repos", {}) or {}
    vercel_projects = entry.get("vercel", {}).get("projects", {}) or {}
    OWNER = "FChecklist"  # real, per this census's own github.org_or_user_account finding

    github_ids_by_repo_name = {}
    github_count = 0
    for repo_name, row in github_repos.items():
        eid = f"github_repo-{slug(OWNER)}__{slug(repo_name)}"
        github_ids_by_repo_name[repo_name] = eid
        mirror_rel_path = f"repos/{repo_name}"
        mirror_exists = os.path.isdir(normalize_path(mirror_rel_path))
        reg.add({
            "entity_id": eid,
            "entity_type": "github_repo",
            "source_system": "github",
            "path": mirror_rel_path if mirror_exists else None,
            "relationships": [{
                "target_entity_id": master_index_file_id, "relationship_type": "defined_in",
                "evidence": f"{master_index_rel} registries[id in ({census_ids_note})].github.repos.{repo_name}",
            }],
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if mirror_exists else "PATH_MISSING",
            "source_ref": ["vercel_github_state_census"],
            "metadata": {
                "owner": OWNER, "visibility": row.get("visibility"), "default_branch": row.get("default_branch"),
                "open_pr_count": row.get("open_pr_count"), "active_workflows": row.get("active_workflows"),
                "required_status_checks_contexts": row.get("required_status_checks_contexts"),
            },
        })
        github_count += 1

    vercel_count = 0
    for project_key, row in vercel_projects.items():
        eid = f"vercel_project-{slug(project_key)}"
        repo_full = row.get("github_repo")
        repo_name = repo_full.split("/")[-1] if repo_full else None
        target_repo_id = github_ids_by_repo_name.get(repo_name) if repo_name else None
        rels = [{
            "target_entity_id": master_index_file_id, "relationship_type": "defined_in",
            "evidence": f"{master_index_rel} registries[id in ({census_ids_note})].vercel.projects.{project_key}",
        }]
        if target_repo_id:
            rels.append({"target_entity_id": target_repo_id, "relationship_type": "deployed_from",
                         "evidence": f"github_repo={repo_full}"})
            reg.find_by_id(target_repo_id)["relationships"].append({
                "target_entity_id": eid, "relationship_type": "deployed_to", "evidence": f"github_repo={repo_full}",
            })
        reg.add({
            "entity_id": eid,
            "entity_type": "vercel_project",
            "source_system": "vercel",
            "path": None,
            "relationships": rels,
            "last_verified_ts": now_iso(),
            "verification_status": "VERIFIED_MATCH" if master_index_exists else "UNVERIFIED",
            "source_ref": ["vercel_github_state_census"],
            "metadata": {
                "project_key": project_key, "project_id": row.get("project_id"), "github_repo": repo_full,
                "framework": row.get("framework"),
                "latest_production_deployment": row.get("latest_production_deployment"),
                "env_vars_present": row.get("env_vars_present"),
            },
        })
        vercel_count += 1

    for repo_name, gid in github_ids_by_repo_name.items():
        prefix = normalize_path(f"repos/{repo_name}").rstrip("/") + "/"
        repo_entity = reg.find_by_id(gid)
        for e in reg.entities:
            if e["entity_id"] == gid or e["entity_type"] not in ("file", "engine", "gateway"):
                continue
            candidate = e.get("path")
            if not candidate:
                continue
            sub_paths = candidate.split("; ") if e["entity_type"] in ("engine", "gateway") else [candidate]
            for p in sub_paths:
                if normalize_path(p).startswith(prefix):
                    repo_entity["relationships"].append({
                        "target_entity_id": e["entity_id"], "relationship_type": "contains", "evidence": p,
                    })
                    break

    return vercel_count, github_count


def upsert_live_wiring_registry(entities):
    """Phase 3 (ai-os/WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml
    phase_3_wiring_registry_live_wiring): bulk-upsert every entity into the live
    wiring_registry sqlite table -- direct sqlite3 + the register's own
    _write_lock/_ensure_wiring_registry_table/register_entity_row (imported, single
    source of truth for the DDL/upsert SQL), one open transaction for the whole
    batch, same bypass-the-CLI-for-bulk-writes convention
    scripts/batch-import-conversation-log.py already established for exactly this
    reason (a ~7600-row batch via one register-entity subprocess per row would be
    far too slow). Returns the live row count after the upsert.

    _ensure_wiring_registry_table() alone is a bare CREATE TABLE IF NOT EXISTS -- a
    no-op against this DB's real pre-existing wiring_registry table, so it never picks
    up a newly-added column or CHECK-widened entity_type (this is the EXACT bug class
    PR #101's round-1 was rejected for: a migration that only ever ran against fresh
    fixture DBs). _migrate_wiring_registry_entity_types() is the real migration --
    called explicitly here, same as dispatch_core._upsert_wiring_row's own write path,
    never left to a bare _ensure_*_table() call to cover on its own."""
    with _sbr._write_lock():
        conn = _sbr._connect()
        _sbr._ensure_wiring_registry_table(conn)
        _sbr._migrate_wiring_registry_entity_types(conn)
        for entity in entities:
            _sbr.register_entity_row(conn, entity)
        conn.commit()
        live_count = conn.execute("SELECT COUNT(*) FROM wiring_registry").fetchone()[0]
        conn.close()
    return live_count


def generate(out_path=None):
    """The real generation pipeline -- factored out of main() (task-20260727-025248)
    so status-remediation-tick.py can call this in-process on its own existing tick
    (see dispatch_core.py's own importlib pattern for loading superboss-register.py,
    reused the same way here) instead of needing a new standalone cron entry or a
    subprocess call per tick. main() below is now a thin argparse/print wrapper
    around this function; behavior for the existing CLI entrypoint is unchanged."""
    out_path = out_path or DEFAULT_OUT
    reg = Registry()

    ge_doc = load_engines_gateways()
    engine_ids_by_no, gateway_ids_by_id = build_engines_and_gateways(reg, ge_doc)

    table_count = build_tables(reg)
    function_count = build_functions(reg, engine_ids_by_no, ge_doc.get("engine_inventory", []))
    ai_role_count = build_ai_roles(reg)
    cap_by_name = load_capability_registry()
    route_count = build_routes(reg, engine_ids_by_no, gateway_ids_by_id, cap_by_name)
    script_count, cron_count = build_scripts_and_cron(reg)
    # Runs BEFORE build_from_knowledge_engine -- see build_governance_docs()'s own
    # docstring for why the ordering is load-bearing, not incidental.
    governance_doc_count = build_governance_docs(reg)
    ke_count = build_from_knowledge_engine(reg)
    browser_component_count = build_browser_components(reg, ge_doc.get("engine_inventory", []), engine_ids_by_no)
    master_index_doc = load_master_index()
    vercel_project_count, github_repo_count = build_vercel_and_github(reg, master_index_doc)

    counts_by_type = {}
    counts_by_rel = {}
    for e in reg.entities:
        counts_by_type[e["entity_type"]] = counts_by_type.get(e["entity_type"], 0) + 1
        for r in e["relationships"]:
            counts_by_rel[r["relationship_type"]] = counts_by_rel.get(r["relationship_type"], 0) + 1

    output = {
        "meta": {
            "generated_by": "scripts/generate_wiring_registry.py",
            "generated_ts": now_iso(),
            "schema": "ai-os/WIRING_ENGINE_SCHEMA_2026-07-25.yaml",
            "entity_count": len(reg.entities),
        },
        "entities": reg.entities,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    live_row_count = upsert_live_wiring_registry(reg.entities)

    return {
        "ok": True,
        "output_path": out_path,
        "entity_count": len(reg.entities),
        "live_wiring_registry_row_count": live_row_count,
        "counts_by_entity_type": counts_by_type,
        "counts_by_relationship_type": counts_by_rel,
        "raw_source_counts": {
            "engine_inventory": len(ge_doc.get("engine_inventory", [])),
            "gateway_inventory": len(ge_doc.get("gateway_inventory", [])),
            "database_catalog_supabase_tables": table_count,
            "function_catalog_functions": function_count,
            "ai_roster_roles": ai_role_count,
            "route_registry_routes": route_count,
            "software_catalog_scripts": script_count,
            "software_catalog_cron_jobs": cron_count,
            "governance_docs": governance_doc_count,
            "knowledge_engine_rows": ke_count,
            "capability_registry_rows_crosschecked": len(cap_by_name),
            "engine_inventory_browser_components": browser_component_count,
            "vercel_github_state_census_vercel_projects": vercel_project_count,
            "vercel_github_state_census_github_repos": github_repo_count,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--report-only", action="store_true",
                         help="Print the real coverage-delta report (SCOPE item 1) and exit -- "
                              "never writes WIRING_ENGINE_REGISTRY_*.json or the live DB.")
    args = parser.parse_args()

    if args.report_only:
        print(json.dumps(coverage_delta(), indent=2))
        return 0

    summary = generate(args.out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
