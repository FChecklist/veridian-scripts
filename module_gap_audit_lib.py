#!/usr/bin/env python3
"""
module_gap_audit_lib.py -- real module-boundary resolver for the module-scoped
ChatGPT/Z.AI gap audit (task-20260725-063204-module-scoped-zai-gap-audit).

Extends the existing domain-scoped manual-bridge audit
(scripts/generate_chatgpt_audit_request.py + scripts/ingest_chatgpt_audit_response.py,
proven for the Security domain as AUDIT-000009/AUDIT-000010) to real
MODULE-scoped requests. This module owns exactly one job: turning a
(repo, module_name) pair into a real, mechanically-discovered set of
git-tracked files, and packing those files into ceiling-bounded request
chunks. No schema changes, no new sandbox/guard/versioning layer -- those
stay exactly as scripts/chatgpt_audit_guard.py and
scripts/chatgpt_audit_versioning.py already are.

Module boundary sources (checked live, never hand-curated):
  - compliance-tracker: the repo's OWN real module taxonomy --
    compliance.module_registry, a Postgres table seeded via ~30 real
    drizzle/*.sql migrations (Wave 20 "module reusability" onward, see
    drizzle/0017_wave20_module_registry_and_product_branches.sql's own
    header). This is not an invented grouping: it is the product's own
    module_key/table_name/domain/category catalog, used at runtime by
    src/lib/services/module-registry-service.ts to gate which modules a
    product branch has enabled. Extracted here by parsing the real INSERT
    statements (never guessed), deduped by module_key.
  - projexa: no equivalent module_registry table exists in this repo
    (checked: no `module_registry`/`moduleRegistry` hit anywhere under
    drizzle/ or src/). Falls back to the KNOWN_CONTEXT-sanctioned mechanical
    derivation: each top-level src/app/api/<x>/ directory is one module.

File discovery per module unions two real, mechanical signals:
  1. filename substring match against `git ls-files` (same technique
     generate_chatgpt_audit_request.py's DOMAIN_KEYWORDS search already uses).
  2. content match via `git grep -lI` for the module's snake_case and
     camelCase identifier forms (module_key/table_name are DB identifiers,
     e.g. `board_meetings` -> `boardMeetings` in TS source -- a pure
     filename substring match misses these; content grep does not).
Never a hand-typed file list, never a fabricated path.
"""
import os
import re
import subprocess

REPOS_ROOT = "/opt/veridian/repos"

EXCLUDED_DIR_SUBSTRINGS = (
    "node_modules/", ".next/", "dist/", "build/", "coverage/", ".git/",
    ".vercel/", "bin/", "public/",
)
EXCLUDED_EXTENSIONS = (
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
    ".woff2", ".ttf", ".map", ".webp",
)

FALLBACK_DOCS = [
    "PLATFORM_STRATEGY.md",
    "VERIDIAN_AI_CONSTITUTION.md",
    "AGENTS.md",
]

# Reference ceiling per single request, per KNOWN_CONTEXT: "roughly 400
# lines / 20KB" -- similar order of magnitude to the working Security
# request (19574 bytes). Whole files only, never split mid-file.
DEFAULT_MAX_REQUEST_LINES = 400
DEFAULT_MAX_REQUEST_BYTES = 20000

# A single monolithic shared file (e.g. src/lib/db/schema.ts, which defines
# every table in the whole app, not just one module's) can match a module's
# keyword yet be almost entirely irrelevant noise for that module -- and
# including it whole would blow the per-module request out to hundreds of
# KB for one file. Auto-discovery excludes any single candidate file above
# this size (still real content, never truncated -- just not auto-pulled
# in; the Owner can force it in with --files if genuinely needed). Set well
# above the per-request ceiling so normal module files are never affected.
MAX_AUTO_FILE_LINES = 1500
MAX_AUTO_FILE_BYTES = 60000

MODULE_REGISTRY_INSERT_RE = re.compile(r"INSERT INTO compliance\.module_registry[^;]*;", re.S)
MODULE_REGISTRY_ROW_RE = re.compile(
    r"\(\s*'(?P<key>[a-z0-9_]+)',\s*"
    r"'(?P<display_name>(?:[^']|'')*)',\s*"
    r"'(?P<table_name>[a-z0-9_]+)',\s*"
    r"'(?P<domain>[a-z_]+)',\s*"
    r"(?:'(?P<category>[A-Za-z0-9_ &-]+)'|NULL),\s*"
    r"(?P<is_core>true|false),\s*"
    r"'(?P<description>(?:[^']|'')*)'\s*\)"
)


def run_git(repo_path, *args):
    proc = subprocess.run(["git", "-C", repo_path, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_path}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_excluded(path):
    lower = path.lower()
    if any(sub in lower for sub in EXCLUDED_DIR_SUBSTRINGS):
        return True
    if any(lower.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return True
    return False


def list_tracked_files(repo_path):
    all_files = run_git(repo_path, "ls-files").splitlines()
    return [f for f in all_files if not is_excluded(f)]


def to_camel(snake_str):
    parts = snake_str.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def repo_path_for(repo):
    path = os.path.join(REPOS_ROOT, repo)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"repo not found: {path}")
    return path


# --------------------------------------------------------------------------
# Module discovery
# --------------------------------------------------------------------------

def discover_compliance_tracker_modules(repo_path):
    """Real compliance.module_registry catalog, parsed from the actual
    drizzle/*.sql seed migrations -- never hand-typed. Deduped by
    module_key (later INSERTs would fail the real migration's UNIQUE
    constraint, so any repeat here is a same-key re-seed; last one parsed
    wins, matching Postgres's own INSERT-then-ON-CONFLICT-DO-NOTHING
    semantics used by these migrations)."""
    drizzle_dir = os.path.join(repo_path, "drizzle")
    modules = {}
    if not os.path.isdir(drizzle_dir):
        return [], "compliance.module_registry (drizzle/*.sql seed migrations) -- drizzle/ not found"

    for fname in sorted(os.listdir(drizzle_dir)):
        if not fname.endswith(".sql"):
            continue
        fpath = os.path.join(drizzle_dir, fname)
        with open(fpath, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if "INSERT INTO compliance.module_registry" not in text:
            continue
        for block_match in MODULE_REGISTRY_INSERT_RE.finditer(text):
            for row_match in MODULE_REGISTRY_ROW_RE.finditer(block_match.group(0)):
                d = row_match.groupdict()
                modules[d["key"]] = {
                    "module_key": d["key"],
                    "display_name": d["display_name"].replace("''", "'"),
                    "table_name": d["table_name"],
                    "domain": d["domain"],
                    "category": d["category"],
                    "is_core": d["is_core"] == "true",
                    "description": d["description"].replace("''", "'"),
                    "source_file": f"drizzle/{fname}",
                }

    ordered = [modules[k] for k in sorted(modules)]
    source = "compliance.module_registry (real Postgres module catalog, parsed from drizzle/*.sql seed migrations)"
    return ordered, source


def discover_projexa_modules(repo_path):
    """No module_registry-equivalent table exists in projexa (checked live:
    no module_registry/moduleRegistry hit under drizzle/ or src/). Falls
    back to the KNOWN_CONTEXT-sanctioned mechanical derivation: each
    top-level src/app/api/<x>/ directory is one real module boundary."""
    api_dir = os.path.join(repo_path, "src", "app", "api")
    if not os.path.isdir(api_dir):
        return [], "src/app/api/ directory structure -- src/app/api/ not found"

    modules = []
    for name in sorted(os.listdir(api_dir)):
        full = os.path.join(api_dir, name)
        if not os.path.isdir(full) or name.startswith("."):
            continue
        modules.append({
            "module_key": name,
            "display_name": name,
            "table_name": None,
            "domain": None,
            "category": None,
            "is_core": None,
            "description": None,
            "source_file": f"src/app/api/{name}/",
        })
    source = "src/app/api/<x>/ directory structure (no module_registry-equivalent table exists in this repo -- confirmed live)"
    return modules, source


def list_modules(repo, repo_path):
    if repo == "compliance-tracker":
        return discover_compliance_tracker_modules(repo_path)
    if repo == "projexa":
        return discover_projexa_modules(repo_path)
    raise ValueError(
        f"no module-boundary source is registered for repo '{repo}' -- only compliance-tracker "
        f"(compliance.module_registry) and projexa (src/app/api/ directories) are in scope for "
        f"this audit per SCOPE item 1."
    )


def find_module(repo, repo_path, module_name):
    modules, source = list_modules(repo, repo_path)
    target = module_name.strip().lower().replace("-", "_")
    for m in modules:
        if m["module_key"].lower().replace("-", "_") == target:
            return m, source, modules
    return None, source, modules


# --------------------------------------------------------------------------
# File discovery per module
# --------------------------------------------------------------------------

def module_keywords(module):
    """Real identifier variants to search for -- snake_case, kebab-case,
    and camelCase forms of module_key/table_name, since DB identifiers
    (module_key, table_name) show up as camelCase in TS source
    (e.g. board_meetings -> boardMeetings) and as-is in migration/route
    filenames."""
    keys = {module["module_key"]}
    if module.get("table_name"):
        keys.add(module["table_name"])
    variants = set()
    for k in keys:
        variants.add(k)
        variants.add(k.replace("_", "-"))
        variants.add(to_camel(k))
    return sorted(variants)


def _defines_table(repo_path, sql_rel_path, table_name):
    """True iff this migration actually CREATEs the module's table (its
    schema-defining migration) rather than merely mentioning the table name
    in passing (an unrelated ALTER/seed/description elsewhere) -- keeps SQL
    matches to the files that really define the module's business logic
    instead of every incidental cross-reference."""
    abs_path = os.path.join(repo_path, sql_rel_path)
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    return bool(re.search(rf"CREATE TABLE[^;]*\b{re.escape(table_name)}\b", text, re.IGNORECASE))


def find_module_files(repo_path, module, explicit_files=None, tracked_files=None):
    """Real, git-tracked files only. Unions a filename-substring match
    (over `git ls-files`) with a content match (`git grep -lI`) for every
    keyword variant -- returns (files, used_fallback, method,
    excluded_oversized). SQL matches are narrowed to the module's
    registry-seed migration and its actual CREATE TABLE-defining
    migration(s) (see _defines_table) -- otherwise a module's table name
    showing up in one ALTER/comment/seed line of an unrelated migration
    would pull that whole unrelated file's business logic into the
    request, which is noise, not signal.

    tracked_files: pass a pre-fetched list_tracked_files(repo_path) result
    when scanning many modules in the same repo back-to-back (e.g. the
    module-list generator), to avoid re-running `git ls-files` per module."""
    if explicit_files:
        matched = []
        for rel in explicit_files:
            abs_path = os.path.join(repo_path, rel)
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(f"--files entry does not exist in repo: {rel} ({abs_path})")
            matched.append(rel)
        return sorted(matched), False, "explicit --files override", []

    keywords = module_keywords(module)
    tracked = tracked_files if tracked_files is not None else list_tracked_files(repo_path)
    matched = set()

    for f in tracked:
        if f.endswith(".sql"):
            continue  # SQL matches are handled separately below (defining-migration only)
        lower = f.lower()
        if any(kw.lower() in lower for kw in keywords):
            matched.add(f)

    for kw in keywords:
        proc = subprocess.run(
            ["git", "-C", repo_path, "grep", "-lI", "-e", kw, "--"] +
            ["*.ts", "*.tsx", "*.js", "*.jsx"],
            capture_output=True, text=True,
        )
        if proc.returncode not in (0, 1):  # 1 == no matches, not an error
            continue
        for line in proc.stdout.splitlines():
            if not is_excluded(line):
                matched.add(line)

    table_name = module.get("table_name")
    if table_name:
        source_file = module.get("source_file")
        if source_file and source_file.endswith(".sql") and os.path.isfile(os.path.join(repo_path, source_file)):
            matched.add(source_file)  # the module_registry seed migration itself -- always relevant
        proc = subprocess.run(
            ["git", "-C", repo_path, "grep", "-lI", "-e", table_name, "--", "*.sql"],
            capture_output=True, text=True,
        )
        if proc.returncode in (0, 1):
            for line in proc.stdout.splitlines():
                if not is_excluded(line) and _defines_table(repo_path, line, table_name):
                    matched.add(line)

    kept, excluded_oversized = [], []
    for rel in sorted(matched):
        lines, nbytes, _ = file_stats(repo_path, rel)
        if lines > MAX_AUTO_FILE_LINES or nbytes > MAX_AUTO_FILE_BYTES:
            excluded_oversized.append({"path": rel, "lines": lines, "bytes": nbytes})
        else:
            kept.append(rel)

    if kept:
        return kept, False, ("filename substring match + `git grep -lI` content match on "
                              "module_key/table_name identifier variants (SQL matches narrowed "
                              "to the module_registry seed + real CREATE TABLE-defining migration; "
                              "monolithic shared files above the auto-discovery size ceiling excluded "
                              "-- see excluded_oversized)"), excluded_oversized

    fallback = [f for f in FALLBACK_DOCS if os.path.isfile(os.path.join(repo_path, f))]
    return fallback, True, "no keyword match (filename or content) -- fell back to repo-overview docs", excluded_oversized


# --------------------------------------------------------------------------
# Chunk packing (the size-ceiling rule from KNOWN_CONTEXT)
# --------------------------------------------------------------------------

def file_stats(repo_path, rel_path):
    abs_path = os.path.join(repo_path, rel_path)
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return 0, 0, ""
    return content.count("\n") + 1, len(content.encode("utf-8")), content


def pack_into_chunks(repo_path, files, max_lines=DEFAULT_MAX_REQUEST_LINES, max_bytes=DEFAULT_MAX_REQUEST_BYTES):
    """Packs whole files (never truncated) into request-sized chunks bounded
    by max_lines/max_bytes. A single file that alone exceeds the ceiling
    still gets its own solo chunk (real content, in full -- SCOPE item 2
    forbids silent truncation, not forbids an oversized outlier chunk)."""
    chunks = []
    current = []
    current_lines = 0
    current_bytes = 0

    for rel in files:
        lines, nbytes, _ = file_stats(repo_path, rel)
        if current and (current_lines + lines > max_lines or current_bytes + nbytes > max_bytes):
            chunks.append(current)
            current = []
            current_lines = 0
            current_bytes = 0
        current.append(rel)
        current_lines += lines
        current_bytes += nbytes

    if current:
        chunks.append(current)

    return chunks


def module_real_stats(repo_path, files):
    total_files = len(files)
    total_lines = 0
    total_bytes = 0
    for rel in files:
        lines, nbytes, _ = file_stats(repo_path, rel)
        total_lines += lines
        total_bytes += nbytes
    return {"file_count": total_files, "line_count": total_lines, "byte_count": total_bytes}
