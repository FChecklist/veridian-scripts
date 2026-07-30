#!/usr/bin/env python3
"""
generate_module_gap_audit_module_list.py -- mechanical module-list generator
for the module-scoped ChatGPT/Z.AI gap audit
(task-20260725-063204-module-scoped-zai-gap-audit), SCOPE item 1.

Owner directive (repo-wide convention, see e.g.
scripts/generate_chatgpt_audit_index.py's own header): "all registry/catalog
data must be produced by scripts, not hand-authored AI prose." This script is
that mechanism for ai-os/MODULE_GAP_AUDIT_MODULE_LIST_2026-07-25.yaml -- it
never invents a module or a count: for every real module in
scripts/module_gap_audit_lib.py's module-boundary resolution (compliance.
module_registry for compliance-tracker, src/app/api/ directory structure for
projexa), it actually runs the same real file-discovery + chunk-packing logic
generate_chatgpt_audit_request.py --module uses, and records the real
file/line/byte counts and the real number of request files full coverage of
that module would need.

Run: python3 scripts/generate_module_gap_audit_module_list.py
"""
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import module_gap_audit_lib as mgal  # noqa: E402

import yaml  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_PATH = os.path.join(REPO_ROOT, "ai-os", "MODULE_GAP_AUDIT_MODULE_LIST_2026-07-25.yaml")
REPOS = ["compliance-tracker", "projexa"]


def scan_repo(repo):
    repo_path = mgal.repo_path_for(repo)
    modules, source = mgal.list_modules(repo, repo_path)
    tracked = mgal.list_tracked_files(repo_path)
    commit_hash = mgal.run_git(repo_path, "rev-parse", "HEAD")
    branch = mgal.run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")

    module_records = []
    total_chunks = 0
    for m in modules:
        files, used_fallback, method, excluded_oversized = mgal.find_module_files(
            repo_path, m, tracked_files=tracked,
        )
        stats = mgal.module_real_stats(repo_path, files)
        chunks = mgal.pack_into_chunks(repo_path, files)
        total_chunks += len(chunks)
        module_records.append({
            "module_key": m["module_key"],
            "display_name": m.get("display_name"),
            "table_name": m.get("table_name"),
            "domain": m.get("domain"),
            "category": m.get("category"),
            "source_file": m.get("source_file"),
            "file_count": stats["file_count"],
            "line_count": stats["line_count"],
            "byte_count": stats["byte_count"],
            "chunk_count_for_full_coverage": len(chunks),
            "used_fallback_docs": used_fallback,
            "excluded_oversized_file_count": len(excluded_oversized),
        })

    return {
        "module_source": source,
        "commit_hash": commit_hash,
        "repository_version": branch,
        "module_count": len(modules),
        "total_request_files_for_full_coverage": total_chunks,
        "modules": module_records,
    }


def build_yaml():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repos_out = {}
    grand_total = 0
    for repo in REPOS:
        repo_data = scan_repo(repo)
        repos_out[repo] = repo_data
        grand_total += repo_data["total_request_files_for_full_coverage"]

    return {
        "meta": {
            "id": "MODULE_GAP_AUDIT_MODULE_LIST",
            "version": "1.0.0",
            "generated_ts": now,
            "created_by_task": "task-20260725-063204-module-scoped-zai-gap-audit",
            "generated_by_script": "scripts/generate_module_gap_audit_module_list.py",
            "purpose": (
                "Real, mechanically-computed module list for the module-scoped ChatGPT/Z.AI gap "
                "audit covering compliance-tracker and projexa. Every file/line/byte count and "
                "chunk_count_for_full_coverage below comes from actually running "
                "scripts/module_gap_audit_lib.py's real file-discovery (filename substring + `git "
                "grep -lI` content match, SQL narrowed to schema-defining migrations, monolithic "
                "shared files excluded above the auto-discovery size ceiling) and chunk-packing "
                "(~400 lines / ~20KB reference ceiling per request, whole files only, never "
                "truncated) against each repo's real git-tracked files at the commit recorded per "
                "repo below -- nothing here is estimated or hand-typed. Re-run this script any time "
                "to refresh against a newer commit (safe, idempotent, always overwrites with a fresh "
                "live scan)."
            ),
        },
        "repos": repos_out,
        "grand_total_request_files_for_full_coverage": grand_total,
    }


def main():
    doc = build_yaml()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)

    print(json.dumps({
        "ok": True,
        "output_path": OUTPUT_PATH,
        "repos": {repo: {"module_count": data["module_count"],
                          "total_request_files_for_full_coverage": data["total_request_files_for_full_coverage"]}
                  for repo, data in doc["repos"].items()},
        "grand_total_request_files_for_full_coverage": doc["grand_total_request_files_for_full_coverage"],
    }, indent=2))


if __name__ == "__main__":
    main()
