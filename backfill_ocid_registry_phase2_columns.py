#!/usr/bin/env python3
"""backfill_ocid_registry_phase2_columns.py -- OCID-068 Phase 2 real, one-off
backfill (Owner directive UMR-20260805-090549-9710, extending the
now-superseded UMR-20260805-085025-c257; reinforced by
UMR-20260805-091934-86a2's mandatory active-file-fetch requirement; citing
the canonical OCID-068 UMR UMR-20260804-170055-a069 and its permanent
closure record UMR-20260805-032731-b412).

For each of the real existing rows in ocid_canonical_registry that cites a
real pr_number, ACTIVELY fetches the real, current PR data --
`gh pr view <pr_number> --repo FChecklist/<pr_repo> --json
mergeCommit,files,state,mergedAt`, using the real /usr/bin/gh binary
directly (never a bare unqualified `gh` that could resolve to a session
wrapper), never passively trusting whatever text happened to already be in
evidence_json -- to recover:
  - merge_status ('merged' if mergedAt set, 'open' if state==OPEN,
    'closed_unmerged' if state==CLOSED with no mergedAt)
  - commit_sha (mergeCommit.oid, only when merged)
  - the REAL, COMPLETE list of every file the PR changed

Per UMR-20260805-091934-86a2: every one of those real changed files is
recorded as its own real linkage row in the existing (PR #20) ocid_artifact_
links graph via insert_ocid_artifact_link(..., link_kind='changed_file') --
one OCID can genuinely link to many real files, not just the single
"primary artifact" file_path column on ocid_canonical_registry (which still
gets its own best-single-file pick, unchanged in spirit from the original
directive, for whichever real single file most plausibly is this OCID's own
canonical artifact). A row's is_fully_complete can therefore stay honestly
0 (has_real_file_path=0) even after every one of its real files was
recorded in the linkage graph, if no single file was an unambiguous primary
pick -- that is the correct, honest behavior per both directives: real
information is never lost (it is in ocid_artifact_links), but the single
file_path column is never guessed.

Same (pr_number, pr_repo) PR is fetched at most once per run (many OCID
rows cite the same real PR, e.g. #793, #907, #725) -- real API-call
efficiency, not a change in what is fetched per PR.

The ONLY real exception to "actively fetch": the 8 real rows already
honestly confirmed not_found (OCID-007..OCID-011, OCID-012, OCID-013,
OCID-014) -- these get NO gh calls and NO linkage rows; a real file path
does not apply to an OCID that was never real / never registered, which is
what not_found already, honestly records. Their real
not_applicable_confirmed=1 marker is DB-trigger-derived from not_found
(see _ensure_ocid_canonical_registry_completion_triggers()), not set here.

Any OTHER row with no real pr_number at all (e.g. OCID-004, OCID-005,
OCID-020, OCID-069 -- real, not not_found, but never dispatched under a PR)
has no real PR to fetch from either; commit_sha/file_name/file_path/
merge_status stay honestly NULL for these too, same as the original
directive, but they are NOT part of the not_applicable_confirmed exception
set (that set is exactly the 8 OCID numbers above, derived from not_found,
not from "has no pr_number").

Default is a dry run (prints the real proposed per-row backfill plus real
linkage-row plan as JSON, writes nothing). Pass --apply to actually write,
via the real, already-merged upsert_ocid_canonical_registry() and
insert_ocid_artifact_link() (never raw SQL), inside a real _write_lock().
--ocid-number limits to one row (debugging / targeted re-run of a single
OCID after a transient `gh` failure).
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

GH_BIN = "/usr/bin/gh"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
GITHUB_OWNER = "FChecklist"

# The 8 real rows already honestly confirmed not_found (Owner reinforcement
# UMR-20260805-091934-86a2's own exception list, verbatim) -- no gh calls,
# no linkage rows, for these; not_applicable_confirmed is trigger-derived
# from their own real not_found=1.
NOT_FOUND_EXCEPTION_OCIDS = {
    "OCID-007", "OCID-008", "OCID-009", "OCID-010", "OCID-011",
    "OCID-012", "OCID-013", "OCID-014",
}


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_backfill_ocid_phase2", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def gh_pr_view(pr_number, pr_repo):
    """Real subprocess call to the real gh binary (explicit /usr/bin/gh
    path, not a bare `gh` that could resolve to a wrapper). Returns
    (data_dict_or_None, error_string_or_None) -- never raises, so one PR's
    real gh failure (auth, rate-limit, transient network) does not abort
    the whole real backfill run; the caller records the honest failure and
    moves on rather than guessing a value."""
    repo_full = pr_repo if "/" in pr_repo else f"{GITHUB_OWNER}/{pr_repo}"
    cmd = [GH_BIN, "pr", "view", str(pr_number), "--repo", repo_full,
           "--json", "mergeCommit,files,state,mergedAt"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return None, f"subprocess error: {e}"
    if result.returncode != 0:
        return None, (result.stderr or "").strip()[:300]
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as e:
        return None, f"gh returned non-JSON output: {e}"


def derive_merge_status(state, merged_at):
    if merged_at:
        return "merged"
    if state == "OPEN":
        return "open"
    if state == "CLOSED":
        return "closed_unmerged"
    return None


def pick_primary_file(files, ocid_number):
    """Real, honest single-"primary artifact" file identification for the
    ocid_canonical_registry.file_path column. Exactly one changed file:
    unambiguous, use it. More than one: only claim a match when exactly one
    changed file's own path contains this OCID's own number in the
    established `OCID-NNN`/`OCID_NNN` naming convention AND ends in `.md`
    -- otherwise return (None, None, ambiguous=True) rather than guess.
    (This does not affect the FULL real file list, which is recorded
    separately, unconditionally, in ocid_artifact_links.)"""
    if not files:
        return None, None, False
    if len(files) == 1:
        path = files[0]["path"]
        return os.path.basename(path), path, False
    num = re.sub(r"^OCID-0*", "", ocid_number)
    pattern = re.compile(rf"OCID[-_]0*{re.escape(num)}(?!\d)", re.IGNORECASE)
    candidates = [f["path"] for f in files if pattern.search(f["path"]) and f["path"].lower().endswith(".md")]
    if len(candidates) == 1:
        path = candidates[0]
        return os.path.basename(path), path, False
    return None, None, True


def build_evidence_summary(status, evidence, ambiguous_multi_file, gh_error, file_count, not_found):
    """Short, real, one-sentence synthesis drawn ONLY from this row's own
    existing status/evidence_json -- never a new fabricated claim."""
    if not_found:
        base = (status or "").strip().rstrip(".")
        return (f"{base} -- confirmed not_found (never real / never registered): no real PR/file "
                f"path applies; not_applicable_confirmed=1.")[:500]

    parts = []
    for key in ("method", "note", "merge_commit", "db_status"):
        v = evidence.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if not parts:
        for v in evidence.values():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
                break
    base = (status or "").strip().rstrip(".")
    detail = "; ".join(parts)[:200]
    summary = base + (f" -- evidence: {detail}" if detail else "")
    if file_count:
        summary += f" (real PR diff actively fetched: {file_count} real changed file(s) recorded in ocid_artifact_links)"
    if ambiguous_multi_file:
        summary += (" (PR changed multiple files; no single canonical primary artifact file could be "
                     "honestly identified, ocid_canonical_registry.file_path left NULL)")
    if gh_error:
        summary += f" (gh pr view failed during backfill: {gh_error})"
    return summary[:500]


def backfill_row(row, pr_cache):
    ocid = row["ocid_number"]
    pr_number = row["pr_number"]
    pr_repo = row["pr_repo"]
    not_found = bool(row["not_found"])
    commit_sha = file_name = file_path = merge_status = None
    ambiguous = False
    gh_error = None
    changed_files = []

    is_exception = ocid in NOT_FOUND_EXCEPTION_OCIDS

    if not is_exception and pr_number and pr_repo:
        cache_key = (pr_number, pr_repo)
        if cache_key not in pr_cache:
            pr_cache[cache_key] = gh_pr_view(pr_number, pr_repo)
        data, gh_error = pr_cache[cache_key]
        if data is not None:
            merge_status = derive_merge_status(data.get("state"), data.get("mergedAt"))
            if merge_status == "merged":
                mc = data.get("mergeCommit") or {}
                commit_sha = mc.get("oid")
            changed_files = [f["path"] for f in (data.get("files") or [])]
            file_name, file_path, ambiguous = pick_primary_file(data.get("files") or [], ocid)

    evidence_summary = build_evidence_summary(
        row["status"], row["evidence"], ambiguous, gh_error, len(changed_files), not_found
    )

    return {
        "ocid_number": ocid,
        "pr_number": pr_number,
        "pr_repo": pr_repo,
        "canonical_umr_id": row["canonical_umr_id"],
        "not_found": not_found,
        "is_exception": is_exception,
        "commit_sha": commit_sha,
        "file_name": file_name,
        "file_path": file_path,
        "merge_status": merge_status,
        "evidence_summary": evidence_summary,
        "ambiguous_multi_file": ambiguous,
        "gh_error": gh_error,
        "changed_files": changed_files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the backfilled rows and linkage rows; default is a dry run that only prints them")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    rows = sbr.query_ocid_canonical_registry(conn, ocid_number=args.ocid_number)
    conn.close()

    if not rows:
        print(json.dumps({"error": "no real rows found", "ocid_number": args.ocid_number}, indent=2))
        return 1

    pr_cache = {}
    results = [backfill_row(row, pr_cache) for row in rows]
    print(json.dumps(results, indent=2, default=str))

    non_excepted = [r for r in results if not r["is_exception"]]
    non_excepted_with_pr = [r for r in non_excepted if r["pr_number"]]
    recovered_commit = sum(1 for r in non_excepted_with_pr if r["commit_sha"])
    recovered_primary_file = sum(1 for r in non_excepted_with_pr if r["file_path"])
    recovered_any_files = sum(1 for r in non_excepted_with_pr if r["changed_files"])
    total_linkage_rows_planned = sum(len(r["changed_files"]) for r in non_excepted_with_pr)
    no_pr_non_excepted = sum(1 for r in non_excepted if not r["pr_number"])
    gh_failures = sum(1 for r in non_excepted_with_pr if r["gh_error"])
    exceptions = sum(1 for r in results if r["is_exception"])

    print(
        f"SUMMARY: {len(results)} total rows | {exceptions} not_found exception rows (no fetch attempted) | "
        f"{len(non_excepted_with_pr)} non-excepted rows with a real pr_number (actively fetched) | "
        f"{no_pr_non_excepted} non-excepted rows with no pr_number (honestly NULL, no PR to fetch) | "
        f"commit_sha recovered: {recovered_commit} | primary file_path recovered: {recovered_primary_file} | "
        f"rows with >=1 real changed file recovered via gh: {recovered_any_files} | "
        f"total real per-file linkage rows planned: {total_linkage_rows_planned} | gh failures: {gh_failures}",
        file=sys.stderr,
    )

    if not args.apply:
        print("DRY RUN -- pass --apply to actually write these rows + linkage rows", file=sys.stderr)
        return 0

    conn = sbr._connect()
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    linkage_written = 0
    with sbr._write_lock():
        for row, r in zip(rows, results):
            sbr.upsert_ocid_canonical_registry(
                conn, row["ocid_number"],
                canonical_umr_id=row["canonical_umr_id"], status=row["status"],
                all_umr_ids=row["all_umr_ids"], evidence=row["evidence"],
                pr_number=row["pr_number"], pr_repo=row["pr_repo"],
                duplicate_reason=row["duplicate_reason"], not_found=bool(row["not_found"]),
                commit_sha=r["commit_sha"], file_name=r["file_name"], file_path=r["file_path"],
                merge_status=r["merge_status"], evidence_summary=r["evidence_summary"],
            )
            if r["changed_files"] and r["canonical_umr_id"]:
                for fpath in r["changed_files"]:
                    link_id = sbr.insert_ocid_artifact_link(
                        conn, ocid_number=r["ocid_number"], umr_id=r["canonical_umr_id"],
                        repo=r["pr_repo"], link_kind="changed_file",
                        pr_number=r["pr_number"], commit_sha=r["commit_sha"], file_path=fpath,
                    )
                    if link_id is not None:
                        linkage_written += 1
        conn.commit()
    conn.close()
    print(f"APPLIED: wrote {len(results)} real ocid_canonical_registry rows + {linkage_written} real "
          f"ocid_artifact_links linkage rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
