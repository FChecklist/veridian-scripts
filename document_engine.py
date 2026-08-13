#!/usr/bin/env python3
"""
document_engine.py -- Phase 4 (document_notification_data) of
20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml (task
task-20260724-133622-phase4-unify-document-pipeline-pdf-gener), closes_engines: [11].

The real Document Engine facade for the AI-OS operational layer. Does NOT
reimplement pdf-generator.ts / document-extraction-service.ts /
services/doc-processing (those stay real, live, and untouched in
compliance-tracker's own repo -- see ai-os/DOCUMENT_ENGINE_CONTRACT_2026-07-24.yaml's
own repo_boundary_honestly_stated). This script does the two things that ARE
real work for the AI-OS layer:

  1. register-capabilities: registers the 5 real document-pipeline operations
     (ai-os/DOCUMENT_ENGINE_CONTRACT_2026-07-24.yaml's own
     document_engine_operation_schema) into Phase 1's live capability_registry
     table, via scripts/superboss-register.py register-capability -- so a
     future caller's lookup-capability check surfaces "does a document
     operation already exist" the same way Phase 1 did for the 5 VCEL
     calculators.
  2. detect-duplicates: a direct, portable port of
     document-processing-engine.ts's detectDuplicateDocumentsByHash() --
     the one operation in the facade that is genuinely reachable from this
     layer (exact contentHash grouping has no framework dependency).
  3. report-failure: the real, live cross-call realizing dependency_table's
     "Document Engine (11) -> Notification Engine (12)" edge -- fires
     scripts/automation_rule_engine.py evaluate-rules against
     trigger_type=document.pipeline_failed, which (once the
     document-pipeline-failure-notify-owner rule is registered, see this
     script's own module docstring real_evidence in the phase plan) dispatches
     a real notify_owner action through this phase's newly committed
     scripts/notify-owner.py.

Run: python3 scripts/document_engine.py <register-capabilities|detect-duplicates|report-failure> ...
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

VERIDIAN_ROOT = "/opt/veridian"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS = f"{SCRIPT_DIR}/superboss-register.py"
AUTOMATION_RULE_ENGINE = f"{SCRIPT_DIR}/automation_rule_engine.py"

# One row per ai-os/DOCUMENT_ENGINE_CONTRACT_2026-07-24.yaml's
# document_engine_operation_schema.operations entry. Fields match
# CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's capability_record_schema
# field-for-field, same convention ai-os-scripts/populate_capability_registry.py
# already established for the 5 VCEL calculator rows.
DOCUMENT_ENGINE_CAPABILITIES = [
    {
        "capability_name": "document_pdf_generation",
        "inputs": [
            {"key": "orgName", "label": "Organisation name for the branded header", "type": "string"},
            {"key": "documentTitle", "label": "Document title", "type": "string"},
        ],
        "business_rules": [],
        "workflow": "repos/compliance-tracker/src/lib/pdf-generator.ts (createBrandedDocument/drawDocumentHeader)",
        "automation": None,
        "documents": "repos/compliance-tracker/src/lib/pdf-generator.ts",
        "reports": None,
        "apis": [],
        "ui_screens": [],
        "permissions": "in-process TS call only, no standalone API surface",
        "ai_required": False,
        "confidence": 1.0,
        "version": "unversioned",
        "owner": "Priority 15 Wave 2 (PROJEXA HR & Payroll follow-up)",
    },
    {
        "capability_name": "document_ocr_paddleocr",
        "inputs": [{"key": "image_base64", "label": "Base64-encoded image", "type": "string"}],
        "business_rules": [],
        "workflow": "repos/compliance-tracker/src/lib/services/ocr-client.ts (runOcr, GitHub Actions "
        "repository_dispatch to .github/workflows/doc-processing-job.yml)",
        "automation": "repos/compliance-tracker/services/doc-processing/main.py (FastAPI + PaddleOCR)",
        "documents": None,
        "reports": None,
        "apis": ["repos/compliance-tracker/services/doc-processing/main.py"],
        "ui_screens": [],
        "permissions": "org-scoped via withTenantContext (docProcessingJobs row)",
        "ai_required": False,
        "confidence": 0.8,
        "version": "unversioned",
        "owner": "T1.2, docs/infra/TOOL_INTEGRATION_PLAN.md",
    },
    {
        "capability_name": "document_extraction_vision",
        "inputs": [{"key": "documentId", "label": "Uploaded document id", "type": "string"}],
        "business_rules": [],
        "workflow": "repos/compliance-tracker/src/lib/services/document-extraction-service.ts (callLLMVision)",
        "automation": None,
        "documents": "repos/compliance-tracker/src/lib/services/document-extraction-service.ts",
        "reports": None,
        "apis": [],
        "ui_screens": [],
        "permissions": "fire-and-forget from the upload route, never blocks/fails the upload",
        "ai_required": True,
        "confidence": 0.9,
        "version": "unversioned",
        "owner": "Wave 35 (Document AI, PLATFORM_STRATEGY.md #17)",
    },
    {
        "capability_name": "document_extraction_text",
        "inputs": [{"key": "documentId", "label": "Uploaded document id", "type": "string"}],
        "business_rules": [],
        "workflow": "repos/compliance-tracker/src/lib/services/document-extraction-service.ts "
        "(extractRawTextForMimeType + callLLMJson)",
        "automation": None,
        "documents": "repos/compliance-tracker/src/lib/services/document-extraction-service.ts",
        "reports": None,
        "apis": [],
        "ui_screens": [],
        "permissions": "fire-and-forget from the upload route, never blocks/fails the upload",
        "ai_required": True,
        "confidence": 0.9,
        "version": "unversioned",
        "owner": "Wave 35 (Document AI, PLATFORM_STRATEGY.md #17)",
    },
    {
        "capability_name": "document_duplicate_detection",
        "inputs": [{"key": "documents", "label": "List of {id, contentHash}", "type": "array"}],
        "business_rules": [],
        "workflow": "repos/compliance-tracker/src/lib/engines/document-processing-engine.ts "
        "(detectDuplicateDocumentsByHash) -- ported for the AI-OS layer by this script's "
        "own detect-duplicates subcommand",
        "automation": None,
        "documents": None,
        "reports": None,
        "apis": ["scripts/document_engine.py detect-duplicates"],
        "ui_screens": [],
        "permissions": "exact-hash match only, no embedding/near-duplicate detection",
        "ai_required": False,
        "confidence": 1.0,
        "version": "unversioned",
        "owner": "task-20260724-133622-phase4-unify-document-pipeline-pdf-gener",
    },
]


def cmd_register_capabilities(_args):
    init_proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True)
    if init_proc.returncode != 0:
        print(json.dumps({"error": "superboss-register.py init failed", "stderr": init_proc.stderr[-1000:]}))
        sys.exit(1)

    registered, failed = [], []
    for cap in DOCUMENT_ENGINE_CAPABILITIES:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(cap, tf)
            record_file = tf.name
        try:
            proc = subprocess.run(
                ["python3", SUPERBOSS, "register-capability", "--record-file", record_file],
                capture_output=True, text=True,
            )
        finally:
            os.unlink(record_file)

        if proc.returncode != 0:
            failed.append({"capability_name": cap["capability_name"], "stderr": proc.stderr[-1000:]})
            continue
        try:
            registered.append(json.loads(proc.stdout))
        except json.JSONDecodeError:
            failed.append({"capability_name": cap["capability_name"], "stdout": proc.stdout[-1000:]})

    print(json.dumps({
        "schema_rows_found": len(DOCUMENT_ENGINE_CAPABILITIES),
        "registered_count": len(registered),
        "failed_count": len(failed),
        "failed": failed,
    }, indent=2))
    sys.exit(1 if failed else 0)


def detect_duplicate_documents_by_hash(documents):
    """Field-for-field port of document-processing-engine.ts's
    detectDuplicateDocumentsByHash(): groups document ids by exact contentHash
    match, returns only groups with more than one member. Unchanged by
    UMR-20260806-171945-5767 issue #921's git-hash-object wiring below --
    this function's contract (a pre-supplied contentHash per document) is the
    real field-for-field port fidelity to the TS original and is what this
    module's own tests exercise directly; the real hash-computation lookup
    is wired in one layer up, in cmd_detect_duplicates()'s new --files mode."""
    groups = {}
    for doc in documents:
        groups.setdefault(doc["contentHash"], []).append(doc["id"])
    return [ids for ids in groups.values() if len(ids) > 1]


def git_hash_object_of(path):
    """Real content hash via git's own blob object model -- the identical
    algorithm a real `git hash-object <file>` invocation computes:
    sha1('blob ' + str(len(content)) + '\\0' + content). Verified
    byte-identical against a real `git hash-object` subprocess in
    test_document_engine.py, not assumed.

    This is the real 'thin lookup' UMR-20260806-171945-5767 /
    UMR_5767_ISSUE_RESOLUTION_MATRIX.json issue_number 921 asks to be wired
    into this script: 'Wire a thin lookup (git hash-object <file>) into
    existing file-registration scripts (document_engine.py's
    detectDuplicateDocumentsByHash...) rather than a new ID service.'
    Computed in-process rather than shelling out to a real `git hash-object`
    subprocess per file, per that same issue's own sanctioned fallback ('the
    local equivalent blob <len>\\0<content> SHA-1 computation if shelling out
    to git per-file is judged too slow') -- one real subprocess per document
    would multiply real fork/exec overhead across a real document corpus for
    zero behavioral gain over computing the identical real SHA-1 in-process."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    h = hashlib.sha1()
    h.update(("blob %d" % size).encode() + b"\x00")
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return h.hexdigest()


def cmd_detect_duplicates(args):
    if args.files:
        try:
            files = json.loads(args.files)
        except json.JSONDecodeError:
            print(json.dumps({"error": "--files must be valid JSON: a list of {id, path}"}))
            sys.exit(1)
        documents = []
        errors = []
        for entry in files:
            ch = git_hash_object_of(entry["path"])
            if ch is None:
                errors.append(entry["path"])
                continue
            documents.append({"id": entry["id"], "contentHash": ch})
        if errors:
            print(json.dumps({"error": "unreadable path(s), refusing to guess a hash",
                               "unreadable_paths": errors}))
            sys.exit(1)
    else:
        try:
            documents = json.loads(args.documents)
        except json.JSONDecodeError:
            print(json.dumps({"error": "--documents must be valid JSON: a list of {id, contentHash}"}))
            sys.exit(1)
    duplicate_groups = detect_duplicate_documents_by_hash(documents)
    print(json.dumps({"input_count": len(documents), "duplicate_groups": duplicate_groups}, indent=2))


def cmd_hash_object(args):
    """Standalone thin lookup: real git hash-object equivalent for one file,
    the same real content-addressable hash cmd_detect_duplicates()'s --files
    mode uses internally."""
    ch = git_hash_object_of(args.file)
    if ch is None:
        print(json.dumps({"error": f"unreadable path: {args.file}"}))
        sys.exit(1)
    print(json.dumps({"path": args.file, "contentHash": ch}, indent=2))


def cmd_report_failure(args):
    """Real cross-call realizing dependency_table's 'Document Engine (11) ->
    Notification Engine (12)' edge -- see
    ai-os/DOCUMENT_ENGINE_CONTRACT_2026-07-24.yaml's document_to_notification_edge."""
    payload = {
        "operation": args.operation,
        "reason": args.reason,
        "job_id": args.job_id,
    }
    proc = subprocess.run(
        ["python3", AUTOMATION_RULE_ENGINE, "evaluate-rules",
         "--trigger-type", "document.pipeline_failed",
         "--payload", json.dumps(payload)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(json.dumps({"error": "automation_rule_engine.py evaluate-rules failed", "stderr": proc.stderr[-1000:]}))
        sys.exit(1)
    print(proc.stdout)


def main():
    parser = argparse.ArgumentParser(description="VERIDIAN Document Engine facade (AI-OS operational layer)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("register-capabilities", help="register the 5 document-pipeline operations into "
        "capability_registry").set_defaults(func=cmd_register_capabilities)

    p_dedup = sub.add_parser("detect-duplicates", help="exact contentHash duplicate grouping")
    dedup_input = p_dedup.add_mutually_exclusive_group(required=True)
    dedup_input.add_argument("--documents", help='JSON list of {"id": ..., "contentHash": ...}')
    dedup_input.add_argument("--files", help='JSON list of {"id": ..., "path": ...} -- contentHash is '
        "computed for real via git's own blob object model (git hash-object), not pre-supplied")
    p_dedup.set_defaults(func=cmd_detect_duplicates)

    p_hash = sub.add_parser("hash-object", help="real git hash-object equivalent for one file "
        "(thin content-addressable-hash lookup, UMR-20260806-171945-5767 issue #921)")
    p_hash.add_argument("--file", required=True)
    p_hash.set_defaults(func=cmd_hash_object)

    p_fail = sub.add_parser("report-failure", help="cross-call into the Notification Engine on a real "
        "document-pipeline failure")
    p_fail.add_argument("--operation", required=True, help="e.g. document_ocr_paddleocr")
    p_fail.add_argument("--reason", required=True)
    p_fail.add_argument("--job-id", default=None)
    p_fail.set_defaults(func=cmd_report_failure)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
