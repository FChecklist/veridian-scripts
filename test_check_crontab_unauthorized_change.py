#!/usr/bin/env python3
"""
Real, executable test for check_crontab_unauthorized_change() (governance
item 50). Never touches the real live crontab or the real
CRONTAB_APPROVED_SNAPSHOT.txt / OWNER_DECISIONS_NEEDED_2026-07-23.yaml --
everything is a temp file, and crontab_cmd is overridden to a fake command
instead of the real `crontab -l`.

Usage: python3 test_check_crontab_unauthorized_change.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# preflight-guard.py has a hyphen -- not a valid module name, load by path instead.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "preflight_guard", os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight-guard.py")
)
preflight_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight_guard)


LIVE_CRONTAB_FAKE = "0 3 * * * /opt/veridian/scripts/run-logged.sh \"sync-verdian-ai-data\" ...\n"
CHANGED_CRONTAB_FAKE = LIVE_CRONTAB_FAKE + "* * * * * /tmp/some-newly-added-unauthorized-job.sh\n"


def run_check(task_dir, snapshot_content, live_crontab_content, decisions_content=None):
    """Runs check_crontab_unauthorized_change with everything faked/temp, captures
    whether it called fail() (sys.exit(1) + JSON on stdout) or returned normally."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = os.path.join(tmp, "CRONTAB_APPROVED_SNAPSHOT.txt")
        with open(snapshot_path, "w") as f:
            f.write(snapshot_content)

        decisions_path = os.path.join(tmp, "OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
        with open(decisions_path, "w") as f:
            f.write(decisions_content or "decisions: []\n")

        # Fake "crontab -l" via a tiny shell one-liner instead of the real binary.
        fake_cmd = ["/bin/sh", "-c", f"printf %s {shell_quote(live_crontab_content)}"]

        stdout_capture = io.StringIO()
        exited_code = {"code": None}
        real_exit = sys.exit

        def fake_exit(code=0):
            exited_code["code"] = code
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            with contextlib.redirect_stdout(stdout_capture):
                try:
                    preflight_guard.check_crontab_unauthorized_change(
                        task_dir,
                        snapshot_path=snapshot_path,
                        decisions_path=decisions_path,
                        crontab_cmd=fake_cmd,
                    )
                    returned_normally = True
                except SystemExit:
                    returned_normally = False
        finally:
            sys.exit = real_exit

        return returned_normally, exited_code["code"], stdout_capture.getvalue()


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def make_task_dir(prompt_text):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "prompt.txt"), "w") as f:
        f.write(prompt_text)
    return tmp


def main():
    failures = []

    # Test 1: crontab UNCHANGED from snapshot -> must pass clean (no fail(), no sys.exit).
    task_dir = make_task_dir("## OBJECTIVE\nsome unrelated task\n")
    try:
        returned_normally, code, out = run_check(
            task_dir, LIVE_CRONTAB_FAKE, LIVE_CRONTAB_FAKE
        )
        if not returned_normally:
            failures.append(f"Test 1 (unchanged crontab) FAILED: expected pass-clean, "
                             f"got sys.exit({code}) stdout={out!r}")
        else:
            print("Test 1 (unchanged crontab -> pass clean): PASS")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    # Test 2: crontab CHANGED, prompt.txt has NO citation -> must fail closed.
    task_dir = make_task_dir("## OBJECTIVE\nsome unrelated task with no owner citation at all\n")
    try:
        returned_normally, code, out = run_check(
            task_dir, LIVE_CRONTAB_FAKE, CHANGED_CRONTAB_FAKE
        )
        if returned_normally or code != 1:
            failures.append(f"Test 2 (changed crontab, no citation) FAILED: expected fail-closed "
                             f"(sys.exit(1)), got returned_normally={returned_normally} code={code}")
        else:
            parsed = json.loads(out)
            if parsed.get("proceed") is not False or parsed.get("reason") != "crontab_unauthorized_change":
                failures.append(f"Test 2 FAILED: wrong fail payload: {parsed}")
            else:
                print("Test 2 (changed crontab, no citation -> fail closed): PASS")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    # Test 3: crontab CHANGED, prompt.txt cites an id but that id is NOT approved
    # in the (fake) decisions file -> must still fail closed (proves it doesn't
    # trust the prompt's own unverified claim).
    task_dir = make_task_dir(
        "## KNOWN_CONTEXT\n"
        "OWNER_DECISIONS_NEEDED_2026-07-23.yaml entry id=fake-unapproved-id status=approved\n"
    )
    decisions_yaml = (
        "decisions:\n"
        "- id: fake-unapproved-id\n"
        "  status: awaiting_owner_decision\n"
    )
    try:
        returned_normally, code, out = run_check(
            task_dir, LIVE_CRONTAB_FAKE, CHANGED_CRONTAB_FAKE, decisions_yaml
        )
        if returned_normally or code != 1:
            failures.append(f"Test 3 (unverified/false citation) FAILED: expected fail-closed, "
                             f"got returned_normally={returned_normally} code={code}")
        else:
            print("Test 3 (citation present but NOT actually approved in real file -> fail closed): PASS")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    # Test 4: crontab CHANGED, prompt.txt cites an id that IS approved in the
    # (fake) decisions file -> must pass clean.
    task_dir = make_task_dir(
        "## KNOWN_CONTEXT\n"
        "OWNER_DECISIONS_NEEDED_2026-07-23.yaml entry id=real-approved-change status=approved\n"
    )
    decisions_yaml = (
        "decisions:\n"
        "- id: real-approved-change\n"
        "  status: approved\n"
    )
    try:
        returned_normally, code, out = run_check(
            task_dir, LIVE_CRONTAB_FAKE, CHANGED_CRONTAB_FAKE, decisions_yaml
        )
        if not returned_normally:
            failures.append(f"Test 4 (verified approved citation) FAILED: expected pass-clean, "
                             f"got sys.exit({code}) stdout={out!r}")
        else:
            print("Test 4 (changed crontab, verified-approved citation -> pass clean): PASS")
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)

    if failures:
        print("\n=== TEST FAILURES ===")
        for f in failures:
            print(f)
        sys.exit(1)
    print("\nAll tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
