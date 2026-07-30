#!/usr/bin/env python3
"""
Build the fixed gold-standard content-coverage checklist for
VERIDIAN_Architecture_v2.0_extracted.txt.

This is the "independent read" of the source document, made deterministic and
reproducible: every gold item is derived programmatically from the fixed
extracted-text artifact (ai-os/owner-attachments/VERIDIAN_Architecture_v2.0_extracted.txt),
plus a small hand-curated list of key numeric/structural facts that are stated
in prose rather than in a heading or table (numeric_facts below) -- these were
identified by directly reading the document text once, and are cited by line
number in the extracted .txt so they can be checked against the source.

Categories:
  headings        - every markdown heading (#, ##, ###) in the doc (53 total)
  engines         - every named engine listed in the "2.1 Complete Engine
                     Catalog" tables (74 total, first column of each row)
  ycml_specs      - every named specification in the "2.7 Modular YCML
                     Specification Catalog" table (40 total, "Specification" column)
  technologies    - every named technology listed in the Part III
                     ("Research References and Technology Verification") tables
                     (first column of each row across 3.1/3.2/3.3)
  numeric_facts   - hand-curated key quantitative/structural claims, cited by
                     source line number, e.g. "74 engines (up from 44)"

Output: gold_items.json -- a flat list of {category, text, source_line} plus
a total count, used by measure_accuracy.py as the fixed denominator for every run.
"""
import json
import re
import sys
from pathlib import Path

DOC_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/opt/veridian/ai-os/tasks/task-20260725-104606-owner-engine-document-accuracy-convergen/"
    "workspace/ai-os/owner-attachments/VERIDIAN_Architecture_v2.0_extracted.txt"
)
OUT_PATH = Path(__file__).parent / "gold_items.json"


def load_lines():
    return DOC_PATH.read_text(encoding="utf-8").splitlines()


def extract_headings(lines):
    items = []
    in_table = False
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "[TABLE]":
            in_table = True
            continue
        if stripped == "[/TABLE]":
            in_table = False
            continue
        if in_table:
            continue  # table header rows like "# | Specification | ..." aren't headings
        m = re.match(r"^(#{1,3})\s+(.*\S)\s*$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            items.append({"category": "headings", "text": text, "meta": f"H{level}", "source_line": i})
    return items


def extract_table_column(lines, section_heading_prefix, column_index):
    """
    Find all [TABLE]...[/TABLE] blocks that occur after a heading whose text
    starts with `section_heading_prefix`, up until the next heading at the
    same or shallower level (subheadings nested inside the section, e.g. the
    eight ### engine-category headings inside ## 2.1, do not end the section).
    Returns the given 0-indexed pipe-delimited column from every data row
    (skipping the header row of each table).
    """
    results = []
    in_section = False
    section_level = None
    in_table = False
    table_row_idx = 0
    for i, line in enumerate(lines, start=1):
        m = re.match(r"^(#{1,3})\s+(.*\S)\s*$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if text.startswith(section_heading_prefix):
                in_section = True
                section_level = level
                continue
            if in_section and level <= section_level:
                in_section = False
            continue
        if not in_section:
            continue
        if line.strip() == "[TABLE]":
            in_table = True
            table_row_idx = 0
            continue
        if line.strip() == "[/TABLE]":
            in_table = False
            continue
        if in_table:
            table_row_idx += 1
            if table_row_idx == 1:
                continue  # header row
            cols = [c.strip() for c in line.split("|")]
            if len(cols) > column_index and cols[column_index]:
                results.append((cols[column_index], i))
    return results


def extract_engines(lines):
    rows = extract_table_column(lines, "2.1 Complete Engine Catalog", 0)
    return [{"category": "engines", "text": t, "source_line": ln} for t, ln in rows]


def extract_ycml_specs(lines):
    rows = extract_table_column(lines, "2.7 Modular YCML Specification Catalog", 1)
    return [{"category": "ycml_specs", "text": t, "source_line": ln} for t, ln in rows]


def extract_technologies(lines):
    rows = []
    for prefix in ("3.1 Browser AI Frameworks", "3.2 Browser APIs", "3.3 Prompt Engineering Tools"):
        rows.extend(extract_table_column(lines, prefix, 0))
    seen = set()
    out = []
    for t, ln in rows:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"category": "technologies", "text": t, "source_line": ln})
    return out


# Hand-curated key quantitative/structural facts, cited to their source line
# in the extracted .txt (verified by direct reading of the document).
NUMERIC_FACTS = [
    ("Engine catalog expanded from 44 to 74 engines (30 added)", 69),
    ("Pipeline expanded from 25 stages to 41 stages (16 added)", 211),
    ("Prompt metadata payload spanning 18 categories and 200+ fields", 405),
    ("YCML specification catalog expanded from 30 to 40 specs", 430),
    ("Cache architecture defines 10 levels, L0 through L9", 261),
    ("Four-layer defense-in-depth security architecture", 479),
    ("Total browser-execution budget: 500ms for cached/confident prompts", 43),
    ("Total execution budget: 3 seconds maximum for full LLM inference", 239),
    ("Server escalation budget: 10 seconds with progressive loading", 366),
    ("OPFS model weight storage capped at 4GB", 44),
    ("Total browser tab memory footprint must stay under 2GB", 44),
    ("L5 Semantic Cache: similarity greater than 0.95 returns cached response immediately", 282),
    ("NPU inference (Tier 1) target under 30ms", 239),
    ("Lightweight Analysis Layer (Stages 3-7) must complete within 15ms total", 219),
    ("Semantic caching can eliminate up to 80% of LLM inference calls", 282),
]


def extract_numeric_facts():
    return [{"category": "numeric_facts", "text": t, "source_line": ln} for t, ln in NUMERIC_FACTS]


def main():
    lines = load_lines()
    items = []
    items += extract_headings(lines)
    items += extract_engines(lines)
    items += extract_ycml_specs(lines)
    items += extract_technologies(lines)
    items += extract_numeric_facts()

    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)

    out = {
        "source_doc": str(DOC_PATH),
        "total_items": len(items),
        "counts_by_category": {k: len(v) for k, v in by_cat.items()},
        "items": items,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(items)} gold items to {OUT_PATH}")
    for k, v in out["counts_by_category"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
