#!/usr/bin/env python3
"""Real tests for check_live_scripts_drift.py (task-20260813-103224,
UMR-20260813-101142-5d24, SCOPE item 3). Uses real local git repos (a real
bare "origin" plus a real clone standing in for the live checkout) -- never
mocks git itself, same convention as this repo's other real-subprocess
tests (e.g. superboss-register.py's _umr_terminal_commit_exists tests)."""
import importlib.util
import os
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DRIFT_PATH = os.path.join(SCRIPTS_DIR, "check_live_scripts_drift.py")


def _load():
    spec = importlib.util.spec_from_file_location("check_live_scripts_drift", DRIFT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(argv, cwd):
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"{argv} failed in {cwd}: {r.stderr}"
    return r.stdout


def _make_origin_and_live_clone(tmp):
    origin = os.path.join(tmp, "origin.git")
    live = os.path.join(tmp, "live")
    _run(["git", "init", "--quiet", "--bare", "--initial-branch=main", origin], tmp)

    seed = os.path.join(tmp, "seed")
    _run(["git", "clone", "--quiet", origin, seed], tmp)
    _run(["git", "config", "user.email", "test@test.local"], seed)
    _run(["git", "config", "user.name", "test"], seed)
    with open(os.path.join(seed, "a.py"), "w") as f:
        f.write("print('a')\n")
    _run(["git", "add", "a.py"], seed)
    _run(["git", "commit", "--quiet", "-m", "init"], seed)
    _run(["git", "push", "--quiet", "origin", "main"], seed)

    _run(["git", "clone", "--quiet", origin, live], tmp)
    return origin, live, seed


def test_in_sync_reports_true_and_exit_0():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        _origin, live, _seed = _make_origin_and_live_clone(tmp)
        result, code = mod.check_drift(live_dir=live)
        assert result["in_sync"] is True, result
        assert result["changed_files"] == []
        assert result["commits_behind"] == 0
        assert code == 0
        assert result["current_branch"] == "main"
        assert result["on_main_branch"] is True


def test_off_main_branch_flagged_even_when_that_branch_itself_is_up_to_date():
    """Real incident this reproduces (2026-08-13, task-20260813-103224): the
    live checkout was found sitting on an open PR's own worker branch, whose
    own upstream was fully caught up -- a naive same-branch-only check would
    have missed it entirely. on_main_branch must be False regardless."""
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        origin, live, seed = _make_origin_and_live_clone(tmp)
        _run(["git", "checkout", "--quiet", "-b", "worker/some-task"], seed)
        with open(os.path.join(seed, "c.py"), "w") as f:
            f.write("print('c')\n")
        _run(["git", "add", "c.py"], seed)
        _run(["git", "commit", "--quiet", "-m", "worker commit"], seed)
        _run(["git", "push", "--quiet", "origin", "worker/some-task"], seed)
        _run(["git", "fetch", "--quiet", "origin"], live)
        _run(["git", "checkout", "--quiet", "-b", "worker/some-task", "origin/worker/some-task"], live)

        result, code = mod.check_drift(live_dir=live)
        assert result["current_branch"] == "worker/some-task"
        assert result["on_main_branch"] is False
        assert result["in_sync"] is False
        assert code == 1


def test_drift_reports_false_boolean_plus_real_file_list_and_exit_1():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        _origin, live, seed = _make_origin_and_live_clone(tmp)
        with open(os.path.join(seed, "b.py"), "w") as f:
            f.write("print('b')\n")
        _run(["git", "add", "b.py"], seed)
        _run(["git", "commit", "--quiet", "-m", "add b.py"], seed)
        _run(["git", "push", "--quiet", "origin", "main"], seed)

        result, code = mod.check_drift(live_dir=live)
        assert result["in_sync"] is False, result
        assert result["commits_behind"] == 1
        assert result["commits_ahead"] == 0
        paths = [c["path"] for c in result["changed_files"]]
        assert paths == ["b.py"], result
        assert result["changed_files"][0]["status"] == "A"
        assert code == 1


def test_fetch_failure_fails_closed_never_reports_in_sync():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        # Not a git repo at all -> git fetch fails -> must fail closed.
        not_a_repo = os.path.join(tmp, "not-a-repo")
        os.makedirs(not_a_repo)
        result, code = mod.check_drift(live_dir=not_a_repo)
        assert result["in_sync"] is False
        assert code == 2
        assert "error" in result


if __name__ == "__main__":
    test_in_sync_reports_true_and_exit_0()
    test_off_main_branch_flagged_even_when_that_branch_itself_is_up_to_date()
    test_drift_reports_false_boolean_plus_real_file_list_and_exit_1()
    test_fetch_failure_fails_closed_never_reports_in_sync()
    print("OK")
