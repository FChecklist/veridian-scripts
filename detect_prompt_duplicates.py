#!/usr/bin/env python3
"""
detect_prompt_duplicates.py -- duplicate-prompt/duplicate-intent detection for
the VERIDIAN ChatGPT Prompt Library (task-20260724-141538-chatgpt-prompt-library-infrastructure,
instruction INS-20260724-141341-5f67).

Two independent detectors, run over ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml-shaped
prompt rows:
  1. exact_duplicate_prompt: normalized (whitespace-collapsed, casefolded) Prompt
     text is byte-identical to another row's. Real duplicates, not near-misses.
  2. intent_collision: same (Capability, Intent) pair used by >1 Prompt ID with
     DIFFERENT Prompt text -- not necessarily wrong, but worth a human look
     (same declared intent phrased two different ways for the same capability).

No real prompts exist yet (see ai-os/CHATGPT_PROMPTLIB_BLOCKER_2026-07-24.yaml --
no OPENAI_API_KEY), so there is nothing real to run this against today. Proven
correct instead against a small synthetic fixture: --test-fixture plants 3 fake
rows (2 exact duplicates of the same capability/intent, 1 distinct row) and
asserts the detector finds exactly the planted duplicate group and nothing else,
exiting non-zero if the logic is wrong rather than silently reporting a passing
result.

Run:
  python3 scripts/detect_prompt_duplicates.py --csv-dir /opt/veridian/chatgpt-prompt-library/CSV
  python3 scripts/detect_prompt_duplicates.py --test-fixture
Exit code: 0 if the run completed and (for --test-fixture) the planted
duplicate was correctly found; 1 if the fixture assertion fails.
"""
import argparse
import csv
import glob
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_promptlib_guard import SANDBOX_ROOT, guarded_write_json  # noqa: E402

DEFAULT_CSV_DIR = os.path.join(SANDBOX_ROOT, "CSV")

# Synthetic fixture: rows 1 and 2 are an EXACT duplicate (same normalized
# Prompt text, same Capability+Intent) -- planted on purpose. Row 3 is
# distinct (different capability, different prompt) and must NOT be flagged.
TEST_FIXTURE_ROWS = [
    {
        "Prompt ID": "PROMPT-TEST-0001",
        "Prompt": "Calculate the gratuity payout for <Users.Name> at <Clients.Name>.",
        "Intent": "calculate_gratuity",
        "Capability": "gratuity_calculator",
    },
    {
        "Prompt ID": "PROMPT-TEST-0002",
        "Prompt": "  Calculate the gratuity payout for <Users.Name> at <Clients.Name>.  ",
        "Intent": "calculate_gratuity",
        "Capability": "gratuity_calculator",
    },
    {
        "Prompt ID": "PROMPT-TEST-0003",
        "Prompt": "Explain the current GST rate applicable to <Clients.Name>'s invoice.",
        "Intent": "explain_gst_rate",
        "Capability": "gst_calculation_engine",
    },
]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def load_prompts_from_csv_dir(csv_dir):
    rows = []
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    for path in files:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["_source_file"] = os.path.basename(path)
                rows.append(row)
    return rows, files


def load_prompts_from_rows(rows):
    return list(rows), ["<inline_fixture>"]


def detect_exact_duplicates(rows):
    groups = {}
    for row in rows:
        key = _normalize(row.get("Prompt"))
        if not key:
            continue
        groups.setdefault(key, []).append(row.get("Prompt ID"))
    return [
        {"normalized_prompt": key, "prompt_ids": ids}
        for key, ids in groups.items()
        if len(ids) > 1
    ]


def detect_intent_collisions(rows):
    groups = {}
    for row in rows:
        cap = (row.get("Capability") or "").strip()
        intent = (row.get("Intent") or "").strip()
        if not cap or not intent:
            continue
        key = (cap, intent)
        groups.setdefault(key, {})[row.get("Prompt ID")] = _normalize(row.get("Prompt"))

    collisions = []
    for (cap, intent), prompt_id_to_norm in groups.items():
        distinct_prompt_texts = set(prompt_id_to_norm.values())
        if len(prompt_id_to_norm) > 1 and len(distinct_prompt_texts) > 1:
            collisions.append({
                "capability": cap,
                "intent": intent,
                "prompt_ids": sorted(prompt_id_to_norm.keys()),
                "distinct_prompt_text_count": len(distinct_prompt_texts),
            })
    return collisions


def run_detection(rows, source_files, source_label):
    exact_duplicates = detect_exact_duplicates(rows)
    intent_collisions = detect_intent_collisions(rows)
    return {
        "meta": {
            "script": "scripts/detect_prompt_duplicates.py",
            "run_ts": _now_iso(),
            "source": source_label,
            "source_files": source_files,
            "total_rows_scanned": len(rows),
        },
        "summary": {
            "exact_duplicate_groups": len(exact_duplicates),
            "intent_collision_groups": len(intent_collisions),
        },
        "exact_duplicates": exact_duplicates,
        "intent_collisions": intent_collisions,
    }


def run_test_fixture():
    rows, files = load_prompts_from_rows(TEST_FIXTURE_ROWS)
    report = run_detection(rows, files, "synthetic_test_fixture (--test-fixture, not real data)")

    errors = []
    exact = report["exact_duplicates"]
    if len(exact) != 1:
        errors.append(f"expected exactly 1 exact-duplicate group, found {len(exact)}")
    else:
        found_ids = set(exact[0]["prompt_ids"])
        expected_ids = {"PROMPT-TEST-0001", "PROMPT-TEST-0002"}
        if found_ids != expected_ids:
            errors.append(f"expected duplicate group {expected_ids}, found {found_ids}")

    non_duplicate_id = "PROMPT-TEST-0003"
    flagged_ids = {pid for g in exact for pid in g["prompt_ids"]}
    if non_duplicate_id in flagged_ids:
        errors.append(f"{non_duplicate_id} is distinct and must not be flagged as a duplicate")

    report["test_fixture_assertions"] = {
        "expected_duplicate_group": ["PROMPT-TEST-0001", "PROMPT-TEST-0002"],
        "expected_non_duplicate": non_duplicate_id,
        "passed": not errors,
        "errors": errors,
    }

    print(json.dumps(report, indent=2))

    if errors:
        print(f"FAIL: {len(errors)} assertion(s) failed: {errors}", file=sys.stderr)
        sys.exit(1)

    print("PASS: synthetic fixture's planted duplicate (PROMPT-TEST-0001, PROMPT-TEST-0002) "
          "correctly detected; distinct row PROMPT-TEST-0003 correctly not flagged.", file=sys.stderr)
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR)
    parser.add_argument("--test-fixture", action="store_true",
                         help="run against the small synthetic fixture instead of real CSV/ data, "
                              "and assert the planted duplicate is found")
    args = parser.parse_args()

    if args.test_fixture:
        run_test_fixture()
        return

    rows, files = load_prompts_from_csv_dir(args.csv_dir)
    report = run_detection(rows, [os.path.basename(p) for p in files], args.csv_dir)
    print(json.dumps(report, indent=2))

    guarded_write_json(os.path.join(SANDBOX_ROOT, "Duplicates", "PROMPT_DUPLICATES_REPORT.json"), report)
    safe_ts = report["meta"]["run_ts"].replace(":", "-")
    guarded_write_json(
        os.path.join(SANDBOX_ROOT, "History", f"PROMPT_DUPLICATES_REPORT_{safe_ts}.json"), report
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
