"""Regression tests for tight_task_validation.py's contradiction detector.

Covers 4 real false-positive patterns hit this session (2026-07-25) by
manually written and scripts/auto_phase_continuation.py-generated prompts,
plus a true-positive case proving the detector still catches a genuine
conflict. Run with: python3 -m pytest scripts/ -k tight_task_validation
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from tight_task_validation import detect_field_contradiction, validate_tight_task  # noqa: E402


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


def test_file_paths_missing_fails():
    """An absent filePaths (the pre-existing gap this closes: no field today
    requires an explicit file-path list) must fail validate_tight_task with
    a real, actionable reason -- not silently pass."""
    task = _base_task(filePaths=None)
    result = validate_tight_task(task)
    assert result["valid"] is False, result
    assert "File paths" in result["reason"], result


def test_file_paths_placeholder_entry_fails():
    """A filePaths list with a placeholder entry (reusing is_placeholder(),
    this module's own existing helper) must fail, same as any other
    placeholder value elsewhere in this validator."""
    task = _base_task(filePaths=["scripts/task-gateway.py", "TBD"])
    result = validate_tight_task(task)
    assert result["valid"] is False, result
    assert "placeholder" in result["reason"], result
