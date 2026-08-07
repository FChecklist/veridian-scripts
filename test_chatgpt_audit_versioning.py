#!/usr/bin/env python3
"""Real tests for chatgpt_audit_versioning.py -- real, unique, incrementing
audit-ID + filename allocation for the ChatGPT Audit Workspace.

chatgpt_audit_versioning.py does `from chatgpt_audit_guard import
ALLOWED_ROOT, assert_path_allowed` at import time, and computes
SEQUENCE_FILE = os.path.join(ALLOWED_ROOT, "Metadata", ".audit_sequence.json")
also at import time. So every test here:
  1. loads chatgpt_audit_guard.py fresh under its REAL module name
     ("chatgpt_audit_guard") so versioning's `from chatgpt_audit_guard
     import ...` (real import-system lookup, triggered by
     sys.path.insert(0, <this dir>) inside the SUT itself) resolves to the
     SAME module object we can patch;
  2. loads chatgpt_audit_versioning.py fresh via importlib file-path
     loading;
  3. monkeypatches BOTH modules' ALLOWED_ROOT (and versioning's derived
     SEQUENCE_FILE) to a real, throwaway tempfile.mkdtemp() directory --
     never the real /opt/veridian/chatgpt-audit tree;
  4. tears sys.modules["chatgpt_audit_guard"] back down afterward so tests
     never see each other's patched state.

Real files (the persisted sequence counter, and pre-created "collision"
files) are actually written to disk under the throwaway directory and read
back for every assertion below.
"""
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime as _real_datetime

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
GUARD_PATH = os.path.join(SCRIPTS_DIR, "chatgpt_audit_guard.py")
VERSIONING_PATH = os.path.join(SCRIPTS_DIR, "chatgpt_audit_versioning.py")


def _load_versioning_bound_to(tmp_root):
    """Fresh-load both modules, wired together the same way the real
    interpreter wires them (via a real `from chatgpt_audit_guard import
    ...`), then point both at tmp_root."""
    sys.modules.pop("chatgpt_audit_guard", None)

    name = f"sut_chatgpt_audit_versioning_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, VERSIONING_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # triggers `from chatgpt_audit_guard import ...`

    guard_mod = sys.modules["chatgpt_audit_guard"]
    guard_mod.ALLOWED_ROOT = tmp_root
    mod.ALLOWED_ROOT = tmp_root
    mod.SEQUENCE_FILE = os.path.join(tmp_root, "Metadata", ".audit_sequence.json")
    return mod, guard_mod


@pytest.fixture
def sut(tmp_path):
    root = str(tmp_path / "chatgpt-audit")
    os.makedirs(root, exist_ok=True)
    mod, guard_mod = _load_versioning_bound_to(root)
    yield mod, guard_mod, root
    sys.modules.pop("chatgpt_audit_guard", None)


class _FrozenDateTime(_real_datetime):
    """Deterministic clock for tests that need to predict allocate()'s
    generated timestamp exactly (e.g. to pre-create a real on-disk
    collision at a known path). Subclassing real datetime keeps
    .strftime() etc real; only .now() is stubbed."""
    _fixed = _real_datetime(2026, 1, 1, 0, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed.replace(tzinfo=tz) if tz else cls._fixed


# ---------------------------------------------------------------------------
# allocate() -- real sequence file + real path construction
# ---------------------------------------------------------------------------

def test_allocate_first_call_is_seq_1_with_real_persisted_sequence_file(sut):
    mod, guard_mod, root = sut
    result = mod.allocate("Architecture")

    assert result["audit_id"] == "AUDIT-000001"
    assert result["subfolder"] == "Architecture"
    assert result["path"] == os.path.join(
        root, "Architecture", f"AUDIT-000001-{result['timestamp']}.yaml"
    )
    # allocate() only reserves the name; it does not write the target file
    assert not os.path.exists(result["path"])

    assert os.path.exists(mod.SEQUENCE_FILE)
    state = json.loads(open(mod.SEQUENCE_FILE).read())
    assert state == {"last_id": 1}


def test_allocate_second_call_increments_real_persisted_counter(sut):
    mod, guard_mod, root = sut
    first = mod.allocate("Reports")
    second = mod.allocate("Reports")
    assert first["audit_id"] == "AUDIT-000001"
    assert second["audit_id"] == "AUDIT-000002"
    assert first["path"] != second["path"]
    state = json.loads(open(mod.SEQUENCE_FILE).read())
    assert state == {"last_id": 2}


def test_allocate_custom_extension_is_honored(sut):
    mod, guard_mod, root = sut
    result = mod.allocate("Database", extension="json")
    assert result["path"].endswith(".json")


def test_allocate_unknown_subfolder_raises_value_error(sut):
    mod, guard_mod, root = sut
    with pytest.raises(ValueError, match="unknown subfolder"):
        mod.allocate("NotARealSubfolder")
    # a rejected subfolder must not have consumed a sequence number
    assert not os.path.exists(mod.SEQUENCE_FILE)


def test_allocate_persists_across_separate_module_loads_same_sequence_file(sut):
    """Simulates two separate process invocations sharing the same
    on-disk sequence file -- monotonicity must hold across module
    instances, not just within one."""
    mod1, guard_mod, root = sut
    first = mod1.allocate("Architecture")
    assert first["audit_id"] == "AUDIT-000001"

    mod2, guard_mod2 = _load_versioning_bound_to(root)
    second = mod2.allocate("Architecture")
    assert second["audit_id"] == "AUDIT-000002"


def test_allocate_skips_sequence_number_when_target_path_already_exists_on_disk(sut, monkeypatch):
    """Real on-disk collision test (per the module's own docstring: 'never
    overwrite' is enforced, not just documented). Freezes the module's
    clock so the generated timestamp -- and therefore the exact target
    path -- is fully predictable, pre-creates a real file at the seq-1
    path, then proves allocate() skips straight to seq-2 with a real,
    different, non-colliding path, and that the persisted counter
    reflects BOTH consumed sequence numbers (proof it actually looped,
    not that seq-1 was silently reused)."""
    mod, guard_mod, root = sut
    monkeypatch.setattr(mod, "datetime", _FrozenDateTime)

    expected_ts = _FrozenDateTime.now().strftime("%Y%m%dT%H%M%SZ")
    colliding_path = os.path.join(root, "Architecture", f"AUDIT-000001-{expected_ts}.yaml")
    os.makedirs(os.path.dirname(colliding_path), exist_ok=True)
    with open(colliding_path, "w") as f:
        f.write("pre-existing file occupying seq 1's path")

    result = mod.allocate("Architecture")

    assert result["audit_id"] == "AUDIT-000002"
    assert result["path"] != colliding_path
    assert result["path"] == os.path.join(
        root, "Architecture", f"AUDIT-000002-{expected_ts}.yaml"
    )
    # the pre-existing seq-1 file was never touched/overwritten
    assert open(colliding_path).read() == "pre-existing file occupying seq 1's path"

    state = json.loads(open(mod.SEQUENCE_FILE).read())
    assert state == {"last_id": 2}, "counter must reflect the skipped seq too, not just the returned one"


def test_allocate_raises_pathnotallowed_when_guard_and_versioning_roots_diverge(sut):
    """Real integration test between the two scripts: allocate() delegates
    its final safety check to chatgpt_audit_guard.assert_path_allowed,
    which reads chatgpt_audit_guard's OWN ALLOWED_ROOT global (not
    versioning's copy). If the two ever diverge, allocate() must still
    refuse to hand back a path -- proving it is a real, live delegation
    and not a check that was silently inlined/duplicated."""
    mod, guard_mod, root = sut
    guard_mod.ALLOWED_ROOT = tempfile.mkdtemp()  # diverges from mod.ALLOWED_ROOT
    with pytest.raises(guard_mod.PathNotAllowed):
        mod.allocate("Architecture")


def test_next_sequence_creates_metadata_directory_that_did_not_exist(sut):
    mod, guard_mod, root = sut
    assert not os.path.exists(os.path.dirname(mod.SEQUENCE_FILE))
    mod._next_sequence()
    assert os.path.isdir(os.path.dirname(mod.SEQUENCE_FILE))


# ---------------------------------------------------------------------------
# CLI: main()
# ---------------------------------------------------------------------------

def test_cli_allocate_prints_real_json_result(sut, monkeypatch, capsys):
    mod, guard_mod, root = sut
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_versioning.py", "allocate", "--subfolder", "Testing"])
    mod.main()
    printed = json.loads(capsys.readouterr().out)
    assert printed["audit_id"] == "AUDIT-000001"
    assert printed["subfolder"] == "Testing"
    assert printed["path"].startswith(os.path.join(root, "Testing"))


def test_cli_allocate_invalid_subfolder_rejected_by_argparse_choices(sut, monkeypatch, capsys):
    mod, guard_mod, root = sut
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_versioning.py", "allocate", "--subfolder", "NotReal"])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cli_missing_subcommand_exits_2(sut, monkeypatch, capsys):
    mod, guard_mod, root = sut
    monkeypatch.setattr(sys, "argv", ["chatgpt_audit_versioning.py"])
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
