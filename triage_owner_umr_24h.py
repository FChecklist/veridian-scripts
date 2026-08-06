#!/usr/bin/env python3
"""triage_owner_umr_24h.py -- deterministic 4-bucket triage of the real
trailing-24h owner_dispatch_gateway 'failed'/'killed' backlog
(UMR-20260806-091345-d90c, child of the standing 24h owner-dispatch closure
mandate UMR-20260806-071025-1d28).

Recomputes the trailing-24h owner_dispatch_gateway umr_tasks set FRESH from
the real, live DB every run -- no hardcoded counts anywhere (the starting
numbers a dispatch cites are explicitly a snapshot, not ground truth; this
script is the ground truth, live, every time it runs).

For every row with status IN ('failed', 'killed'), cross-references real
evidence -- task.yaml under /opt/veridian/ai-os/tasks/<task_id>/ (derived
from unit_name, never hand-typed), real git/GitHub PR state for any repo
cited, and the row's own real `reason`/`metadata_json` columns (which this
project's own prior reconciliation passes already populated with real,
structured evidence strings -- see the `reason` column's own
"one-time backfill reconciliation" and `metadata_json`'s
"reconciliation_<umr>_evidence"/"reuse_check_result" keys) -- and buckets
each row into EXACTLY one of:

  1. already_done   - real work landed under a different task/PR; row
                       status is merely stale. Evidence MUST be a real
                       merged commit confirmed via
                       `git merge-base --is-ancestor <sha> origin/main`
                       against a real local clone -- never inferred from
                       GitHub's `mergedAt` field alone.
  2. superseded      - a later real UMR row (same task_identity, later
                       ts_submitted, non-terminal-failure status) covers
                       the same real goal. Cites that UMR by id.
  3. retryable       - real failure cause is mechanical (merge conflict,
                       quality-gate failure, transient worker death/no
                       task.yaml ever written) with no real remaining
                       blocker.
  4. blocked         - a real external blocker remains: credit-accountant
                       rejection, the missing-reviewer-identity gap already
                       confirmed real and repo-wide on compliance-tracker
                       this session (OCID-070, citing
                       UMR-20260805-034917-33a9), a genuine Owner-only
                       decision, OR (deterministic fallback) insufficient
                       real evidence exists to safely place the row in
                       buckets 1-3 -- absence of evidence is itself treated
                       as "needs an Owner-only look", never guessed into a
                       more convenient bucket.

Reproducibility: classification depends ONLY on the row's own real DB
columns, real task.yaml content, and real git/GitHub state -- never on
wall-clock-relative "recency" (e.g. "this looks old so it's probably X").
A second independent run over an unchanged row returns the same bucket by
construction. The one exception, `superseded`'s ts_submitted ordering, is
inherently a property of the two rows being compared, not of when the
script itself runs, so it too is stable across runs.

Writes classifications back ONLY through superboss-register.py's own
update_umr_task() inside its own _write_lock() (matches
reconcile_owner_dispatch_status.py / PR #147's established convention) --
never a raw UPDATE. A read-existing-metadata/merge/write-back, never a
blind replace, so no pre-existing metadata_json key is ever destroyed.

Per the standing propose-then-approve-then-execute mandate, this script
NEVER implements a fix for bucket 3/4 rows and NEVER calls
decide-owner-proposal -- it only deposits one real child-UMR proposal per
bucket-3/4 row via superboss-register.py's `insert-owner-proposal` CLI
(issue = the real problem, proposal = the real proposed fix), under parent
UMR-20260806-071025-1d28.

Usage:
    python3 triage_owner_umr_24h.py                    # report only, no writes (default)
    python3 triage_owner_umr_24h.py --apply             # write bucket/evidence back via update_umr_task()
    python3 triage_owner_umr_24h.py --file-proposals     # also deposit bucket-3/4 child-UMR proposals (requires --apply)
    python3 triage_owner_umr_24h.py --json out.json      # write full per-row evidence to a file
    python3 triage_owner_umr_24h.py --umr-id UMR-...     # limit to one row (debugging/re-run)
"""
import argparse
import importlib.util as _ilu
import json
import os
import re
import subprocess
import sys
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = "/opt/veridian/ai-os/tasks"
REPO_CACHE_ROOT = os.environ.get("TRIAGE_REPO_CACHE_ROOT", "/opt/veridian/ai-os/.repo-cache")
PARENT_UMR = "UMR-20260806-071025-1d28"
THIS_UMR = "UMR-20260806-091345-d90c"

# Real, already-confirmed-this-session finding (OCID-070) -- cited verbatim
# for any row whose real blocker is the missing independent-reviewer-identity
# gap on compliance-tracker, never re-derived or re-asserted here.
MISSING_REVIEWER_IDENTITY_CITATION = (
    "OCID-070 (UMR-20260805-034917-33a9): compliance-tracker has no genuinely "
    "independent reviewer identity -- FChecklist is the sole collaborator and "
    "every credential in this environment resolves to that same account; "
    "provisioning a real second identity requires an interactive human step "
    "(GitHub App creation or a second personal account) that cannot be "
    "scripted. Confirmed real and repo-wide this session, not re-derived here."
)

BUCKETS = ("already_done", "superseded", "retryable", "blocked")

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)

# ---------------------------------------------------------------------------
# Real regex signals over this project's own already-structured evidence text
# (the `reason` column and metadata_json's reconciliation/reuse_check_result
# keys -- populated by prior real reconciliation passes, not narration this
# script invents). Kept narrow and literal on purpose: a miss here means the
# row correctly falls through to the safe blocked/needs-Owner-look default,
# never a false-positive mechanical/superseded/already_done classification.
# ---------------------------------------------------------------------------
CREDIT_ACCOUNTANT_RE = re.compile(r"credit[- ]accountant rejected", re.I)
MISSING_REVIEWER_RE = re.compile(r"missing[- ]reviewer identity|second reviewer identity|no genuinely independent reviewer", re.I)
OWNER_DECISION_RE = re.compile(r"owner[- ]only decision|awaiting owner decision|requires? owner (approval|sign-?off)", re.I)
MERGE_CONFLICT_RE = re.compile(r"merge conflict", re.I)
QUALITY_GATE_RE = re.compile(r"quality[- ]gate (failed|failure)", re.I)
NO_TASK_YAML_RE = re.compile(r"no task\.yaml found under TASKS_DIR", re.I)
PUSHED_MERGE_COMMIT_RE = re.compile(r"pushed merge commit ([0-9a-f]{7,40})", re.I)
PR_NUMBER_RE = re.compile(r"PR\s*#(\d+)", re.I)
RELATED_UMR_RE = re.compile(r"UMR-\d{8}-\d{6}-[0-9a-f]{4}")
REPO_HINT_RE = re.compile(r"\b(compliance-tracker|veridian-scripts)\b")


def run(cmd, timeout=30, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return -1, "", str(e)


def task_dir_from_unit(unit_name):
    if not unit_name:
        return None
    m = re.match(r"veridian-worker@(.+)\.service$", unit_name)
    return m.group(1) if m else None


def read_task_yaml(task_id):
    """Real task.yaml read. Prefers PyYAML; falls back to a line-scan (same
    fallback shape this project's other reconciliation scripts already use)
    so a missing PyYAML install degrades to fewer fields, never to silently
    treating the task as having none. Returns None if no real file exists."""
    if not task_id:
        return None
    path = os.path.join(TASKS_DIR, task_id, "task.yaml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        info = {}
        with open(path) as f:
            for line in f:
                for key in ("status", "adopted_pr_url", "branch", "repo", "last_checkpoint_at"):
                    if line.startswith(f"{key}:"):
                        info[key] = line.split(":", 1)[1].strip().strip("'\"")
        return info


def local_repo_path(repo):
    """Returns a real local git clone path for `repo`, cloning/reusing a
    cache under REPO_CACHE_ROOT if it isn't the veridian-scripts checkout
    this script itself lives in. Only the two repos this project's own
    dispatches actually cite (veridian-scripts, compliance-tracker) are
    ever cloned -- no arbitrary repo name from row text is ever passed to
    `git clone` unvalidated."""
    if repo == "veridian-scripts":
        return SCRIPT_DIR
    if repo != "compliance-tracker":
        return None
    path = os.path.join(REPO_CACHE_ROOT, repo)
    if not os.path.isdir(os.path.join(path, ".git")):
        os.makedirs(REPO_CACHE_ROOT, exist_ok=True)
        rc, _, _ = run(["git", "clone", f"https://github.com/FChecklist/{repo}.git", path], timeout=120)
        if rc != 0:
            return None
    return path


def is_commit_on_main(repo, sha, git_runner=run, repo_path_resolver=local_repo_path):
    """Real merge-base ancestry check -- the only signal this script ever
    accepts as proof a commit is genuinely on main, never GitHub's
    `mergedAt` field alone (a PR can be merged into a non-main base, or
    into a branch later reverted from main)."""
    if not sha:
        return False
    path = repo_path_resolver(repo)
    if not path:
        return None  # unknown/unresolvable repo -- honestly unknown, never guessed True
    git_runner(["git", "fetch", "origin", "main"], cwd=path, timeout=60)
    git_runner(["git", "fetch", "origin", sha], cwd=path, timeout=60)
    rc, _, _ = git_runner(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=path, timeout=30)
    return rc == 0


def gh_pr_view(repo, number, gh_runner=run):
    if repo not in ("veridian-scripts", "compliance-tracker") or not number:
        return None
    rc, out, err = gh_runner(
        ["gh", "pr", "view", str(number), "--repo", f"FChecklist/{repo}",
         "--json", "number,state,mergedAt,mergeCommit,baseRefName,headRefOid"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def lookup_umr_row(conn, umr_id):
    row = conn.execute(
        "SELECT umr_id, task_identity, status, ts_submitted FROM umr_tasks WHERE umr_id=?",
        (umr_id,),
    ).fetchone()
    return dict(row) if row else None


def find_superseding_row(conn, row):
    """Deterministic 'same real goal, later, non-terminal-failure' lookup:
    exact task_identity match only (no fuzzy text matching -- fuzzy
    matching is exactly the kind of non-reproducible signal the spec rules
    out), later ts_submitted, status not in ('failed','killed'). Among
    candidates, picks the row with the MAX ts_submitted -- a deterministic
    tie-break, not "most recent as of when I happened to run"."""
    candidates = conn.execute(
        "SELECT umr_id, task_identity, status, ts_submitted FROM umr_tasks "
        "WHERE task_identity=? AND umr_id != ? AND ts_submitted > ? "
        "AND status NOT IN ('failed','killed') ORDER BY ts_submitted DESC LIMIT 1",
        (row["task_identity"], row["umr_id"], row["ts_submitted"]),
    ).fetchone()
    return dict(candidates) if candidates else None


def load_rows(conn, umr_id=None):
    if umr_id:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_submitted, unit_name, logs_ref, reason, metadata_json "
            "FROM umr_tasks WHERE umr_id=? AND source_trigger='owner_dispatch_gateway' "
            "AND status IN ('failed','killed')",
            (umr_id,),
        )
    else:
        cur = conn.execute(
            "SELECT umr_id, task_identity, status, ts_submitted, unit_name, logs_ref, reason, metadata_json "
            "FROM umr_tasks WHERE source_trigger='owner_dispatch_gateway' "
            "AND ts_submitted >= datetime('now', '-24 hours') AND status IN ('failed','killed') "
            "ORDER BY ts_submitted"
        )
    return [dict(r) for r in cur.fetchall()]


def load_24h_owner_dispatch_total(conn):
    """Real, fresh count of the FULL trailing-24h owner_dispatch_gateway set
    (all statuses) -- used only to report context, never to drive a
    classification."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM umr_tasks WHERE source_trigger='owner_dispatch_gateway' "
        "AND ts_submitted >= datetime('now', '-24 hours')"
    ).fetchone()["n"]


def gather_evidence(conn, row, *, task_yaml_reader=read_task_yaml,
                     ancestor_checker=is_commit_on_main, pr_viewer=gh_pr_view,
                     superseding_finder=find_superseding_row):
    """Builds the full real evidence dict for one row. Pure with respect to
    its injected collaborators -- callers can substitute mocks for
    task_yaml_reader/ancestor_checker/pr_viewer/superseding_finder to make
    this deterministic and offline for tests."""
    umr_id = row["umr_id"]
    unit_name = row["unit_name"]
    reason = row.get("reason") or ""
    try:
        metadata = json.loads(row["metadata_json"]) if row.get("metadata_json") else {}
    except Exception:
        metadata = {}
    metadata_text = json.dumps(metadata)
    combined_text = f"{reason}\n{metadata_text}"

    task_id = task_dir_from_unit(unit_name)
    yml = task_yaml_reader(task_id) or {}

    evidence = {
        "umr_id": umr_id,
        "task_identity": row["task_identity"],
        "db_status": row["status"],
        "ts_submitted": row["ts_submitted"],
        "unit_name": unit_name,
        "task_dir": task_id,
        "task_yaml_found": bool(yml),
        "task_yaml_status": yml.get("status"),
        "adopted_pr_url": yml.get("adopted_pr_url"),
        "yaml_repo": yml.get("repo"),
        "yaml_branch": yml.get("branch"),
    }

    # --- repo/PR number resolution: prefer structured task.yaml, fall back
    # to the row's own real, previously-recorded evidence text (never to
    # arbitrary free text elsewhere). ---
    repo = yml.get("repo")
    if not repo:
        m = REPO_HINT_RE.search(combined_text)
        repo = m.group(1) if m else None
    pr_number = None
    if yml.get("adopted_pr_url"):
        m = re.search(r"/pull/(\d+)", yml["adopted_pr_url"])
        pr_number = int(m.group(1)) if m else None
    if pr_number is None:
        m = PR_NUMBER_RE.search(combined_text)
        pr_number = int(m.group(1)) if m else None
    evidence["repo"] = repo
    evidence["pr_number"] = pr_number

    pr_state = None
    if repo and pr_number:
        pr_state = pr_viewer(repo, pr_number)
    evidence["pr_state"] = pr_state

    # --- bucket 1 signal: a real pushed-merge-commit sha, confirmed on main. ---
    merge_sha = None
    m = PUSHED_MERGE_COMMIT_RE.search(combined_text)
    if m:
        merge_sha = m.group(1)
    elif pr_state and pr_state.get("mergedAt") and (pr_state.get("mergeCommit") or {}).get("oid"):
        merge_sha = pr_state["mergeCommit"]["oid"]
    evidence["merge_commit_sha"] = merge_sha
    evidence["merge_commit_is_ancestor_of_main"] = (
        ancestor_checker(repo, merge_sha) if (merge_sha and repo) else None
    )

    # --- bucket 2 signal: a later real row, same task_identity. ---
    superseding = superseding_finder(conn, row)
    evidence["superseding_umr"] = superseding["umr_id"] if superseding else None
    evidence["superseding_umr_status"] = superseding["status"] if superseding else None

    # --- bucket 3/4 mechanical & blocker signals, from this row's own real
    # previously-recorded evidence text. ---
    # A compliance-tracker row whose own evidence text shows a real
    # AUDIT:PASS already posted, blocked only by an OPEN (unmergeable-by-self)
    # PR, is the same real independent-reviewer-identity gap OCID-070 already
    # confirmed repo-wide -- surfaced here from the row's own real recorded
    # text, never re-derived or assumed for rows that don't show it.
    audit_pass_recorded = bool(AUDIT_PASS_TEXT_RE.search(combined_text))
    blocked_by_open_pr_despite_audit_pass = (
        repo == "compliance-tracker" and audit_pass_recorded
        and bool(pr_state) and pr_state.get("state") == "OPEN"
    )
    evidence["credit_accountant_rejected"] = bool(CREDIT_ACCOUNTANT_RE.search(combined_text))
    evidence["missing_reviewer_identity"] = (
        bool(MISSING_REVIEWER_RE.search(combined_text)) or blocked_by_open_pr_despite_audit_pass
    )
    evidence["owner_decision_required"] = bool(OWNER_DECISION_RE.search(combined_text))
    evidence["merge_conflict"] = bool(MERGE_CONFLICT_RE.search(combined_text))
    evidence["quality_gate_failed"] = bool(QUALITY_GATE_RE.search(combined_text))
    evidence["no_task_yaml_ever_written"] = bool(NO_TASK_YAML_RE.search(combined_text)) and not yml
    evidence["related_umr_mentions"] = sorted(set(RELATED_UMR_RE.findall(combined_text)) - {umr_id})

    return evidence


def classify(evidence):
    """Pure deterministic decision tree -- exactly one of the 4 buckets,
    priority order fixed and documented so a second run over unchanged
    evidence always returns the same result."""
    if evidence["merge_commit_sha"] and evidence["merge_commit_is_ancestor_of_main"] is True:
        return "already_done", (
            f"real merge commit {evidence['merge_commit_sha']} confirmed on origin/main via "
            f"git merge-base --is-ancestor (repo={evidence.get('repo')})"
        )

    if evidence["superseding_umr"]:
        return "superseded", (
            f"later real row {evidence['superseding_umr']} (status={evidence.get('superseding_umr_status')}) "
            f"shares this row's exact task_identity and was submitted after it"
        )

    if evidence["credit_accountant_rejected"]:
        return "blocked", "real credit-accountant rejection recorded in this row's own evidence text"

    if evidence["missing_reviewer_identity"]:
        return "blocked", MISSING_REVIEWER_IDENTITY_CITATION

    if evidence["owner_decision_required"]:
        return "blocked", "row's own evidence text records this as a genuine Owner-only decision"

    if evidence["merge_conflict"] or evidence["quality_gate_failed"]:
        cause = "merge conflict" if evidence["merge_conflict"] else "quality-gate failure"
        return "retryable", f"real mechanical failure cause ({cause}) recorded, no remaining blocker found in evidence"

    if evidence["no_task_yaml_ever_written"] and not evidence["pr_number"]:
        return "retryable", (
            "no task.yaml was ever written and no PR was ever opened -- real signature of a "
            "transient worker death before real work started; mechanically retryable"
        )

    return "blocked", (
        "no real evidence found for a confirmed merge, a confirmed superseding row, or a known "
        "mechanical/blocker signal -- deterministic fallback: treated as needing an Owner-only look "
        "rather than guessed into a more convenient bucket"
    )


AUDIT_PASS_TEXT_RE = re.compile(r"AUDIT[:\s]+PASS", re.I)  # kept for evidence dict readability only


def apply_classification(conn, evidence, bucket, reason):
    """Writes back ONLY through update_umr_task(), read-existing-metadata/
    merge/write-back so no pre-existing metadata_json key is ever
    destroyed -- same convention as reconcile_owner_dispatch_status.py's
    apply_correction()."""
    umr_id = evidence["umr_id"]
    existing_row = conn.execute(
        "SELECT metadata_json FROM umr_tasks WHERE umr_id=?", (umr_id,)
    ).fetchone()
    existing_metadata = {}
    if existing_row and existing_row["metadata_json"]:
        try:
            existing_metadata = json.loads(existing_row["metadata_json"])
        except Exception:
            existing_metadata = {"_unparseable_prior_metadata_json": existing_row["metadata_json"]}
    existing_metadata[f"triage_{THIS_UMR}"] = {
        "bucket": bucket,
        "reason": reason,
        "evidence": evidence,
        "parent_umr": PARENT_UMR,
    }
    _sbr.update_umr_task(conn, umr_id, metadata=existing_metadata)


def file_proposal(evidence, bucket, reason):
    """Deposits exactly one real child-UMR proposal via the canonical
    `insert-owner-proposal` CLI -- propose only, per the standing
    propose-then-approve-then-execute mandate. Never calls
    decide-owner-proposal; never implements the fix itself."""
    issue = (
        f"UMR {evidence['umr_id']} (task_identity={evidence['task_identity']}, "
        f"db_status={evidence['db_status']}) triaged as bucket '{bucket}' under "
        f"{THIS_UMR} (parent {PARENT_UMR}): {reason}"
    )
    if bucket == "retryable":
        proposal = (
            f"Retry/redispatch the real work for {evidence['task_identity']} "
            f"(unit_name={evidence['unit_name']}); the recorded failure cause is mechanical "
            f"and no external blocker remains."
        )
    else:
        proposal = (
            f"Owner review requested for {evidence['task_identity']}: {reason}. No mechanical "
            f"fix is proposed here because a real external blocker (or insufficient evidence) "
            f"remains; this proposal only asks for an Owner decision on how to proceed."
        )
    rc, out, err = run(
        [sys.executable, os.path.join(SCRIPT_DIR, "superboss-register.py"),
         "insert-owner-proposal", "--issue", issue, "--proposal", proposal],
        timeout=30,
    )
    if rc != 0:
        return {"error": err.strip() or out.strip()}
    try:
        return json.loads(out)
    except Exception:
        return {"raw_output": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write bucket/evidence back via update_umr_task()")
    ap.add_argument("--file-proposals", action="store_true",
                     help="also deposit bucket-3/4 child-UMR proposals via insert-owner-proposal (requires --apply)")
    ap.add_argument("--json", dest="json_out", default=None, help="write full per-row evidence to a file")
    ap.add_argument("--umr-id", dest="umr_id", default=None, help="limit to one row (debugging/re-run)")
    args = ap.parse_args()

    if args.file_proposals and not args.apply:
        print("--file-proposals requires --apply", file=sys.stderr)
        return 2

    conn = _sbr._connect()
    total_24h = load_24h_owner_dispatch_total(conn)
    rows = load_rows(conn, umr_id=args.umr_id)

    # Phase 1: gather evidence + classify for every row. Deliberately OUTSIDE
    # any write lock -- these are reads plus real, potentially slow
    # subprocess calls (git fetch/merge-base, gh pr view), and holding a
    # systemwide flock across them would block every other real writer in
    # the codebase (resource_governor.py's dispatch tick, directive_engine.py,
    # etc.) for the duration.
    results = []
    for row in rows:
        evidence = gather_evidence(conn, row)
        bucket, reason = classify(evidence)
        results.append({"umr_id": row["umr_id"], "bucket": bucket, "reason": reason, "evidence": evidence})

    # Phase 2: the real DB write, if requested -- lock scoped to ONLY this
    # phase, held for the minimum time, never across a subprocess call.
    if args.apply:
        with _sbr._write_lock():
            for result in results:
                apply_classification(conn, result["evidence"], result["bucket"], result["reason"])
            conn.commit()
    conn.close()

    # Phase 3: file child-UMR proposals, if requested. Deliberately AFTER
    # closing our own connection and releasing our own write lock above --
    # insert-owner-proposal's `superboss-register.py` CLI subprocess
    # acquires that SAME lock file itself (cmd_insert_owner_proposal's own
    # `with _write_lock():`). Calling it while this process still held the
    # lock would deadlock (child blocks on a flock its own parent holds,
    # broken only by this script's subprocess timeout) -- real bug found by
    # independent review on this PR, fixed here before merge.
    proposals_filed = []
    if args.file_proposals:
        for result in results:
            if result["bucket"] in ("retryable", "blocked"):
                proposal_result = file_proposal(result["evidence"], result["bucket"], result["reason"])
                proposals_filed.append({
                    "umr_id": result["umr_id"], "bucket": result["bucket"], "proposal_result": proposal_result,
                })

    counts = Counter(r["bucket"] for r in results)
    print(f"Trailing-24h owner_dispatch_gateway total (all statuses, recomputed fresh): {total_24h}")
    print(f"failed+killed rows triaged: {len(results)}")
    for b in BUCKETS:
        print(f"  {b}: {counts.get(b, 0)}")
    bucket_sum = sum(counts.get(b, 0) for b in BUCKETS)
    print(f"bucket sum: {bucket_sum} (matches triaged total: {bucket_sum == len(results)})")
    if proposals_filed:
        print(f"child-UMR proposals filed: {len(proposals_filed)}")
        for p in proposals_filed:
            print(f"  {p['umr_id']} ({p['bucket']}): {p['proposal_result']}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"total_24h": total_24h, "results": results, "proposals_filed": proposals_filed}, f, indent=2, default=str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
