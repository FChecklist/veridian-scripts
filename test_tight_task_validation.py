"""Regression tests for tight_task_validation.py's contradiction detector.

Covers 4 real false-positive patterns hit this session (2026-07-25) by
manually written and scripts/auto_phase_continuation.py-generated prompts,
plus a true-positive case proving the detector still catches a genuine
conflict. Run with: python3 -m pytest scripts/ -k tight_task_validation
"""
import sys
import os
import importlib.util

sys.path.insert(0, os.path.dirname(__file__))

from tight_task_validation import detect_field_contradiction, parse_labeled_fields, validate_tight_task  # noqa: E402

# phase-continuation-tick.py has a hyphen -- not a valid module name, load by path
# instead (same pattern test_check_crontab_unauthorized_change.py uses for
# preflight-guard.py).
_pct_spec = importlib.util.spec_from_file_location(
    "phase_continuation_tick", os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase-continuation-tick.py")
)
phase_continuation_tick = importlib.util.module_from_spec(_pct_spec)
_pct_spec.loader.exec_module(phase_continuation_tick)


def _base_task(**overrides):
    task = {
        "objective": "Wire the real gateway.",
        "scope": "Only the gateway module.",
        "successCriteria": "python3 scripts/check.py --real",
        "expectedOutput": "A committed PR.",
        "complexityTier": "integrative",
        "knownContext": "Read the gateway spec first.",
        "constraints": "",
        "filePaths": ["scripts/task-gateway.py"],
    }
    task.update(overrides)
    return task


def test_pattern1_negative_negative_is_not_a_contradiction():
    """Both mentions are negative/prohibitive (Constraints forbids building
    infra; Scope says that same infra is documented as NOT implemented) --
    agreement, not conflict."""
    task = _base_task(
        constraints="Do not attempt to build Temporal, OPA, Qdrant, or local-model infra as part of this task.",
        objective="Wire the real gateway using only the services that already exist in this repo.",
        scope=(
            "This task's spec explicitly documents that Temporal, OPA, Qdrant, and "
            "local-model infra are not implemented anywhere in the current system."
        ),
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is False, result


def test_pattern2_permitted_mention_of_same_path_is_not_a_contradiction():
    """Constraints forbids writing to a path; Scope mentions the same path
    only in a permitted, non-writing context -- not a real conflict."""
    task = _base_task(
        constraints="Never write to /etc/veridian/secrets.yaml.",
        objective="Add audit logging for config file reads.",
        scope=(
            "Log the file path /etc/veridian/secrets.yaml for identification, "
            "without writing to it, purely for the audit trail."
        ),
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is False, result


def test_pattern3_conditional_qualifier_without_citation_is_not_a_contradiction():
    """Constraints forbids adding cron entries WITHOUT an approved citation;
    Scope requires a cron-driven entrypoint and adds entries only WITH a
    citation on file. The two conditions are complementary, not conflicting
    (auto_phase_continuation.py's Auditor Engine phase-3 false positive)."""
    task = _base_task(
        constraints="Do not add cron entries without an approved citation.",
        objective="Build a cron-driven entrypoint for the auditor engine.",
        scope="Add cron entries only when there is an approved citation on file for that entry.",
        successCriteria="crontab -l | grep auditor",
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is False, result


def test_pattern4_already_done_vs_where_missing_is_not_a_contradiction():
    """Constraints forbids rebuilding anything ALREADY done; Objective
    requires building the minimum wiring WHERE MISSING. "Already done" and
    "missing" are complementary conditions, not the same unconditional rule
    (auto_phase_continuation.py's Wiring Engine phase-0 false positive)."""
    task = _base_task(
        constraints="Do not rebuild or relitigate anything already done.",
        objective=(
            "Rebuild anything already done to keep the wiring current, and build "
            "the minimum real wiring where missing."
        ),
        scope="Only touch wiring paths that are currently missing.",
        successCriteria="python3 scripts/check_wiring.py --missing-only",
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is False, result


def test_scattered_unrelated_mentions_are_not_a_contradiction():
    """Real auto-generated Auditor Engine phase-8 prompt (cached at
    /tmp/auto_phase_continuation_phase8-master-orchestration-cron-wiring-
    report-software.txt): Constraints says "do not add cron entries
    without that citation". Scope separately mentions "cron" once (an
    unrelated cron-driven audit entrypoint) and "entries" once in a
    negated disclaimer ("adds no cron entries") and once in a general,
    citation-conditioned procedural sentence -- the words "cron" and
    "entries" never actually co-occur as a real, unconditional restatement
    of "add cron entries" anywhere in the requirement text; they only
    scatter across unrelated or negated sentences."""
    task = _base_task(
        constraints=(
            "Any crontab change needs an approved entry in "
            "ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml plus a matching "
            "ai-os/CRONTAB_APPROVED_SNAPSHOT.txt update -- do not add cron entries without that citation."
        ),
        objective="Master orchestration, cron wiring, report software",
        scope=(
            "- Orchestrate all domains' audit-run scripts under one cron-driven entrypoint (zero AI in the loop, per PART5)\n"
            "- Build the master report software (aggregates finding-record rows across all domains + repos)\n"
            "- File any new crontab entries through the existing Owner-approval-citation pattern "
            "(ai-os/OWNER_DECISIONS_NEEDED_*.yaml + CRONTAB_APPROVED_SNAPSHOT.txt) -- NONE filed this phase "
            "(Phase 0 adds no cron entries; deferred to whichever later phase first needs one)"
        ),
        successCriteria="python3 scripts/superboss-register.py query-knowledge phase-8",
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is False, result


def test_true_positive_unconditional_conflict_is_still_detected():
    """Constraints unconditionally forbids adding any cron entry; Objective
    unconditionally requires adding one. A real conflict -- must still be
    caught so the fix doesn't just disable the check."""
    task = _base_task(
        constraints="Must never add any cron entry under any circumstances.",
        objective="Must add a cron entry to schedule the nightly job.",
        scope="Wire the nightly job into cron.",
        successCriteria="crontab -l | grep nightly",
    )
    result = detect_field_contradiction(task)
    assert result["detected"] is True, result
    assert "cron" in result["conflictingTerm"]


# --- filePaths (UMR-20260814-132703-a1f9) ------------------------------------

def test_file_paths_valid_real_repo_relative_paths_passes():
    """A real, non-empty list of real repo-relative paths passes the whole
    validator (proving the check is actually wired into validate_tight_task,
    not just a standalone helper)."""
    task = _base_task(filePaths=["tight_task_validation.py", "test_tight_task_validation.py"])
    result = validate_tight_task(task)
    assert result["valid"] is True, result
    assert not result.get("warnings"), result


def test_file_paths_missing_warns_but_does_not_fail():
    """2026-08-14 PR#376 AUDIT:FAIL correction: an absent filePaths must NOT
    hard-fail validate_tight_task -- no real prompt generator (task-gateway.py,
    prompt_gateway/gateway.py, zai_agent_loop.py, status-remediation-tick.py,
    veridian_remediation_dispatcher.py, veridian-task-watchdog.py,
    auto_phase_continuation.py, phase-continuation-tick.py's build_prompt())
    emits a '## FILE_PATHS' section yet, and both real enforcement points
    (preflight-guard.py's check_tight_task_schema(), task-gateway.py's
    cmd_start()) read this same function -- a hard failure here would abort
    every task those generators dispatch, fleet-wide. It must still be
    surfaced, non-blocking, via `warnings`."""
    task = _base_task(filePaths=None)
    result = validate_tight_task(task)
    assert result["valid"] is True, result
    assert result["warnings"], result
    assert "File paths" in result["warnings"][0]["reason"], result


def test_file_paths_placeholder_entry_warns_but_does_not_fail():
    """Same advisory-only correction as test_file_paths_missing_warns_but_does_not_fail,
    for a filePaths list with a placeholder entry (reusing is_placeholder(),
    this module's own existing helper) -- must warn, not fail."""
    task = _base_task(filePaths=["scripts/task-gateway.py", "TBD"])
    result = validate_tight_task(task)
    assert result["valid"] is True, result
    assert result["warnings"], result
    assert "placeholder" in result["warnings"][0]["reason"], result


# --- real generator, real prompt output, full parse path (PR#376 AUDIT:FAIL) --
#
# Every other test above builds a `task` dict directly, or a hardcoded
# filePaths-bearing base task -- none of them run an actual generator's real
# prompt text through parse_labeled_fields() the way preflight-guard.py's
# check_tight_task_schema() and task-gateway.py's cmd_start() really do. That
# gap is exactly what let the original hard-required filePaths change (PR#376)
# ship without anything catching that it would abort every real dispatch.
# This test closes it: phase-continuation-tick.py's build_prompt() (a live,
# cron-driven dispatcher, INS-20260724-113032-8032) is called for real, its
# real output is fed through the real parse_labeled_fields() -> validate_tight_task()
# path, and the result must NOT be a hard failure -- only a warning.

def test_real_phase_continuation_tick_prompt_is_not_hard_rejected_for_missing_file_paths():
    prompt, title = phase_continuation_tick.build_prompt(
        initiative_name="Testing Engine",
        plan_filename="TESTING_ENGINE_PHASE_PLAN_2026-07-24.yaml",
        phase_id="phase-2",
        praw={
            "objective": "Add real regression coverage for the phase-2 validator changes.",
            "scope": "Only the validator module and its own test file.",
        },
        dep_task_ids=["task-20260810-000000-phase-1-example"],
    )
    assert title, "build_prompt() must return a real, non-empty title"

    fields = parse_labeled_fields(prompt)
    # If this is None, phase-continuation-tick.py stopped emitting labeled
    # '## HEADER' sections and this whole test (and the real preflight/submit
    # gates) silently stop validating it -- that would be its own regression.
    assert fields is not None, "real generator output must be recognized as a labeled-field prompt"
    assert "filePaths" not in fields, (
        "this test's premise is that the real generator does NOT emit FILE_PATHS yet -- "
        "if this now fails, phase-continuation-tick.py has been migrated and "
        "check_file_paths() should be flipped back to a hard failure (see this "
        "module's docstring follow-up note)."
    )

    result = validate_tight_task(fields)
    assert result["valid"] is True, (
        "a real generator's real (FILE_PATHS-less) output must not hard-fail "
        f"validate_tight_task() -- got: {result}"
    )
    assert result.get("warnings"), "missing FILE_PATHS should still be surfaced as a non-blocking warning"
    assert "File paths" in result["warnings"][0]["reason"], result
