#!/usr/bin/env python3
"""
ingest_chatgpt_promptbatch_response.py -- manual-bridge response ingester for
the VERIDIAN ChatGPT Prompt Library (INS-20260724-214122-f0ed,
task-20260724-214215-chatgpt-manual-bridge-workflow).

Pairs with scripts/generate_chatgpt_promptbatch_request.py. The Owner pastes
the request text (produced by that script) into a free ChatGPT web session,
saves the CSV (or YAML list-of-rows) reply to a file (or pipes it via
stdin), and this script:
  1. parses it (CSV or YAML, auto-detected),
  2. validates every row has all required
     ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml columns (17 as of
     task-20260725-180624's End User Role + Industry extension), a real
     Capability (either a real capability_name from the live
     capability_registry, or the documented "<module_slug>_unregistered"
     fallback for one of the 25 real compliance-tracker/src/lib/engines/*.ts
     modules -- see that schema's anchor_decision_2026-07-25 -- never any
     other invented value), a real End User Role (must be one of the
     schema's own enum values, never invented), and a well-formed AI
     Required Yes/No + Confidence,
  3. rejects any row using a hardcoded/placeholder-style company or person
     name (or an unregistered <Entity.Attribute>-shaped token) instead of a
     real registered placeholder -- reusing
     ai-os/TERMINOLOGY_GUARDRAIL_2026-07-24.py's own pattern families and
     registered-placeholder loader rather than reimplementing that
     detection,
  4. rejects any row whose Prompt ID collides with one already on disk,
  5. appends accepted rows into CSV/PROMPTS_<capability>_<date>.csv through
     scripts/chatgpt_promptlib_guard.py's guarded_write (never overwrites --
     appends to an existing same-day batch file or creates a new one with a
     header),
  6. refreshes Coverage/ and Duplicates/ for real (no --test-fixture) via
     scripts/generate_prompt_coverage_report.py and
     scripts/detect_prompt_duplicates.py.

No LLM API call happens here -- this is a pure parse/validate/write script
over content the Owner already obtained manually.

Run:
  python3 scripts/ingest_chatgpt_promptbatch_response.py --file /tmp/chatgpt_reply.csv
  cat /tmp/chatgpt_reply.csv | python3 scripts/ingest_chatgpt_promptbatch_response.py
Exit code: 0 if the file was parsed and processed (even if some/all rows were
rejected -- rejections are reported, not fatal); 1 only if the input could
not be parsed as either CSV or YAML rows, or the capability registry could
not be reached.
"""
import argparse
import csv
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_promptlib_guard import SANDBOX_ROOT, guarded_write  # noqa: E402
from generate_prompt_coverage_report import load_real_capabilities  # noqa: E402
from generate_chatgpt_promptbatch_request import load_real_engine_modules, load_schema_enum  # noqa: E402

import yaml  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SCHEMA_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml")
VARIABLE_DICTIONARY_PATH = os.path.join(REPO_ROOT, "ai-os", "VARIABLE_DICTIONARY_2026-07-24.yaml")
TERMINOLOGY_GUARDRAIL_PATH = os.path.join(REPO_ROOT, "ai-os", "TERMINOLOGY_GUARDRAIL_2026-07-24.py")
CSV_DIR = os.path.join(SANDBOX_ROOT, "CSV")
COVERAGE_SCRIPT = os.path.join(SCRIPT_DIR, "generate_prompt_coverage_report.py")
DUPLICATES_SCRIPT = os.path.join(SCRIPT_DIR, "detect_prompt_duplicates.py")


def _load_terminology_guardrail():
    """Import ai-os/TERMINOLOGY_GUARDRAIL_2026-07-24.py by file path (its
    filename contains hyphens/digits, not a valid bare `import` target) so
    this script reuses its PATTERN_FAMILIES + placeholder loader instead of
    re-implementing hardcoded-name detection a second time."""
    spec = importlib.util.spec_from_file_location("chatgpt_terminology_guardrail", TERMINOLOGY_GUARDRAIL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_prompt_ids(csv_dir):
    ids = set()
    for path in glob.glob(os.path.join(csv_dir, "*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pid = (row.get("Prompt ID") or "").strip()
                if pid:
                    ids.add(pid)
    return ids


def parse_rows(raw, fmt, required_columns):
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    def _try_csv(text):
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return None
        if not any(col in reader.fieldnames for col in required_columns):
            return None
        return list(reader)

    def _try_yaml(text):
        doc = yaml.safe_load(text)
        if isinstance(doc, list):
            return doc
        if isinstance(doc, dict):
            for key in ("rows", "prompts", "data"):
                if isinstance(doc.get(key), list):
                    return doc[key]
        return None

    if fmt in ("auto", "csv"):
        rows = _try_csv(stripped)
        if rows is not None:
            return rows, "csv"
        if fmt == "csv":
            return None, "csv"
    if fmt in ("auto", "yaml"):
        try:
            rows = _try_yaml(stripped)
        except yaml.YAMLError:
            rows = None
        if rows is not None:
            return rows, "yaml"
    return None, fmt


def validate_and_classify_row(row, required_columns, real_capability_names, registered_placeholders,
                               pattern_families, placeholder_token_re, existing_ids, seen_ids_this_batch,
                               real_module_unregistered_slugs, real_end_user_roles):
    reasons = []

    missing = [c for c in required_columns if not (row.get(c) or "").strip()]
    if missing:
        reasons.append(f"missing_required_columns: {missing}")
        return reasons  # can't validate further meaningfully without the columns

    capability = row["Capability"].strip()
    if capability not in real_capability_names and capability not in real_module_unregistered_slugs:
        reasons.append(
            f"unknown_capability: '{capability}' is neither a real capability_name in the live registry "
            f"nor a real '<module_slug>_unregistered' fallback for one of the 25 real "
            f"compliance-tracker/src/lib/engines/*.ts modules (see CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml's "
            f"anchor_decision_2026-07-25)"
        )

    end_user_role = row["End User Role"].strip()
    if end_user_role not in real_end_user_roles:
        reasons.append(f"invalid_end_user_role: '{end_user_role}' must be one of {sorted(real_end_user_roles)}")

    ai_required = row["AI Required Yes/No"].strip()
    if ai_required not in ("Yes", "No"):
        reasons.append(f"invalid_ai_required_value: '{ai_required}' must be 'Yes' or 'No'")

    try:
        confidence = float(row["Confidence"])
        if not (0.0 <= confidence <= 1.0):
            reasons.append(f"confidence_out_of_range: {confidence} must be within [0.0, 1.0]")
    except ValueError:
        reasons.append(f"confidence_not_numeric: '{row['Confidence']}'")

    scan_text = f"{row['Prompt']} {row.get('Variables', '')}"
    for category, pattern, note in pattern_families:
        m = pattern.search(scan_text)
        if m:
            reasons.append(f"hardcoded_literal_detected ({category}): matched '{m.group(0)}' -- {note}")
    for m in placeholder_token_re.finditer(scan_text):
        token = m.group(0)
        if token not in registered_placeholders:
            reasons.append(f"unregistered_placeholder_token: '{token}' is not registered in VARIABLE_DICTIONARY_2026-07-24.yaml")

    prompt_id = row["Prompt ID"].strip()
    if prompt_id in existing_ids or prompt_id in seen_ids_this_batch:
        reasons.append(f"duplicate_prompt_id: '{prompt_id}' already exists on disk or elsewhere in this batch")

    return reasons


def write_accepted_rows(accepted_rows, column_order, csv_dir, run_date):
    """Groups accepted rows by Capability and appends each group to
    CSV/PROMPTS_<capability>_<run_date>.csv -- creates the file with a header
    if it doesn't exist yet, appends header-less rows if it does. Never
    overwrites existing rows."""
    by_capability = {}
    for row in accepted_rows:
        by_capability.setdefault(row["Capability"].strip(), []).append(row)

    written_files = []
    for capability, rows in by_capability.items():
        filename = f"PROMPTS_{capability}_{run_date}.csv"
        path = os.path.join(csv_dir, filename)
        file_exists = os.path.isfile(path)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=column_order)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in column_order})

        guarded_write(path, buf.getvalue(), mode="a" if file_exists else "w")
        written_files.append(path)
    return written_files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=None, help="path to ChatGPT's saved response; omit to read stdin")
    parser.add_argument("--format", choices=["auto", "csv", "yaml"], default="auto")
    parser.add_argument("--csv-dir", default=CSV_DIR)
    args = parser.parse_args()

    if args.file:
        if not os.path.isfile(args.file):
            print(json.dumps({"ok": False, "error": f"file not found: {args.file}"}, indent=2))
            sys.exit(1)
        with open(args.file, encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    schema = load_schema()
    column_order = [c["name"] for c in schema["csv_schema"]["columns"]]

    rows, detected_format = parse_rows(raw, args.format, column_order)
    if rows is None:
        print(json.dumps({
            "ok": False,
            "error": "parse_failed",
            "detail": f"could not parse input as CSV or YAML rows (format={args.format})",
        }, indent=2))
        sys.exit(1)
    if not rows:
        print(json.dumps({"ok": False, "error": "no_rows_found", "detail": "input parsed but contained zero rows"}, indent=2))
        sys.exit(1)

    try:
        capabilities = load_real_capabilities()
    except (RuntimeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": "capability_registry_unreachable", "detail": str(e)}, indent=2))
        sys.exit(1)
    real_capability_names = {c["capability_name"] for c in capabilities}

    real_modules, _dropped = load_real_engine_modules()
    real_module_unregistered_slugs = {f"{m['module_slug']}_unregistered" for m in real_modules}
    real_end_user_roles = set(load_schema_enum(schema, "End User Role"))

    guardrail = _load_terminology_guardrail()
    registered_placeholders, _covered_tables = guardrail.load_registered_placeholders(VARIABLE_DICTIONARY_PATH)
    pattern_families = guardrail.PATTERN_FAMILIES
    placeholder_token_re = guardrail.PLACEHOLDER_TOKEN_RE

    existing_ids = load_existing_prompt_ids(args.csv_dir)
    seen_ids_this_batch = set()

    accepted, rejected = [], []
    for row in rows:
        reasons = validate_and_classify_row(
            row, column_order, real_capability_names, registered_placeholders,
            pattern_families, placeholder_token_re, existing_ids, seen_ids_this_batch,
            real_module_unregistered_slugs, real_end_user_roles,
        )
        if reasons:
            rejected.append({"row": row, "reasons": reasons})
        else:
            accepted.append(row)
            seen_ids_this_batch.add(row["Prompt ID"].strip())

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written_files = write_accepted_rows(accepted, column_order, args.csv_dir, run_date) if accepted else []

    coverage_proc = subprocess.run(["python3", COVERAGE_SCRIPT], capture_output=True, text=True)
    duplicates_proc = subprocess.run(["python3", DUPLICATES_SCRIPT], capture_output=True, text=True)

    print(json.dumps({
        "ok": True,
        "detected_format": detected_format,
        "total_rows": len(rows),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted_prompt_ids": [r["Prompt ID"] for r in accepted],
        "rejected": rejected,
        "files_written": written_files,
        "coverage_report_refreshed": coverage_proc.returncode == 0,
        "duplicates_report_refreshed": duplicates_proc.returncode == 0,
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
