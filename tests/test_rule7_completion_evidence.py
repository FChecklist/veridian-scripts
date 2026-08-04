#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 7
(UMR-20260804-180711-7f96, UMR-20260804-205741-cf3f, citing OCID-068's own
UMR-20260804-170055-a069): "implementation completion, an implementation is
not complete until real code, a real database change, a real test, a real
artifact, a real pull request, and real evidence all match, never declare
complete from narration alone. On completion, return the real evidence, the
real files modified, the real database changes, the UMR, the OCID, the PR,
the commit, the real test results, any open items, any blockers, and the
real next action, no assumptions, no narration, no estimates."

Covers validate_completion_evidence() (pure function) and cmd_checkpoint's
own --evidence-json wiring, in-process against the real functions in
veridian-task.py directly -- no subprocess, no real task.yaml, no real
checkpoint written to the real production /opt/veridian/ai-os/tasks tree
(same isolation convention test_ocid063_handoff_envelope.py already uses:
the one test that reaches cmd_checkpoint's real body monkey-patches AI_OS to
an isolated scratch dir first).
"""
import argparse
import importlib.util
import json as json_module
import os
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_veridian_task():
    spec = importlib.util.spec_from_file_location(
        "veridian_task_r7", os.path.join(SCRIPTS_DIR, "veridian-task.py"))
    vt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vt)
    return vt


_REAL_EVIDENCE = {
    "pr_url": "https://github.com/FChecklist/veridian-scripts/pull/26",
    "commit_sha": "29a153bb7a51a12f1868a372bc7e20b90818b152",
    "test_results": "7/7 tests passed, tests/test_umr_reuse_on_resume.py",
    "umr_id": "UMR-20260804-194355-be9c",
    "next_action": "none -- Rule 1 genuinely merged",
    "open_items": [],
    "blockers": [],
}


def test_valid_evidence_passes():
    vt = _load_veridian_task()
    valid, errors = vt.validate_completion_evidence(dict(_REAL_EVIDENCE))
    assert valid is True, errors
    print("PASS: test_valid_evidence_passes")


def test_missing_required_field_rejected():
    vt = _load_veridian_task()
    for field in ("pr_url", "commit_sha", "test_results", "umr_id", "next_action"):
        evidence = dict(_REAL_EVIDENCE)
        del evidence[field]
        valid, errors = vt.validate_completion_evidence(evidence)
        assert valid is False, (field, errors)
        assert any(field in e for e in errors), (field, errors)
    print("PASS: test_missing_required_field_rejected")


def test_narration_placeholders_rejected():
    """The whole point of this check: a vague 'done'-style self-report must
    never pass as real evidence."""
    vt = _load_veridian_task()
    for placeholder in ("N/A", "n/a", "None", "TBD", "  ", "", "unknown"):
        evidence = dict(_REAL_EVIDENCE)
        evidence["test_results"] = placeholder
        valid, errors = vt.validate_completion_evidence(evidence)
        assert valid is False, (placeholder, errors)
        assert any("test_results" in e for e in errors), (placeholder, errors)
    print("PASS: test_narration_placeholders_rejected")


def test_malformed_pr_url_rejected():
    vt = _load_veridian_task()
    for bad_url in ("not a url", "https://example.com/pull/26", "github.com/FChecklist/veridian-scripts/pull/26"):
        evidence = dict(_REAL_EVIDENCE)
        evidence["pr_url"] = bad_url
        valid, errors = vt.validate_completion_evidence(evidence)
        assert valid is False, (bad_url, errors)
    print("PASS: test_malformed_pr_url_rejected")


def test_malformed_commit_sha_rejected():
    vt = _load_veridian_task()
    for bad_sha in ("not-hex-zzz", "abc", "G1234567"):
        evidence = dict(_REAL_EVIDENCE)
        evidence["commit_sha"] = bad_sha
        valid, errors = vt.validate_completion_evidence(evidence)
        assert valid is False, (bad_sha, errors)
    print("PASS: test_malformed_commit_sha_rejected")


def test_malformed_umr_id_rejected():
    vt = _load_veridian_task()
    evidence = dict(_REAL_EVIDENCE)
    evidence["umr_id"] = "not-a-real-umr-id"
    valid, errors = vt.validate_completion_evidence(evidence)
    assert valid is False, errors
    print("PASS: test_malformed_umr_id_rejected")


def test_open_items_and_blockers_must_be_real_lists():
    vt = _load_veridian_task()
    evidence = dict(_REAL_EVIDENCE)
    del evidence["open_items"]
    valid, errors = vt.validate_completion_evidence(evidence)
    assert valid is False and any("open_items" in e for e in errors), errors

    evidence2 = dict(_REAL_EVIDENCE)
    evidence2["blockers"] = "no blockers"  # narrated string, not a real list
    valid2, errors2 = vt.validate_completion_evidence(evidence2)
    assert valid2 is False and any("blockers" in e for e in errors2), errors2
    print("PASS: test_open_items_and_blockers_must_be_real_lists")


def test_empty_open_items_and_blockers_lists_are_valid():
    """An empty list is a real, honest 'genuinely nothing open' state -- not
    itself a violation."""
    vt = _load_veridian_task()
    valid, errors = vt.validate_completion_evidence(dict(_REAL_EVIDENCE))
    assert valid is True, errors
    print("PASS: test_empty_open_items_and_blockers_lists_are_valid")


def test_db_changes_optional_but_narration_rejected_if_present():
    vt = _load_veridian_task()
    evidence = dict(_REAL_EVIDENCE)
    # Omitted entirely: valid (no claim made either way).
    valid, errors = vt.validate_completion_evidence(evidence)
    assert valid is True, errors

    # Explicit honest "none": valid, not treated as a placeholder.
    evidence["db_changes"] = "none"
    valid2, errors2 = vt.validate_completion_evidence(evidence)
    assert valid2 is True, errors2

    # A real, specific claim: valid.
    evidence["db_changes"] = "added ocid_artifact_links table via _ensure_ocid_artifact_links_table()"
    valid3, errors3 = vt.validate_completion_evidence(evidence)
    assert valid3 is True, errors3

    # A vague placeholder MASQUERADING as a db_changes claim: rejected.
    evidence["db_changes"] = "TBD"
    valid4, errors4 = vt.validate_completion_evidence(evidence)
    assert valid4 is False and any("db_changes" in e for e in errors4), errors4
    print("PASS: test_db_changes_optional_but_narration_rejected_if_present")


def test_non_dict_evidence_rejected():
    vt = _load_veridian_task()
    for bad in (None, "a string", ["a", "list"], 42):
        valid, errors = vt.validate_completion_evidence(bad)
        assert valid is False, (bad, errors)
    print("PASS: test_non_dict_evidence_rejected")


def test_cmd_checkpoint_rejects_bad_evidence_json_before_any_write():
    """End-to-end: cmd_checkpoint's own --evidence-json wiring rejects a
    failing evidence file via sys.exit(1) BEFORE the task lock is ever
    taken -- proven by pointing task_id at a real, deliberately
    nonexistent task in an isolated scratch AI_OS dir; if the rejection
    happened after the lock/load, this would instead raise
    FileNotFoundError from load_task(), not a clean sys.exit(1)."""
    vt = _load_veridian_task()
    with tempfile.TemporaryDirectory() as scratch_dir:
        bad_evidence_path = os.path.join(scratch_dir, "bad_evidence.json")
        with open(bad_evidence_path, "w") as f:
            json_module.dump({"pr_url": "not-a-real-url"}, f)

        args = argparse.Namespace(
            task_id="rule7-regression-test-nonexistent-task",
            status="completed", note=None, auto=False,
            handoff_envelope=None, evidence_json=bad_evidence_path,
        )
        try:
            vt.cmd_checkpoint(args)
            assert False, "expected sys.exit(1), checkpoint proceeded instead"
        except SystemExit as e:
            assert e.code == 1, e.code
        print("PASS: test_cmd_checkpoint_rejects_bad_evidence_json_before_any_write")


def test_cmd_checkpoint_ignores_evidence_json_for_non_completed_status():
    """--evidence-json is only enforced for --status completed -- a
    checkpoint at any other status must be unaffected, even with a
    deliberately invalid evidence file."""
    vt = _load_veridian_task()
    real_ai_os = vt.AI_OS
    scratch_dir = tempfile.mkdtemp(prefix="rule7-non-completed-test-")
    task_id = "rule7-regression-scratch-task-no-yaml"
    os.makedirs(os.path.join(scratch_dir, "tasks", task_id))
    try:
        vt.AI_OS = scratch_dir
        bad_evidence_path = os.path.join(scratch_dir, "bad_evidence.json")
        with open(bad_evidence_path, "w") as f:
            json_module.dump({"pr_url": "not-a-real-url"}, f)
        args = argparse.Namespace(
            task_id=task_id, status="in_progress", note=None, auto=False,
            handoff_envelope=None, evidence_json=bad_evidence_path,
        )
        try:
            vt.cmd_checkpoint(args)
            assert False, "expected FileNotFoundError from load_task(), got a clean return"
        except FileNotFoundError:
            print("PASS: test_cmd_checkpoint_ignores_evidence_json_for_non_completed_status "
                  "(reached real task lookup, not rejected by evidence validation)")
    finally:
        vt.AI_OS = real_ai_os


def test_cmd_checkpoint_never_persists_evidence_for_non_completed_status_real_task():
    """Round-1 review finding (PR #35): completion_evidence used to persist
    unconditionally whenever --evidence-json was supplied, regardless of
    --status -- bypassing validate_completion_evidence() (which only runs
    for --status completed) and allowing a later non-completed checkpoint
    to silently overwrite a previously-validated completed evidence record
    with unvalidated data. This is the real end-to-end regression test the
    round-1 review named as missing: a genuinely-invalid --evidence-json,
    against a REAL task.yaml (not a missing one that short-circuits via
    FileNotFoundError before the persistence line is ever reached), at a
    non-'completed' status, must reach save_task() successfully and must
    NOT have written completion_evidence onto the task."""
    vt = _load_veridian_task()
    real_ai_os = vt.AI_OS
    scratch_dir = tempfile.mkdtemp(prefix="rule7-real-persist-test-")
    task_id = "rule7-real-task-with-yaml"
    task_dir = os.path.join(scratch_dir, "tasks", task_id)
    os.makedirs(task_dir)
    real_task_doc = {
        "id": task_id, "title": "test", "status": "in_progress",
        "repo": "veridian-scripts", "branch": f"worker/{task_id}",
        "created_at": "2026-08-04T00:00:00+00:00",
        "service": f"veridian-worker@{task_id}.service", "task_dir": task_dir,
        "workspace": os.path.join(task_dir, "workspace"),  # deliberately absent -- os.path.isdir() guard skips git status
        "checkpoints": [], "files_modified": [],
    }
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        vt.yaml.safe_dump(real_task_doc, f)

    bad_evidence_path = os.path.join(scratch_dir, "bad_evidence.json")
    with open(bad_evidence_path, "w") as f:
        json_module.dump({"pr_url": "not-a-real-url"}, f)  # genuinely invalid: missing required fields

    # sync_controller_entry()'s own CONTROLLER path is a module-level
    # constant fixed at import time from the REAL AI_OS -- reassigning
    # vt.AI_OS (as done below, which correctly redirects load_task/
    # save_task, both of which build their path from the global AI_OS at
    # CALL time) does not redirect it, so calling the real function here
    # would write a fake entry into the real, live production
    # CONTROLLER.yaml. Neutralized for this test only, restored after --
    # same real risk test_ocid063_handoff_envelope.py's own comment already
    # documents and deliberately avoids a different way (stopping short of
    # this call entirely via a real FileNotFoundError).
    real_sync_controller_entry = vt.sync_controller_entry
    vt.sync_controller_entry = lambda task: None
    try:
        vt.AI_OS = scratch_dir
        args = argparse.Namespace(
            task_id=task_id, status="in_progress", note=None, auto=False,
            handoff_envelope=None, evidence_json=bad_evidence_path,
        )
        vt.cmd_checkpoint(args)  # must NOT raise/exit -- invalid evidence is irrelevant at this status

        with open(os.path.join(task_dir, "task.yaml")) as f:
            saved = vt.yaml.safe_load(f)
        assert "completion_evidence" not in saved, (
            f"Rule 7 regression: invalid, unvalidated evidence was persisted onto a "
            f"non-completed checkpoint: {saved.get('completion_evidence')!r}"
        )
        assert saved["status"] == "in_progress", saved
    finally:
        vt.AI_OS = real_ai_os
        vt.sync_controller_entry = real_sync_controller_entry
    print("PASS: test_cmd_checkpoint_never_persists_evidence_for_non_completed_status_real_task")


def test_db_changes_none_case_insensitive():
    """Round-1 review finding (PR #35, minor): 'None' (capitalized) must be
    accepted the same as 'none' (lowercase) as an honest claim of absence."""
    vt = _load_veridian_task()
    for honest_none in ("none", "None", "NONE", "  none  ", "No schema or data changes", "NO SCHEMA OR DATA CHANGES"):
        evidence = dict(_REAL_EVIDENCE)
        evidence["db_changes"] = honest_none
        valid, errors = vt.validate_completion_evidence(evidence)
        assert valid is True, (honest_none, errors)
    print("PASS: test_db_changes_none_case_insensitive")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
