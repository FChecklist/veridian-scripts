#!/usr/bin/env python3
"""Conflict-free per-task progress files + a real completion gate.

REAL DEFECT (UMR-20260813-195922-f548, dispatched off PM-desktop-sentinel
tick 2026-08-13T19:45Z, governing chain UMR-20260806-171945-5767): every
worker rewrote ONE shared PROGRESS.md on its own branch --
worker-entrypoint.sh's own PROGRESS_INSTRUCTION literally told every worker
"maintain PROGRESS.md", no per-task namespacing at all. Two compounding
failures resulted, confirmed live against FChecklist/veridian-scripts open
PRs on 2026-08-13:

  (A) EMPTY FIXES SHIPPED AS REAL ONES. PR #317 ("swap gate vetoes on
      STATIC occupancy, dispatch_core.py") and PR #321 ("pm-sentinel-tick.sh
      positional systemctl show parse") each have exactly one file in their
      diff: PROGRESS.md. Neither dispatch_core.py nor pm-sentinel-tick.sh is
      touched. Both got recorded as real completed work.
  (B) UNIVERSAL MERGE CONFLICTS. Because every branch rewrites overlapping
      regions of the one PROGRESS.md, every long-lived branch that touches
      it conflicts with every OTHER branch that also touched it, regardless
      of whether their real code overlaps at all. Of the 25 parseable open,
      mergeStateStatus=DIRTY PRs on veridian-scripts at the time this was
      written, 17 were PROGRESS.md-only diffs stuck CONFLICTING for exactly
      this reason (gh pr list -R FChecklist/veridian-scripts --state open
      --json number,mergeStateStatus,files).

Correction against the dispatching SPEC's own claim: PR #297 is NOT
explained by this defect -- its diff never touches PROGRESS.md at all
(dispatch-owner-task.sh, pm-sentinel-tick.sh, superboss-register.py,
test_pm_sentinel_tick.py, tests/test_target_identifier_dedup.py); it is
CONFLICTING/DIRTY for a real, unrelated code-conflict reason. Only
#315/#317/#321 are genuinely PROGRESS.md-only.

This module provides the two pieces of real (mechanical, not
prompt-instruction) enforcement worker-entrypoint.sh now calls:

  1. `check-completion` -- if a task's own prompt.txt names a specific
     source/script file as its objective, that file must appear in the
     task's real git diff (committed + staged + unstaged). A diff that only
     touches progress/doc artifacts for a code-named objective is REJECTED
     with an explicit reason and a non-zero exit code -- never silently
     accepted as complete.
  2. `rollup` -- deterministically regenerates a single rolled-up view from
     every progress/<task_id>.md file, sorted by filename. This is
     GENERATED OUTPUT, never a hand-edit target -- no worker branch ever
     writes to it, so it can never be the shared-file merge conflict
     PROGRESS.md was.

Not a duplicate of tests/test_ocid_registry_completion_gate.py -- that gate
checks OCID registry row completeness fields, an unrelated registry-schema
concept, not diff-content-vs-stated-objective.

REAL FIX (2026-08-14, UMR-20260814-070059-6484, governing chain
UMR-20260806-171945-5767): check_completion() above used to hard-reject the
moment a named code file was absent from the TASK BRANCH's own diff. That
is wrong for legitimate cross-repo work -- concretely, task-20260814-060148
(repo claude-control) deliberately built its real code fix + 8 passing
tests in an isolated clone of a DIFFERENT repo (veridian-scripts), opened a
real PR there (veridian-scripts#356), and closed two superseded PRs. Its
task branch diff was 6 markdown/txt files only, so this gate rejected it
even though the work genuinely succeeded (systemd Result=success,
ExecMainStatus=0) -- worker-exit-status-bridge.py then wrote
umr_tasks.status=failed for a task that actually succeeded.

find_cross_repo_pr_evidence() below closes that hole WITHOUT weakening the
original catch (a task that touched no code in ANY repo must still be
rejected): a named file missing from the task branch diff is now also
checked against a real, `gh`-confirmed PR in another repo, but only when
ALL of the following are true, mechanically, not by trusting a self-
declared claim in result.json's prose:
  (a) a real PR URL is present in this task's own result.json,
  (b) that PR's owner/repo is in this org's own real, known repo set
      (resource_governor.py's GH_ORG/ALL_KNOWN_REPOS) -- never shells out
      to `gh pr view` for an arbitrary owner/repo pulled from free text,
  (c) `gh pr view` confirms that PR really exists and returns its real
      body/headRefName/files/createdAt,
  (d) this exact task_id string is really present in that PR's own
      headRefName (branch name -- set at PR-creation time by the worker
      that opened it, not editable after the fact the way body text is)
      AND that PR's real createdAt is not earlier than this task's own
      dispatch time (decoded from task_id's own embedded
      task-YYYYMMDD-HHMMSS timestamp) -- together these prove the PR was
      genuinely opened BY this task, not an unrelated pre-existing PR
      whose body was edited after the fact to retroactively cite this
      task_id, and
  (e) that PR's own real `files` list (from `gh`, not from result.json)
      contains at least one file that actually matches one of the
      objective's own `named` file(s) -- the same basename/path match
      check_completion() already requires of the task branch's own diff,
      not merely "some code file, any code file" (a PR with only
      unrelated code changes plus a task_id-correlated branch name is not
      evidence that THIS objective was really done).
A PR reference that fails any of (b)/(c)/(d)/(e) is not evidence and falls
through to the original reject path.

REAL FIX (2026-08-14, UMR-20260814-071422, AUDIT: FAIL on PR #360):
independent Tier-1 audit found the FIRST version of this cross-repo path
accepted ANY real, non-progress code file in a gh-confirmed,
task_id-correlated (body-OR-branch, live-editable) PR from ANY owner/repo
-- demonstrated by that version's own accepted-path test, which took a PR
whose real files were resource_governor.py + an unrelated test file
against a prompt naming a completely different file, and still accepted
it. (b)/(d)/(e) above are the real corrective fix: owner/repo allowlisted
against this org's own known set, task_id correlation tightened to the
non-retroactively-editable branch name plus a real creation-time check,
and the named-file match now mirrors the task-branch diff's own real
matching rule instead of accepting any unrelated code file.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resource_governor import ALL_KNOWN_REPOS, GH_ORG  # noqa: E402

# Extensions that count as "a specific source file or script" for the
# completion gate. Deliberately excludes .md/.txt/.json/.yaml doc/config
# extensions -- the gate exists to catch "objective named a CODE file, diff
# has no code", not to force every task to touch a file of some kind.
CODE_EXTENSIONS = (
    "py", "sh", "js", "jsx", "ts", "tsx", "go", "rb",
    "java", "c", "h", "cpp", "hpp", "rs", "sql",
)

FILENAME_RE = re.compile(
    r"[A-Za-z0-9_\-./]+\.(?:" + "|".join(CODE_EXTENSIONS) + r")\b"
)

# Progress/doc artifacts the gate must never itself treat as "the named
# objective file", even when they appear in prose right next to a real code
# filename (e.g. "update PROGRESS.md after fixing dispatch_core.py") -- these
# are exactly the artifacts this whole fix exists to stop conflating with
# real code.
_PROGRESS_ARTIFACT_RES = (
    re.compile(r"^PROGRESS\.md$", re.IGNORECASE),
    re.compile(r"^progress/.*\.md$", re.IGNORECASE),
    re.compile(r"^RCA.*\.md$", re.IGNORECASE),
)

# Real fix (RCA of UMR-20260813-060311-6eea, UMR-20260814-013850-fd7f): same
# false-positive class already fixed same-day in superboss-register.py's
# extract_target_identifiers() (commit 29947ca,
# _TARGET_ID_SCRIPT_NAME_BOILERPLATE_EXCLUDED) -- resource_governor.py and
# superboss-register.py are cited as the real, standing query/mutation front
# doors in essentially every dispatch prompt pm-sentinel-tick.sh's own RCA
# template generates (e.g. Check 2a: "query resource_governor.py --query-umr
# ... yourself first ... record ... via superboss-register.py mark-umr-
# terminal"). Every such RCA task's own prompt.txt therefore names these two
# files as CLI tools to *run*, not as objective files inside the task's own
# --workspace repo -- and for a genuinely cross-repo RCA (the real fix
# belongs in veridian-scripts, not the task's own compliance-tracker
# workspace) or a genuinely no-code-needed disposition ("already correctly
# resolved, re-confirming is redundant"), this gate would reject an honest,
# real completion as if it fabricated a doc-only diff. A bare mention of
# either name is never a real, distinguishing objective the way a nested
# path (e.g. "scripts/resource_governor.py") or another script's own name
# is -- a task that genuinely targets either file's own code inside this
# workspace is still caught by FILENAME_RE's path-prefixed form, so this is
# a narrowing of one specific false-positive-prone signal, not a removal of
# real coverage.
_BOILERPLATE_TOOL_NAME_EXCLUDED = {
    "resource_governor.py", "superboss-register.py",
    # 2026-08-14 (UMR-20260814-051532-2ae4): same false-positive class as
    # the two above, caught live by this exact recurring task type
    # ("reconcile live deploy drift") -- pm-sentinel-tick.sh's own RCA/
    # dispatch template for this task cites check_live_scripts_drift.py by
    # name as "the tool to run first" and sync-repos.sh by name as "the
    # script whose dirty/non-main-skip behavior to read" in essentially
    # every dispatch of this task, the same standing-CLI-tool-citation
    # pattern as resource_governor.py/superboss-register.py above -- not a
    # distinguishing objective inside the task's own --workspace repo. A
    # task that genuinely targets either file's own code is still caught by
    # FILENAME_RE's path-prefixed form (e.g. "scripts/sync-repos.sh").
    "check_live_scripts_drift.py", "sync-repos.sh",
}


def is_progress_artifact(path):
    base = path.rsplit("/", 1)[-1]
    return any(p.match(path) or p.match(base) for p in _PROGRESS_ARTIFACT_RES)


# 2026-08-14 (UMR-20260814-051532-2ae4): a THIRD instance of the same
# false-positive class as _BOILERPLATE_TOOL_NAME_EXCLUDED above, this time
# for check_live_scripts_drift.py's own real output format. Its "N real
# tracked file(s) differ: a.py, b.py, ..." line (quoted verbatim into
# essentially every "reconcile live deploy drift" dispatch prompt) lists
# filenames that DIFFER BETWEEN TWO GIT REFS -- i.e. real evidence of what
# the drift check found, not an instruction to edit those files inside
# *this* task's own --workspace repo. This task's own real completion
# shape proved it: the fix for two of those cited files (resource_governor.py
# + a companion test file) had already landed via a separate, already-
# merged PR; the real reconciliation action was merging that PR and
# fast-forwarding the live checkout, never editing those files inside this
# workspace. A filename cited ONLY inside such an evidence list, and
# nowhere else in the prompt, is not a real, distinguishing objective the
# way a path-prefixed mention outside the list still is.
_EVIDENCE_LIST_RE = re.compile(
    r"\d+\s+real\s+tracked\s+file\(s\)\s+differ:\s*(?P<list>[^\n]*?)"
    r"(?:\.(?:\s|$)|\n|$)",
    re.IGNORECASE,
)


def _evidence_list_spans(text):
    """Character spans of filename lists following a real
    check_live_scripts_drift.py-style "N real tracked file(s) differ:"
    citation -- see _EVIDENCE_LIST_RE's own comment."""
    return [m.span("list") for m in _EVIDENCE_LIST_RE.finditer(text or "")]


# 2026-08-14 (UMR-20260814-080423-bd93, real dispatch off PM-desktop-sentinel
# tick 2026-08-14T07:45-07:55Z): a FOURTH instance of the same false-positive
# class as _BOILERPLATE_TOOL_NAME_EXCLUDED and _EVIDENCE_LIST_RE above, this
# time for pm-sentinel-tick.sh's own Check 2a killed-row RCA template, which
# quotes the TARGET row's own live `reason` field verbatim into the dispatch
# prompt (`real recorded reason: "${REASON}"`). That reason field is real,
# historical evidence about what caused the ORIGINAL incident under
# investigation -- it frequently cites the script/unit responsible for that
# past event (e.g. a `.sh` monitor script named inside a killed task's own
# decline reason), never an instruction to edit that file inside *this* RCA
# task's own workspace. Confirmed live against real evidence gathered by PM
# sentinel (2026-08-14T07:45-07:55Z, resource_governor.py --query-umr):
# UMR-20260814-071851-4d86 (the RCA worker dispatched for
# UMR-20260807-003517-23bb) did real, correct work -- independently verified
# the original decline, wrote a real RCA doc, and called
# `superboss-register.py mark-umr-terminal` to record a real, honest
# terminal outcome for the target row exactly as instructed -- touching zero
# application code, a genuinely correct disposition. Because the target
# row's own quoted `reason` text happened to cite
# `directive-engine-stop-audit-monitor.sh`, extract_named_code_files() (pre-
# fix) treated that citation as this RCA task's own objective file, absent
# from its doc-only diff, and rejected it. worker-entrypoint.sh's
# COMPLETION-GATE-BLOCK then checkpointed task.yaml status='blocked' (a
# real self-reported negative outcome, per its own vocabulary) even though
# the worker process went on to exit 0 -- and worker-exit-status-bridge.py
# then (correctly, per its own narrower, already-audited scope: it only
# ever bridges an ALREADY self-reported negative task.yaml status, never
# invents one from a bare exit code) bridged that 'blocked' checkpoint into
# umr_tasks.status='failed'. The exit-status bridge is not the defect; this
# gate's over-broad filename extraction, applied to a quoted historical
# citation rather than the task's own real objective, is.
_REASON_CITATION_RE = re.compile(
    r"reason:\s*\"(?P<list>.*?)\"", re.IGNORECASE | re.DOTALL
)


def _reason_citation_spans(text):
    """Character spans of quoted text following a real `reason:` (or
    `recorded reason:` / `original reason:`) citation -- see
    _REASON_CITATION_RE's own comment. Same 'excluded only when the
    filename appears NOWHERE else in the text' rule _evidence_list_spans
    above already established -- a filename that is ALSO named as a real,
    distinguishing objective elsewhere in the prompt is still kept."""
    return [m.span("list") for m in _REASON_CITATION_RE.finditer(text or "")]


def extract_named_code_files(text):
    """Real source/script filenames referenced in a task's own spec text
    (prompt.txt). Order-preserving de-dup; progress/doc artifacts excluded
    even though their extension (.md) is not in CODE_EXTENSIONS anyway --
    kept explicit so the exclusion is provable, not incidental.

    Bare (no path prefix) mentions of _BOILERPLATE_TOOL_NAME_EXCLUDED are
    also excluded -- see that set's own comment. A path-prefixed mention
    (e.g. "scripts/resource_governor.py") is NOT excluded: that is a real,
    distinguishing reference to the file's own code, not a generic
    boilerplate tool citation.

    A filename that appears ONLY inside a check_live_scripts_drift.py-style
    "N real tracked file(s) differ: ..." evidence list, and nowhere else in
    the text, is also excluded -- see _EVIDENCE_LIST_RE's own comment. If
    the same filename is also named elsewhere (outside any evidence list),
    it is still a real objective and is kept.

    A filename that appears ONLY inside a quoted `reason:` citation (a
    target row's own historical `reason` field, quoted verbatim into an RCA
    dispatch prompt) is excluded the same way -- see
    _REASON_CITATION_RE's own comment."""
    text = text or ""
    evidence_spans = _evidence_list_spans(text) + _reason_citation_spans(text)
    all_matches = list(FILENAME_RE.finditer(text))
    outside_evidence = {
        m.group(0) for m in all_matches
        if not any(lo <= m.start() and m.end() <= hi for lo, hi in evidence_spans)
    }

    seen = []
    for m in all_matches:
        candidate = m.group(0)
        if is_progress_artifact(candidate):
            continue
        if candidate in _BOILERPLATE_TOOL_NAME_EXCLUDED:
            continue
        in_evidence_only = (
            any(lo <= m.start() and m.end() <= hi for lo, hi in evidence_spans)
            and candidate not in outside_evidence
        )
        if in_evidence_only:
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


def _git(workspace, *args):
    return subprocess.run(
        ["git", "-C", workspace, *args], capture_output=True, text=True
    )


def git_diff_files(workspace, default_branch):
    """Real changed-file set for this branch: committed-since-merge-base,
    PLUS staged, PLUS unstaged. This gate can run before the worker's own
    final `git add -A && git commit` (see worker-entrypoint.sh's
    COMPLETION-GATE-BLOCK, which runs before that commit), so committed-only
    would miss real in-progress work; and it must also see fully-committed
    branches if invoked standalone (e.g. from a test or a resume), so
    committed history is not skipped either."""
    out = set()
    mb = _git(workspace, "merge-base", f"origin/{default_branch}", "HEAD")
    merge_base = mb.stdout.strip() if mb.returncode == 0 else f"origin/{default_branch}"
    for args in (
        ("diff", "--name-only", merge_base, "HEAD"),
        ("diff", "--name-only", "HEAD"),
        ("diff", "--name-only", "--cached"),
    ):
        res = _git(workspace, *args)
        if res.returncode == 0:
            out.update(f for f in res.stdout.splitlines() if f)
    return out


# Real GitHub PR URL, e.g. https://github.com/FChecklist/veridian-scripts/pull/356.
# Deliberately matches only the full URL form (not a bare "owner/repo#123"
# shorthand) -- result.json's own "result" text (both this worker's example
# and gh's own markdown link rendering) always carries the full URL, and
# requiring it avoids matching an unrelated bare "#356" mentioned in prose.
_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[A-Za-z0-9_.\-]+)/(?P<repo>[A-Za-z0-9_.\-]+)/pull/(?P<num>\d+)"
)

# Real `gh pr view` subprocess timeout, same shape/knob as
# resource_governor.py's own GH_PR_CHECK_TIMEOUT_SECONDS.
GH_PR_CHECK_TIMEOUT_SECONDS = int(
    os.environ.get("COMPLETION_GATE_GH_TIMEOUT_SECONDS", "10")
)


def _read_result_text(task_dir):
    """Text to scan for real PR references: the worker's own result.json,
    preferring its human-readable "result" field (where the worker's own
    final summary, and any PR URLs it cites, live) and falling back to the
    raw file text if it is not JSON or has no "result" field, so a PR URL
    anywhere in the record is still found."""
    path = os.path.join(task_dir, "result.json")
    try:
        with open(path) as f:
            raw = f.read()
    except (FileNotFoundError, OSError):
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    text = data.get("result") if isinstance(data, dict) else None
    return text if isinstance(text, str) and text else raw


def extract_pr_references(text):
    """Order-preserving de-duped (owner, repo, number) tuples for every real
    GitHub PR URL mentioned in text. This alone is NOT evidence of
    completion -- a bare mention is a self-declared claim; see
    find_cross_repo_pr_evidence() for the real `gh`-confirmed check."""
    seen = []
    for m in _PR_URL_RE.finditer(text or ""):
        ref = (m.group("owner"), m.group("repo"), int(m.group("num")))
        if ref not in seen:
            seen.append(ref)
    return seen


def _gh_pr_view(owner, repo, number):
    """Real `gh pr view` call -- returns the parsed JSON dict on success, or
    None if `gh` is unavailable, the PR doesn't really exist, or the call
    times out. Kept as its own top-level function (not inlined) so tests
    can monkeypatch it and never make a real network call -- see
    tests/test_progress_completion_gate.py's TestCrossRepoPrEvidence."""
    try:
        res = subprocess.run(
            [
                "gh", "pr", "view", str(number),
                "--repo", f"{owner}/{repo}",
                "--json", "number,state,body,headRefName,files,url,createdAt",
            ],
            capture_output=True, text=True,
            timeout=GH_PR_CHECK_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def _matched_named_files(named, files):
    """The subset of `named` (objective files extracted from prompt.txt)
    that is really present in `files` (a real file-path list -- either the
    task branch's own git diff, or a cross-repo PR's real `files` list),
    matched by exact path or by basename -- the one real matching rule this
    module uses everywhere, so a task branch diff and a cross-repo PR's
    diff are held to the identical standard."""
    basenames = {f.rsplit("/", 1)[-1] for f in files}
    return [n for n in named if n in files or n.rsplit("/", 1)[-1] in basenames]


# task-YYYYMMDD-HHMMSS-<slug> -- the one real task_id shape this repo's
# dispatch tooling uses everywhere (same convention as
# generate_pm_report_v3.py's own TASK_IDENTITY_RE).
_TASK_ID_TS_RE = re.compile(r"^task-(\d{8})-(\d{6})-")


def _task_dispatch_timestamp(task_id):
    """The real UTC moment this task_id's own embedded timestamp encodes
    (when the dispatcher created this task), or None if task_id doesn't
    match the standard shape. Real anti-replay signal: a PR genuinely
    opened FOR this task cannot have been created before the task itself
    was dispatched -- an already-existing, unrelated PR whose body/branch
    happens to contain this task_id string necessarily predates it."""
    m = _TASK_ID_TS_RE.match(task_id)
    if not m:
        return None
    try:
        return datetime.strptime(
            m.group(1) + m.group(2), "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_gh_timestamp(value):
    """Parse `gh`'s own ISO-8601 'Z'-suffixed timestamp shape (e.g.
    '2026-08-14T06:10:43Z') into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def find_cross_repo_pr_evidence(task_id, task_dir, named):
    """Real, mechanical evidence that this task's genuine code fix landed as
    a PR in a DIFFERENT repository than the task branch's own repo --
    concretely the task-20260814-060148 shape: real code + 8 passing tests
    landed as veridian-scripts#356 while the task branch itself (repo
    claude-control) never touched the named file.

    Never trusts a bare mention / self-declared claim. A PR URL found in
    this task's own result.json is only real evidence once ALL of these are
    true, each checked against `gh`'s own live answer, not our own prose --
    see this module's own top docstring for why each check exists:
      (a) owner/repo is really in this org's own known repo set
          (GH_ORG/ALL_KNOWN_REPOS) -- never queries an arbitrary owner/repo,
      (b) `gh pr view` confirms the PR really exists,
      (c) this exact task_id string is really present in that PR's own
          headRefName (branch name, set at PR-creation time -- not the
          body, which is editable after creation by anyone with write
          access and so can be made to cite any task_id retroactively),
      (d) that PR's real createdAt is not earlier than this task's own
          real dispatch time (decoded from task_id itself) -- a PR that
          predates this task cannot genuinely be its output, however its
          branch name reads, and
      (e) that PR's own real `files` list contains at least one file that
          actually matches one of the objective's own `named` file(s) --
          not merely any unrelated code file.

    Returns (True, description) on real evidence, else (False, None).
    """
    task_ts = _task_dispatch_timestamp(task_id)
    for owner, repo, number in extract_pr_references(_read_result_text(task_dir)):
        if owner != GH_ORG or repo not in ALL_KNOWN_REPOS:
            continue
        pr = _gh_pr_view(owner, repo, number)
        if not pr:
            continue
        head_ref = pr.get("headRefName") or ""
        if task_id not in head_ref:
            continue
        if task_ts is not None:
            pr_created = _parse_gh_timestamp(pr.get("createdAt"))
            if pr_created is None or pr_created < task_ts:
                continue
        pr_files = [
            f.get("path", "") for f in (pr.get("files") or []) if isinstance(f, dict)
        ]
        matched = _matched_named_files(named, pr_files)
        if matched:
            return True, (
                f"real code change landed cross-repo as {owner}/{repo}#{number} "
                f"({pr.get('url', '')}), confirmed by gh: task_id present in its "
                f"branch name, created at {pr.get('createdAt')} (at/after this "
                f"task's own dispatch), objective-named file(s) present in its "
                f"real diff: {matched}"
            )
    return False, None


def check_completion(task_dir, workspace, default_branch):
    """Returns (ok, reason).

    ok=True whenever there is nothing real to gate on (objective names no
    code file), at least one named file is really present in the task
    branch diff, OR find_cross_repo_pr_evidence() finds a real, gh-
    confirmed PR in another repo carrying the real code.

    ok=False -- a REAL rejection, never downgraded to a success status --
    only when the objective names >=1 code file, the diff is non-empty,
    NONE of the named files are in it, AND no real cross-repo PR evidence
    exists either (i.e. no code changed anywhere, in this repo or another).
    """
    prompt_path = os.path.join(task_dir, "prompt.txt")
    try:
        with open(prompt_path) as f:
            spec_text = f.read()
    except FileNotFoundError:
        return True, "no prompt.txt found -- nothing to gate on"

    named = extract_named_code_files(spec_text)
    if not named:
        return True, "objective names no specific source/script file -- gate does not apply"

    diff_files = git_diff_files(workspace, default_branch)
    if not diff_files:
        return True, "empty diff -- handled by the separate no-op path, not this gate"

    matched = _matched_named_files(named, diff_files)
    if matched:
        return True, f"objective-named file(s) present in diff: {matched}"

    task_id = os.path.basename(os.path.normpath(task_dir))
    cross_repo_ok, cross_repo_desc = find_cross_repo_pr_evidence(task_id, task_dir, named)
    if cross_repo_ok:
        return True, (
            f"objective-named file(s) {named} not in the task branch diff, "
            f"but {cross_repo_desc} -- accepted as real cross-repo "
            f"completion evidence"
        )

    non_progress = sorted(f for f in diff_files if not is_progress_artifact(f))
    reason = (
        f"objective named {named} but the diff touches no code in this repo "
        f"and no real cross-repo PR evidence was found either -- "
        f"diff only contains: {sorted(diff_files)}"
    )
    if non_progress:
        reason += f" (non-progress files present but none of them match: {non_progress})"
    return False, reason


def cmd_check_completion(args):
    ok, reason = check_completion(args.task_dir, args.workspace, args.default_branch)
    print(reason)
    return 0 if ok else 1


def cmd_rollup(args):
    """Deterministic, generated-only rollup -- never a merge target. Reads
    every progress/*.md file in the workspace, in filename-sorted order,
    and concatenates them under an explicit generated-file banner. Safe to
    run from any number of concurrent branches: it only READS progress/*.md
    (each worker's own file) and WRITES a single output path that no worker
    branch itself commits to as part of its own progress protocol."""
    progress_dir = os.path.join(args.workspace, "progress")
    lines = [
        "<!-- GENERATED by progress_completion_gate.py rollup -- do not hand-edit. -->",
        "<!-- Source of truth is progress/<task_id>.md, one file per task. -->",
        "",
        "# Progress rollup",
        "",
    ]
    if os.path.isdir(progress_dir):
        for name in sorted(os.listdir(progress_dir)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(progress_dir, name)) as f:
                body = f.read().rstrip()
            lines.append(f"## {name}")
            lines.append("")
            lines.append(body)
            lines.append("")
    output = "\n".join(lines) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        sys.stdout.write(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check-completion",
        help="reject a doc/progress-only diff for a code-named objective",
    )
    p_check.add_argument("--task-dir", required=True)
    p_check.add_argument("--workspace", required=True)
    p_check.add_argument("--default-branch", required=True)
    p_check.set_defaults(func=cmd_check_completion)

    p_roll = sub.add_parser(
        "rollup", help="regenerate the deterministic progress rollup view"
    )
    p_roll.add_argument("--workspace", required=True)
    p_roll.add_argument("--output", help="write here instead of stdout")
    p_roll.set_defaults(func=cmd_rollup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
