#!/usr/bin/env python3
"""Real tests for chatgpt_promptlib_guard.py.

Every test loads a FRESH module instance (importlib, by literal path, per the
house style in test_apply_owner_dispatch_status_corrections.py) and then
monkeypatches the module-level SANDBOX_ROOT constant to a real, throwaway
temp directory -- never the real /opt/veridian/chatgpt-prompt-library sandbox
and never any path outside a pytest tmp_path. The source file itself is never
modified."""
import importlib.util
import json
import os
import sys
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "chatgpt_promptlib_guard.py")

_counter = [0]


def _load_fresh():
    _counter[0] += 1
    spec = importlib.util.spec_from_file_location(
        f"chatgpt_promptlib_guard_scratch_{_counter[0]}", SUT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sandboxed_mod(tmp_path):
    """A fresh module instance whose SANDBOX_ROOT points at a real, empty
    temp directory (not yet created on disk -- init_sandbox() is what
    creates it, mirroring real usage)."""
    mod = _load_fresh()
    root = tmp_path / "chatgpt-prompt-library"
    mod.SANDBOX_ROOT = str(root)
    return mod


# ---------------------------------------------------------------------------
# assert_sandboxed
# ---------------------------------------------------------------------------

def test_assert_sandboxed_accepts_absolute_path_inside_root(sandboxed_mod):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    target = os.path.join(sandboxed_mod.SANDBOX_ROOT, "CSV", "prompts.csv")
    real = sandboxed_mod.assert_sandboxed(target)
    assert real == os.path.realpath(target)
    assert real.startswith(os.path.realpath(sandboxed_mod.SANDBOX_ROOT) + os.sep)


def test_assert_sandboxed_treats_relative_path_as_relative_to_root(sandboxed_mod):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    real = sandboxed_mod.assert_sandboxed("Keywords/index.json")
    expected = os.path.realpath(os.path.join(sandboxed_mod.SANDBOX_ROOT, "Keywords", "index.json"))
    assert real == expected


def test_assert_sandboxed_rejects_absolute_path_outside_root(sandboxed_mod, tmp_path):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    outside = tmp_path / "elsewhere" / "secret.txt"
    with pytest.raises(sandboxed_mod.SandboxViolation) as exc:
        sandboxed_mod.assert_sandboxed(str(outside))
    assert "outside" in str(exc.value)
    assert str(outside.resolve()) in str(exc.value) or "secret.txt" in str(exc.value)


def test_assert_sandboxed_rejects_sibling_path_with_shared_prefix(sandboxed_mod, tmp_path):
    """SANDBOX_ROOT + '-evil' shares a string prefix with SANDBOX_ROOT but is
    a different real directory -- must still be rejected (guards against a
    naive startswith(SANDBOX_ROOT) check without the os.sep join)."""
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    evil = sandboxed_mod.SANDBOX_ROOT + "-evil" + os.sep + "file.txt"
    with pytest.raises(sandboxed_mod.SandboxViolation):
        sandboxed_mod.assert_sandboxed(evil)


def test_assert_sandboxed_rejects_symlink_escape(sandboxed_mod, tmp_path):
    """A real symlink placed inside the sandbox pointing outside of it must
    not be usable to escape the boundary -- assert_sandboxed resolves
    symlinks via os.path.realpath before checking."""
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()
    (outside_dir / "real_secret.txt").write_text("do not touch")

    link_path = os.path.join(sandboxed_mod.SANDBOX_ROOT, "Reports", "escape_link")
    os.makedirs(os.path.dirname(link_path), exist_ok=True)
    os.symlink(str(outside_dir), link_path)

    escaping_path = os.path.join(link_path, "real_secret.txt")
    with pytest.raises(sandboxed_mod.SandboxViolation):
        sandboxed_mod.assert_sandboxed(escaping_path)


def test_assert_sandboxed_accepts_root_itself(sandboxed_mod):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    real = sandboxed_mod.assert_sandboxed(sandboxed_mod.SANDBOX_ROOT)
    assert real == os.path.realpath(sandboxed_mod.SANDBOX_ROOT)


# ---------------------------------------------------------------------------
# init_sandbox
# ---------------------------------------------------------------------------

def test_init_sandbox_creates_all_15_real_subfolders(sandboxed_mod):
    result = sandboxed_mod.init_sandbox()
    assert os.path.isdir(sandboxed_mod.SANDBOX_ROOT)
    for sub in sandboxed_mod.SUBFOLDERS:
        assert os.path.isdir(os.path.join(sandboxed_mod.SANDBOX_ROOT, sub)), sub
    assert len(sandboxed_mod.SUBFOLDERS) == 15
    assert sorted(result["newly_created"]) == sorted(sandboxed_mod.SUBFOLDERS)
    assert result["root"] == os.path.realpath(sandboxed_mod.SANDBOX_ROOT)


def test_init_sandbox_is_idempotent_second_call_creates_nothing_new(sandboxed_mod):
    sandboxed_mod.init_sandbox()
    result2 = sandboxed_mod.init_sandbox()
    assert result2["newly_created"] == []
    # every subfolder still real and present
    for sub in sandboxed_mod.SUBFOLDERS:
        assert os.path.isdir(os.path.join(sandboxed_mod.SANDBOX_ROOT, sub))


def test_init_sandbox_partial_pre_existing_only_reports_truly_new_ones(sandboxed_mod):
    os.makedirs(os.path.join(sandboxed_mod.SANDBOX_ROOT, "CSV"), exist_ok=True)
    result = sandboxed_mod.init_sandbox()
    assert "CSV" not in result["newly_created"]
    assert "Reports" in result["newly_created"]


# ---------------------------------------------------------------------------
# guarded_write / guarded_write_json
# ---------------------------------------------------------------------------

def test_guarded_write_writes_real_content_and_makes_parent_dirs(sandboxed_mod):
    target = os.path.join(sandboxed_mod.SANDBOX_ROOT, "CSV", "nested", "prompts.csv")
    real_path = sandboxed_mod.guarded_write(target, "id,prompt\n1,hello\n")
    assert os.path.isfile(real_path)
    with open(real_path, encoding="utf-8") as f:
        assert f.read() == "id,prompt\n1,hello\n"


def test_guarded_write_rejects_path_outside_sandbox_and_writes_nothing(sandboxed_mod, tmp_path):
    outside = tmp_path / "not_sandboxed" / "evil.txt"
    with pytest.raises(sandboxed_mod.SandboxViolation):
        sandboxed_mod.guarded_write(str(outside), "malicious content")
    assert not outside.exists()
    assert not outside.parent.exists()


def test_guarded_write_json_round_trips_real_object(sandboxed_mod):
    target = os.path.join(sandboxed_mod.SANDBOX_ROOT, "Statistics", "counts.json")
    payload = {"total_prompts": 42, "domains": ["Finance", "HR"]}
    real_path = sandboxed_mod.guarded_write_json(target, payload)
    with open(real_path, encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == payload


def test_guarded_write_overwrite_mode_truncates_existing_file(sandboxed_mod):
    target = os.path.join(sandboxed_mod.SANDBOX_ROOT, "Coverage", "latest.txt")
    sandboxed_mod.guarded_write(target, "first version, much longer than the second")
    sandboxed_mod.guarded_write(target, "v2")
    with open(target, encoding="utf-8") as f:
        assert f.read() == "v2"


# ---------------------------------------------------------------------------
# main() CLI, invoked in-process against the monkeypatched SANDBOX_ROOT
# ---------------------------------------------------------------------------

def _run_main(mod, argv, capsys):
    old_argv = sys.argv
    sys.argv = ["chatgpt_promptlib_guard.py"] + argv
    try:
        with pytest.raises(SystemExit) as exc:
            mod.main()
        return exc.value.code, capsys.readouterr()
    finally:
        sys.argv = old_argv


def test_main_check_outside_sandbox_exits_1_with_real_json(sandboxed_mod, capsys, tmp_path):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    outside = str(tmp_path / "totally_outside" / "x.txt")
    code, captured = _run_main(sandboxed_mod, ["check", "--path", outside], capsys)
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"] == "sandbox_violation"


def test_main_check_inside_sandbox_exits_0_with_resolved_path(sandboxed_mod, capsys):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    code, captured = _run_main(sandboxed_mod, ["check", "--path", "Domains/finance.yaml"], capsys)
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["resolved"] == os.path.realpath(
        os.path.join(sandboxed_mod.SANDBOX_ROOT, "Domains", "finance.yaml")
    )


def test_main_write_with_literal_content_creates_real_file(sandboxed_mod, capsys):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    code, captured = _run_main(
        sandboxed_mod,
        ["write", "--path", "Reports/summary.md", "--content", "# Summary\nAll good."],
        capsys,
    )
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["bytes"] == len("# Summary\nAll good.")
    on_disk = os.path.join(sandboxed_mod.SANDBOX_ROOT, "Reports", "summary.md")
    with open(on_disk, encoding="utf-8") as f:
        assert f.read() == "# Summary\nAll good."


def test_main_write_with_content_file_reads_real_source_file(sandboxed_mod, capsys, tmp_path):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    src = tmp_path / "source_content.txt"
    src.write_text("data pulled from a real content file\n")
    code, captured = _run_main(
        sandboxed_mod,
        ["write", "--path", "History/2026-08-07.txt", "--content-file", str(src)],
        capsys,
    )
    assert code == 0
    on_disk = os.path.join(sandboxed_mod.SANDBOX_ROOT, "History", "2026-08-07.txt")
    with open(on_disk, encoding="utf-8") as f:
        assert f.read() == "data pulled from a real content file\n"


def test_main_write_outside_sandbox_exits_1_and_writes_nothing(sandboxed_mod, capsys, tmp_path):
    os.makedirs(sandboxed_mod.SANDBOX_ROOT, exist_ok=True)
    outside = str(tmp_path / "escape" / "pwned.txt")
    code, captured = _run_main(
        sandboxed_mod, ["write", "--path", outside, "--content", "pwned"], capsys
    )
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"] == "sandbox_violation"
    assert not os.path.exists(outside)


def test_main_init_exits_0_and_creates_real_tree(sandboxed_mod, capsys):
    code, captured = _run_main(sandboxed_mod, ["init"], capsys)
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert sorted(payload["subfolders"]) == sorted(sandboxed_mod.SUBFOLDERS)
    for sub in sandboxed_mod.SUBFOLDERS:
        assert os.path.isdir(os.path.join(sandboxed_mod.SANDBOX_ROOT, sub))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
