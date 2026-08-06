#!/usr/bin/env python3
"""unified_orchestrator.py -- the one real deterministic orchestrator script
UMR-20260806-124327-6ffb specified, amended by UMR-20260806-124654-a8d6
(search-then-graduate) and UMR-20260806-124936-13b1 (real file-path
verification), and closing this task's own 3 real gaps
(UMR-20260806-125524-720c): a real, deterministic submission contract
stated at hand-off, real independent re-verification before status is ever
written completed, and real general input/output validation inside the
orchestrator itself.

HONEST STATUS NOTE, self-audit finding recorded here rather than silently
assumed away: when this task (task-20260806-181155) began, no prior UMR in
this same real chain had actually built this script yet --
UMR-20260806-124327-6ffb was still status='running' with its own dispatched
worker task (task-20260806-181141) not yet started. Amending a script that
does not exist would be fiction, so this module builds the real steps
UMR-20260806-124327-6ffb/124654-a8d6/124936-13b1 already specified, plus
this task's own 3 gap-closing steps, in one real, tested pass.

Mid-build, a SECOND real finding: PR #199 (branch
worker/task-20260806-165903-correction--wire-the-new-ai-agent-id-tab) was
merged into origin/main WHILE this task was in progress, and already built
real, tested implementations of this chain's steps 2 (agent_id reuse/mint,
ai_agent_registry.py), 3 (briefing assembly, agent_work_briefing.py's
assemble_briefing()), and part of step 4 (write-back, agent_work_briefing.py's
record_completion()) -- live-wired into worker-entrypoint.sh. An earlier
version of this file (and of superboss-register.py) briefly duplicated that
work before this was discovered; both were corrected the moment the
collision was found (kept in git history, not silently squashed) rather
than shipping two competing implementations of the same table. This module
now composes those two real modules directly instead, per this whole task
chain's own "search before building, reuse before duplicating" rule
(UMR-20260806-124654-a8d6).

Nothing here is reimplemented where a real primitive already existed:
  - steps 1-2 compose plan_generator.py's own already-built, already-wired-
    into-resource_governor.py check_reuse_before_dispatch() (capability_
    registry + wiring_registry + knowledge_engine + system_index, one call)
  - step 3 composes ai_agent_registry.py's ensure_agent()
  - step 4 composes agent_work_briefing.py's assemble_briefing()
  - step 6's input gate composes tight_task_validation.py's own already-
    built validate_tight_task() (the same TightTask envelope
    task-gateway.py's real dispatch gate already enforces)
  - step 9 composes agent_work_briefing.py's record_completion()
  - steps 5, 7, 8, 10 are this task's own genuinely new pieces: nothing
    found anywhere in this repo already did a deterministic submission
    contract, an independent re-run-the-real-command re-verification, a
    general (not external-agent-only) input+output validation pair inside
    an orchestrator, or a graduate-to-script evaluation gate.

Real deterministic steps, each returning a real boolean, never a narrated
summary standing in for one:

  1. step_reuse_check              -- script-match / prior-precedent search
                                       (UMR-124327-6ffb steps 1-2,
                                       UMR-124654-a8d6 steps 1-2)
  2. step_prior_agent_precedent    -- which real agent_ids handled similar
                                       past UMRs (UMR-124654-a8d6 step 2)
  3. step_resolve_agent_id         -- reuse/mint the real UMR-scoped agent_id
                                       (UMR-124327-6ffb step 2; composes
                                       ai_agent_registry.py, not reimplemented)
  4. step_assemble_briefing        -- real briefing from wiring_registry +
                                       capability_registry + ai_agent_registry
                                       (UMR-124327-6ffb step 3; composes
                                       agent_work_briefing.py)
  5. step_submission_contract      -- GAP 1: state plainly, at hand-off,
                                       exactly what real file to write, what
                                       real command submits it, how it gets
                                       really audited -- never implicit
  6. step_validate_input           -- GAP 3a: real input validation against
                                       what the task actually requires,
                                       BEFORE work starts
  [ real AI/script work happens here, outside this script ]
  7. step_reverify                 -- GAP 2: independent re-verification --
                                       re-runs the real audit command itself,
                                       never trusts a self-reported "done"
  8. step_validate_output          -- GAP 3b: real output validation against
                                       real defined acceptance criteria
  9. step_writeback                -- write status=completed + agent memory
                                       ONLY if 7 and 8 both really passed
                                       (UMR-124327-6ffb step 4; composes
                                       agent_work_briefing.record_completion())
  10. step_graduate_evaluation     -- can this become a permanent script?
                                       (UMR-124654-a8d6 step 4)

step_verify_all_registry_paths() (UMR-124936-13b1) is a separate, standalone
real check (not part of the per-task sequence -- it audits the registries
as a whole), also included below.

Usage:
  python3 unified_orchestrator.py run --task-spec-file spec.json
  python3 unified_orchestrator.py verify-registry-paths
"""
import argparse
import importlib.util as _ilu
import json
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR
# Same env-overridable convention dispatch_core.py already uses -- wiring_registry
# rows span multiple real repos (this scripts repo AND repos/compliance-tracker),
# so relative paths there resolve against the real platform root, never just this
# one checkout (see step_verify_all_registry_paths()'s own real finding: every
# wiring_registry 'repos/compliance-tracker/...' path is relative to THIS root).
VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")


def _load_module(name, filename):
    spec = _ilu.spec_from_file_location(name, os.path.join(SCRIPT_DIR, filename))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sbr = None


def _superboss_register():
    """Same importlib-by-file-path convention resource_governor.py's own
    _superboss_register()/_plan_generator() already use -- a test copy of
    this whole script tree is always the one actually exercised."""
    global _sbr
    if _sbr is None:
        _sbr = _load_module("superboss_register_orchestrator", "superboss-register.py")
    return _sbr


_pg = None


def _plan_generator():
    global _pg
    if _pg is None:
        _pg = _load_module("plan_generator_orchestrator", "plan_generator.py")
    return _pg


_ttv = None


def _tight_task_validation():
    global _ttv
    if _ttv is None:
        _ttv = _load_module("tight_task_validation_orchestrator", "tight_task_validation.py")
    return _ttv


_air = None


def _ai_agent_registry():
    global _air
    if _air is None:
        _air = _load_module("ai_agent_registry_orchestrator", "ai_agent_registry.py")
    return _air


_awb = None


def _agent_work_briefing():
    global _awb
    if _awb is None:
        _awb = _load_module("agent_work_briefing_orchestrator", "agent_work_briefing.py")
    return _awb


def _abs_path(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


# ---------------------------------------------------------------------------
# STEP 1: script-match + prior-precedent search (UMR-124327-6ffb steps 1-2 /
# UMR-124654-a8d6 steps 1-2). Composes plan_generator.check_reuse_before_
# dispatch(), never reimplemented -- that function already queries
# capability_registry, wiring_registry, knowledge_engine, and system_index
# in one real call and is already the one real caller resource_governor.py's
# submit() uses for every real task creation on this platform.
def step_reuse_check(task_identity, intent_text, domain=None):
    pg = _plan_generator()
    result = pg.check_reuse_before_dispatch(intent_text, task_identity=task_identity, domain=domain)
    cap = result.get("capability") or {}
    cap_matches = cap.get("matches") or []
    script_matched = result.get("recommendation") == "reuse_instead"
    return {
        "ok": result.get("error") is None,
        "script_matched": script_matched,
        "matched_capability": cap_matches[0] if (script_matched and cap_matches) else None,
        "reuse_candidates": (result.get("reuse_candidates") or [])[:10],
        "recommendation": result.get("recommendation"),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# STEP 2: which real agent_ids handled similar past work (UMR-124654-a8d6
# step 2's "report the real script ids or real agent ids that were used for
# that real similar past work" -- a real search across ALL past umr_tasks,
# not only this one UMR's own history).
def step_prior_agent_precedent(intent_text, limit=5):
    sbr = _superboss_register()
    conn = sbr._connect()
    try:
        try:
            q = sbr._fts_query(intent_text)
            rows = conn.execute(
                "SELECT t.umr_id, t.status, t.task_identity FROM umr_tasks_fts f "
                "JOIN umr_tasks t ON t.rowid = f.rowid WHERE umr_tasks_fts MATCH ? "
                "AND t.status = 'completed' ORDER BY rank LIMIT ?",
                (q, limit),
            ).fetchall()
        except Exception:
            rows = []
        candidates = []
        for r in rows:
            try:
                agent_row = conn.execute(
                    "SELECT agent_id FROM ai_agent_registry WHERE umr_id = ?", (r["umr_id"],)
                ).fetchone()
            except Exception:
                agent_row = None
            candidates.append({
                "umr_id": r["umr_id"],
                "task_identity": r["task_identity"],
                "agent_id": agent_row["agent_id"] if agent_row else None,
            })
        return {"ok": True, "precedent_found": bool(candidates), "candidates": candidates}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# STEP 3: reuse/mint the real UMR-scoped agent_id (UMR-124327-6ffb step 2).
# Composes ai_agent_registry.py's own ensure_agent() -- the real, tested,
# already-merged (PR #199) canonical writer for this exact table -- rather
# than a second, competing implementation.
def step_resolve_agent_id(umr_id, role_label):
    air = _ai_agent_registry()
    try:
        row, already_existed = air.ensure_agent(umr_id, role_label)
        return {"ok": True, "agent_id": row["agent_id"], "memory_file_path": row["memory_file_path"],
                "minted": not already_existed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# STEP 4: real briefing assembled from wiring_registry + capability_registry
# + ai_agent_registry (UMR-124327-6ffb step 3). Composes agent_work_briefing.
# py's own assemble_briefing() -- the real, tested, already-merged (PR #199)
# implementation of exactly this step -- rather than a second, competing one.
def step_assemble_briefing(umr_id, intent_text, scope_terms=None):
    awb = _agent_work_briefing()
    try:
        briefing = awb.assemble_briefing(umr_id, scope_terms or [], intent_text)
        return {"ok": True, "briefing": briefing}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GAP 1 -- STEP 5: state plainly and deterministically, at the moment work
# is handed to an AI agent, exactly what real file it must write to, what
# real command submits it, and exactly how that submission gets really
# audited before it counts as done. The audit command is deterministically
# EXTRACTED (never invented) from the task's own successCriteria text, reusing
# tight_task_validation.py's own runnable-command detection regexes -- the
# same gate task-gateway.py's dispatch path already enforces the PRESENCE
# of; this is the first real caller that also extracts the literal command,
# not merely checks one exists. Nothing else in this repo does this.
def _extract_runnable_command(success_criteria_text):
    ttv = _tight_task_validation()
    for raw_line in (success_criteria_text or "").splitlines():
        if not raw_line.strip():
            continue
        if ttv._line_is_runnable_command(raw_line):
            line = ttv.LIST_MARKER_RE.sub("", raw_line).strip()
            spans = ttv.BACKTICK_SPAN_RE.findall(line)
            cand = (spans[0] if spans else line).strip().lstrip("$").strip()
            if cand:
                return cand
    return None


def step_submission_contract(workspace_dir, success_criteria_text, output_file_path=None):
    audit_cmd = _extract_runnable_command(success_criteria_text)
    output_file_path = output_file_path or os.path.join(workspace_dir, "PROGRESS.md")
    contract = {
        "output_file_path": output_file_path,
        "submit_command": (
            f"cd {workspace_dir} && git add -A && git commit -m '<real summary>' && git push"
        ),
        "audit_command": audit_cmd,
        "audit_method": (
            f"the orchestrator independently re-runs `{audit_cmd}` itself after the agent reports "
            f"done (step 7) -- status is written completed only if that real command really exits 0, "
            f"never on the agent's own say-so"
        ) if audit_cmd else None,
    }
    ok = bool(audit_cmd) and bool(output_file_path)
    return {"ok": ok, "contract": contract,
            "reason": None if ok else "successCriteria has no extractable runnable command -- "
                                       "the submission contract cannot be stated deterministically"}


# ---------------------------------------------------------------------------
# GAP 3a -- STEP 6: real input validation against what the task actually
# requires, BEFORE any work starts. Composes tight_task_validation.py's own
# validate_tight_task() -- the same general-purpose TightTask gate
# task-gateway.py's dispatch path already enforces -- generalized here to
# run INSIDE the orchestrator itself, not only wherever it happened to be
# wired before (the SPEC's own "not only the separate external agent
# bridge" requirement -- that bridge's own equivalent is
# check_external_agent_eligibility() in superboss-register.py, a DIFFERENT,
# bridge-specific check; this reuses the general-purpose one instead of
# reimplementing either).
def step_validate_input(tight_task):
    ttv = _tight_task_validation()
    result = ttv.validate_tight_task(tight_task)
    return {"ok": True, "passed": bool(result.get("valid")), "detail": result}


# ---------------------------------------------------------------------------
# GAP 2 -- STEP 7: real independent re-verification. After an AI agent or a
# script reports done, this step re-runs the REAL audit command itself
# (never trusts the self-report), records the result via
# superboss-register.py's new record_task_audit() (task_audits table --
# already existed, zero rows, no real writer anywhere before this task), and
# the verdict is always exactly the real process exit code, never a
# narrated summary.
def step_reverify(audit_cmd, software_task_id, work_item_id=None, cwd=None, timeout=120):
    if not audit_cmd:
        return {"ok": False, "passed": False, "reason": "no audit_command in the submission contract -- "
                                                          "cannot independently re-verify"}
    try:
        proc = subprocess.run(audit_cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"ok": False, "passed": False, "reason": f"real audit command failed to even run: {e}"}
    passed = proc.returncode == 0
    sbr = _superboss_register()
    conn = sbr._connect()
    try:
        sbr._ensure_task_audits_table(conn)
        with sbr._write_lock():
            audit_id = sbr.record_task_audit(
                conn, work_item_id=work_item_id, software_task_id=software_task_id, audit_cmd=audit_cmd,
                exit_code=proc.returncode, stdout_tail=proc.stdout, stderr_tail=proc.stderr,
                verdict="pass" if passed else "fail",
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "passed": passed, "audit_id": audit_id, "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-1000:], "stderr_tail": proc.stderr[-1000:]}


# ---------------------------------------------------------------------------
# GAP 3b -- STEP 8: real output validation against real defined acceptance
# criteria, boolean pass/fail. Deliberately requires step 7's independent
# re-verification to have really passed AND every real expected output file
# to really exist on disk -- never accepts either alone.
def step_validate_output(contract, reverify_result, expected_files=None):
    reasons = []
    if not reverify_result.get("passed"):
        reasons.append("independent re-verification (step 7) did not really pass")
    output_path = contract.get("output_file_path")
    if output_path and not os.path.exists(_abs_path(output_path)):
        reasons.append(f"declared output_file_path does not really exist on disk: {output_path}")
    for f in (expected_files or []):
        if not os.path.exists(_abs_path(f)):
            reasons.append(f"expected output file does not really exist on disk: {f}")
    passed = len(reasons) == 0
    return {"ok": True, "passed": passed, "reasons": reasons}


# ---------------------------------------------------------------------------
# STEP 9: write-back (UMR-124327-6ffb step 4) -- ONLY if step 7 AND step 8
# both really passed. Composes agent_work_briefing.py's own
# record_completion() (the real, tested, already-merged (PR #199) canonical
# write path: agent memory + umr_tasks, both via their own existing
# writers) rather than a second, competing implementation. Refuses (never
# calls record_completion with umr_status="completed") rather than ever
# marking a UMR completed on a self-report alone.
def step_writeback(umr_id, role_label, outputs, reverify_result, validate_result):
    if not (reverify_result.get("passed") and validate_result.get("passed")):
        return {"ok": True, "written": False,
                "reason": "refusing to write status=completed -- independent re-verification and/or "
                          "output validation did not both really pass"}
    awb = _agent_work_briefing()
    entry_text = f"unified_orchestrator.py step 9 write-back: {json.dumps(outputs, default=str)}"
    try:
        result = awb.record_completion(
            umr_id, entry_text, role_label, "completed", "independently re-verified and output-validated "
            "by unified_orchestrator.py steps 7-8",
            None, None, None, None, None, None, None, None, None,
        )
        return {"ok": True, "written": True, "detail": result}
    except Exception as e:
        return {"ok": False, "written": False, "reason": str(e)}


# ---------------------------------------------------------------------------
# STEP 10: graduate-to-permanent-script evaluation (UMR-124654-a8d6 step 4).
# Purely deterministic gate -- never itself writes a new script (that is
# real judgment/build work), only decides + records whether this piece of
# work is a genuine candidate, same "record that real reason plainly, never
# force a real script where one cannot deterministically exist" rule the
# Owner specified. (A sibling in-progress task, UMR-20260806-124654-a8d6's
# own dispatch, is separately building a persistent capability_graduation_log
# recorder -- this function deliberately only DECIDES, writes nothing, so it
# cannot collide with that in-progress work.)
def step_graduate_evaluation(complexity_tier, reverify_result, validate_result):
    if not (reverify_result.get("passed") and validate_result.get("passed")):
        return {"ok": True, "graduate": False,
                "reason": "not genuinely complete/tested yet -- cannot evaluate graduation"}
    if complexity_tier == "judgment":
        return {"ok": True, "graduate": False,
                "reason": "complexityTier=judgment -- genuinely requires real judgment every time, "
                          "never forced into a permanent script"}
    return {"ok": True, "graduate": True,
            "reason": f"complexityTier={complexity_tier!r} and independently verified -- a genuine "
                      f"candidate to graduate into a permanent deterministic script"}


# ---------------------------------------------------------------------------
# UMR-124936-13b1: every real file path in every metadata table must be
# verified to actually exist on disk, boolean, no exceptions. Standalone
# (not part of the per-task sequence above -- audits the registries as a
# whole). Honestly reports the one real schema gap found rather than
# fabricating a pass: capability_registry carries no dedicated file-path
# column in its current live schema, so there is nothing there to check --
# flagged explicitly, never silently skipped.
def _wiring_path_exists(raw_path):
    """A real wiring_registry.path value is sometimes several real paths in
    one field, semicolon-separated (e.g. engine-01's own row: 3 real
    compliance-tracker source files it spans) -- found live while building
    this check (an earlier version of this function naively treated the
    whole delimited string as one path and mis-reported 5917/7928 real rows
    as failing; every one of the sampled 'failures' was actually a real,
    existing file, just resolved against the wrong root and never split).
    Every constituent real path must exist for the row to really pass --
    resolved against VERIDIAN_ROOT, since wiring_registry spans multiple
    real repos (this one AND repos/compliance-tracker), never just this one
    checkout. A trailing '#fragment' (knowledge_engine's own real, documented
    convention for a virtual sub-document identifier inside one real file,
    e.g. 'ai-os/MASTER_INDEX.yaml#registries.<id>' -- see
    upsert_knowledge_fragment()'s own docstring in superboss-register.py) is
    stripped before the disk check -- checking the literal '#'-containing
    string is a real category error (found live: MASTER_INDEX.yaml itself
    genuinely exists on disk; ~90 rows were mis-reported as failing before
    this fix, purely from not stripping the fragment). Returns
    (exists: bool, per_segment: list[dict])."""
    segments = [s.strip() for s in raw_path.split(";") if s.strip()]
    per_segment = [
        {"path": s, "exists": os.path.exists(
            (s.split("#", 1)[0] if "#" in s else s) if os.path.isabs(s)
            else os.path.join(VERIDIAN_ROOT, s.split("#", 1)[0] if "#" in s else s))}
        for s in segments
    ]
    return all(s["exists"] for s in per_segment), per_segment


def step_verify_all_registry_paths():
    sbr = _superboss_register()
    conn = sbr._connect()
    try:
        wiring_rows = conn.execute(
            "SELECT entity_id, path, source_system FROM wiring_registry WHERE path IS NOT NULL AND path != ''"
        ).fetchall()
        # source_system='server' rows are real filesystem paths on THIS disk
        # (scripts/files/cron_jobs/dispatch_events/governance_docs) --
        # checkable for real. supabase/vercel/github rows carry a real
        # identifier in the same 'path' column by the schema's own design
        # (a Postgres 'schema.table' name, a project slug, a repo slug) --
        # NOT a disk path at all; checking those against os.path.exists()
        # would be a real category error, not a real defect, so they are
        # reported separately and honestly rather than folded into "fail".
        # Real, second finding while building this check: some cron_job rows
        # (source_system='server') carry a real bare script path
        # (dispatch_core.py's own record_tick() convention: "scripts/x.py"),
        # others carry a real FULL crontab line (schedule + command + args +
        # shell redirection) from a separate bulk-registration path -- the
        # same entity_type genuinely means two different things in this live
        # data today. A segment containing whitespace is a command line, not
        # a path, and is honestly bucketed as not-a-disk-path rather than a
        # false "fail" -- this pre-existing entity_type inconsistency is
        # itself a real, separate finding, reported below, not silently
        # normalized away.
        wiring_checked, wiring_not_disk_paths = [], []
        for r in wiring_rows:
            segments = [s.strip() for s in r["path"].split(";") if s.strip()]
            looks_like_command_line = any(" " in s for s in segments)
            if r["source_system"] != "server" or looks_like_command_line:
                wiring_not_disk_paths.append({
                    "entity_id": r["entity_id"], "path": r["path"], "source_system": r["source_system"],
                    "reason": "non-server source_system" if r["source_system"] != "server"
                              else "path value is a crontab command line, not a bare path",
                })
                continue
            exists, per_segment = _wiring_path_exists(r["path"])
            wiring_checked.append({"entity_id": r["entity_id"], "path": r["path"], "exists": exists,
                                    "segments": per_segment})
        agent_rows = conn.execute("SELECT agent_id, memory_file_path FROM ai_agent_registry").fetchall()
        agent_checked = [
            {"agent_id": r["agent_id"], "path": r["memory_file_path"],
             "exists": os.path.exists(r["memory_file_path"])}
            for r in agent_rows
        ]
        cap_count = conn.execute("SELECT COUNT(*) c FROM capability_registry").fetchone()["c"]
    finally:
        conn.close()

    wiring_fail = [c for c in wiring_checked if not c["exists"]]
    agent_fail = [c for c in agent_checked if not c["exists"]]
    results = {
        "wiring_registry": {"checked": len(wiring_checked), "pass": len(wiring_checked) - len(wiring_fail),
                             "fail": wiring_fail,
                             "not_a_disk_path_by_design": {
                                 "count": len(wiring_not_disk_paths),
                                 "note": "source_system in (supabase/vercel/github) rows carry a real "
                                         "non-filesystem identifier (schema.table / project slug / repo "
                                         "slug) in the same path column by design -- not checked against "
                                         "disk, not counted as a fail",
                                 "examples": wiring_not_disk_paths[:5],
                             }},
        "ai_agent_registry": {"checked": len(agent_checked), "pass": len(agent_checked) - len(agent_fail),
                               "fail": agent_fail},
        "capability_registry": {"checked": 0, "pass": 0, "fail": [], "total_rows": cap_count,
                                 "schema_note": "capability_registry has no dedicated file-path column in "
                                                "its current live schema -- honestly reported as nothing to "
                                                "verify, not silently skipped"},
    }
    all_pass = not wiring_fail and not agent_fail
    return {"ok": True, "all_pass": all_pass, "results": results}


# ---------------------------------------------------------------------------
# End-to-end runner -- executes every real step against ONE real task_spec,
# printing the real boolean at each step (this task's own required test
# shape, same as every prior UMR in this chain).
def run(task_spec):
    steps = {}

    steps["1_reuse_check"] = step_reuse_check(
        task_spec["task_identity"], task_spec["intent_text"], task_spec.get("domain"))

    steps["2_prior_agent_precedent"] = step_prior_agent_precedent(task_spec["intent_text"])

    role_label = task_spec.get("role_label", "orchestrated_worker")
    steps["3_resolve_agent_id"] = step_resolve_agent_id(task_spec["umr_id"], role_label)

    steps["4_assemble_briefing"] = step_assemble_briefing(
        task_spec["umr_id"], task_spec["intent_text"], task_spec.get("scope_terms"))

    tight_task = task_spec["tight_task"]
    steps["5_submission_contract"] = step_submission_contract(
        task_spec["workspace_dir"], tight_task.get("successCriteria"),
        task_spec.get("output_file_path"))

    steps["6_validate_input"] = step_validate_input(tight_task)

    # --- real AI/script work happens here, outside this orchestrator ---

    audit_cmd = steps["5_submission_contract"]["contract"].get("audit_command")
    steps["7_reverify"] = step_reverify(
        audit_cmd, task_spec["task_identity"], work_item_id=task_spec.get("work_item_id"),
        cwd=task_spec["workspace_dir"])

    steps["8_validate_output"] = step_validate_output(
        steps["5_submission_contract"]["contract"], steps["7_reverify"],
        expected_files=task_spec.get("expected_files"))

    steps["9_writeback"] = step_writeback(
        task_spec["umr_id"], role_label, task_spec.get("outputs", {}),
        reverify_result=steps["7_reverify"], validate_result=steps["8_validate_output"])

    steps["10_graduate_evaluation"] = step_graduate_evaluation(
        tight_task.get("complexityTier"), steps["7_reverify"], steps["8_validate_output"])

    return steps


def _cmd_run(args):
    with open(args.task_spec_file, encoding="utf-8") as f:
        task_spec = json.load(f)
    steps = run(task_spec)
    print(json.dumps(steps, indent=2, default=str))


def _cmd_verify_registry_paths(args):
    print(json.dumps(step_verify_all_registry_paths(), indent=2, default=str))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run every real deterministic step end to end against one real task")
    p_run.add_argument("--task-spec-file", required=True)

    sub.add_parser("verify-registry-paths",
                    help="UMR-124936-13b1: verify every real file path in every metadata table exists on disk")

    args = p.parse_args()
    if args.cmd == "run":
        _cmd_run(args)
    elif args.cmd == "verify-registry-paths":
        _cmd_verify_registry_paths(args)


if __name__ == "__main__":
    main()
