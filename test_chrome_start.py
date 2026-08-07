"""Real subprocess tests for chrome_start.sh.

These tests execute the actual chrome_start.sh file as a real bash
subprocess (not a re-implementation, not a grep-for-string check). The
real `docker` binary is never invoked: a fake `docker` executable is put
first on PATH and made to log its full argv to a file that the tests
read back and assert on. No real docker/container action ever fires.
"""
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "chrome_start.sh"


def _write_docker_stub(bin_dir: Path, log_file: Path, exit_code: int) -> None:
    stub = bin_dir / "docker"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo "$@" >> "{log_file}"
            exit {exit_code}
            """
        )
    )
    stub.chmod(0o755)


def _run_script(bin_dir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_chrome_start_success_path_runs_real_docker_compose_and_prints_urls(tmp_path):
    docker_log = tmp_path / "docker_calls.log"
    _write_docker_stub(tmp_path, docker_log, exit_code=0)

    result = _run_script(tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "isolated_chrome started" in result.stdout
    assert "http://127.0.0.1:6080/vnc.html" in result.stdout
    assert "ssh -L 6080:127.0.0.1:6080 rajat@167.233.220.35" in result.stdout

    calls = docker_log.read_text().strip().splitlines()
    assert calls == ["compose up -d --build"], calls


def test_chrome_start_set_e_aborts_before_success_echo_when_docker_fails(tmp_path):
    docker_log = tmp_path / "docker_calls.log"
    _write_docker_stub(tmp_path, docker_log, exit_code=1)

    result = _run_script(tmp_path)

    # chrome_start.sh has `set -e` and no `||` fallback around the docker
    # call, so a failing `docker compose up -d --build` must make the whole
    # script exit non-zero *before* reaching either echo line.
    assert result.returncode != 0
    assert "isolated_chrome started" not in result.stdout
    assert "noVNC" not in result.stdout

    calls = docker_log.read_text().strip().splitlines()
    assert calls == ["compose up -d --build"], calls
