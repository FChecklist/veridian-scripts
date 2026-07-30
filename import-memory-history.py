#!/usr/bin/env python3
"""
One-time bulk import: local memory index (MEMORY.md, ~60 entries spanning
the full project history) -> server-side Superboss Register work_items.
2026-07-20, per Owner directive: "document all sessions ... so it becomes
the real memory."

Deliberately imports as work_items (not instructions) -- these are
summarized OUTCOMES of past sessions (decisions, builds, gaps found), not
the original verbatim instructions, which were never captured server-side
before tonight. Each import is tagged historical_import=true in its
metadata so it's never confused with a real-time log entry.

Date extraction: a YYYY-MM-DD pattern in the filename or description is
used as the real event date when present. When absent, ts is left null
and the entry is tagged date_confidence=unknown rather than guessing --
no assumptions.

Campaign tagging: keyword-based, deliberately simple and auditable rather
than an LLM classification call (software first) -- each memory's title/
description is checked against a small ordered keyword->campaign map.
"""
import json
import re
import subprocess
import sys

MEMORY_INDEX_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/memory_index_raw.md"
REGISTER_CLI = "/opt/veridian/scripts/superboss-register.py"

LINE_RE = re.compile(r"^-\s*\[(?P<title>[^\]]+)\]\((?P<filename>[^)]+)\)\s*—\s*(?P<desc>.+)$")
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

CAMPAIGN_KEYWORDS = [
    ("cost_control", ["cost", "cache", "openrouter", "glm", "credit", "budget"]),
    ("server_memory_architecture", ["superboss register", "memory", "session"]),
    ("system_gap_analysis", ["constitution", "gap analysis", "orchestra", "guardrail"]),
    ("reverse_engineering", ["infisuite", "odoo", "reverse-engineer", "crm", "billstack"]),
    ("projexa", ["projexa", "construction"]),
    ("veda_advisors", ["veda advisors", "veda_advisors", "graphy"]),
    ("fchecklist_ops", ["fchecklist", "worktree", "sub-agent", "subagent"]),
    ("veridian_platform", ["veridian", "veri ", "ai os", "vaios"]),
    ("meettrack", ["meettrack"]),
]


def derive_campaign(title, desc):
    text = f"{title} {desc}".lower()
    for campaign, keywords in CAMPAIGN_KEYWORDS:
        if any(kw in text for kw in keywords):
            return campaign
    return "general"


def derive_content_tag(title):
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug[:60]


def main():
    with open(MEMORY_INDEX_PATH, encoding="utf-8") as f:
        lines = f.readlines()

    imported, skipped = 0, 0
    for line in lines:
        line = line.rstrip("\n")
        m = LINE_RE.match(line)
        if not m:
            skipped += 1
            continue
        title = m.group("title").strip()
        filename = m.group("filename").strip()
        desc = m.group("desc").strip()

        date_match = DATE_RE.search(filename) or DATE_RE.search(desc)
        event_date = date_match.group(1) if date_match else None
        date_confidence = "explicit" if event_date else "unknown"

        campaign = derive_campaign(title, desc)
        content_tag = derive_content_tag(title)
        terms = ",".join(sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", title.lower()))))[:200]

        metadata = {
            "historical_import": True,
            "source": "local_memory_index",
            "source_file": filename,
            "date_confidence": date_confidence,
            "full_description": desc,
        }

        cmd = [
            sys.executable, REGISTER_CLI, "log-work",
            "--source", "owner", "--medium", "historical_import",
            "--campaign", campaign, "--content", content_tag, "--term", terms,
            "--status", "historical", "--metadata", json.dumps(metadata),
        ]
        if event_date:
            cmd += ["--ts", f"{event_date}T12:00:00+00:00"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            print(f"FAILED: {title} -- {result.stderr[:200]}", file=sys.stderr)
            skipped += 1
            continue
        imported += 1

    print(json.dumps({"imported": imported, "skipped_unparsed": skipped, "total_lines": len(lines)}))


if __name__ == "__main__":
    main()
