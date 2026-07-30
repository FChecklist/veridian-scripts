#!/usr/bin/env python3
"""
generate_prompt_coverage_report.py -- real coverage report for the VERIDIAN
ChatGPT Prompt Library (task-20260724-141538-chatgpt-prompt-library-infrastructure,
instruction INS-20260724-141341-5f67).

Reads TWO real, already-live data sources -- never invents either:
  1. The real capability registry: `python3 scripts/superboss-register.py
     list-capabilities` (ai-os/memory/superboss-register.sqlite, populated by
     task-20260724-083420-phase1-capability-registry-live-wiring). Shelled out to
     rather than opening the sqlite file directly, same discipline
     ai-os-scripts/populate_capability_registry.py already uses: the CLI is the
     single source of truth for that table, not a second reader of its schema.
  2. The real prompt CSVs: /opt/veridian/chatgpt-prompt-library/CSV/*.csv,
     conforming to ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml's csv_schema.

Computes three metrics, each with an explicit, honest definition (documented
inline, not just a bare percentage):
  - Capability-Coverage-pct: real capabilities with >=1 real prompt row citing
    them (Capability column exact match) / total real capabilities.
  - Prompt-Coverage-pct: real prompt rows generated so far / TARGET_TOTAL_PROMPTS
    (10,000 -- the volume figure this task's own spec names as the eventual
    generation target; this metric tracks progress toward that number, it is
    NOT the same thing as Capability-Coverage-pct).
  - Intent-Coverage-pct: real capabilities with >=1 prompt row whose Intent
    column is non-empty / total real capabilities (a proxy for "at least one
    canonical intent captured per capability" -- no separate canonical intent
    taxonomy exists yet, see ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml's
    Intent column description).

With zero real prompts (the honest state as of this task -- no OPENAI_API_KEY
exists yet, see ai-os/CHATGPT_PROMPTLIB_BLOCKER_2026-07-24.yaml), all three are
0.0 and every real capability is listed under missing_capabilities. This is the
correct baseline, not a bug.

Output is written through scripts/chatgpt_promptlib_guard.py's guarded_write
(never a bare open()) to:
  - Coverage/PROMPT_COVERAGE_REPORT.json (latest, overwritten each run)
  - History/PROMPT_COVERAGE_REPORT_<run_ts>.json (timestamped, never overwritten)

Run: python3 scripts/generate_prompt_coverage_report.py [--csv-dir <dir>] [--run-ts <iso ts>]
Exit code: always 0 -- this is a report, not a gate. A failure to reach the
capability registry (e.g. superboss-register.py errors) is the one case that
exits non-zero, since without it there is nothing honest to report.
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_promptlib_guard import SANDBOX_ROOT, guarded_write_json  # noqa: E402

SUPERBOSS = os.path.join(SCRIPT_DIR, "superboss-register.py")
DEFAULT_CSV_DIR = os.path.join(SANDBOX_ROOT, "CSV")
TARGET_TOTAL_PROMPTS = 10000


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_real_capabilities():
    """Real capability_name list from the live capability_registry table, via
    the same CLI populate_capability_registry.py uses -- never a direct sqlite
    read, so this always reflects whatever the live registry actually has."""
    proc = subprocess.run(["python3", SUPERBOSS, "list-capabilities"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"superboss-register.py list-capabilities failed: {proc.stderr[-1000:]}")
    data = json.loads(proc.stdout)
    return data.get("capabilities", [])


def load_real_prompts(csv_dir):
    """Every real prompt row across all CSV/*.csv files. Returns [] (not an
    error) if the directory has no CSVs yet -- that is the honest baseline
    this script exists to report, not a failure."""
    rows = []
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    for path in files:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["_source_file"] = os.path.basename(path)
                rows.append(row)
    return rows, files


def compute_coverage(capabilities, prompts):
    total_capabilities = len(capabilities)
    capability_names = [c["capability_name"] for c in capabilities]

    prompts_per_capability = {name: 0 for name in capability_names}
    intents_per_capability = {name: set() for name in capability_names}
    for row in prompts:
        cap = (row.get("Capability") or "").strip()
        if cap in prompts_per_capability:
            prompts_per_capability[cap] += 1
            intent = (row.get("Intent") or "").strip()
            if intent:
                intents_per_capability[cap].add(intent)

    covered_capabilities = [name for name, n in prompts_per_capability.items() if n > 0]
    missing_capabilities = [name for name, n in prompts_per_capability.items() if n == 0]
    capabilities_with_intent = [name for name, s in intents_per_capability.items() if s]

    capability_coverage_pct = (
        round(100.0 * len(covered_capabilities) / total_capabilities, 4) if total_capabilities else 0.0
    )
    prompt_coverage_pct = round(100.0 * len(prompts) / TARGET_TOTAL_PROMPTS, 4)
    intent_coverage_pct = (
        round(100.0 * len(capabilities_with_intent) / total_capabilities, 4) if total_capabilities else 0.0
    )

    return {
        "total_real_capabilities": total_capabilities,
        "total_real_prompts": len(prompts),
        "target_total_prompts": TARGET_TOTAL_PROMPTS,
        "capability_coverage_pct": capability_coverage_pct,
        "prompt_coverage_pct": prompt_coverage_pct,
        "intent_coverage_pct": intent_coverage_pct,
        "covered_capabilities": sorted(covered_capabilities),
        "missing_capabilities": sorted(missing_capabilities),
        "prompts_per_capability": prompts_per_capability,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR)
    parser.add_argument("--run-ts", default=None, help="override run timestamp (for reproducible test runs)")
    args = parser.parse_args()

    run_ts = args.run_ts or _now_iso()

    try:
        capabilities = load_real_capabilities()
    except (RuntimeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": "capability_registry_unreachable", "detail": str(e)}, indent=2))
        sys.exit(1)

    prompts, csv_files = load_real_prompts(args.csv_dir)
    coverage = compute_coverage(capabilities, prompts)

    report = {
        "meta": {
            "script": "scripts/generate_prompt_coverage_report.py",
            "run_ts": run_ts,
            "capability_source": "python3 scripts/superboss-register.py list-capabilities (live sqlite)",
            "prompt_csv_dir": args.csv_dir,
            "prompt_csv_files_found": [os.path.basename(p) for p in csv_files],
            "schema": "ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml",
            "metric_definitions": {
                "capability_coverage_pct": "real capabilities with >=1 real prompt row citing them / "
                                            "total real capabilities",
                "prompt_coverage_pct": f"real prompt rows generated / target_total_prompts "
                                        f"({TARGET_TOTAL_PROMPTS})",
                "intent_coverage_pct": "real capabilities with >=1 prompt row whose Intent is non-empty / "
                                       "total real capabilities",
            },
        },
        "coverage": coverage,
    }

    print(json.dumps(report, indent=2))

    guarded_write_json(os.path.join(SANDBOX_ROOT, "Coverage", "PROMPT_COVERAGE_REPORT.json"), report)
    safe_ts = run_ts.replace(":", "-")
    guarded_write_json(
        os.path.join(SANDBOX_ROOT, "History", f"PROMPT_COVERAGE_REPORT_{safe_ts}.json"), report
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
