#!/usr/bin/env python3
"""
Measure OWNER_ENGINE's real content-coverage accuracy against the fixed
gold_items.json checklist (built by build_gold_items.py from an independent,
deterministic read of the source document).

Method (stated once here, reused unchanged for every run):
  1. Take OWNER_ENGINE's `final_output` string for the run (the same text a
     downstream AI would actually receive as the compressed prompt).
  2. Normalize both the gold item text and the output: lowercase, strip all
     characters except [a-z0-9], collapse whitespace.
  3. For each gold item, extract its "significant tokens": every token of
     length >= 3, OR any token that is purely numeric (so a fact like
     "74 engines" still requires the literal 74 to be recovered) -- common
     stopwords (the, a, an, and, or, of, in, on, at, to, for, with, by, is,
     are, was, were, via, using, that, this, from, as) are dropped.
  4. A gold item is COVERED iff every one of its significant tokens appears
     as a whole word somewhere in the normalized output. This is stricter
     than a fuzzy/substring match (it does not give credit for a plausible-
     sounding but wrong recollection) and looser than requiring the exact
     phrase verbatim (it tolerates the compression/reordering a real prompt
     gateway is expected to do).
  5. accuracy_pct = covered / total * 100 (over all 165 fixed gold items,
     same denominator every run). gap_pct = 100 - accuracy_pct.

This script is deterministic and takes no model/AI judgment calls -- same
input always produces the same score, so run-to-run numbers are comparable.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
GOLD_PATH = HERE / "gold_items.json"

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "is", "are", "was", "were", "via", "using", "that",
    "this", "from", "as",
}


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def significant_tokens(text: str):
    norm = normalize(text)
    tokens = norm.split()
    return [t for t in tokens if t.isdigit() or (len(t) >= 3 and t not in STOPWORDS)]


def is_covered(item_text: str, output_norm: str) -> bool:
    tokens = significant_tokens(item_text)
    if not tokens:
        return normalize(item_text) in output_norm
    for tok in tokens:
        if not re.search(rf"\b{re.escape(tok)}\b", output_norm):
            return False
    return True


def measure(output_text: str, gold_path: Path = GOLD_PATH) -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    output_norm = normalize(output_text)

    results_by_cat = {}
    covered_items = []
    missed_items = []

    for item in gold["items"]:
        covered = is_covered(item["text"], output_norm)
        cat = item["category"]
        bucket = results_by_cat.setdefault(cat, {"covered": 0, "total": 0})
        bucket["total"] += 1
        if covered:
            bucket["covered"] += 1
            covered_items.append(item)
        else:
            missed_items.append(item)

    total = gold["total_items"]
    covered_n = len(covered_items)
    accuracy_pct = round(covered_n / total * 100, 2)
    gap_pct = round(100 - accuracy_pct, 2)

    return {
        "total_gold_items": total,
        "covered": covered_n,
        "accuracy_pct": accuracy_pct,
        "gap_pct": gap_pct,
        "by_category": {
            k: {
                "covered": v["covered"],
                "total": v["total"],
                "pct": round(v["covered"] / v["total"] * 100, 2) if v["total"] else 0,
            }
            for k, v in results_by_cat.items()
        },
        "missed_items": [{"category": m["category"], "text": m["text"]} for m in missed_items],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: measure_accuracy.py <output_text_file> [--summary-only]", file=sys.stderr)
        sys.exit(1)
    output_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    result = measure(output_text)
    if "--summary-only" in sys.argv:
        print(json.dumps({
            "total_gold_items": result["total_gold_items"],
            "covered": result["covered"],
            "accuracy_pct": result["accuracy_pct"],
            "gap_pct": result["gap_pct"],
            "by_category": result["by_category"],
        }, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
