"""Real subprocess tests for chrome_stop.sh.

These tests execute the actual chrome_stop.sh file as a real bash
subprocess. The real `docker` binary is never invoked: a fake `docker`
executable is put first on PATH and made to log its full argv to a file
that the tests read back and assert on. No real docker/container action
ever fires.
"""
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "chrome_stop.sh"


def _write_docker_stub(bin_dir: Path, log_file: Path, exit_code: int, stderr_text: str = "") -> None:
    stub = bin_dir / "docker"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo "$@" >> "{log_file}"
            if [ -n "{stderr_text}" ]; then
              echo "{stderr_text}" >&2
            fi
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


def test_chrome_stop_success_path_stops_container_and_prints_final_message(tmp_path):
    docker_log = tmp_path / "docker_calls.log"
    _write_docker_stub(tmp_path, docker_log, exit_code=0)

    result = _run_script(tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    # The `|| echo "...was not running."` fallback must NOT fire on success.
    assert "was not running" not in result.stdout
    assert (
        "isolated_chrome stopped. Profile volume preserved at /opt/veridian/isolated_chrome/profile."
        in result.stdout
    )

    calls = docker_log.read_text().strip().splitlines()
    assert calls == ["stop --timeout 5 isolated_chrome"], calls


def test_chrome_stop_falls_back_to_was_not_running_message_on_docker_failure(tmp_path):
    docker_log = tmp_path / "docker_calls.log"
    _write_docker_stub(
        tmp_path, docker_log, exit_code=1, stderr_text="Error: No such container: isolated_chrome"
    )

    result = _run_script(tmp_path)

    # `docker stop ... || echo "...was not running."` masks the docker
    # failure with exit 0 from the echo, and the script has no further
    # `set -e`-triggering command after that, so the script must still
    # reach its own final success line even though docker itself failed.
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "isolated_chrome was not running." in result.stdout
    assert (
        "isolated_chrome stopped. Profile volume preserved at /opt/veridian/isolated_chrome/profile."
        in result.stdout
    )

    calls = docker_log.read_text().strip().splitlines()
    assert calls == ["stop --timeout 5 isolated_chrome"], calls
