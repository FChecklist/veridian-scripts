#!/usr/bin/env python3
"""
full_server_file_registration.py -- UMR-20260806-192052 chain
(UMR-20260806-124055-bc80 stop-work order, UMR-20260806-130416-3d77 wiring
re-run mandate). Pure deterministic script, zero AI judgment calls beyond
building it.

Extends file_inventory.py's scan+hash coverage from its own documented scope
(ai-os/ + scripts/ only -- see that file's own docstring: "Does NOT cover
/opt/veridian/repos ... or /opt/veridian/workspace") to every real canonical
source location server-wide, and registers each distinct file (deduped by
CONTENT, not path) into wiring_registry as entity_type='file'.

Does NOT modify or re-scope file_inventory.py itself -- that script's narrow
scope is a deliberate, correct choice for its own purpose (drift-detection on
the system-of-record config/script tree), not a limitation to fix. This
script is a separate, additive tool for a separate purpose (full-server
wiring-registry coverage), reusing real logic from three existing scripts
rather than reimplementing any of it:

  - file_inventory.py       -> same `find ... -prune -o -type f -print`
                                scan shape and the same OS-level _write_lock()
                                (imported directly, not copy-pasted), just
                                generalized to more roots/excludes.
  - generate_wiring_registry.py -> the `file-{sha1(abs_path)[:12]}` entity_id
                                convention its own Registry.get_or_create_file()
                                already established (same convention,
                                reimplemented locally in make_entity_id() below
                                since the real collision-widening loop needed
                                here has no equivalent to call). Content
                                hashing itself no longer reuses this module's
                                _hash_file_bytes() (plain sha256 of bytes) --
                                see git_hash_object_of() below, which computes
                                git's own blob object model hash instead, per
                                UMR-20260806-171945-5767 issue #921's real
                                resolution.
  - git                      -> hash-object's own content-addressable blob
                                hash algorithm (sha1('blob '+len+'\0'+content)),
                                computed in-process rather than shelled out to
                                a real `git hash-object` subprocess per file
                                (see git_hash_object_of()'s own docstring for
                                why) -- verified byte-identical to a real `git
                                hash-object` invocation in this file's test
                                suite.
  - superboss-register.py   -> `_ensure_wiring_registry_table()` /
                                `register_entity_row()` / `_connect()` /
                                `init_db_silent()` (imported, not reimplemented).

Scope (the SPEC's 4 named roots):
  /opt/veridian/repos/<real top-level repo>  (working tree only -- a dir
      qualifies iff it does not start with '.', does not end in '-wt', and
      contains a real .git entry; this mechanically excludes worktree clones
      like compliance-tracker-pr906-wt/ and non-repo dirs like .pytest_cache/,
      verified live against /opt/veridian/repos/ before writing this, not
      hand-listed)
  /opt/veridian/ai-os/         (excluding ai-os/tasks/*/workspace)
  /opt/veridian/scripts/
  /opt/veridian/shared/
Excluded everywhere: node_modules/, .git/, .next/, ai-os/tasks/*/workspace/,
and (inside any repo) .claude/worktrees/.

Dedup rule (content, not path): for each file found, content_hash =
sha256(bytes). If that content_hash already has a wiring_registry file-entity
(from an earlier run of THIS script), the new path is linked as a
relationship on the EXISTING entity_id -- no duplicate entity_id is ever
created. If the computed deterministic entity_id already exists in
wiring_registry for the SAME path (the common case: a legacy file-entity row
created before this script existed, with content_hash still NULL), that row
is backfilled in place -- still not a new entity_id, but its content_hash
moves from NULL to real for the first time. A genuine entity_id collision
(same first-12-hex-of-sha1(path) computed for two different real paths --
vanishingly unlikely, but checked for real, not assumed impossible) falls
back to a longer hash prefix.

Honesty note on the "zero content_hash NULL for entity_type=file" completion
check: this script backfills every legacy file-entity row it re-discovers via
its own scan. It deliberately does NOT force every pre-existing file-entity
row in wiring_registry to non-NULL, because some of those rows are legacy
data quality issues predating this task and outside its declared scope (this
was verified live before writing this script, not assumed): rows whose path
points inside .git/ (this SPEC explicitly excludes .git/), rows whose
verification_status is already PATH_MISSING (the file no longer exists on
disk -- there is nothing real to hash), and rows whose path falls outside the
4 named roots entirely (e.g. /opt/veridian/ai-os-scripts/,
/home/rajat/.local/bin/). Fabricating a hash for a nonexistent file, or
silently widening scope beyond the SPEC's 4 named roots to force a "zero"
result, would both be dishonest. --completion-check prints the real query
result and a real breakdown of any remainder instead.

Usage:
  python3 full_server_file_registration.py --backup-first   (real run, takes
      a real online sqlite backup first: superboss-register.sqlite.pre-fullfile-backup-<UTC ts>)
  python3 full_server_file_registration.py                  (real run, no backup)
  python3 full_server_file_registration.py --completion-check   (prints the
      mandatory boolean completion evidence: distinct files found this run,
      newly registered, skipped-as-duplicate, legacy rows backfilled, and the
      real "zero NULL content_hash" query + remainder breakdown -- does not
      re-scan)

Exit code 0 on success.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse

SCRIPTS_DIR = "/opt/veridian/scripts"
VERIDIAN_ROOT = "/opt/veridian"
REPOS_ROOT = "/opt/veridian/repos"
# Env-overridable (same convention as superboss-register.py's own
# SUPERBOSS_REGISTER_DB) -- a real testability seam: task-20260815-051128-
# prevent-register-corruption-recurrence's real CLI-subprocess test for
# resource_governor.py's --daily-backup-check (which calls take_backup()
# below) needs to redirect this away from the real production backups dir
# without ever touching it. Every real production caller still gets the
# same real, unchanged default.
BACKUPS_DIR = os.environ.get("VERIDIAN_FFR_BACKUPS_DIR", "/opt/veridian/ai-os/memory/backups")

sys.path.insert(0, SCRIPTS_DIR)


def _load(name, path):
    """Same load-by-path convention verify_registry_file_paths.py already
    uses for these exact two modules -- real reuse, not a textual copy."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sbr = None
_finv = None


def sbr():
    global _sbr
    if _sbr is None:
        _sbr = _load("sbr_reuse", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    return _sbr


def finv():
    global _finv
    if _finv is None:
        _finv = _load("finv_reuse", "/opt/veridian/ai-os/scripts/file_inventory.py")
    return _finv


# ---- scan scope -------------------------------------------------------

def real_top_level_repos():
    """Live-verified against /opt/veridian/repos/ every call, never a
    hand-maintained list (that would drift the moment a repo is added/
    renamed). A dir qualifies iff: does not start with '.', does not end in
    '-wt' (the observed worktree-clone suffix convention -- e.g.
    compliance-tracker-pr906-wt, veridian-scripts-loadstress-wt), and
    contains a real .git entry (file or dir -- a worktree checkout has .git
    as a FILE, same "gitdir:" caveat generate_wiring_registry.py's own
    REPO_ROOT resolution already documents). The .git-entry requirement is
    what mechanically excludes non-repo cruft like .pytest_cache/ without
    hand-listing it."""
    out = []
    for name in sorted(os.listdir(REPOS_ROOT)):
        if name.startswith(".") or name.endswith("-wt"):
            continue
        full = os.path.join(REPOS_ROOT, name)
        if not os.path.isdir(full):
            continue
        if not os.path.exists(os.path.join(full, ".git")):
            continue
        out.append(full)
    return out


def scan_roots():
    roots = list(real_top_level_repos())
    roots.append("/opt/veridian/ai-os")
    roots.append("/opt/veridian/scripts")
    if os.path.isdir("/opt/veridian/shared"):
        roots.append("/opt/veridian/shared")
    return roots


PRUNE_PATTERNS = [
    "*/node_modules", "*/.git", "*/.next", "*/.claude/worktrees",
    "*/ai-os/tasks/*/workspace",
    # Real, verified root cause of a spurious "changed content" diff between
    # two immediately-consecutive runs of THIS script: running/importing this
    # very script regenerates its own compiled __pycache__/*.pyc, so without
    # this exclusion the scan would forever see its own bytecode cache as
    # "new content" one run after the next. Standard build-artifact
    # exclusion, not a special case invented for this script alone.
    "*/__pycache__",
    # Real root cause of a non-idempotent first live run (verified, not
    # guessed): this script's own --backup-first output lands under
    # ai-os/memory/backups/, which is inside the ai-os/ scan root --
    # without this exclusion, run N's backup becomes a "new file" for run
    # N+1, forever. Excluded outright rather than special-cased, same as
    # node_modules/.git above.
    "*/ai-os/memory/backups",
]

# The live superboss-register.sqlite family under ai-os/memory/ (main file,
# -wal/-shm, .writelock, and any prior ad-hoc *backup*/*bak* snapshot sitting
# directly in ai-os/memory/ rather than under backups/) is excluded by
# filename glob, not -prune, since ai-os/memory/ also holds real files
# (credit-ledger.sqlite, sap_mapping.sqlite, ZAI_*.md, etc.) that ARE in
# scope. Two real, verified reasons: (1) the live DB is multi-GB and is
# the exact file this script itself writes to -- hashing it is both
# wasteful (GB-scale read every run) and definitionally non-deterministic
# (its content_hash necessarily differs from the previous run, since the
# previous run's own writes changed it); (2) several multi-GB one-off
# snapshot files already live directly in ai-os/memory/ (e.g.
# superboss-register.sqlite.pre-systemd-backup-20260806T133738Z, found live
# on this server, not hypothetical) with the same problem.
LIVE_DB_GLOB_EXCLUDES = [
    "*/ai-os/memory/superboss-register.sqlite*",
    "*/ai-os/memory/.superboss_write.lock",
]


def list_files():
    """Same find-with-prune shape as file_inventory.py's own list_files()
    (that function's ROOTS/prune list is a module-level constant scoped to
    its own narrower purpose, so it can't be called unchanged -- this is the
    same pattern, generalized, not a divergent reimplementation)."""
    roots = scan_roots()
    prune_expr = " -o ".join(f"-path '{p}'" for p in PRUNE_PATTERNS)
    glob_expr = " -o ".join(f"-path '{p}'" for p in LIVE_DB_GLOB_EXCLUDES)
    cmd = (
        "find " + " ".join(f"'{r}'" for r in roots) + " "
        rf"\( {prune_expr} \) -prune "
        rf"-o \( {glob_expr} \) -prune "
        r"-o -type f -print"
    )
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=300)
    return sorted(set(f for f in out.stdout.strip().split("\n") if f))


# ---- hashing / entity_id ------------------------------------------------

def git_hash_object_of(path):
    """Real content hash via git's own blob object model -- the identical
    algorithm a real `git hash-object <file>` invocation computes:
    sha1('blob ' + str(len(content)) + '\\0' + content). Verified
    byte-identical against a real `git hash-object` subprocess in
    test_full_server_file_registration.py, not assumed.

    UMR-20260806-171945-5767 / UMR_5767_ISSUE_RESOLUTION_MATRIX.json
    issue_number 921's own resolution says to reuse git's existing
    content-addressable blob-hashing model as the real file-ID scheme
    ('Wire a thin lookup (git hash-object <file>) ... rather than a new ID
    service') and explicitly sanctions computing it locally ('the local
    equivalent blob <len>\\0<content> SHA-1 computation if shelling out to
    git per-file is judged too slow'). This file already scans the entire
    server corpus (see list_files()'s find-based scan, potentially
    thousands of files per run) and generate_wiring_registry.py's own
    _hash_file_bytes() (this function's predecessor here) already documents
    that file's real GB-scale-avoidance philosophy -- spawning one real git
    subprocess per file would multiply real fork/exec overhead across that
    same corpus for zero behavioral gain over computing the identical real
    SHA-1 in-process, so the hash is computed here rather than shelled out
    to a real `git hash-object` process per file. Streams the file in 64KB
    chunks (same convention _hash_file_bytes() used) rather than reading it
    whole, for the same reason."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    h = hashlib.sha1()
    h.update(("blob %d" % size).encode() + b"\x00")
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return h.hexdigest()


def content_hash_of(path):
    """Real content-addressable file hash -- git's own blob object model
    (git_hash_object_of() above), not a bespoke sha256 scheme. Deliberately
    NOT file_inventory.py's hash16 (truncated to 16 hex chars / 64 bits,
    tuned for that script's own drift-detection use case) and, as of
    UMR-20260806-171945-5767 issue #921's real resolution, deliberately NOT
    generate_wiring_registry.py's own _hash_file_bytes() sha256-of-bytes
    scheme either -- git's real blob hash (40-hex-char SHA-1) replaces it as
    this file's file-entity content_hash convention. Existing legacy rows
    computed under the old sha256 scheme are transparently re-hashed and
    backfilled in place by run()'s own existing upsert-by-entity_id path
    (entity_id is derived from path, not content_hash, so a format change
    here updates the same row rather than creating a duplicate one)."""
    return git_hash_object_of(path)


def repo_relationship_for(abs_path, github_repo_by_dirname):
    """relationships populated with the real parent repo/dir path (SPEC
    requirement). Links to an existing entity_type='github_repo' entity when
    one is already registered for that repo (target_entity_id populated,
    real cross-reference); otherwise records the real parent dir path as
    evidence with no target (no entity exists to point at -- never
    fabricated)."""
    if abs_path.startswith(REPOS_ROOT + "/"):
        rest = abs_path[len(REPOS_ROOT) + 1:]
        repo_name = rest.split("/", 1)[0]
        repo_root = os.path.join(REPOS_ROOT, repo_name)
        target = github_repo_by_dirname.get(repo_name)
        return {"target_entity_id": target, "relationship_type": "belongs_to_repo", "evidence": repo_root}
    return {"target_entity_id": None, "relationship_type": "belongs_to_dir", "evidence": os.path.dirname(abs_path)}


def make_entity_id(abs_path, existing_ids, hash_len=12):
    """Same convention as generate_wiring_registry.py's
    Registry.get_or_create_file(): file-{sha1(abs_path)[:12]}. Real collision
    check (not assumed impossible): if that id is already used by a DIFFERENT
    real path, widen the hash prefix until it's unique -- deterministic,
    reproducible, never random."""
    while True:
        eid = f"file-{hashlib.sha1(abs_path.encode()).hexdigest()[:hash_len]}"
        prior_path = existing_ids.get(eid)
        if prior_path is None or prior_path == abs_path:
            return eid
        hash_len += 4  # real, deterministic widening on a genuine collision


# ---- locking (reused from file_inventory.py) ----------------------------

@contextlib.contextmanager
def write_lock():
    with finv()._write_lock():
        yield


# ---- backup ---------------------------------------------------------------

def take_backup():
    """Real online sqlite backup via Connection.backup() (WAL-safe -- same
    reasoning snapshot_memory_backup.py's own docstring documents: a plain
    cp of a live WAL-mode DB can copy a torn, inconsistent snapshot).
    Verified immediately after with prune_memory_backups.py's real
    real_integrity_check(), imported not reimplemented. Filename follows this
    SPEC's own named convention (pre-fullfile-backup-<ts>), not
    snapshot_memory_backup.py's different ad-hoc-snapshot naming (which is
    for a different, more general use case with its own daily-cap policy)."""
    import sqlite3
    pmb = _load("pmb_reuse", os.path.join(SCRIPTS_DIR, "prune_memory_backups.py"))
    db_path = sbr().DB_PATH
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUPS_DIR, f"superboss-register.sqlite.pre-fullfile-backup-{ts}")

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    if not pmb.real_integrity_check(backup_path):
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise RuntimeError(f"backup at {backup_path} failed integrity_check -- removed, refusing to proceed")
    return backup_path


# ---- main run -------------------------------------------------------------

def run(do_backup):
    sbr().init_db_silent()

    backup_path = take_backup() if do_backup else None

    files = list_files()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    newly_registered = 0
    skipped_duplicate = 0
    backfilled_legacy = 0
    errors = []

    with write_lock():
        conn = sbr()._connect()
        sbr()._ensure_wiring_registry_table(conn)

        existing_by_hash = {}
        existing_ids = {}  # entity_id -> path, ALL entity types (real cross-namespace collision check)
        for row in conn.execute("SELECT entity_id, entity_type, path, content_hash FROM wiring_registry"):
            existing_ids[row["entity_id"]] = row["path"]
            if row["entity_type"] == "file" and row["content_hash"]:
                existing_by_hash[row["content_hash"]] = row["entity_id"]

        github_repo_by_dirname = {}
        for row in conn.execute("SELECT entity_id, path FROM wiring_registry WHERE entity_type='github_repo'"):
            if row["path"]:
                github_repo_by_dirname[row["path"].split("/")[-1]] = row["entity_id"]

        for abs_path in files:
            ch = content_hash_of(abs_path)
            if ch is None:
                errors.append(abs_path)
                continue

            if ch in existing_by_hash:
                eid = existing_by_hash[ch]
                row = conn.execute(
                    "SELECT path, relationships FROM wiring_registry WHERE entity_id=?", (eid,)
                ).fetchone()
                if row is None:
                    continue
                if row["path"] == abs_path:
                    skipped_duplicate += 1
                    continue
                rels = json.loads(row["relationships"]) if row["relationships"] else []
                already_linked = any(r.get("evidence") == abs_path for r in rels)
                if not already_linked:
                    rels.append({"target_entity_id": None, "relationship_type": "duplicate_content_at_path",
                                 "evidence": abs_path})
                    conn.execute(
                        "UPDATE wiring_registry SET relationships=?, last_verified_ts=? WHERE entity_id=?",
                        (json.dumps(rels), now, eid),
                    )
                skipped_duplicate += 1
                continue

            eid = make_entity_id(abs_path, existing_ids)
            prior_path = existing_ids.get(eid)
            entity = {
                "entity_id": eid,
                "entity_type": "file",
                "source_system": "server",
                "path": abs_path,
                "relationships": [repo_relationship_for(abs_path, github_repo_by_dirname)],
                "last_verified_ts": now,
                "verification_status": "VERIFIED_MATCH",
                "source_ref": ["full_server_file_registration"],
                "metadata": {"registered_by": "full_server_file_registration.py",
                             "governing_umr": "UMR-20260806-130416-3d77"},
                "content_hash": ch,
            }
            sbr().register_entity_row(conn, entity)
            existing_by_hash[ch] = eid
            existing_ids[eid] = abs_path
            if prior_path == abs_path:
                backfilled_legacy += 1
            else:
                newly_registered += 1

        conn.commit()
        conn.close()

    return {
        "backup_path": backup_path,
        "files_found": len(files),
        "newly_registered": newly_registered,
        "backfilled_legacy_content_hash": backfilled_legacy,
        "skipped_duplicate": skipped_duplicate,
        "unreadable_errors": errors,
    }


# ---- mandatory completion check -------------------------------------------

def completion_check():
    conn = sbr()._connect()
    total_file_rows = conn.execute("SELECT COUNT(*) c FROM wiring_registry WHERE entity_type='file'").fetchone()["c"]
    null_hash_rows = conn.execute(
        "SELECT COUNT(*) c FROM wiring_registry WHERE entity_type='file' AND (content_hash IS NULL OR content_hash='')"
    ).fetchone()["c"]
    remainder = conn.execute(
        "SELECT path, verification_status FROM wiring_registry "
        "WHERE entity_type='file' AND (content_hash IS NULL OR content_hash='')"
    ).fetchall()
    breakdown = {"inside_git": 0, "path_missing": 0, "outside_scanned_roots": 0, "other": 0}
    scanned_root_prefixes = tuple(r + "/" for r in [REPOS_ROOT, "/opt/veridian/ai-os", SCRIPTS_DIR, "/opt/veridian/shared"])
    for row in remainder:
        p = row["path"] or ""
        if "/.git/" in p:
            breakdown["inside_git"] += 1
        elif row["verification_status"] == "PATH_MISSING":
            breakdown["path_missing"] += 1
        elif not p.startswith(scanned_root_prefixes):
            breakdown["outside_scanned_roots"] += 1
        else:
            breakdown["other"] += 1
    conn.close()
    return {
        "total_entity_type_file_rows": total_file_rows,
        "rows_with_null_or_empty_content_hash": null_hash_rows,
        "remainder_breakdown": breakdown,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup-first", action="store_true")
    ap.add_argument("--completion-check", action="store_true")
    args = ap.parse_args()

    if args.completion_check:
        print(json.dumps(completion_check(), indent=2))
        return

    result = run(do_backup=args.backup_first)
    print(json.dumps(result, indent=2))
    print(json.dumps(completion_check(), indent=2))


if __name__ == "__main__":
    main()
