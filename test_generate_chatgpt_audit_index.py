#!/usr/bin/env python3
"""Real tests for generate_chatgpt_audit_index.py -- auto-index generator for
the ChatGPT Audit Workspace.

The SUT does `from chatgpt_audit_guard import ALLOWED_ROOT` at import time and
derives CATALOGUE_PATH / LOCAL_INDEX_PATH / SCHEMA_PATH from its own __file__
location and that imported ALLOWED_ROOT -- none of which point anywhere
real/safe in this sandboxed workspace. Every test here:
  1. loads a FRESH module instance via importlib file-path loading (after
     evicting chatgpt_audit_guard from sys.modules first);
  2. monkeypatches the SUT's own ALLOWED_ROOT (used directly inside scan())
     AND chatgpt_audit_guard's ALLOWED_ROOT (used by guarded_write's real
     path check) to the SAME real, throwaway tmp_path directory;
  3. monkeypatches SCHEMA_PATH to a real, throwaway schema yaml file mirror
     the real schema's documented shape (domain_subfolders);
  4. writes REAL AUDIT-*.yaml record files to disk under the throwaway
     ALLOWED_ROOT and lets scan()/build_catalogue()/main() actually read
     them back, parse real yaml, and write real output files -- nothing is
     stubbed beyond the two path constants above.
"""
import glob
import importlib.util
import json
import os
import sys
import uuid

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "generate_chatgpt_audit_index.py")

SUBFOLDERS = [
    "Architecture", "Business", "Capabilities", "Modules", "Metadata", "Database",
    "Rules", "Workflow", "APIs", "UI", "Reports", "Security", "Performance", "AI",
    "Prompts", "Routes", "Testing", "Dependencies", "Integrations", "Observability",
    "Release", "Recommendations", "History",
]
SCHEMA_DOC = {"domain_subfolders": SUBFOLDERS}


def _load_fresh(allowed_root, schema_path, catalogue_path, local_index_path):
    sys.modules.pop("chatgpt_audit_guard", None)

    name = f"sut_gen_chatgpt_audit_index_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # real `from chatgpt_audit_guard import ALLOWED_ROOT` runs here

    guard_mod = sys.modules["chatgpt_audit_guard"]
    guard_mod.ALLOWED_ROOT = str(allowed_root)
    mod.ALLOWED_ROOT = str(allowed_root)
    mod.SCHEMA_PATH = str(schema_path)
    mod.CATALOGUE_PATH = str(catalogue_path)
    mod.LOCAL_INDEX_PATH = str(local_index_path)
    return mod, guard_mod


@pytest.fixture
def env(tmp_path):
    allowed_root = tmp_path / "chatgpt-audit"
    allowed_root.mkdir()
    schema_path = tmp_path / "SCHEMA.yaml"
    with open(schema_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(SCHEMA_DOC, f)
    catalogue_path = tmp_path / "ai-os" / "CATALOGUE.yaml"
    local_index_path = allowed_root / "Metadata" / "INDEX.yaml"

    mod, guard_mod = _load_fresh(allowed_root, schema_path, catalogue_path, local_index_path)
    return mod, guard_mod, str(allowed_root), str(schema_path), str(catalogue_path), str(local_index_path)


def _write_record(allowed_root, subfolder, filename, doc):
    d = os.path.join(allowed_root, subfolder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)


# ---------------------------------------------------------------------------
# scan() -- real glob + real yaml parsing across the real directory tree
# ---------------------------------------------------------------------------

def test_scan_with_zero_records_returns_zero_counts_for_every_subfolder(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert records == []
    assert parse_errors == []
    assert subfolders == SUBFOLDERS
    assert all(count == 0 for count in by_subfolder.values())


def test_scan_finds_real_audit_record_and_extracts_summary_fields(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    _write_record(allowed_root, "Security", "AUDIT-000001-20260724T141530Z.yaml", {
        "audit_id": "AUDIT-000001", "timestamp": "2026-07-24T14:15:30Z",
        "repository_version": "main", "commit_hash": "abc123", "risk_score": 42,
        "priority": "high", "confidence_pct": 88,
        "missing_items": ["rate limiting"],  # not a SUMMARY_FIELDS entry -- must not leak into summary
    })
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert len(records) == 1
    rec = records[0]
    assert rec["subfolder"] == "Security"
    assert rec["audit_id"] == "AUDIT-000001"
    assert rec["risk_score"] == 42
    assert rec["priority"] == "high"
    assert "missing_items" not in rec
    assert by_subfolder["Security"] == 1
    assert parse_errors == []


def test_scan_two_records_in_different_subfolders_both_counted(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    _write_record(allowed_root, "Security", "AUDIT-000001-x.yaml", {"audit_id": "AUDIT-000001"})
    _write_record(allowed_root, "Reports", "AUDIT-000002-y.yaml", {"audit_id": "AUDIT-000002"})
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert len(records) == 2
    assert by_subfolder["Security"] == 1
    assert by_subfolder["Reports"] == 1
    assert by_subfolder["Architecture"] == 0


def test_scan_malformed_yaml_surfaces_as_parse_error_not_a_crash(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    d = os.path.join(allowed_root, "Security")
    os.makedirs(d, exist_ok=True)
    bad_path = os.path.join(d, "AUDIT-000099-broken.yaml")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("key: [unclosed list\n  another: value\n")  # real invalid YAML

    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert records == []
    assert len(parse_errors) == 1
    assert parse_errors[0]["path"] == os.path.join("Security", "AUDIT-000099-broken.yaml")
    assert "error" in parse_errors[0]
    assert by_subfolder["Security"] == 0  # malformed record is not counted as a valid one


def test_scan_ignores_files_not_matching_audit_prefix(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    d = os.path.join(allowed_root, "Security")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "NOTES.yaml"), "w") as f:
        f.write("audit_id: SHOULD-NOT-BE-PICKED-UP\n")
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert records == []


def test_scan_null_yaml_document_treated_as_empty_dict_not_crash(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    d = os.path.join(allowed_root, "Reports")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "AUDIT-000001-empty.yaml"), "w") as f:
        f.write("")  # yaml.safe_load("") -> None
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    assert len(records) == 1
    assert records[0]["audit_id"] is None
    assert parse_errors == []


# ---------------------------------------------------------------------------
# build_catalogue() -- real note text branches on total == 0 vs > 0
# ---------------------------------------------------------------------------

def test_build_catalogue_zero_records_note_mentions_blocker(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    doc = mod.build_catalogue(records, parse_errors, by_subfolder, subfolders)
    assert doc["summary"]["total_audit_records"] == 0
    assert "0 records is expected and correct today" in doc["summary"]["note"]
    assert doc["meta"]["workspace_root"] == allowed_root


def test_build_catalogue_nonzero_records_note_reports_real_count(env):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    _write_record(allowed_root, "Security", "AUDIT-000001-x.yaml", {"audit_id": "AUDIT-000001"})
    records, parse_errors, by_subfolder, subfolders = mod.scan()
    doc = mod.build_catalogue(records, parse_errors, by_subfolder, subfolders)
    assert doc["summary"]["total_audit_records"] == 1
    assert "1 real audit record(s) scanned from disk" in doc["summary"]["note"]
    assert doc["records"][0]["audit_id"] == "AUDIT-000001"


# ---------------------------------------------------------------------------
# main() -- real CLI, real writes to both output paths, idempotent re-run
# ---------------------------------------------------------------------------

def test_main_writes_real_catalogue_and_local_index_files(env, monkeypatch, capsys):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    _write_record(allowed_root, "Security", "AUDIT-000001-x.yaml", {
        "audit_id": "AUDIT-000001", "risk_score": 10,
    })
    monkeypatch.setattr(sys, "argv", ["generate_chatgpt_audit_index.py"])
    mod.main()

    out = json.loads(capsys.readouterr().out)
    assert out["total_audit_records"] == 1
    assert out["parse_errors"] == 0
    assert out["catalogue_written"] == catalogue_path
    assert out["local_index_written"] == local_index_path

    assert os.path.isfile(catalogue_path)
    assert os.path.isfile(local_index_path)

    with open(catalogue_path, encoding="utf-8") as f:
        cat_doc = yaml.safe_load(f)
    with open(local_index_path, encoding="utf-8") as f:
        idx_doc = yaml.safe_load(f)
    assert cat_doc["summary"]["total_audit_records"] == 1
    assert idx_doc == cat_doc  # local index is a real byte-identical (re-parsed) mirror


def test_main_is_idempotent_rerun_reflects_new_record_without_duplicating(env, monkeypatch, capsys):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    monkeypatch.setattr(sys, "argv", ["generate_chatgpt_audit_index.py"])

    mod.main()
    first = json.loads(capsys.readouterr().out)
    assert first["total_audit_records"] == 0

    _write_record(allowed_root, "Reports", "AUDIT-000001-x.yaml", {"audit_id": "AUDIT-000001"})
    mod.main()
    second = json.loads(capsys.readouterr().out)
    assert second["total_audit_records"] == 1


def test_main_local_index_write_goes_through_guarded_write_and_is_rejected_outside_root(env, monkeypatch, capsys, tmp_path):
    """--local-index-out pointing outside chatgpt_audit_guard's ALLOWED_ROOT
    must raise PathNotAllowed from the real guarded_write call inside
    main() -- proves the index writer really is routed through the guard,
    not through a plain open()."""
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    outside_index = str(tmp_path / "outside" / "INDEX.yaml")
    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_index.py", "--local-index-out", outside_index,
    ])
    with pytest.raises(guard_mod.PathNotAllowed):
        mod.main()
    assert not os.path.exists(outside_index)
    # the catalogue write happens before the guarded local-index write, so
    # it should still have landed on disk (real evidence of write ordering)
    assert os.path.isfile(catalogue_path)


def test_main_catalogue_out_override_creates_missing_parent_dirs(env, monkeypatch, capsys, tmp_path):
    mod, guard_mod, allowed_root, schema_path, catalogue_path, local_index_path = env
    custom_out = tmp_path / "deep" / "nested" / "custom_catalogue.yaml"
    monkeypatch.setattr(sys, "argv", [
        "generate_chatgpt_audit_index.py", "--catalogue-out", str(custom_out),
    ])
    mod.main()
    assert custom_out.is_file()
    out = json.loads(capsys.readouterr().out)
    assert out["catalogue_written"] == str(custom_out)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
