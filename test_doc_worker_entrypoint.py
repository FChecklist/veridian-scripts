"""Real subprocess tests for doc-worker-entrypoint.sh.

The script is executed the natural way: `bash doc-worker-entrypoint.sh
<task_id>`, with TASK_DIR under a real temp directory and WORKSPACE a real
temp git repo (with a real local bare "origin" remote, so `git push` really
runs, against localhost disk only -- never a real network call).

True external boundaries are stubbed via fake executables placed first on
PATH, each logging its real argv to a file the tests assert on:

  * `claude`     -- the real Claude Code subscription CLI. Controlled via
                     env vars (CLAUDE_STUB_EXIT_CODE / CLAUDE_STUB_SLEEP /
                     CLAUDE_STUB_WRITE_FILE) so it can simulate success,
                     failure, or a wall-clock timeout, and optionally write
                     a real file into the workspace so the git commit/push
                     logic downstream has something real to commit.
  * `systemctl`  -- logs argv only; a real `systemctl --user disable` here
                     would mutate this box's real user systemd state for a
                     unit name derived from a fake task id, so it is never
                     let through for real.
  * `python3`    -- passthrough to the REAL interpreter for everything
                     (including `-c` / `-` snippets that only parse
                     task.yaml or hash worker.log -- pure local logic, left
                     real) EXCEPT four scripts whose real, hard-coded
                     absolute paths (/opt/veridian/scripts/veridian-task.py,
                     preflight-guard.py, credit-accountant.py,
                     superboss-register.py) point at the live production
                     system (the live task registry, the live credit
                     ledger, and -- via automation_rule_engine.py's own
                     hard-coded DB_PATH -- the live
                     /opt/veridian/ai-os/memory/superboss-register.sqlite,
                     none of which have a test-time override available).
                     Those four are intercepted, logged, and answered with
                     a controlled response instead.

`bash /opt/veridian/scripts/quality-gate.sh` (also a hard-coded live path,
not stubbed) is deliberately allowed to run for real: read, it is a pure
function of $WORKSPACE's contents, and with no package.json/pyproject.toml/
requirements.txt present (true for every WORKSPACE these tests create) its
own documented behavior is a clean, side-effect-free no-op -- confirmed by
reading its detection branch before relying on it.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "doc-worker-entrypoint.sh"

FAKE_PYTHON3 = """\
import json
import os
import sys

log_file = os.environ["FAKE_PY3_LOG"]
with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")

INTERCEPT_SUFFIXES = (
    "veridian-task.py", "preflight-guard.py", "credit-accountant.py", "superboss-register.py",
)

if len(sys.argv) >= 2 and sys.argv[1].endswith(INTERCEPT_SUFFIXES):
    target = next(s for s in INTERCEPT_SUFFIXES if sys.argv[1].endswith(s))
    argv = sys.argv[2:]

    if target == "veridian-task.py" and argv[:1] == ["checkpoint"]:
        sys.exit(int(os.environ.get("FAKE_CHECKPOINT_EXIT", "0")))
    if target == "veridian-task.py" and argv[:1] == ["resume-context"]:
        sys.stdout.write(os.environ.get("FAKE_RESUME_CONTEXT", "no prior context"))
        sys.exit(0)
    if target == "veridian-task.py" and argv[:1] == ["record-usage"]:
        sys.exit(0)
    if target == "preflight-guard.py":
        sys.stdout.write(os.environ.get("FAKE_PREFLIGHT_STDOUT", "{}"))
        sys.exit(int(os.environ.get("FAKE_PREFLIGHT_EXIT", "0")))
    if target == "credit-accountant.py":
        sys.exit(0)
    if target == "superboss-register.py":
        sys.exit(0)
    sys.exit(0)

# Any other python3 invocation (yaml parsing, hashlib snippets, `-c`, `-`
# heredocs inside quality-gate.sh) is pure local logic -- run for real.
real_python3 = os.environ["REAL_PYTHON3"]
os.execv(real_python3, [real_python3] + sys.argv[1:])
"""

FAKE_CLAUDE = """\
import json
import os
import sys
import time

log_file = os.environ["FAKE_CLAUDE_LOG"]
with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")

write_file = os.environ.get("CLAUDE_STUB_WRITE_FILE")
if write_file:
    with open(write_file, "w", encoding="utf-8") as f:
        f.write("real content written by the fake claude stub\\n")

sleep_s = float(os.environ.get("CLAUDE_STUB_SLEEP", "0"))
if sleep_s:
    time.sleep(sleep_s)

result_text = os.environ.get("CLAUDE_STUB_RESULT_TEXT", "did some doc work")
print(json.dumps({"result": result_text, "total_cost_usd": 0.05}))
sys.exit(int(os.environ.get("CLAUDE_STUB_EXIT_CODE", "0")))
"""

FAKE_LOGGER_ONLY = """\
#!/bin/bash
echo "$@" >> "{log_file}"
exit {exit_code}
"""


def _write_py_stub(bin_dir: Path, name: str, body: str):
    stub = bin_dir / name
    stub.write_text(f"#!{sys.executable}\n" + body)
    stub.chmod(0o755)


def _write_bash_stub(bin_dir: Path, name: str, log_file: Path, exit_code: int = 0):
    stub = bin_dir / name
    stub.write_text(FAKE_LOGGER_ONLY.format(log_file=log_file, exit_code=exit_code))
    stub.chmod(0o755)


def _init_git_repo_with_remote(workspace: Path, remote_bare: Path, branch: str):
    workspace.mkdir(parents=True, exist_ok=True)
    remote_bare.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-b", branch, str(remote_bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", branch, str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
    (workspace / "README.md").write_text("initial\n")
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "remote", "add", "origin", str(remote_bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "push", "-u", "origin", branch], check=True, capture_output=True)


def _setup_task(tmp_path: Path, task_id: str, checkpoints=None, branch="worker/test-branch"):
    ai_os = tmp_path / "ai-os"
    task_dir = ai_os / "tasks" / task_id
    task_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    _init_git_repo_with_remote(workspace, remote_bare, branch)

    task_yaml = {
        "workspace": str(workspace),
        "branch": branch,
        "checkpoints": checkpoints or [],
    }
    import yaml
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task_yaml))
    (task_dir / "prompt.txt").write_text("Document the external system.")
    return task_dir, workspace, remote_bare


def _base_env(tmp_path: Path, bin_dir: Path):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["HOME"] = str(tmp_path)
    env["REAL_PYTHON3"] = sys.executable
    return env


class _ScriptResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_script(task_id: str, ai_os_root: Path, env: dict):
    # doc-worker-entrypoint.sh hard-codes TASK_DIR under
    # /opt/veridian/ai-os/tasks/$TASK_ID -- it is NOT relocatable via env.
    # We therefore always operate against the REAL /opt/veridian/ai-os/tasks
    # path, but scope every test to a unique, disposable, pytest-only
    # task_id directory that is created fresh and removed after the test,
    # never touching any real task's directory.
    #
    # GENUINE BUG (doc-worker-entrypoint.sh:89-96): the periodic-checkpoint
    # background loop is started as `( while true; do sleep 300; ...; done ) &`
    # and `kill $CHECKPOINT_PID` (its EXIT trap, and the explicit kill+wait
    # near the end of the script) only signals the subshell wrapper, not the
    # `sleep 300` it is currently blocked in -- SIGTERM to a bash process
    # blocked in `wait` for a foreground child does not propagate to that
    # child, so `sleep 300` is orphaned (reparented to init) and keeps running
    # for up to 5 real minutes after the script itself exits, still holding
    # its inherited stdout/stderr file descriptors open. A caller that reads
    # the script's output via an anonymous pipe until EOF (e.g.
    # `subprocess.run(capture_output=True)` -> `Popen.communicate()`) blocks
    # for however long that orphan survives, not just for the script's own
    # exit -- exactly what made every test past the pre-flight guard appear
    # to hang for 60s (our own subprocess timeout) instead of returning in
    # under a second. Real production impact is minor (a stray `sleep`, not a
    # correctness bug) since systemd tracks the unit's main PID rather than
    # pipe closure, so this is worth documenting rather than patching the
    # script itself. Worked around here, not in the script: capture output to
    # real files (whose reads are not EOF-blocked by an unrelated orphan
    # process still holding a dup'd write handle) and wait only on the direct
    # child's own exit status.
    with tempfile.TemporaryDirectory() as capture_dir:
        out_path = os.path.join(capture_dir, "stdout.txt")
        err_path = os.path.join(capture_dir, "stderr.txt")
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            proc = subprocess.Popen(["bash", str(SCRIPT), task_id], env=env, stdout=out_f, stderr=err_f)
            try:
                returncode = proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
        stdout = Path(out_path).read_text()
        stderr = Path(err_path).read_text()
    return _ScriptResult(returncode, stdout, stderr)


REAL_TASKS_ROOT = Path("/opt/veridian/ai-os/tasks")


@pytest.fixture
def real_task_dir(tmp_path):
    """doc-worker-entrypoint.sh hard-codes TASK_DIR="/opt/veridian/ai-os/tasks/$TASK_ID"
    with no override. To exercise it for real without ever colliding with
    or disturbing a real task, every test gets a unique pytest-prefixed
    task_id and the directory is deleted again in the fixture teardown."""
    import uuid
    task_id = f"pytest-docworker-selftest-{uuid.uuid4().hex[:10]}"
    task_dir = REAL_TASKS_ROOT / task_id
    assert not task_dir.exists()
    task_dir.mkdir(parents=True)
    try:
        yield task_id, task_dir
    finally:
        import shutil
        shutil.rmtree(task_dir, ignore_errors=True)


def _write_task_yaml(task_dir: Path, workspace: Path, branch: str, checkpoints=None):
    import yaml
    (task_dir / "task.yaml").write_text(yaml.safe_dump({
        "workspace": str(workspace), "branch": branch, "checkpoints": checkpoints or [],
    }))
    (task_dir / "prompt.txt").write_text("Document the external system.")


def test_lifetime_invocation_cap_blocks_without_ever_calling_preflight_or_claude(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    _init_git_repo_with_remote(workspace, remote_bare, "worker/test-branch")
    _write_task_yaml(task_dir, workspace, "worker/test-branch")
    (task_dir / ".invocation_count").write_text("1")

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    systemctl_log = tmp_path / "systemctl.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", systemctl_log)
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["VERIDIAN_DOC_MAX_LIFETIME_INVOCATIONS"] = "1"

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (task_dir / ".invocation_count").read_text().strip() == "2"
    assert not claude_log.exists(), "claude must never be invoked once the lifetime cap is hit"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    assert len(checkpoint_calls) == 1
    note = checkpoint_calls[0][checkpoint_calls[0].index("--note") + 1]
    assert "PREVENTION CAP HIT" in note
    assert "--status" in checkpoint_calls[0]
    assert checkpoint_calls[0][checkpoint_calls[0].index("--status") + 1] == "blocked"

    assert f"veridian-docworker@{task_id}.service" in systemctl_log.read_text()


def test_preflight_hard_stop_circuit_breaker_blocks_before_claude(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    _init_git_repo_with_remote(workspace, remote_bare, "worker/test-branch")
    _write_task_yaml(task_dir, workspace, "worker/test-branch")

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "1"
    env["FAKE_PREFLIGHT_STDOUT"] = json.dumps({"reason": "circuit_breaker_tripped", "detail": "3 consecutive failures"})

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not claude_log.exists(), "claude must never be invoked when preflight hard-stops"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    assert len(checkpoint_calls) == 1
    assert checkpoint_calls[0][checkpoint_calls[0].index("--status") + 1] == "blocked"
    note = checkpoint_calls[0][checkpoint_calls[0].index("--note") + 1]
    assert "PRE-FLIGHT HARD STOP" in note
    assert "circuit_breaker_tripped" in note


def test_preflight_transient_rejection_fails_without_hard_stop(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    _init_git_repo_with_remote(workspace, remote_bare, "worker/test-branch")
    _write_task_yaml(task_dir, workspace, "worker/test-branch")

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "1"
    env["FAKE_PREFLIGHT_STDOUT"] = json.dumps({"reason": "proxy_unhealthy", "detail": "canary call failed"})

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 1
    assert not claude_log.exists()
    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    assert checkpoint_calls[0][checkpoint_calls[0].index("--status") + 1] == "failed"
    note = checkpoint_calls[0][checkpoint_calls[0].index("--note") + 1]
    assert "PRE-FLIGHT REJECTED" in note
    assert "proxy_unhealthy" in note


def test_success_path_commits_real_file_and_pushes_to_real_local_remote(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    branch = "worker/test-branch"
    _init_git_repo_with_remote(workspace, remote_bare, branch)
    _write_task_yaml(task_dir, workspace, branch)

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    new_file = workspace / "DOCS.md"
    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "0"
    env["CLAUDE_STUB_EXIT_CODE"] = "0"
    env["CLAUDE_STUB_WRITE_FILE"] = str(new_file)

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert new_file.exists()
    assert new_file.read_text() == "real content written by the fake claude stub\n"

    # Real git side effect: the commit really landed and was really pushed
    # to the real (local, bare) "origin" remote -- verified by cloning it.
    log = subprocess.run(["git", "-C", str(remote_bare), "log", "--oneline", branch],
                          capture_output=True, text=True, check=True)
    assert "Doc-worker" in log.stdout
    show = subprocess.run(["git", "-C", str(remote_bare), "show", f"{branch}:DOCS.md"],
                           capture_output=True, text=True, check=True)
    assert show.stdout == "real content written by the fake claude stub\n"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    statuses = [c[c.index("--status") + 1] for c in checkpoint_calls if "--status" in c]
    assert "in_progress" in statuses
    assert "pending_review" in statuses

    claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
    assert len(claude_calls) == 1
    assert "--dangerously-skip-permissions" in claude_calls[0]
    assert "SPEC: Document the external system." in claude_calls[0][claude_calls[0].index("-p") + 1]


def test_claude_failure_still_commits_and_pushes_partial_progress_and_records_failure_signature(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    branch = "worker/test-branch"
    _init_git_repo_with_remote(workspace, remote_bare, branch)
    _write_task_yaml(task_dir, workspace, branch)

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    new_file = workspace / "PARTIAL.md"
    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "0"
    env["CLAUDE_STUB_EXIT_CODE"] = "1"
    env["CLAUDE_STUB_WRITE_FILE"] = str(new_file)

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 1
    sig_file = task_dir / ".failure_signatures.json"
    assert sig_file.exists()
    sigs = json.loads(sig_file.read_text())
    assert len(sigs) == 1
    assert len(sigs[0]) == 24

    log = subprocess.run(["git", "-C", str(remote_bare), "log", "--oneline", branch],
                          capture_output=True, text=True, check=True)
    assert "invocation failed" in log.stdout

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    last_status = checkpoint_calls[-1][checkpoint_calls[-1].index("--status") + 1]
    assert last_status == "failed"


def test_wall_clock_timeout_kills_claude_and_checkpoints_in_progress_not_failed(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    branch = "worker/test-branch"
    _init_git_repo_with_remote(workspace, remote_bare, branch)
    _write_task_yaml(task_dir, workspace, branch)

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    new_file = workspace / "INPROGRESS.md"
    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "0"
    env["VERIDIAN_DOC_MAX_WALL_SECONDS"] = "1"
    env["CLAUDE_STUB_WRITE_FILE"] = str(new_file)
    env["CLAUDE_STUB_SLEEP"] = "5"

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 1
    assert new_file.exists(), "the fake claude wrote its file before sleeping past the wall-clock cap"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    last = checkpoint_calls[-1]
    assert last[last.index("--status") + 1] == "in_progress"
    note = last[last.index("--note") + 1]
    assert "wall-clock cap" in note

    log = subprocess.run(["git", "-C", str(remote_bare), "log", "--oneline", branch],
                          capture_output=True, text=True, check=True)
    assert "wall-clock cap hit" in log.stdout


def test_no_changes_to_commit_still_pushes_because_of_unconditional_mcp_json_write(tmp_path, real_task_dir):
    """GENUINE BUG (doc-worker-entrypoint.sh:105-117 vs. its own "no changes to
    commit" fast path at line 239): the script unconditionally (re)writes
    $WORKSPACE/.mcp.json (the Playwright MCP config) on every single
    invocation, BEFORE the `git diff --quiet && ... && [ -z "$(git status
    --porcelain)" ]` clean-tree check that is supposed to short-circuit
    straight to a "completed, no changes to commit" checkpoint with no
    push. Because .mcp.json is untracked and never gitignored, that check
    always sees a dirty tree -- even when the AI genuinely made zero real
    content changes -- so the "no changes" fast path can never actually
    fire in practice, and every invocation always commits+pushes at least
    .mcp.json as if it were real progress, ending in a `pending_review`
    checkpoint instead of the intended `completed`. Documented as a
    regression test (not fixed in the script) so this file's tests reflect
    the real, currently-reproducible behavior."""
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    branch = "worker/test-branch"
    _init_git_repo_with_remote(workspace, remote_bare, branch)
    _write_task_yaml(task_dir, workspace, branch)
    before_head = subprocess.run(["git", "-C", str(remote_bare), "rev-parse", branch],
                                  capture_output=True, text=True, check=True).stdout.strip()

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "0"
    env["CLAUDE_STUB_EXIT_CODE"] = "0"
    # No CLAUDE_STUB_WRITE_FILE -- claude makes no real workspace changes.

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    after_head = subprocess.run(["git", "-C", str(remote_bare), "rev-parse", branch],
                                 capture_output=True, text=True, check=True).stdout.strip()
    # Documents the real, currently-reproducible bug: this SHOULD be equal
    # (no push) on a fixed script, but isn't -- the unconditional .mcp.json
    # write always makes the tree dirty.
    assert after_head != before_head, (
        "if this assertion starts failing, the real bug has been fixed upstream "
        "in doc-worker-entrypoint.sh (the .mcp.json write no longer defeats the "
        "no-changes fast path) and this test should be rewritten to assert no push"
    )
    show = subprocess.run(["git", "-C", str(remote_bare), "show", "--stat", branch],
                           capture_output=True, text=True, check=True)
    assert ".mcp.json" in show.stdout, "the only real diff should be the unconditionally-written MCP config"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    checkpoint_calls = [c for c in calls if c[0].endswith("veridian-task.py") and c[1] == "checkpoint"]
    last_status = checkpoint_calls[-1][checkpoint_calls[-1].index("--status") + 1]
    assert last_status == "pending_review"
    note = checkpoint_calls[-1][checkpoint_calls[-1].index("--note") + 1]
    assert "pushed branch" in note


def test_resume_mode_fetches_resume_context_and_skips_full_spec_reembed(tmp_path, real_task_dir):
    task_id, task_dir = real_task_dir
    workspace = tmp_path / "workspace_repo"
    remote_bare = tmp_path / "origin_bare.git"
    branch = "worker/test-branch"
    _init_git_repo_with_remote(workspace, remote_bare, branch)
    _write_task_yaml(task_dir, workspace, branch, checkpoints=[
        {"ts": "2026-08-01T00:00:00Z", "status": "in_progress", "note": "first pass"},
    ])

    # Must be exactly $HOME/.local/bin: doc-worker-entrypoint.sh's own
    # `export PATH="$HOME/.local/bin:$HOME/.local/share/supabase:/usr/bin:$PATH"`
    # (line ~25) puts /usr/bin ahead of whatever this test prepended to the
    # inherited $PATH, so a stub directory anywhere else is silently shadowed
    # by the box's real /usr/bin/python3 and /usr/bin/systemctl -- this
    # exact path is the only stub location the script's own PATH rewrite
    # still searches before /usr/bin.
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    py3_log = tmp_path / "py3.log"
    claude_log = tmp_path / "claude.log"
    _write_py_stub(bin_dir, "python3", FAKE_PYTHON3)
    _write_bash_stub(bin_dir, "systemctl", tmp_path / "systemctl.log")
    _write_py_stub(bin_dir, "claude", FAKE_CLAUDE)

    env = _base_env(tmp_path, bin_dir)
    env["FAKE_PY3_LOG"] = str(py3_log)
    env["FAKE_CLAUDE_LOG"] = str(claude_log)
    env["FAKE_PREFLIGHT_EXIT"] = "0"
    env["CLAUDE_STUB_EXIT_CODE"] = "0"
    env["FAKE_RESUME_CONTEXT"] = "LAST DONE: scraped page 3 of 10"

    result = _run_script(task_id, tmp_path, env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    calls = [json.loads(line) for line in py3_log.read_text().splitlines()]
    assert any(c[0].endswith("veridian-task.py") and c[1] == "resume-context" for c in calls)

    claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
    prompt = claude_calls[0][claude_calls[0].index("-p") + 1]
    assert prompt.startswith(f"RESUME task={task_id}")
    assert "DO_NOT restart from scratch" in prompt
    assert "LAST DONE: scraped page 3 of 10" in prompt
    assert "SPEC: Document the external system." not in prompt
