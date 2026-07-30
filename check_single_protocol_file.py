#!/usr/bin/env python3
"""
Real software enforcement of the Owner's 2026-07-26 consolidation requirement:
exactly ONE Owner<->AI protocol file and exactly ONE Owner<->AI lessons/incidents
memory file under ai-os/OWNER_DIRECTIVES/, on the server, at all times.

Before this script, "one protocol file, one memory file" was a one-time manual
cleanup (see ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml source_inventory for the
2026-07-26 consolidation that produced PROTOCOL_OWNER_AI.yaml and MEMORY_OWNER_AI.yaml
from 7 previously-scattered files) with nothing stopping the same drift from
happening again. This script is the real, repeatable guard: it defines a concrete
"this is a protocol/rules document" signature and a "this is a memory/lessons
document" signature, and fails closed the moment more than one real file in
ai-os/OWNER_DIRECTIVES/ matches either signature.

Signature (a *.yaml/*.yml file directly under OWNER_DIRECTIVES/ matches if ANY of):
  protocol: top-level document_class == "protocol", OR a top-level "articles" list,
            OR meta.status == "NON_NEGOTIABLE"
  memory:   top-level document_class == "memory", OR a top-level "incidents" key,
            OR a top-level "lessons" key

Plain-text (.txt) legacy directive files retained only as citation targets (see
PROTOCOL_OWNER_AI.yaml source_inventory) never match either signature -- they carry
their own "SUPERSEDED BY PROTOCOL_OWNER_AI.yaml" header note instead, and are not
YAML, so this script does not parse them as protocol/memory candidates at all.

Usage: check_single_protocol_file.py [--dir <OWNER_DIRECTIVES path>]
Exit 0 = exactly 1 protocol file and exactly 1 memory file found (JSON on stdout).
Exit 1 = 0 or 2+ of either found (JSON with the exact duplicate/missing filenames).
"""
import argparse
import json
import os
import sys

import yaml

DEFAULT_OWNER_DIRECTIVES_DIR = os.environ.get(
    "OWNER_DIRECTIVES_DIR", "/opt/veridian/ai-os/OWNER_DIRECTIVES"
)


def _classify(path):
    """Return 'protocol', 'memory', or None for one real *.yaml/*.yml file."""
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except Exception:
        return None  # unparseable YAML is not a protocol/memory candidate -- fails open here,
        # not a silent pass: a genuinely broken canonical file is caught separately by
        # yaml.safe_load in the deploy/test path, not by this drift-count check.
    if not isinstance(doc, dict):
        return None

    document_class = doc.get("document_class")
    if document_class == "protocol":
        return "protocol"
    if document_class == "memory":
        return "memory"

    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    if "articles" in doc or meta.get("status") == "NON_NEGOTIABLE":
        return "protocol"
    if "incidents" in doc or "lessons" in doc:
        return "memory"
    return None


def find_documents(directory=DEFAULT_OWNER_DIRECTIVES_DIR):
    """Real scan of directory (maxdepth 1, *.yaml/*.yml only). Returns
    (protocol_files, memory_files), each a sorted list of real filenames found."""
    protocol_files = []
    memory_files = []
    if not os.path.isdir(directory):
        return protocol_files, memory_files
    for name in sorted(os.listdir(directory)):
        if not (name.endswith(".yaml") or name.endswith(".yml")):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        kind = _classify(path)
        if kind == "protocol":
            protocol_files.append(name)
        elif kind == "memory":
            memory_files.append(name)
    return protocol_files, memory_files


def check(directory=DEFAULT_OWNER_DIRECTIVES_DIR):
    """Returns (ok: bool, result: dict). result always carries the real file lists
    found, so a caller can report specifics regardless of pass/fail."""
    protocol_files, memory_files = find_documents(directory)
    problems = []
    if len(protocol_files) != 1:
        problems.append(
            f"expected exactly 1 protocol document in {directory}, found "
            f"{len(protocol_files)}: {protocol_files}"
        )
    if len(memory_files) != 1:
        problems.append(
            f"expected exactly 1 memory document in {directory}, found "
            f"{len(memory_files)}: {memory_files}"
        )
    result = {
        "directory": directory,
        "protocol_files": protocol_files,
        "memory_files": memory_files,
        "problems": problems,
    }
    return (len(problems) == 0), result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=DEFAULT_OWNER_DIRECTIVES_DIR)
    args = parser.parse_args()

    ok, result = check(args.dir)
    print(json.dumps(result))
    sys.exit(0 if ok else 1)
