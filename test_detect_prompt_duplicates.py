"""Real tests for detect_prompt_duplicates.py.

Every test calls the script's real functions in-process or invokes it as a
real subprocess. The one thing these tests deliberately never do is call
main() without --test-fixture (or otherwise invoke guarded_write_json)
against SANDBOX_ROOT: chatgpt_promptlib_guard.SANDBOX_ROOT is a hard-coded
absolute path ("/opt/veridian/chatgpt-prompt-library") with NO env-var
override, and it is a real, live, shared directory on this box (confirmed
to exist, with real prior content under Duplicates/ and History/) -- writing
into it from a test would corrupt real data outside this git workspace. The
--test-fixture code path never calls guarded_write_json (only main()'s
non-fixture branch does), so it is safe to exercise as a real subprocess.
"""
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "detect_prompt_duplicates.py"

sys.path.insert(0, str(REPO_ROOT))
import detect_prompt_duplicates as dpd  # noqa: E402


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# _normalize -- real function
# ---------------------------------------------------------------------------

def test_normalize_collapses_whitespace_and_casefolds():
    assert dpd._normalize("  Calculate   the\n\tgratuity  ") == "calculate the gratuity"
    assert dpd._normalize("ABC def") == "abc def"
    assert dpd._normalize(None) == ""
    assert dpd._normalize("") == ""


# ---------------------------------------------------------------------------
# detect_exact_duplicates -- real function, real rows
# ---------------------------------------------------------------------------

def test_detect_exact_duplicates_finds_normalized_match_and_ignores_distinct_row():
    rows = [
        {"Prompt ID": "P1", "Prompt": "Explain the GST rate for <Clients.Name>."},
        {"Prompt ID": "P2", "Prompt": "  explain the GST rate for <Clients.Name>.  "},
        {"Prompt ID": "P3", "Prompt": "Calculate gratuity for <Users.Name>."},
    ]
    groups = dpd.detect_exact_duplicates(rows)
    assert len(groups) == 1
    assert set(groups[0]["prompt_ids"]) == {"P1", "P2"}
    assert groups[0]["normalized_prompt"] == "explain the gst rate for <clients.name>."


def test_detect_exact_duplicates_skips_blank_or_missing_prompt_text():
    rows = [
        {"Prompt ID": "P1", "Prompt": "   "},
        {"Prompt ID": "P2"},
        {"Prompt ID": "P3", "Prompt": "   "},
    ]
    # Both blank-prompt rows normalize to "" and "if not key: continue" skips
    # them individually -- they must never be reported as a duplicate group
    # of each other.
    assert dpd.detect_exact_duplicates(rows) == []


def test_detect_exact_duplicates_no_false_positive_for_all_unique_rows():
    rows = [
        {"Prompt ID": "P1", "Prompt": "Alpha prompt text."},
        {"Prompt ID": "P2", "Prompt": "Beta prompt text."},
        {"Prompt ID": "P3", "Prompt": "Gamma prompt text."},
    ]
    assert dpd.detect_exact_duplicates(rows) == []


# ---------------------------------------------------------------------------
# detect_intent_collisions -- real function, real rows
# ---------------------------------------------------------------------------

def test_detect_intent_collisions_flags_same_intent_different_wording():
    rows = [
        {"Prompt ID": "P1", "Capability": "gratuity_calculator", "Intent": "calculate_gratuity",
         "Prompt": "Work out the gratuity for <Users.Name>."},
        {"Prompt ID": "P2", "Capability": "gratuity_calculator", "Intent": "calculate_gratuity",
         "Prompt": "Compute the gratuity payout for <Users.Name>."},
    ]
    collisions = dpd.detect_intent_collisions(rows)
    assert len(collisions) == 1
    assert collisions[0]["capability"] == "gratuity_calculator"
    assert collisions[0]["intent"] == "calculate_gratuity"
    assert collisions[0]["prompt_ids"] == ["P1", "P2"]
    assert collisions[0]["distinct_prompt_text_count"] == 2


def test_detect_intent_collisions_not_flagged_when_text_is_identical():
    # Same capability+intent AND same normalized text -- an exact duplicate,
    # not an intent collision (distinct_prompt_texts stays at 1).
    rows = [
        {"Prompt ID": "P1", "Capability": "gst_calculation_engine", "Intent": "explain_gst_rate",
         "Prompt": "Explain the GST rate."},
        {"Prompt ID": "P2", "Capability": "gst_calculation_engine", "Intent": "explain_gst_rate",
         "Prompt": "explain the gst rate."},
    ]
    assert dpd.detect_intent_collisions(rows) == []


def test_detect_intent_collisions_skips_rows_missing_capability_or_intent():
    rows = [
        {"Prompt ID": "P1", "Capability": "", "Intent": "calculate_gratuity", "Prompt": "a"},
        {"Prompt ID": "P2", "Capability": "gratuity_calculator", "Intent": "", "Prompt": "b"},
    ]
    assert dpd.detect_intent_collisions(rows) == []


# ---------------------------------------------------------------------------
# load_prompts_from_csv_dir -- real filesystem I/O against a real temp dir
# ---------------------------------------------------------------------------

def test_load_prompts_from_csv_dir_reads_real_csv_rows_from_multiple_files(tmp_path):
    csv_dir = tmp_path / "CSV"
    csv_dir.mkdir()
    fieldnames = ["Prompt ID", "Prompt", "Intent", "Capability"]

    with open(csv_dir / "a_prompts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerow({"Prompt ID": "A1", "Prompt": "Alpha", "Intent": "i1", "Capability": "c1"})

    with open(csv_dir / "b_prompts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerow({"Prompt ID": "B1", "Prompt": "Beta", "Intent": "i2", "Capability": "c2"})
        w.writerow({"Prompt ID": "B2", "Prompt": "Gamma", "Intent": "i3", "Capability": "c3"})

    rows, files = dpd.load_prompts_from_csv_dir(str(csv_dir))

    assert len(rows) == 3
    assert [Path(f).name for f in files] == ["a_prompts.csv", "b_prompts.csv"]
    ids = [r["Prompt ID"] for r in rows]
    assert ids == ["A1", "B1", "B2"]
    assert rows[0]["_source_file"] == "a_prompts.csv"
    assert rows[1]["_source_file"] == "b_prompts.csv"


def test_load_prompts_from_csv_dir_empty_dir_returns_no_rows(tmp_path):
    empty_dir = tmp_path / "empty_csv"
    empty_dir.mkdir()
    rows, files = dpd.load_prompts_from_csv_dir(str(empty_dir))
    assert rows == []
    assert files == []


# ---------------------------------------------------------------------------
# run_detection -- real end-to-end (in-process, no filesystem writes)
# ---------------------------------------------------------------------------

def test_run_detection_end_to_end_reports_correct_summary_counts():
    rows = [
        {"Prompt ID": "P1", "Capability": "cap_a", "Intent": "int_a", "Prompt": "Do the thing."},
        {"Prompt ID": "P2", "Capability": "cap_a", "Intent": "int_a", "Prompt": "do the thing."},
        {"Prompt ID": "P3", "Capability": "cap_a", "Intent": "int_b", "Prompt": "Do a different thing."},
        {"Prompt ID": "P4", "Capability": "cap_a", "Intent": "int_b", "Prompt": "Perform a different thing."},
    ]
    report = dpd.run_detection(rows, ["fake.csv"], "unit-test-source")
    assert report["summary"]["exact_duplicate_groups"] == 1
    assert report["summary"]["intent_collision_groups"] == 1
    assert report["meta"]["total_rows_scanned"] == 4
    assert report["meta"]["source"] == "unit-test-source"
    assert set(report["exact_duplicates"][0]["prompt_ids"]) == {"P1", "P2"}
    assert set(report["intent_collisions"][0]["prompt_ids"]) == {"P3", "P4"}


# ---------------------------------------------------------------------------
# CLI --test-fixture -- the real entry point, real subprocess. Safe: this
# branch never touches SANDBOX_ROOT (see module docstring).
# ---------------------------------------------------------------------------

def test_cli_test_fixture_real_subprocess_finds_the_planted_duplicate():
    result = _run(["--test-fixture"])
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "PASS" in result.stderr
    assert "correctly detected" in result.stderr

    report = json.loads(result.stdout)
    assert report["test_fixture_assertions"]["passed"] is True
    assert report["test_fixture_assertions"]["errors"] == []
    assert report["summary"]["exact_duplicate_groups"] == 1
    found_ids = set(report["exact_duplicates"][0]["prompt_ids"])
    assert found_ids == {"PROMPT-TEST-0001", "PROMPT-TEST-0002"}
    flagged = {pid for g in report["exact_duplicates"] for pid in g["prompt_ids"]}
    assert "PROMPT-TEST-0003" not in flagged
    assert report["summary"]["intent_collision_groups"] == 0
