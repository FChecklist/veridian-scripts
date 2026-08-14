#!/usr/bin/env python3
"""
agent_work_briefing.py -- the real deterministic pre-work briefing +
post-work write-back layer this task's own SPEC requires: a direct
correction and extension to UMR-20260806-121332-6ba4 (ai_agent_registry.py,
"1 UMR = 1 agent_id"), which itself corrected UMR-20260806-121252-3207.

This module invents ZERO new storage and reimplements ZERO existing lookup
or write logic. It is a thin composition layer over three real,
already-existing pieces, called exactly as their own existing callers
already call them:
  - wiring_registry (8k+ real rows across 14 real entity types) --
    superboss-register.py's own lookup_entity()/register_entity_row().
  - capability_registry (13 real rows) -- superboss-register.py's own
    lookup_capability(), the same call ai_agent_registry.py's own
    check-before-dispatch already makes.
  - ai_agent_registry (this exact UMR's own scoped memory row) --
    ai_agent_registry.py's own cmd_lookup_agent/cmd_record_work.

Two real commands:

  assemble-briefing: called BEFORE any AI agent starts real work under a
    real UMR. Runs all three lookups against the UMR's own real scope terms
    (file paths / keywords describing what this UMR touches) and prints ONE
    deterministic, boolean-checkable JSON briefing: which tables have
    matches, which real file paths are relevant, what real prior history
    already exists for this exact UMR -- close-ended fact (counts, ids,
    paths, booleans), never vague guidance.

  record-completion: called AFTER real work completes. Writes the AI
    agent's real output back through this one canonical script into every
    place the SPEC requires:
      1. ai_agent_registry.py's own record-work -- this exact UMR's own
         agent_id memory row, so its own history grows.
      2. umr_tasks, the real standard system of record -- via
         superboss-register.py's own mark-umr-terminal (never a raw SQL
         UPDATE). UMR-20260814-181115: a status=completed/completed_unmerged
         claim is never written on self-report alone -- see
         verify_real_completion_evidence() below, which independently
         fetches a cited PR's real state/files from GitHub (or requires a
         real files_touched list when no PR is cited) and downgrades an
         unverified claim to status=unverified_self_report instead of
         writing a false 'completed'.
      3. gtm_certification_categories, "where relevant" -- via
         gtm_write_category_result.py, the one canonical writer every real
         gtm_check_*.py script already calls (same subprocess convention,
         see gtm_check_accessibility_testing.py) -- only invoked when a real
         --gtm-category-index is actually given, never unconditionally.
      4. a new wiring_registry row -- via superboss-register.py's own
         register_entity_row() -- ONLY if a real search-first check
         (lookup by this exact entity_id) finds nothing, i.e. ONLY if the
         work genuinely created a new function/engine/script that did not
         already have a row. Never a duplicate row for something that
         already has one.

wiring_registry's relationships field is deliberately checked with a direct
LIKE scan, not the FTS index: wiring_registry_fts (see
_ensure_wiring_registry_table in superboss-register.py) only indexes
path/entity_type/source_ref, not relationships -- this module extends that
existing coverage to the relationships field the SPEC explicitly names,
rather than re-deriving lookup_entity()'s own path/keyword resolution.
"""
import argparse
import contextlib
import importlib.util as _ilu
import io
import json
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
GTM_WRITER = os.path.join(SCRIPTS, "gtm_write_category_result.py")

# UMR-20260814-181115: real independent PR-state check for record-completion
# (see verify_real_completion_evidence() below) reuses resource_governor.py's
# own real `gh` subprocess plumbing (_run/GH_ORG/GH_PR_CHECK_TIMEOUT_SECONDS)
# rather than re-deriving it -- same sys.path + plain-import convention
# progress_completion_gate.py already established for pulling real, tested
# primitives out of resource_governor.py (no hyphen in its filename, so a
# plain `import` works, unlike superboss-register.py/ai_agent_registry.py
# above which need the importlib.util loader for their hyphenated names).
sys.path.insert(0, SCRIPTS)
import resource_governor  # noqa: E402

_sbr = None
_air = None


def _superboss_register():
    """In-process importlib load, same convention ai_agent_registry.py's own
    _superboss_register() already uses (the filename has a hyphen, so it
    cannot be a plain `import`)."""
    global _sbr
    if _sbr is None:
        _spec = _ilu.spec_from_file_location(
            "superboss_register_agent_briefing", os.path.join(SCRIPTS, "superboss-register.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _ai_agent_registry():
    """In-process importlib load of ai_agent_registry.py -- the exact module
    this task corrects/extends, never re-derived here."""
    global _air
    if _air is None:
        _spec = _ilu.spec_from_file_location(
            "ai_agent_registry_briefing", os.path.join(SCRIPTS, "ai_agent_registry.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _air = _mod
    return _air


def _capture_json(fn, args_ns):
    """Shared helper: call an existing argparse-shaped cmd_* function that
    prints its JSON result to stdout (never returns it), capture that
    stdout, parse it back into a dict. Same reuse pattern
    ai_agent_registry.py's own cmd_check_before_dispatch already established
    for calling superboss-register.py's lookup_capability -- used here for
    every real lookup this module composes, so none of their own real query
    logic is ever re-derived."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fn(args_ns)
        except SystemExit:
            pass
    try:
        return json.loads(buf.getvalue())
    except json.JSONDecodeError:
        return {"error": f"{fn.__name__} produced unparseable output", "raw": buf.getvalue()}


def _wiring_scope_matches(sbr, conn, scope_terms):
    """Real matches across wiring_registry for every scope term, covering
    BOTH real fields the SPEC names: path (+ entity_type/source_ref, via the
    existing lookup_entity()) and relationships (via a direct LIKE scan,
    see module docstring). Deduped by entity_id; returns (matches_dict,
    resolution_stage_by_term)."""
    matches = {}
    stage_by_term = {}
    for term in scope_terms:
        parsed = _capture_json(sbr.lookup_entity, argparse.Namespace(entity_id=None, query=term))
        for m in parsed.get("matches", []):
            matches[m["entity_id"]] = m
        stage = parsed.get("resolution_stage_used", "none")

        rel_rows = conn.execute(
            "SELECT * FROM wiring_registry WHERE relationships LIKE ? ORDER BY entity_id LIMIT ?",
            (f"%{term}%", sbr.WIRING_LOOKUP_MATCH_LIMIT),
        ).fetchall()
        if rel_rows:
            stage = "relationships_match" if stage == "none" else stage
        for r in rel_rows:
            d = sbr._wiring_row_to_dict(r)
            matches[d["entity_id"]] = d

        stage_by_term[term] = stage
    return matches, stage_by_term


def assemble_briefing(umr_id, scope_terms, intent_text):
    """The real deterministic, boolean-checkable pre-work briefing. Pure
    read-only composition of three existing lookups -- never a write."""
    sbr = _superboss_register()
    air = _ai_agent_registry()

    sbr.init_db_silent()
    conn = sbr._connect()
    sbr._ensure_wiring_registry_table(conn)

    wiring_matches, stage_by_term = _wiring_scope_matches(sbr, conn, scope_terms)
    conn.close()

    capability_result = _capture_json(
        sbr.lookup_capability,
        argparse.Namespace(capability_name=None, intent_text=intent_text, domain=None),
    )
    agent_result = _capture_json(
        air.cmd_lookup_agent,
        argparse.Namespace(umr_id=umr_id, include_memory=True),
    )

    wiring_list = sorted(wiring_matches.values(), key=lambda d: d["entity_id"])
    relevant_paths = sorted({d["path"] for d in wiring_list if d.get("path")})
    by_entity_type = {}
    for d in wiring_list:
        by_entity_type[d["entity_type"]] = by_entity_type.get(d["entity_type"], 0) + 1

    capability_found = bool(capability_result.get("found"))
    agent_found = bool(agent_result.get("found"))

    facts = []
    if wiring_list:
        facts.append(
            f"wiring_registry: {len(wiring_list)} existing row(s) already match this UMR's scope "
            f"terms {scope_terms!r} -- check these entity_ids/paths before writing any new code: "
            f"{[d['entity_id'] for d in wiring_list]!r}"
        )
    else:
        facts.append(
            f"wiring_registry: 0 existing rows match scope terms {scope_terms!r} -- no known-relevant "
            "entity found; a genuinely new one may be justified if real work creates it"
        )
    if capability_found:
        cap_matches = capability_result.get("matches", [])
        names = sorted({m["capability_name"] for m in cap_matches})
        # lookup_capability()'s own keyword stage is a plain OR-of-terms FTS
        # match (see its own docstring in superboss-register.py) -- against a
        # multi-word intent_text this can honestly return many rows that only
        # share incidental vocabulary, not a real single-purpose fit. Past a
        # handful of hits this module reports that plainly (a real, bounded
        # fact -- "N rows, broad overlap") rather than implying every one of
        # them is a confirmed duplicate to reuse, which is itself a form of
        # vague guidance the SPEC's own "never vague guidance" rules out.
        if len(names) > 5:
            facts.append(
                f"capability_registry: {len(cap_matches)} rows share keyword overlap with this intent_text "
                f"(top 5 of {len(names)}: {names[:5]!r}) -- broad OR-keyword match, not a confirmed "
                "single-purpose fit; run `lookup-capability --capability-name <exact name>` or narrow "
                "intent_text before assuming any one of these already does this"
            )
        else:
            facts.append(
                f"capability_registry: {len(cap_matches)} existing script(s) already do this ({names!r}) "
                "-- reuse directly, do not rebuild"
            )
    else:
        facts.append("capability_registry: no existing script found for this intent_text")
    if agent_found:
        facts.append(
            f"ai_agent_registry: this exact UMR ({umr_id}) already has {agent_result.get('total_tasks_handled', 0)} "
            f"prior recorded work entr{'y' if agent_result.get('total_tasks_handled') == 1 else 'ies'} "
            f"under agent_id={agent_result.get('agent_id')!r} -- read its memory_content before starting"
        )
    else:
        facts.append(f"ai_agent_registry: no prior history exists yet for this exact UMR ({umr_id}) -- this is its first")

    return {
        "umr_id": umr_id,
        "briefing_generated_at": sbr._now_iso(),
        "tables_checked": ["wiring_registry", "capability_registry", "ai_agent_registry"],
        "wiring_registry": {
            "has_matches": bool(wiring_list),
            "match_count": len(wiring_list),
            "matches": wiring_list,
            "relevant_file_paths": relevant_paths,
            "by_entity_type": by_entity_type,
            "resolution_stage_by_term": stage_by_term,
        },
        "capability_registry": {
            "has_match": capability_found,
            "match": capability_result,
        },
        "ai_agent_registry": {
            "has_prior_history": agent_found,
            "agent": agent_result if agent_found else None,
        },
        "close_ended_facts": facts,
    }


def _fetch_real_pr_state(pr_number, repo):
    """Real `gh pr view --json state,mergedAt,files` call -- the one real,
    independent ground-truth fetch verify_real_completion_evidence() below
    uses to check a worker's self-reported --umr-pr-number against GitHub
    itself, instead of trusting the self-report at face value. Same
    subprocess convention resource_governor.py's own
    _real_pr_state_for_backfill()/_pr_changed_files_are_docs_only() already
    use (gh pr view, GH_PR_CHECK_TIMEOUT_SECONDS, GH_ORG) -- reused directly
    via `import resource_governor` above rather than re-derived here; not
    those two functions themselves, because their existing fail-open/
    fail-closed direction is tuned for their own callers (a duplicate-PR
    guard that must fail toward "still blocking" on an unverifiable PR),
    the opposite of what this module needs (an unverifiable PR must fail
    toward "not accepted as real evidence").

    Returns {"ok": False, "error": ...} (never raises) on any real
    timeout/non-zero-exit/unparseable-output; a real {"ok": True, "state":
    ..., "merged_at": ..., "files": [...]} otherwise."""
    try:
        r = resource_governor._run(
            ["gh", "pr", "view", str(pr_number), "--repo",
             f"{resource_governor.GH_ORG}/{repo}", "--json", "state,mergedAt,files"],
            timeout=resource_governor.GH_PR_CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"gh pr view #{pr_number} timed out "
                                       f"(>{resource_governor.GH_PR_CHECK_TIMEOUT_SECONDS}s)"}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or f"gh pr view #{pr_number} failed, "
                                                     f"exit {r.returncode}").strip()}
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": f"unparseable `gh pr view #{pr_number}` output"}
    files = [f.get("path") for f in (data.get("files") or []) if f.get("path")]
    return {"ok": True, "state": data.get("state"), "merged_at": data.get("mergedAt"), "files": files}


def verify_real_completion_evidence(status, pr_number, repo, files_touched):
    """UMR-20260814-181115 real independent check, the actual fix this
    task's SPEC asks for: a completion claim of status=completed/
    completed_unmerged (a real "resolved/merged" claim) must never be
    accepted purely on a worker's own self-report. Real, repeated finding
    this session: several register rows recorded status=completed even
    though the cited PR's real diff was docs-only (progress-notes only, no
    real code), or cited no PR and no real file-touched list at all -- i.e.
    zero independently-checkable evidence backing a "real work landed"
    claim.

    Real rule, checked in this order:
      1. a cited pr_number is independently fetched from GitHub itself
         (never trusted at face value, via _fetch_real_pr_state above) and
         must have at least one real changed file outside documentation
         (*.md) in its real diff;
      2. with no PR cited, a real non-empty files_touched list (the
         worker's own self-reported list of real files it touched --
         record_completion() below folds --umr-file-path into this same
         list, so that existing evidence path counts too) is required
         instead, filtered the same non-docs-only (*.md) way as the PR path
         above -- a self-report citing only a progress-notes file is the
         exact same loophole as a docs-only PR and is refused the same way;
      3. neither present -> refused. Never inferred/guessed from entry_text
         prose -- only real, structured evidence counts.

    Returns {"verified": bool, "checked": bool, "reason": str, ...}. Never
    raises. status values other than completed/completed_unmerged (failed,
    killed) always pass through unchecked -- this gate only ever concerns
    the "real work landed" claim, same scoping
    validate_umr_terminal_completion_evidence() in superboss-register.py
    already uses for its own (narrower: existence-only, not docs-only-
    aware) evidence gate. A `gh` error/timeout/malformed response is
    treated as NOT verified -- an unverifiable claim must never silently
    count as real evidence."""
    if status not in ("completed", "completed_unmerged"):
        return {"verified": True, "checked": False,
                "reason": f"status={status!r} is not a resolved/merged claim -- independent "
                          "PR/file check does not apply"}

    if pr_number is not None:
        if not repo:
            return {"verified": False, "checked": True,
                    "reason": f"PR #{pr_number} cited but no --umr-repo given -- cannot "
                              "independently verify which repo to check"}
        pr_state = _fetch_real_pr_state(pr_number, repo)
        if not pr_state.get("ok"):
            return {"verified": False, "checked": True,
                    "reason": f"could not independently verify {repo}#{pr_number} against "
                              f"GitHub: {pr_state.get('error', 'unknown gh error')} -- an "
                              "unverifiable PR citation is never accepted as real evidence",
                    "pr_state": pr_state}
        files = pr_state.get("files") or []
        non_docs_files = [f for f in files if not str(f).lower().endswith(".md")]
        if not non_docs_files:
            return {"verified": False, "checked": True,
                    "reason": f"{repo}#{pr_number}'s real diff has {len(files)} file(s), none "
                              "outside documentation (*.md) -- a docs-only PR can never confirm "
                              "real completed work",
                    "pr_state": pr_state}
        return {"verified": True, "checked": True,
                "reason": f"{repo}#{pr_number} independently confirmed via GitHub: "
                          f"{len(non_docs_files)} non-docs-only file(s) in its real diff "
                          f"(state={pr_state.get('state')}, merged_at={pr_state.get('merged_at')})",
                "pr_state": pr_state}

    touched = [f for f in (files_touched or []) if f]
    non_docs_touched = [f for f in touched if not str(f).lower().endswith(".md")]
    if non_docs_touched:
        return {"verified": True, "checked": True,
                "reason": f"no PR cited; a real non-empty, non-docs-only file-touched list was "
                          f"supplied ({len(non_docs_touched)} file(s))"}
    if touched:
        return {"verified": False, "checked": True,
                "reason": f"no PR cited and the {len(touched)} file(s) self-reported as touched "
                          "are all documentation (*.md) -- the same docs-only loophole as a "
                          "docs-only PR, refused the same way"}

    return {"verified": False, "checked": True,
            "reason": "no PR number cited and no real file-touched list supplied -- a "
                      f"status={status!r} claim with zero independently-checkable evidence "
                      "is refused"}


def record_completion(umr_id, entry_text, role_label, umr_status, umr_reason,
                       gtm_category_index, gtm_result, gtm_script_path,
                       gtm_evidence_summary, gtm_evidence_json, gtm_fix_commit,
                       gtm_fix_file_path, gtm_fix_pr_number, new_entity_record_file,
                       umr_commit_sha=None, umr_file_path=None, umr_pr_number=None,
                       umr_repo=None, umr_repo_root=None, files_touched=None):
    """The real canonical write-back. Every write funnels through an
    existing, already-tested writer -- this function never issues a raw
    INSERT/UPDATE against umr_tasks or gtm_certification_categories itself,
    and only writes wiring_registry after a real search-first duplicate
    check."""
    sbr = _superboss_register()
    air = _ai_agent_registry()
    result = {"umr_id": umr_id}

    # 1. this exact UMR's own agent_id memory row.
    result["ai_agent_registry"] = _capture_json(
        air.cmd_record_work,
        argparse.Namespace(umr_id=umr_id, entry_text=entry_text, role_label=role_label),
    )

    # 2. umr_tasks -- the real standard system of record. cmd_mark_umr_terminal
    #    (UMR-20260806-130914-e7f1) requires real structured completion evidence
    #    (a real --commit-sha ancestor-of-main or a real --file-path) for
    #    status=completed/completed_unmerged, plus repo/repo_root to resolve and
    #    verify that evidence -- this call must supply the same Namespace fields
    #    superboss-register.py's own mark-umr-terminal CLI would.
    #
    #    UMR-20260814-181115 real fix, the actual work item this task's SPEC
    #    is for: mark_umr_terminal's own gate (validate_umr_terminal_completion_evidence)
    #    only checks that a cited commit/file *exists*, never whether the real
    #    diff behind it is docs-only or whether a cited PR number is even a
    #    real PR -- verify_real_completion_evidence() above is the real,
    #    independent check that closes that gap, and runs BEFORE
    #    mark-umr-terminal is ever called. A claim that fails it is never
    #    silently accepted as completed/completed_unmerged: umr_tasks is left
    #    untouched (its real prior status, never a false 'completed') and the
    #    write-back result is explicitly labeled "unverified_self_report" so
    #    the next reader (a PM tier, an audit sweep) sees exactly why this
    #    wasn't recorded as real, verified completion.
    if umr_status:
        # --umr-file-path is itself real, self-reported "a file I touched"
        # evidence -- folded into the same files_touched list
        # verify_real_completion_evidence() checks, so a caller using only
        # the pre-existing --umr-file-path evidence path (never --files-touched)
        # is not spuriously downgraded.
        effective_files_touched = list(files_touched or [])
        if umr_file_path:
            effective_files_touched.append(umr_file_path)
        verification = verify_real_completion_evidence(umr_status, umr_pr_number, umr_repo, effective_files_touched)
        result["completion_verification"] = verification
        if verification["verified"]:
            result["umr_tasks"] = _capture_json(
                sbr.cmd_mark_umr_terminal,
                argparse.Namespace(
                    umr_id=umr_id, status=umr_status, reason=umr_reason,
                    commit_sha=umr_commit_sha, file_path=umr_file_path,
                    pr_number=umr_pr_number, repo=umr_repo, repo_root=umr_repo_root,
                ),
            )
        else:
            result["umr_tasks"] = {
                "written": False,
                "status": "unverified_self_report",
                "claimed_status": umr_status,
                "reason": verification["reason"],
            }

    # 3. gtm_certification_categories -- "where relevant" only: a real
    #    --gtm-category-index must be given, never called unconditionally.
    #    Same subprocess convention every real gtm_check_*.py script already
    #    uses to call this one canonical writer (see
    #    gtm_check_accessibility_testing.py's own WRITER call site).
    if gtm_category_index is not None:
        cmd = [
            sys.executable, GTM_WRITER,
            "--category-index", str(gtm_category_index),
            "--result", gtm_result,
            "--script-path", gtm_script_path,
            "--evidence-summary", gtm_evidence_summary,
            "--evidence-json", gtm_evidence_json,
        ]
        if gtm_fix_commit:
            cmd += ["--fix-commit", gtm_fix_commit]
        if gtm_fix_file_path:
            cmd += ["--fix-file-path", gtm_fix_file_path]
        if gtm_fix_pr_number is not None:
            cmd += ["--fix-pr-number", str(gtm_fix_pr_number)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        try:
            result["gtm_certification_categories"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result["gtm_certification_categories"] = {"error": proc.stderr or proc.stdout}

    # 4. wiring_registry -- ONLY a genuinely new function/engine/script,
    #    search-first (exact entity_id lookup) before ever writing, so a
    #    real duplicate row for something that already has one is refused.
    if new_entity_record_file:
        with open(new_entity_record_file, encoding="utf-8") as f:
            entity = json.load(f)
        sbr.init_db_silent()
        conn = sbr._connect()
        sbr._ensure_wiring_registry_table(conn)
        existing = conn.execute(
            "SELECT entity_id FROM wiring_registry WHERE entity_id = ?", (entity["entity_id"],)
        ).fetchone()
        if existing:
            result["wiring_registry"] = {
                "written": False,
                "reason": f"entity_id {entity['entity_id']!r} already exists -- search-first check "
                          "found a real duplicate, refusing to write a second row",
            }
            conn.close()
        else:
            sbr.register_entity_row(conn, entity)
            conn.commit()
            conn.close()
            result["wiring_registry"] = {"written": True, "entity_id": entity["entity_id"]}

    return result


def cmd_assemble_briefing(args):
    briefing = assemble_briefing(args.umr_id, args.scope_term or [], args.intent_text)
    print(json.dumps(briefing, indent=2, default=str))


def cmd_record_completion(args):
    result = record_completion(
        args.umr_id, args.entry_text, args.role_label, args.umr_status, args.umr_reason,
        args.gtm_category_index, args.gtm_result, args.gtm_script_path,
        args.gtm_evidence_summary, args.gtm_evidence_json, args.gtm_fix_commit,
        args.gtm_fix_file_path, args.gtm_fix_pr_number, args.new_entity_record_file,
        umr_commit_sha=args.umr_commit_sha, umr_file_path=args.umr_file_path,
        umr_pr_number=args.umr_pr_number, umr_repo=args.umr_repo,
        umr_repo_root=args.umr_repo_root, files_touched=args.files_touched,
    )
    print(json.dumps(result, indent=2, default=str))


def build_parser():
    p = argparse.ArgumentParser(prog="agent_work_briefing.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("assemble-briefing",
                        help="the real deterministic, boolean-checkable briefing an AI agent reads "
                             "BEFORE starting real work under a real UMR")
    b.add_argument("--umr-id", dest="umr_id", required=True)
    b.add_argument("--scope-term", dest="scope_term", action="append",
                    help="a real file path or keyword describing this UMR's scope; repeatable")
    b.add_argument("--intent-text", dest="intent_text", required=True)
    b.set_defaults(func=cmd_assemble_briefing)

    r = sub.add_parser("record-completion",
                        help="the one canonical write-back an AI agent calls AFTER real work "
                             "completes -- ai_agent_registry + umr_tasks + (optionally) "
                             "gtm_certification_categories + (optionally, search-first) wiring_registry")
    r.add_argument("--umr-id", dest="umr_id", required=True)
    r.add_argument("--entry-text", dest="entry_text", required=True)
    r.add_argument("--role-label", dest="role_label", default=None)
    r.add_argument("--umr-status", dest="umr_status", default=None, choices=["completed", "failed", "killed"],
                    help="omit to leave umr_tasks untouched (e.g. an interim record-completion call)")
    r.add_argument("--umr-reason", dest="umr_reason", default=None)
    r.add_argument("--umr-commit-sha", dest="umr_commit_sha", default=None,
                    help="real completion evidence for --umr-status completed/completed_unmerged "
                         "(superboss-register.py mark-umr-terminal's own structured-evidence gate, "
                         "UMR-20260806-130914-e7f1) -- a real commit sha; required for status=completed "
                         "unless --umr-file-path is supplied instead")
    r.add_argument("--umr-file-path", dest="umr_file_path", default=None,
                    help="real completion evidence: a real file path that exists on disk (alternative "
                         "to --umr-commit-sha)")
    r.add_argument("--umr-pr-number", dest="umr_pr_number", type=int, default=None)
    r.add_argument("--umr-repo", dest="umr_repo", default=None,
                    choices=["veridian-scripts", "compliance-tracker", "projexa"])
    r.add_argument("--umr-repo-root", dest="umr_repo_root", default=None,
                    help="local checkout used to verify --umr-commit-sha; defaults to the standard "
                         "path for --umr-repo (see DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS)")
    r.add_argument("--files-touched", dest="files_touched", action="append", default=None,
                    help="a real file path this worker's own self-report claims it touched -- "
                         "repeatable; required independent evidence for --umr-status "
                         "completed/completed_unmerged when no --umr-pr-number is cited (see "
                         "verify_real_completion_evidence) -- omitting both gets the claim "
                         "downgraded to status=unverified_self_report rather than accepted")
    r.add_argument("--gtm-category-index", dest="gtm_category_index", type=int, default=None,
                    help="omit unless this real work actually maps to a gtm_certification_categories row")
    r.add_argument("--gtm-result", dest="gtm_result", default=None, choices=["pass", "fail", "blocked"])
    r.add_argument("--gtm-script-path", dest="gtm_script_path", default=None)
    r.add_argument("--gtm-evidence-summary", dest="gtm_evidence_summary", default=None)
    r.add_argument("--gtm-evidence-json", dest="gtm_evidence_json", default=None)
    r.add_argument("--gtm-fix-commit", dest="gtm_fix_commit", default=None)
    r.add_argument("--gtm-fix-file-path", dest="gtm_fix_file_path", default=None)
    r.add_argument("--gtm-fix-pr-number", dest="gtm_fix_pr_number", type=int, default=None)
    r.add_argument("--new-entity-record-file", dest="new_entity_record_file", default=None,
                    help="path to a JSON entity_record_schema file -- ONLY if real work created a "
                         "genuinely new function/engine/script; search-first dedup is automatic")
    r.set_defaults(func=cmd_record_completion)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
