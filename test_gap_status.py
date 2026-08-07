"""Real tests for gap-status.py.

gap-status.py is a top-level (no functions, no __main__ guard) script that
hardcodes an absolute path, /opt/veridian/ai-os/gap_queue.yaml, and prints a
status summary of a gap queue. It is CLI-only / import-time-side-effecting,
so it's exercised here with runpy.run_path() (a real, fresh execution of its
actual top-level code each time) while monkeypatching *only* the true
external boundary -- the hardcoded absolute file path -- so a real, throwaway
tmp_path YAML file is read instead of ever touching the live file. The
script's own grouping/printing logic runs for real and unmodified.
"""
import builtins
import os
import runpy

import pytest
import yaml

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gap-status.py")
LIVE_PATH = "/opt/veridian/ai-os/gap_queue.yaml"


def _run_with_fake_queue_file(monkeypatch, capsys, fake_path):
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if str(path) == LIVE_PATH:
            return real_open(fake_path, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    runpy.run_path(SCRIPT, run_name="__main__")
    return capsys.readouterr()


def _write_queue(tmp_path, doc):
    path = tmp_path / "gap_queue.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_prints_completion_ratio_and_total_findings(tmp_path, monkeypatch, capsys):
    doc = {
        "total_findings": 42,
        "queue": [
            {"id": "g1", "status": "completed"},
            {"id": "g2", "status": "completed"},
            {"id": "g3", "status": "needs_retry"},
        ],
    }
    fake = _write_queue(tmp_path, doc)
    out = _run_with_fake_queue_file(monkeypatch, capsys, fake)
    assert "2/3 groups completed (42 total findings across all groups)" in out.out


def test_lists_each_actionable_status_with_its_item_ids(tmp_path, monkeypatch, capsys):
    doc = {
        "total_findings": 10,
        "queue": [
            {"id": "g1", "status": "completed"},
            {"id": "g2", "status": "awaiting_human_approval"},
            {"id": "g3", "status": "stuck_needs_human"},
            {"id": "g4", "status": "needs_retry"},
            {"id": "g5", "status": "dispatch_failed"},
            {"id": "g6", "status": "skipped_possible_duplicate"},
        ],
    }
    fake = _write_queue(tmp_path, doc)
    out = _run_with_fake_queue_file(monkeypatch, capsys, fake).out
    assert "awaiting_human_approval (1):" in out
    assert "  g2" in out
    assert "stuck_needs_human (1):" in out
    assert "  g3" in out
    assert "needs_retry (1):" in out
    assert "  g4" in out
    assert "dispatch_failed (1):" in out
    assert "  g5" in out
    assert "skipped_possible_duplicate (1):" in out
    assert "  g6" in out


def test_status_not_in_interesting_list_is_not_printed_as_its_own_section(tmp_path, monkeypatch, capsys):
    """'completed' items are counted in the ratio but never get their own
    per-item section (only the 5 actionable statuses do)."""
    doc = {
        "total_findings": 5,
        "queue": [
            {"id": "g1", "status": "completed"},
            {"id": "g2", "status": "in_progress"},  # not in the tracked-status tuple at all
        ],
    }
    fake = _write_queue(tmp_path, doc)
    out = _run_with_fake_queue_file(monkeypatch, capsys, fake).out
    # Only the summary line mentions "completed" (as part of "N/M groups
    # completed"); there must be no separate "completed (n):" section header.
    assert "\ncompleted (" not in out
    assert "in_progress" not in out


def test_missing_total_findings_key_falls_back_to_unknown(tmp_path, monkeypatch, capsys):
    doc = {"queue": [{"id": "g1", "status": "needs_retry"}]}
    fake = _write_queue(tmp_path, doc)
    out = _run_with_fake_queue_file(monkeypatch, capsys, fake).out
    assert "0/1 groups completed (unknown total findings across all groups)" in out.splitlines()[0]


def test_empty_queue_does_not_crash(tmp_path, monkeypatch, capsys):
    doc = {"total_findings": 0, "queue": []}
    fake = _write_queue(tmp_path, doc)
    out = _run_with_fake_queue_file(monkeypatch, capsys, fake).out
    assert "0/0 groups completed (0 total findings across all groups)" in out


def test_item_missing_status_key_raises_keyerror(tmp_path, monkeypatch, capsys):
    """Real failure case: an item with no 'status' field crashes the script
    with a real, unhandled KeyError -- documented here, not papered over."""
    doc = {"total_findings": 1, "queue": [{"id": "g1"}]}
    fake = _write_queue(tmp_path, doc)
    with pytest.raises(KeyError):
        _run_with_fake_queue_file(monkeypatch, capsys, fake)


def test_queue_missing_top_level_key_raises_keyerror(tmp_path, monkeypatch, capsys):
    """Real failure case: a YAML document with no top-level 'queue' key
    crashes with a real, unhandled KeyError on doc["queue"]."""
    doc = {"total_findings": 1, "not_queue": []}
    fake = _write_queue(tmp_path, doc)
    with pytest.raises(KeyError):
        _run_with_fake_queue_file(monkeypatch, capsys, fake)
