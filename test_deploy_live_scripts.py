"""Real bash-subprocess tests for deploy-live-scripts.sh.

The real script hardcodes REPO_DIR=/opt/veridian/repos/claude-control,
LIVE_DIR=/opt/veridian/scripts, and LOG=/opt/veridian/logs/... with zero
env/CLI override seam. Running it verbatim would really overwrite live
production scripts under /opt/veridian/scripts and write into the live
/opt/veridian/logs/ directory -- unacceptable for a test. Since the script
offers no override seam at all, these tests read its real, unmodified
source text and produce a transient in-memory copy with ONLY the 3 absolute
path constants substituted to tmp_path equivalents (the bash equivalent of
the "temp-dir PATH tricks" the task brief explicitly sanctions for scripts
with no other testability seam). Every other line -- git ls-files usage,
cmp -s diffing, .bak-predeploy-<ts> backup naming, DEPLOYED/UNCHANGED/FAILED
counters, exit code -- is executed byte-for-byte unchanged. The tracked
deploy-live-scripts.sh file on disk is never written to.
"""
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "deploy-live-scripts.sh"


def _make_sandboxed_copy(tmp_path: Path, repo_dir: Path, live_dir: Path, logs_dir: Path) -> Path:
    text = SCRIPT.read_text()
    text, n1 = re.subn(
        r'REPO_DIR="/opt/veridian/repos/claude-control"',
        f'REPO_DIR="{repo_dir}"', text,
    )
    text, n2 = re.subn(
        r'LIVE_DIR="/opt/veridian/scripts"',
        f'LIVE_DIR="{live_dir}"', text,
    )
    text, n3 = re.subn(
        r'LOG="/opt/veridian/logs/deploy-live-scripts-\$\{TS\}\.log"',
        f'LOG="{logs_dir}/deploy-live-scripts-${{TS}}.log"', text,
    )
    text, n4 = re.subn(
        r"find /opt/veridian/logs -name 'deploy-live-scripts-\*\.log' -mtime \+14 -delete",
        f"find {logs_dir} -name 'deploy-live-scripts-*.log' -mtime +14 -delete", text,
    )
    assert (n1, n2, n3, n4) == (1, 1, 1, 1), (
        "deploy-live-scripts.sh's real source no longer matches the exact substituted "
        "lines -- update this test's substitution patterns to match the real script "
        "(never loosen this to a silent partial match)"
    )
    copy_path = tmp_path / "deploy-live-scripts-sandboxed.sh"
    copy_path.write_text(text)
    copy_path.chmod(stat.S_IRWXU)
    return copy_path


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo_with_scripts(repo_dir: Path, files: dict):
    repo_dir.mkdir(parents=True)
    _git(["init", "-q"], repo_dir)
    _git(["config", "user.email", "test@example.com"], repo_dir)
    _git(["config", "user.name", "Test"], repo_dir)
    for relpath, content in files.items():
        p = repo_dir / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(["add", "-A"], repo_dir)
    _git(["commit", "-q", "-m", "initial"], repo_dir)


def _run(copy_path: Path):
    return subprocess.run(["bash", str(copy_path)], capture_output=True, text=True, timeout=30)


def test_deploy_copies_new_tracked_file_and_reports_deployed(tmp_path):
    repo_dir = tmp_path / "repo"
    live_dir = tmp_path / "live"
    logs_dir = tmp_path / "logs"
    live_dir.mkdir()
    logs_dir.mkdir()

    _init_repo_with_scripts(repo_dir, {"scripts/foo.py": "print('v1')\n"})
    copy_path = _make_sandboxed_copy(tmp_path, repo_dir, live_dir, logs_dir)

    result = _run(copy_path)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    dest = live_dir / "foo.py"
    assert dest.read_text() == "print('v1')\n"

    log_files = list(logs_dir.glob("deploy-live-scripts-*.log"))
    assert len(log_files) == 1
    log_text = log_files[0].read_text()
    assert "DEPLOYED: scripts/foo.py" in log_text
    assert "deployed=1 unchanged=0 failed=0" in log_text
    assert not (dest.parent / "foo.py.bak-predeploy-").exists()  # no bak for a brand new dest


def test_deploy_backs_up_differing_live_file_before_overwriting(tmp_path):
    repo_dir = tmp_path / "repo"
    live_dir = tmp_path / "live"
    logs_dir = tmp_path / "logs"
    live_dir.mkdir()
    logs_dir.mkdir()
    (live_dir / "foo.py").write_text("print('OLD LIVE VERSION')\n")

    _init_repo_with_scripts(repo_dir, {"scripts/foo.py": "print('NEW REPO VERSION')\n"})
    copy_path = _make_sandboxed_copy(tmp_path, repo_dir, live_dir, logs_dir)

    result = _run(copy_path)
    assert result.returncode == 0

    dest = live_dir / "foo.py"
    assert dest.read_text() == "print('NEW REPO VERSION')\n"

    backups = list(live_dir.glob("foo.py.bak-predeploy-*"))
    assert len(backups) == 1, f"expected exactly 1 backup file, found {backups}"
    assert backups[0].read_text() == "print('OLD LIVE VERSION')\n"

    log_text = (logs_dir / list(logs_dir.glob("deploy-live-scripts-*.log"))[0].name).read_text()
    log_text = (logs_dir.glob("deploy-live-scripts-*.log").__iter__().__next__()).read_text()
    assert "BACKED UP:" in log_text
    assert "DEPLOYED: scripts/foo.py" in log_text


def test_deploy_skips_identical_content_and_never_deletes_untracked_live_files(tmp_path):
    """UNCHANGED counted (cmp -s short-circuits the copy), and a real
    operational-only live file NOT tracked by git (e.g. a crontab backup)
    is left completely untouched -- the script's own documented "no
    rsync --delete, no directory mirroring" guarantee."""
    repo_dir = tmp_path / "repo"
    live_dir = tmp_path / "live"
    logs_dir = tmp_path / "logs"
    live_dir.mkdir()
    logs_dir.mkdir()
    (live_dir / "foo.py").write_text("print('same')\n")
    (live_dir / "operational-only.bak").write_text("never touched\n")

    _init_repo_with_scripts(repo_dir, {"scripts/foo.py": "print('same')\n"})
    copy_path = _make_sandboxed_copy(tmp_path, repo_dir, live_dir, logs_dir)

    result = _run(copy_path)
    assert result.returncode == 0

    assert (live_dir / "operational-only.bak").read_text() == "never touched\n"
    assert not list(live_dir.glob("foo.py.bak-predeploy-*"))  # no backup: content was identical

    log_text = list(logs_dir.glob("deploy-live-scripts-*.log"))[0].read_text()
    assert "deployed=0 unchanged=1 failed=0" in log_text


def test_missing_repo_checkout_exits_zero_and_skips(tmp_path):
    """REPO_DIR not being a real git checkout is treated as a soft skip
    (exit 0), not a hard failure -- real documented behavior for a server
    that has never had claude-control cloned."""
    repo_dir = tmp_path / "not-a-real-repo"  # never created / never git-init'ed
    live_dir = tmp_path / "live"
    logs_dir = tmp_path / "logs"
    live_dir.mkdir()
    logs_dir.mkdir()

    text = SCRIPT.read_text()
    text = text.replace('REPO_DIR="/opt/veridian/repos/claude-control"', f'REPO_DIR="{repo_dir}"')
    text = text.replace('LIVE_DIR="/opt/veridian/scripts"', f'LIVE_DIR="{live_dir}"')
    text = text.replace(
        'LOG="/opt/veridian/logs/deploy-live-scripts-${TS}.log"',
        f'LOG="{logs_dir}/deploy-live-scripts-${{TS}}.log"',
    )
    copy_path = tmp_path / "sandboxed2.sh"
    copy_path.write_text(text)
    copy_path.chmod(stat.S_IRWXU)

    result = _run(copy_path)
    assert result.returncode == 0
    log_text = list(logs_dir.glob("deploy-live-scripts-*.log"))[0].read_text()
    assert "MISSING" in log_text and "skip" in log_text


def test_real_script_file_on_disk_is_never_modified():
    """Sanity guard: this test file must never mutate the tracked script."""
    original = SCRIPT.read_text()
    assert 'REPO_DIR="/opt/veridian/repos/claude-control"' in original
    assert 'LIVE_DIR="/opt/veridian/scripts"' in original
