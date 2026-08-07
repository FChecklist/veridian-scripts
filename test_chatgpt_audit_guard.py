#!/usr/bin/env python3
"""Real tests for chatgpt_audit_guard.py -- mechanical write-scope
enforcement for the ChatGPT Audit Workspace.

Every test loads a FRESH module instance (importlib.util spec_from_file_
location) and monkeypatches its module-level ALLOWED_ROOT to a real,
throwaway tempfile.mkdtemp() directory -- never the real
/opt/veridian/chatgpt-audit tree. Real files are actually written to (or
correctly rejected from) disk; nothing is stubbed beyond that constant.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import uuid

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "chatgpt_audit_guard.py")


def _load_fresh():
    name = f"sut_chatgpt_audit_guard_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sut(tmp_path):
    """Fresh module instance with ALLOWED_ROOT pointed at a real, empty
    temp directory (a subdirectory literally named to mirror the real
    layout, so the 'shares a string prefix but is a sibling' edge case in
    the module's own docstring can be exercised faithfully)."""
    root = tmp_path / "chatgpt-audit"
    root.mkdir()
    mod = _load_fresh()
    mod.ALLOWED_ROOT = str(root)
    return mod, root


# ---------------------------------------------------------------------------
# is_path_allowed / assert_path_allowed
# ---------------------------------------------------------------------------

def test_path_inside_root_is_allowed(sut):
    mod, root = sut
    target = root / "Architecture" / "AUDIT-000001-x.yaml"
    assert mod.is_path_allowed(str(target)) is True


def test_path_equal_to_root_itself_is_allowed(sut):
    mod, root = sut
    assert mod.is_path_allowed(str(root)) is True


def test_sibling_directory_sharing_string_prefix_is_rejected(sut):
    """'/opt/veridian/chatgpt-audit-evil' shares the string prefix
    '/opt/veridian/chatgpt-audit' without being inside it -- the module's
    own documented reason a plain prefix check is insufficient."""
    mod, root = sut
    evil_sibling = str(root) + "-evil" + "/file.yaml"
    assert mod.is_path_allowed(evil_sibling) is False


def test_dotdot_traversal_outside_root_is_rejected(sut):
    mod, root = sut
    traversal = str(root / "Architecture" / ".." / ".." / "evil.yaml")
    assert mod.is_path_allowed(traversal) is False


def test_symlink_pointing_outside_root_is_rejected_even_though_syntactically_inside(sut, tmp_path):
    """A symlink physically located inside ALLOWED_ROOT but whose target
    resolves outside it must be rejected -- proves real os.path.realpath
    symlink-following, not a naive path-string check."""
    mod, root = sut
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    real_target = outside_dir / "secret.yaml"
    real_target.write_text("not audit data")

    symlink_path = root / "escape-link.yaml"
    os.symlink(str(real_target), str(symlink_path))

    assert mod.is_path_allowed(str(symlink_path)) is False


def test_symlink_pointing_inside_root_is_allowed(sut):
    mod, root = sut
    real_target = root / "real-file.yaml"
    real_target.write_text("audit data")
    symlink_path = root / "link-to-real.yaml"
    os.symlink(str(real_target), str(symlink_path))
    assert mod.is_path_allowed(str(symlink_path)) is True


def test_assert_path_allowed_raises_with_descriptive_message_for_outside_path(sut):
    mod, root = sut
    outside = "/tmp/definitely-outside-" + uuid.uuid4().hex
    with pytest.raises(mod.PathNotAllowed) as exc_info:
        mod.assert_path_allowed(outside)
    assert "REJECTED" in str(exc_info.value)
    assert str(mod._resolved_allowed_root()) in str(exc_info.value)


def test_assert_path_allowed_does_not_raise_for_inside_path(sut):
    mod, root = sut
    mod.assert_path_allowed(str(root / "Reports" / "x.yaml"))  # no raise


# ---------------------------------------------------------------------------
# guarded_open / guarded_write -- real filesystem side effects
# ---------------------------------------------------------------------------

def test_guarded_write_actually_writes_real_file_inside_root(sut):
    mod, root = sut
    target = root / "Reports" / "AUDIT-000001.yaml"
    result = mod.guarded_write(str(target), "id: AUDIT-000001\nstatus: draft\n")
    assert result == str(target)
    assert target.exists()
    assert target.read_text() == "id: AUDIT-000001\nstatus: draft\n"


def test_guarded_write_creates_missing_parent_directories(sut):
    mod, root = sut
    target = root / "Deeply" / "Nested" / "Path" / "file.yaml"
    mod.guarded_write(str(target), "data")
    assert target.exists()
    assert target.read_text() == "data"


def test_guarded_write_append_mode_real_concatenation(sut):
    mod, root = sut
    target = root / "log.txt"
    mod.guarded_write(str(target), "line1\n")
    mod.guarded_write(str(target), "line2\n", mode="a")
    assert target.read_text() == "line1\nline2\n"


def test_guarded_write_outside_root_raises_and_creates_nothing(sut, tmp_path):
    mod, root = sut
    outside_target = tmp_path / "outside" / "escape.yaml"
    with pytest.raises(mod.PathNotAllowed):
        mod.guarded_write(str(outside_target), "should never land")
    assert not outside_target.exists()
    assert not outside_target.parent.exists()  # makedirs never even ran


def test_guarded_open_outside_root_raises_before_touching_disk(sut, tmp_path):
    mod, root = sut
    outside_target = tmp_path / "outside2" / "escape.yaml"
    with pytest.raises(mod.PathNotAllowed):
        mod.guarded_open(str(outside_target), "w")
    assert not outside_target.exists()


# ---------------------------------------------------------------------------
# CLI: main() -- real argv, real exit codes, real stdout/stderr, real files
# ---------------------------------------------------------------------------

def test_cli_test_path_allowed_exits_0_and_prints_allowed(sut, monkeypatch, capsys):
    mod, root = sut
    target = str(root / "Architecture" / "x.yaml")
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py", "--test-path", target])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("ALLOWED:")


def test_cli_test_path_rejected_exits_1_and_prints_rejected(sut, monkeypatch, capsys):
    mod, root = sut
    outside = "/tmp/cli-outside-" + uuid.uuid4().hex
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py", "--test-path", outside])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("REJECTED:")


def test_cli_write_stdin_real_write_and_exit_0(sut, monkeypatch, capsys):
    mod, root = sut
    target = str(root / "Prompts" / "from-stdin.yaml")
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py", "write", "--path", target, "--stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("real: content\n"))
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"WROTE: {target}"
    assert os.path.exists(target)
    assert open(target, encoding="utf-8").read() == "real: content\n"


def test_cli_write_content_file_real_write_and_exit_0(sut, monkeypatch, capsys, tmp_path):
    mod, root = sut
    content_file = tmp_path / "source-content.txt"
    content_file.write_text("from a file\n")
    target = str(root / "Modules" / "from-file.yaml")
    monkeypatch.setattr(
        sys, "argv",
        ["chatgpt_audit_guard.py", "write", "--path", target, "--content-file", str(content_file)],
    )
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 0
    assert open(target, encoding="utf-8").read() == "from a file\n"


def test_cli_write_outside_root_exits_1_and_writes_nothing(sut, monkeypatch, capsys, tmp_path):
    mod, root = sut
    outside_target = str(tmp_path / "outside3" / "escape.yaml")
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py", "write", "--path", outside_target, "--stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("malicious content"))
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    assert "REJECTED" in capsys.readouterr().err
    assert not os.path.exists(outside_target)


def test_cli_write_requires_exactly_one_of_content_file_or_stdin_neither(sut, monkeypatch, capsys):
    mod, root = sut
    target = str(root / "x.yaml")
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py", "write", "--path", target])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2
    assert "exactly one" in capsys.readouterr().err
    assert not os.path.exists(target)


def test_cli_write_requires_exactly_one_of_content_file_or_stdin_both(sut, monkeypatch, capsys, tmp_path):
    mod, root = sut
    content_file = tmp_path / "c.txt"
    content_file.write_text("x")
    target = str(root / "x.yaml")
    monkeypatch.setattr(
        sys, "argv",
        ["chatgpt_audit_guard.py", "write", "--path", target, "--content-file", str(content_file), "--stdin"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("y"))
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2
    assert not os.path.exists(target)


def test_cli_no_args_prints_help_and_exits_2(sut, monkeypatch, capsys):
    mod, root = sut
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_guard.py"])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
