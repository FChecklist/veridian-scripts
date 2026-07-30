#!/usr/bin/env python3
"""
generate_chatgpt_audit_request.py -- manual-bridge request generator for the
ChatGPT Audit Workspace (INS-20260724-214122-f0ed,
task-20260724-214215-chatgpt-manual-bridge-workflow).

Owner decision this task implements: no OPENAI_API_KEY will be supplied (see
ai-os/CHATGPT_AUDIT_BLOCKER_2026-07-24.yaml's prior awaiting_owner_decision
state). Instead of an API call, this script produces ONE real, complete,
ready-to-copy text file: a real repo scope (real git-tracked files for the
given domain, their actual content, real commit hash/branch), the exact
ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml field contract, and an
explicit instruction to output valid YAML matching that schema. The Owner
copies this into a free ChatGPT web session by hand, then saves the reply for
scripts/ingest_chatgpt_audit_response.py to parse and write, version-safe,
through the existing guard/versioning scripts.

Nothing here calls any LLM API -- this is pure local file discovery and
templating. The only write this script performs is the request .txt file
itself, under /opt/veridian/chatgpt-audit/_pending_requests/, still routed
through scripts/chatgpt_audit_guard.py's guarded_write (same mechanical
write-scope guarantee as every other write in this workspace).

Run (domain mode, unchanged):
  python3 scripts/generate_chatgpt_audit_request.py --domain Security
  python3 scripts/generate_chatgpt_audit_request.py --domain Security --files src/lib/engines/security-engine.ts,src/lib/supabase/auth-guard.ts
  python3 scripts/generate_chatgpt_audit_request.py --domain Security --repo compliance-tracker --max-files 6

Run (module mode, task-20260725-063204-module-scoped-zai-gap-audit): real
MODULE-scoped requests instead of domain-scoped ones, using
scripts/module_gap_audit_lib.py to resolve real module boundaries (see that
module's own header for the module_registry vs directory-structure source
per repo) and to chunk a module's real files into ceiling-bounded request
files (~400 lines / ~20KB per request, never truncating a file -- splitting
into numbered part files instead):
  python3 scripts/generate_chatgpt_audit_request.py --module board_meetings --repo compliance-tracker
  python3 scripts/generate_chatgpt_audit_request.py --module access-review --repo projexa
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_audit_guard import ALLOWED_ROOT, guarded_write  # noqa: E402
from chatgpt_audit_versioning import VALID_SUBFOLDERS  # noqa: E402
import module_gap_audit_lib as mgal  # noqa: E402

import yaml  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SCHEMA_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml")
PENDING_REQUESTS_DIR = os.path.join(ALLOWED_ROOT, "_pending_requests")
REPOS_ROOT = "/opt/veridian/repos"

DEFAULT_MAX_FILES = 6
DEFAULT_MAX_FILE_BYTES = 20000

EXCLUDED_DIR_SUBSTRINGS = (
    "node_modules/", ".next/", "dist/", "build/", "coverage/", ".git/",
    ".vercel/", "bin/", "public/",
)
EXCLUDED_EXTENSIONS = (
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
    ".woff2", ".ttf", ".map", ".webp",
)

# Real, keyword-based domain -> file-discovery hints. Not a hand-authored
# file list (that would rot the moment the repo changes) -- these are just
# search keywords matched against `git ls-files`' real, live tracked-file
# list at run time, so the resulting files_analysed is always current.
DOMAIN_KEYWORDS = {
    "Architecture": ["architecture", "lib/services", "lib/engines"],
    "Business": ["engines/sales", "engines/hr", "engines/payroll", "business"],
    "Capabilities": ["capability"],
    "Modules": ["lib/engines", "modules"],
    "Metadata": ["metadata"],
    "Database": ["drizzle", "schema", "db/"],
    "Rules": ["rule", "guardrail"],
    "Workflow": ["workflow", "task-execution"],
    "APIs": ["app/api/"],
    "UI": ["components", "app/(app)"],
    "Reports": ["report"],
    "Security": ["security", "auth"],
    "Performance": ["performance", "perf"],
    "AI": ["ai-", "llm", "veri-chat", "prompt"],
    "Prompts": ["prompt"],
    "Routes": ["app/api/", "route.ts"],
    "Testing": ["test", "spec", "playwright"],
    "Dependencies": ["package.json"],
    "Integrations": ["integration", "webhook", "mcp"],
    "Observability": ["observability", "logging", "monitor"],
    "Release": ["release", "deploy"],
    "Recommendations": ["recommend"],
    "History": ["changelog", "history"],
}

# Real overview docs to fall back to (existence checked live, never assumed)
# when a domain's keyword search finds nothing in the target repo -- keeps
# the request honest ("no domain-specific match found") rather than empty.
FALLBACK_DOCS = [
    "PLATFORM_STRATEGY.md",
    "VERIDIAN_AI_CONSTITUTION.md",
    "AGENTS.md",
]

# Real, absolute standards docs on this server -- included only if they
# actually exist at run time, never assumed present.
STANDARDS_CANDIDATES = [
    ("/opt/veridian/ai-os/AI_ENGINEERING_POLICY.yaml", "AI_ENGINEERING_POLICY"),
    ("/opt/veridian/ai-os/RULES_ARTICLES_198.json", "RULES_ARTICLES_198"),
]


def _now_compact():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_git(repo_path, *args):
    proc = subprocess.run(["git", "-C", repo_path, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_path}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _excluded(path):
    lower = path.lower()
    if any(sub in lower for sub in EXCLUDED_DIR_SUBSTRINGS):
        return True
    if any(lower.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return True
    return False


def discover_files(repo_path, domain, max_files, explicit_files):
    """Real, git-tracked files only -- either the Owner's explicit --files
    list (existence-checked) or a live keyword search over `git ls-files`.
    Falls back to real overview docs (existence-checked) if nothing matches,
    never fabricates a path."""
    if explicit_files:
        matched = []
        for rel in explicit_files:
            abs_path = os.path.join(repo_path, rel)
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(f"--files entry does not exist in repo: {rel} ({abs_path})")
            matched.append(rel)
        return matched, False

    all_files = _run_git(repo_path, "ls-files").splitlines()
    tracked = [f for f in all_files if not _excluded(f)]
    keywords = [kw.lower() for kw in DOMAIN_KEYWORDS.get(domain, [domain.lower()])]
    matched = sorted(f for f in tracked if any(kw in f.lower() for kw in keywords))

    if matched:
        return matched[:max_files], False

    fallback = [f for f in FALLBACK_DOCS if os.path.isfile(os.path.join(repo_path, f))]
    return fallback[:max_files], True


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_field_contract(schema):
    lines = []
    for field in schema["fields"]:
        bits = [f"  - {field['name']} ({field['type']}, required={field['required']})"]
        if "enum" in field:
            bits.append(f"    enum: {field['enum']}")
        if "range" in field:
            bits.append(f"    range: {field['range']}")
        if "format" in field:
            bits.append(f"    format: {field['format']}")
        if "description" in field:
            bits.append(f"    {field['description']}")
        lines.append("\n".join(bits))
    lines.append("  Linkage fields (all nullable strings -- omit rather than fabricate a link):")
    for lf in schema["linkage_fields"]["fields"]:
        lines.append(f"  - {lf}")
    return "\n".join(lines)


def resolve_standards(repo_path, override):
    if override:
        return override
    found = [name for path, name in STANDARDS_CANDIDATES if os.path.isfile(path)]
    for doc in ("VERIDIAN_AI_CONSTITUTION.md", "VERIDIAN_TASK_GOVERNANCE_CONSTITUTION.md"):
        if os.path.isfile(os.path.join(repo_path, doc)):
            found.append(doc)
    return found or ["VERIDIAN AI OS engineering standards (no standards doc found on disk -- Owner should confirm)"]


def build_request_text(domain, repo, repo_path, files_analysed, used_fallback, modules_analysed,
                        standards_used, repository_version, commit_hash, schema, max_file_bytes):
    field_contract = render_field_contract(schema)

    file_sections = []
    for rel in files_analysed:
        abs_path = os.path.join(repo_path, rel)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                content = f.read(max_file_bytes + 1)
        except OSError as e:
            content = f"<< could not read file: {e} >>"
        truncated = len(content) > max_file_bytes
        if truncated:
            content = content[:max_file_bytes] + "\n... << truncated at " + str(max_file_bytes) + " bytes >>"
        file_sections.append(f"### repos/{repo}/{rel}\n```\n{content}\n```")

    fallback_note = (
        "\nNOTE: no domain-specific file matched a live keyword search in this repo, so the files "
        "below are real repo-overview docs instead -- treat this as a starter/partial scope, not a "
        "full domain audit.\n" if used_fallback else ""
    )

    parts = [
        "SYSTEM: You are auditing VERIDIAN AI OS source code for gaps, duplication, and "
        "incompleteness against the standards listed below. You have READ-ONLY access to the file "
        "contents pasted below (READ_SCOPE) -- you do not have write access to any production "
        "source, and your output is a single YAML document, nothing else.",
        "",
        f"Output MUST match ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml exactly: every "
        f"required field populated, no fabricated file paths, no fabricated linkage ids. Do not "
        f"include audit_id or timestamp -- those are allocated separately once you respond; if you "
        f"include them anyway they will be ignored and replaced. Required/optional fields:",
        field_contract,
        "",
        f"STANDARDS_USED: {standards_used}",
        f"MODULES_ANALYSED: {modules_analysed}",
        f"REPOSITORY_VERSION: {repository_version}",
        f"COMMIT_HASH: {commit_hash}",
        f"DOMAIN (target chatgpt-audit subfolder): {domain}",
        fallback_note,
        "USER: Here is the real file content for each path in READ_SCOPE, concatenated with a path "
        "header per file. Analyse these against the standards above and produce your audit findings "
        "as a single YAML document matching the schema.",
        "",
        "\n\n".join(file_sections),
        "",
        "Respond with ONLY the YAML document -- no prose before or after, no markdown code fence.",
    ]
    return "\n".join(parts)


def build_module_request_text(repo, module, files_analysed, used_fallback, discovery_method,
                               standards_used, repository_version, commit_hash, schema, chunk_index, chunk_count):
    field_contract = render_field_contract(schema)

    file_sections = []
    for rel in files_analysed:
        abs_path = os.path.join(REPOS_ROOT, repo, rel)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            content = f"<< could not read file: {e} >>"
        file_sections.append(f"### repos/{repo}/{rel}\n```\n{content}\n```")

    fallback_note = (
        "\nNOTE: no keyword match (filename substring or content grep) found this module's identifier "
        "in this repo, so the files below are real repo-overview docs instead -- treat this as a "
        "starter/partial scope, not a full module audit.\n" if used_fallback else ""
    )
    part_note = (
        f"\nThis is PART {chunk_index} of {chunk_count} for this module -- the module's real content "
        f"exceeded the ~400-line/~20KB single-request reference ceiling, so it was split across "
        f"{chunk_count} numbered request files rather than truncated. Audit only the files pasted below; "
        f"the other parts cover the module's remaining real files.\n" if chunk_count > 1 else ""
    )

    parts = [
        "SYSTEM: You are auditing one real VERIDIAN AI OS MODULE for gaps, duplication, and "
        "incompleteness against the standards listed below. You have READ-ONLY access to the file "
        "contents pasted below (READ_SCOPE) -- you do not have write access to any production "
        "source, and your output is a single YAML document, nothing else.",
        "",
        "For EACH real file/function pasted below, identify: (a) what it does (its function), "
        "(b) the business logic/rule it implements, (c) its real inputs and outputs, and (d) any gap "
        "between what it actually does and what this module's own apparent purpose requires. Report "
        "(a)-(c) as grounding inside your recommendation/implementation_suggestion text, and report "
        "(d) using missing_items (required behaviour absent), duplicate_items (redundant "
        "implementations), and incomplete_items (partially-implemented items) -- exactly the existing "
        "schema fields below, no new fields.",
        "",
        f"Output MUST match ai-os/CHATGPT_AUDIT_RECORD_SCHEMA_2026-07-24.yaml exactly: every "
        f"required field populated, no fabricated file paths, no fabricated linkage ids. Do not "
        f"include audit_id or timestamp -- those are allocated separately once you respond; if you "
        f"include them anyway they will be ignored and replaced. Required/optional fields:",
        field_contract,
        "",
        f"STANDARDS_USED: {standards_used}",
        f"MODULES_ANALYSED: {[module['module_key']]}",
        f"REPOSITORY_VERSION: {repository_version}",
        f"COMMIT_HASH: {commit_hash}",
        f"MODULE (real module boundary -- target chatgpt-audit subfolder is 'Modules'): "
        f"{module['module_key']} (repo: {repo}, display_name: {module.get('display_name')}, "
        f"table_name: {module.get('table_name')}, boundary_source: {discovery_method})",
        fallback_note,
        part_note,
        "USER: Here is the real file content for each path in READ_SCOPE, concatenated with a path "
        "header per file. Analyse these against the standards above and produce your audit findings "
        "as a single YAML document matching the schema.",
        "",
        "\n\n".join(file_sections),
        "",
        "Respond with ONLY the YAML document -- no prose before or after, no markdown code fence.",
    ]
    return "\n".join(parts)


def _write_module_requests(args, module, source, repo_path, commit_hash, branch, schema, standards_used):
    files_analysed, used_fallback, discovery_method, excluded_oversized = mgal.find_module_files(
        repo_path, module, explicit_files=[f.strip() for f in args.files.split(",")] if args.files else None,
    )
    if not files_analysed:
        print(json.dumps({
            "ok": False,
            "error": f"no real files found for module '{module['module_key']}' in {repo_path} (keyword "
                     f"search and fallback docs both empty) -- pass --files explicitly",
        }, indent=2))
        sys.exit(1)

    chunks = mgal.pack_into_chunks(repo_path, files_analysed, args.max_request_lines, args.max_request_bytes)
    ts = _now_compact()
    request_paths = []

    for idx, chunk_files in enumerate(chunks, start=1):
        request_text = build_module_request_text(
            repo=args.repo, module=module, files_analysed=chunk_files, used_fallback=used_fallback,
            discovery_method=discovery_method, standards_used=standards_used, repository_version=branch,
            commit_hash=commit_hash, schema=schema, chunk_index=idx, chunk_count=len(chunks),
        )
        part_suffix = f"-part{idx}of{len(chunks)}" if len(chunks) > 1 else ""
        request_filename = f"Module-{args.repo}-{module['module_key']}-request-{ts}{part_suffix}.txt"
        request_path = os.path.join(PENDING_REQUESTS_DIR, request_filename)

        ingest_cmd = (f"python3 scripts/ingest_chatgpt_audit_response.py --module {module['module_key']} "
                       f"--repo {args.repo} --file <response-file>")
        header = (
            "=== VERIDIAN ChatGPT/Z.AI Audit Request (manual-bridge, module-scoped) ===\n"
            f"Module: {module['module_key']} ({args.repo})\n"
            f"Part: {idx} of {len(chunks)}\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n"
            "Generated by: scripts/generate_chatgpt_audit_request.py --module "
            "(task-20260725-063204-module-scoped-zai-gap-audit)\n"
            "\n"
            "INSTRUCTIONS FOR THE OWNER:\n"
            "  1. Copy everything below the '===== COPY BELOW THIS LINE =====' marker into a new,\n"
            "     free ChatGPT or Z.AI web chat session (no API key needed).\n"
            "  2. Save the model's full response (YAML only) to a file.\n"
            f"  3. Run: {ingest_cmd}\n"
            "\n"
            "===== COPY BELOW THIS LINE =====\n"
        )
        guarded_write(request_path, header + request_text)
        request_paths.append({
            "path": request_path, "part": idx, "of": len(chunks), "files": chunk_files,
            "lines": sum(mgal.file_stats(repo_path, f)[0] for f in chunk_files),
            "bytes": sum(mgal.file_stats(repo_path, f)[1] for f in chunk_files),
        })

    print(json.dumps({
        "ok": True,
        "mode": "module",
        "module": module["module_key"],
        "repo": args.repo,
        "module_source": source,
        "files_analysed": files_analysed,
        "excluded_oversized": excluded_oversized,
        "used_fallback_docs": used_fallback,
        "discovery_method": discovery_method,
        "standards_used": standards_used,
        "repository_version": branch,
        "commit_hash": commit_hash,
        "chunk_count": len(chunks),
        "request_paths": request_paths,
        "next_step": (f"python3 scripts/ingest_chatgpt_audit_response.py --module {module['module_key']} "
                      f"--repo {args.repo} --file <response-file> (once per generated part)"),
    }, indent=2))
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--domain", choices=VALID_SUBFOLDERS, help="domain-scoped mode (original)")
    mode_group.add_argument("--module", help="module-scoped mode (task-20260725-063204): real module_key/directory name")
    parser.add_argument("--repo", default="compliance-tracker", help="repo name under /opt/veridian/repos/")
    parser.add_argument("--files", default=None, help="comma-separated repo-relative paths to override auto-discovery")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help="domain mode only")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES, help="domain mode only")
    parser.add_argument("--max-request-lines", type=int, default=mgal.DEFAULT_MAX_REQUEST_LINES, help="module mode only")
    parser.add_argument("--max-request-bytes", type=int, default=mgal.DEFAULT_MAX_REQUEST_BYTES, help="module mode only")
    parser.add_argument("--standards", default=None, help="comma-separated standards_used override")
    args = parser.parse_args()

    repo_path = os.path.join(REPOS_ROOT, args.repo)
    if not os.path.isdir(repo_path):
        print(json.dumps({"ok": False, "error": f"repo not found: {repo_path}"}, indent=2))
        sys.exit(1)

    try:
        commit_hash = _run_git(repo_path, "rev-parse", "HEAD")
        branch = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        sys.exit(1)

    schema = load_schema()
    standards_used = resolve_standards(repo_path, [s.strip() for s in args.standards.split(",")] if args.standards else None)

    if args.module:
        try:
            module, source, all_modules = mgal.find_module(args.repo, repo_path, args.module)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
            sys.exit(1)
        if module is None:
            print(json.dumps({
                "ok": False,
                "error": f"module '{args.module}' not found for repo '{args.repo}'",
                "module_source": source,
                "available_modules_count": len(all_modules),
                "available_modules_sample": [m["module_key"] for m in all_modules[:20]],
            }, indent=2))
            sys.exit(1)
        _write_module_requests(args, module, source, repo_path, commit_hash, branch, schema, standards_used)
        return

    explicit_files = [f.strip() for f in args.files.split(",")] if args.files else None

    try:
        files_analysed, used_fallback = discover_files(repo_path, args.domain, args.max_files, explicit_files)
    except (RuntimeError, FileNotFoundError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        sys.exit(1)

    if not files_analysed:
        print(json.dumps({
            "ok": False,
            "error": f"no real files found for domain '{args.domain}' in {repo_path} (keyword search and "
                     f"fallback docs both empty) -- pass --files explicitly",
        }, indent=2))
        sys.exit(1)

    modules_analysed = sorted({os.path.dirname(f).split("/")[-1] or f for f in files_analysed})

    request_text = build_request_text(
        domain=args.domain, repo=args.repo, repo_path=repo_path, files_analysed=files_analysed,
        used_fallback=used_fallback, modules_analysed=modules_analysed, standards_used=standards_used,
        repository_version=branch, commit_hash=commit_hash, schema=schema, max_file_bytes=args.max_file_bytes,
    )

    ts = _now_compact()
    request_filename = f"{args.domain}-request-{ts}.txt"
    request_path = os.path.join(PENDING_REQUESTS_DIR, request_filename)

    header = (
        "=== VERIDIAN ChatGPT Audit Request (manual-bridge) ===\n"
        f"Domain: {args.domain}\n"
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n"
        "Generated by: scripts/generate_chatgpt_audit_request.py "
        "(INS-20260724-214122-f0ed, task-20260724-214215-chatgpt-manual-bridge-workflow)\n"
        "\n"
        "INSTRUCTIONS FOR THE OWNER:\n"
        "  1. Copy everything below the '===== COPY BELOW THIS LINE =====' marker into a new,\n"
        "     free ChatGPT web session (no API key needed).\n"
        "  2. Save ChatGPT's full response (YAML only) to a file.\n"
        f"  3. Run: python3 scripts/ingest_chatgpt_audit_response.py --domain {args.domain} --file <response-file>\n"
        "\n"
        "===== COPY BELOW THIS LINE =====\n"
    )
    full_text = header + request_text

    guarded_write(request_path, full_text)

    print(json.dumps({
        "ok": True,
        "mode": "domain",
        "request_path": request_path,
        "domain": args.domain,
        "repo": args.repo,
        "files_analysed": files_analysed,
        "used_fallback_docs": used_fallback,
        "modules_analysed": modules_analysed,
        "standards_used": standards_used,
        "repository_version": branch,
        "commit_hash": commit_hash,
        "next_step": f"python3 scripts/ingest_chatgpt_audit_response.py --domain {args.domain} --file <response-file>",
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
