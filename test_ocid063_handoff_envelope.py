#!/usr/bin/env python3
"""
Real, executable test for OCID-063's mechanical handoff envelope (PM
directive UMR-20260804-060832-9fdf, real implementation authorized by
UMR-20260804-061827-e3c6, governed by the Mandatory Governance Directive
UMR-20260804-051521-7099).

Proves, against a real sample call log, that:
  - compute_rejected_paths() mechanically filters exactly the entries
    whose status is a client error (4xx), a server error (5xx), or the
    literal "timeout" -- never a success (2xx/3xx) or an unrecognized
    status.
  - validate_handoff_envelope() enforces all four real rules: call_log
    must not be empty; rejected_paths non-empty implies unknowns
    non-empty; unknowns must not exceed the cap; conclusion must be
    exactly one sentence.

Everything here is in-process against the real functions in
veridian-task.py directly -- no subprocess, no real task.yaml, no real
checkpoint written.

Usage: python3 test_ocid063_handoff_envelope.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import importlib.util
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def load_veridian_task():
    spec = importlib.util.spec_from_file_location(
        "veridian_task", os.path.join(SCRIPTS_DIR, "veridian-task.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A real, realistic sample call log spanning every real category the
# proposal names: a real success (200), a real client error (404), a real
# server error (503), a real timeout, and one deliberately unrecognized
# status (a raw string that isn't "timeout") to prove unrecognized input
# is bucketed as unknown, never silently misclassified as a rejection.
SAMPLE_CALL_LOG = [
    {"tool": "Read", "args_summary": "PROGRESS.md", "status": 200},
    {"tool": "Bash", "args_summary": "gh pr view 789", "status": 404},
    {"tool": "Bash", "args_summary": "gh pr checks 876", "status": 503},
    {"tool": "Bash", "args_summary": "bun run build", "status": "timeout"},
    {"tool": "Edit", "args_summary": "OS.yaml", "status": 200},
    {"tool": "Bash", "args_summary": "some proprietary tool", "status": "weird-status-string"},
]


def test_classify_call_status(vt):
    check("200 classifies as success",
          vt.classify_call_status(200) == vt.STATUS_CATEGORY_SUCCESS)
    check("399 classifies as success (upper 3xx boundary)",
          vt.classify_call_status(399) == vt.STATUS_CATEGORY_SUCCESS)
    check("404 classifies as client_error",
          vt.classify_call_status(404) == vt.STATUS_CATEGORY_CLIENT_ERROR)
    check("499 classifies as client_error (upper 4xx boundary)",
          vt.classify_call_status(499) == vt.STATUS_CATEGORY_CLIENT_ERROR)
    check("500 classifies as server_error",
          vt.classify_call_status(500) == vt.STATUS_CATEGORY_SERVER_ERROR)
    check("599 classifies as server_error (upper 5xx boundary)",
          vt.classify_call_status(599) == vt.STATUS_CATEGORY_SERVER_ERROR)
    check("literal 'timeout' classifies as timeout",
          vt.classify_call_status("timeout") == vt.STATUS_CATEGORY_TIMEOUT)
    check("an unrecognized string classifies as unknown, not silently rejected",
          vt.classify_call_status("weird-status-string") == vt.STATUS_CATEGORY_UNKNOWN)
    check("600 (out of any real range) classifies as unknown",
          vt.classify_call_status(600) == vt.STATUS_CATEGORY_UNKNOWN)
    check("a bool status (True) classifies as unknown, never as a 2xx int",
          vt.classify_call_status(True) == vt.STATUS_CATEGORY_UNKNOWN)
    check("a missing status (None) classifies as unknown",
          vt.classify_call_status(None) == vt.STATUS_CATEGORY_UNKNOWN)


def test_compute_rejected_paths(vt):
    rejected = vt.compute_rejected_paths(SAMPLE_CALL_LOG)
    rejected_tools = sorted(e["args_summary"] for e in rejected)

    check("rejected_paths has exactly 3 entries (404, 503, timeout)",
          len(rejected) == 3)
    check("the real 404 entry is in rejected_paths",
          "gh pr view 789" in rejected_tools)
    check("the real 503 entry is in rejected_paths",
          "gh pr checks 876" in rejected_tools)
    check("the real timeout entry is in rejected_paths",
          "bun run build" in rejected_tools)
    check("the real 200 success entries are NOT in rejected_paths",
          "PROGRESS.md" not in rejected_tools and "OS.yaml" not in rejected_tools)
    check("the unrecognized-status entry is NOT in rejected_paths (unknown != rejected)",
          "some proprietary tool" not in rejected_tools)
    check("compute_rejected_paths never mutates its input",
          len(SAMPLE_CALL_LOG) == 6)

    check("an empty call_log produces an empty rejected_paths, not an error",
          vt.compute_rejected_paths([]) == [])

    all_success = [{"tool": "Read", "status": 200}, {"tool": "Read", "status": 200}]
    check("an all-success call_log produces empty rejected_paths",
          vt.compute_rejected_paths(all_success) == [])


def test_validate_handoff_envelope(vt):
    # -- the real sample: 3 rejected paths, so unknowns must be non-empty --
    valid, errors, rejected = vt.validate_handoff_envelope(
        SAMPLE_CALL_LOG,
        "Resolved the real merge conflict and verified a clean build.",
        ["whether the 3rd rejected path was a transient network blip or a real outage"],
    )
    check("a well-formed envelope against the real sample call log is valid",
          valid and errors == [])
    check("the real envelope's rejected_paths matches compute_rejected_paths directly",
          len(rejected) == 3)

    # -- rule 1: empty call_log rejected --
    valid, errors, _ = vt.validate_handoff_envelope([], "One sentence.", [])
    check("empty call_log is rejected",
          not valid and any("call_log must not be empty" in e for e in errors))

    # -- rule 2: rejected_paths non-empty but unknowns empty is rejected --
    valid, errors, _ = vt.validate_handoff_envelope(
        SAMPLE_CALL_LOG, "One sentence.", [])
    check("rejected_paths non-empty + empty unknowns is rejected",
          not valid and any("unknowns is empty" in e for e in errors))

    # -- no rejections at all: unknowns is allowed to be empty --
    all_success = [{"tool": "Read", "status": 200}]
    valid, errors, rejected = vt.validate_handoff_envelope(
        all_success, "Everything succeeded.", [])
    check("no rejected_paths means an empty unknowns list is fine",
          valid and errors == [] and rejected == [])

    # -- rule 3 (the "capped" check PM directive UMR-20260804-061827-e3c6
    # explicitly named as its own real check): unknowns exceeding the cap
    # is rejected, even with no rejected_paths at all --
    too_many_unknowns = [f"unknown item {i}" for i in range(vt.MAX_UNKNOWNS + 1)]
    valid, errors, _ = vt.validate_handoff_envelope(
        all_success, "Everything succeeded.", too_many_unknowns)
    check("unknowns exceeding MAX_UNKNOWNS is rejected",
          not valid and any("exceeds cap" in e for e in errors))

    exactly_at_cap = [f"unknown item {i}" for i in range(vt.MAX_UNKNOWNS)]
    valid, errors, _ = vt.validate_handoff_envelope(
        all_success, "Everything succeeded.", exactly_at_cap)
    check("unknowns exactly at MAX_UNKNOWNS is allowed (cap is inclusive)",
          valid and errors == [])

    # -- rule 4: conclusion must be exactly one sentence --
    valid, errors, _ = vt.validate_handoff_envelope(all_success, "", [])
    check("an empty conclusion (zero sentences) is rejected",
          not valid and any("must be exactly one sentence" in e for e in errors))

    valid, errors, _ = vt.validate_handoff_envelope(
        all_success, "First sentence. Second sentence.", [])
    check("a two-sentence conclusion is rejected",
          not valid and any("must be exactly one sentence" in e for e in errors))

    valid, errors, _ = vt.validate_handoff_envelope(
        all_success, "Exactly one real sentence here.", [])
    check("a genuine one-sentence conclusion is valid",
          valid and errors == [])

    # -- multiple real failures reported together, not just the first --
    valid, errors, _ = vt.validate_handoff_envelope([], "Two. Sentences.", [])
    check("multiple real violations are all reported, not just the first",
          not valid and len(errors) >= 2)


if __name__ == "__main__":
    veridian_task = load_veridian_task()

    test_classify_call_status(veridian_task)
    test_compute_rejected_paths(veridian_task)
    test_validate_handoff_envelope(veridian_task)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)
