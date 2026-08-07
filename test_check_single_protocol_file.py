#!/usr/bin/env python3
"""Real tests for check_single_protocol_file.py. Every test uses a real,
throwaway temp directory populated with real YAML files -- never the live
/opt/veridian/ai-os/OWNER_DIRECTIVES directory. The module is pure (no
subprocess, no top-level side effects outside __main__), so it is safe to
importlib-load directly, per the house style in
test_apply_owner_dispatch_status_corrections.py."""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "check_single_protocol_file.py")


def _load():
    spec = importlib.util.spec_from_file_location("check_single_protocol_file_scratch", SUT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sut():
    return _load()


def _write(dirpath, name, content):
    path = os.path.join(dirpath, name)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------

def test_classify_document_class_protocol(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        document_class: protocol
        meta:
          status: ACTIVE
        title: Owner AI Protocol
        """)
    assert sut._classify(p) == "protocol"


def test_classify_document_class_memory(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        document_class: memory
        title: Owner AI Memory
        """)
    assert sut._classify(p) == "memory"


def test_classify_articles_list_implies_protocol(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        articles:
          - id: 1
            text: must not lie
          - id: 2
            text: must verify
        """)
    assert sut._classify(p) == "protocol"


def test_classify_meta_status_non_negotiable_implies_protocol(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        meta:
          status: NON_NEGOTIABLE
        some_other_key: true
        """)
    assert sut._classify(p) == "protocol"


def test_classify_incidents_key_implies_memory(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        incidents:
          - date: 2026-07-26
            summary: scattered files consolidated
        """)
    assert sut._classify(p) == "memory"


def test_classify_lessons_key_implies_memory(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        lessons:
          - do not fabricate hang claims
        """)
    assert sut._classify(p) == "memory"


def test_classify_plain_unrelated_yaml_is_none(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        some_key: some_value
        list_key:
          - 1
          - 2
        """)
    assert sut._classify(p) is None


def test_classify_non_dict_top_level_is_none(sut, tmp_path):
    p = _write(tmp_path, "a.yaml", """
        - item1
        - item2
        """)
    assert sut._classify(p) is None


def test_classify_unparseable_yaml_is_none_not_raise(sut, tmp_path):
    p = _write(tmp_path, "broken.yaml", """
        this: [is, not
        valid: yaml: at: all
        """)
    assert sut._classify(p) is None


def test_classify_missing_file_is_none_not_raise(sut, tmp_path):
    missing = os.path.join(tmp_path, "does_not_exist.yaml")
    assert sut._classify(missing) is None


# ---------------------------------------------------------------------------
# find_documents
# ---------------------------------------------------------------------------

def test_find_documents_real_directory_with_one_of_each(sut, tmp_path):
    _write(tmp_path, "PROTOCOL_OWNER_AI.yaml", """
        document_class: protocol
        articles: []
        """)
    _write(tmp_path, "MEMORY_OWNER_AI.yaml", """
        document_class: memory
        incidents: []
        """)
    protocol_files, memory_files = sut.find_documents(str(tmp_path))
    assert protocol_files == ["PROTOCOL_OWNER_AI.yaml"]
    assert memory_files == ["MEMORY_OWNER_AI.yaml"]


def test_find_documents_ignores_txt_files_and_subdirectories(sut, tmp_path):
    _write(tmp_path, "PROTOCOL_OWNER_AI.yaml", """
        document_class: protocol
        articles: []
        """)
    _write(tmp_path, "legacy_notes.txt", "SUPERSEDED BY PROTOCOL_OWNER_AI.yaml\narticles:\nincidents:\n")
    sub = tmp_path / "some_subdir.yaml"
    sub.mkdir()
    protocol_files, memory_files = sut.find_documents(str(tmp_path))
    assert protocol_files == ["PROTOCOL_OWNER_AI.yaml"]
    assert memory_files == []


def test_find_documents_detects_real_duplicates_sorted(sut, tmp_path):
    _write(tmp_path, "z_protocol_dupe.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "a_protocol_original.yml", "document_class: protocol\narticles: []\n")
    protocol_files, memory_files = sut.find_documents(str(tmp_path))
    assert protocol_files == ["a_protocol_original.yml", "z_protocol_dupe.yaml"]
    assert memory_files == []


def test_find_documents_nonexistent_directory_returns_empty_lists(sut, tmp_path):
    missing_dir = str(tmp_path / "does_not_exist_at_all")
    protocol_files, memory_files = sut.find_documents(missing_dir)
    assert protocol_files == []
    assert memory_files == []


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------

def test_check_passes_with_exactly_one_of_each(sut, tmp_path):
    _write(tmp_path, "PROTOCOL_OWNER_AI.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "MEMORY_OWNER_AI.yaml", "document_class: memory\nincidents: []\n")
    ok, result = sut.check(str(tmp_path))
    assert ok is True
    assert result["problems"] == []
    assert result["protocol_files"] == ["PROTOCOL_OWNER_AI.yaml"]
    assert result["memory_files"] == ["MEMORY_OWNER_AI.yaml"]


def test_check_fails_with_zero_protocol_files(sut, tmp_path):
    _write(tmp_path, "MEMORY_OWNER_AI.yaml", "document_class: memory\nincidents: []\n")
    ok, result = sut.check(str(tmp_path))
    assert ok is False
    assert len(result["problems"]) == 1
    assert "expected exactly 1 protocol document" in result["problems"][0]
    assert "found 0" in result["problems"][0]


def test_check_fails_with_two_protocol_files_names_both_in_problem(sut, tmp_path):
    _write(tmp_path, "PROTOCOL_OLD.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "PROTOCOL_NEW.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "MEMORY_OWNER_AI.yaml", "document_class: memory\nincidents: []\n")
    ok, result = sut.check(str(tmp_path))
    assert ok is False
    assert len(result["problems"]) == 1
    assert "PROTOCOL_OLD.yaml" in result["problems"][0]
    assert "PROTOCOL_NEW.yaml" in result["problems"][0]
    assert result["protocol_files"] == ["PROTOCOL_NEW.yaml", "PROTOCOL_OLD.yaml"]


def test_check_fails_on_both_axes_simultaneously(sut, tmp_path):
    _write(tmp_path, "MEMORY_A.yaml", "document_class: memory\nincidents: []\n")
    _write(tmp_path, "MEMORY_B.yaml", "document_class: memory\nlessons: []\n")
    ok, result = sut.check(str(tmp_path))
    assert ok is False
    assert len(result["problems"]) == 2
    assert any("protocol" in p for p in result["problems"])
    assert any("memory" in p for p in result["problems"])


def test_check_on_missing_directory_reports_both_missing(sut, tmp_path):
    missing_dir = str(tmp_path / "nope")
    ok, result = sut.check(missing_dir)
    assert ok is False
    assert result["directory"] == missing_dir
    assert len(result["problems"]) == 2


# ---------------------------------------------------------------------------
# CLI (__main__), invoked as a real subprocess against a real temp --dir
# ---------------------------------------------------------------------------

def test_cli_exit_0_and_real_json_stdout_with_exactly_one_of_each(tmp_path):
    _write(tmp_path, "PROTOCOL_OWNER_AI.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "MEMORY_OWNER_AI.yaml", "document_class: memory\nincidents: []\n")
    result = subprocess.run(
        [sys.executable, SUT_PATH, "--dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["protocol_files"] == ["PROTOCOL_OWNER_AI.yaml"]
    assert payload["memory_files"] == ["MEMORY_OWNER_AI.yaml"]
    assert payload["problems"] == []


def test_cli_exit_1_on_duplicate_memory_files(tmp_path):
    _write(tmp_path, "PROTOCOL_OWNER_AI.yaml", "document_class: protocol\narticles: []\n")
    _write(tmp_path, "MEMORY_OLD.yaml", "document_class: memory\nincidents: []\n")
    _write(tmp_path, "MEMORY_NEW.yaml", "document_class: memory\nincidents: []\n")
    result = subprocess.run(
        [sys.executable, SUT_PATH, "--dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["memory_files"] == ["MEMORY_NEW.yaml", "MEMORY_OLD.yaml"]
    assert len(payload["problems"]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
