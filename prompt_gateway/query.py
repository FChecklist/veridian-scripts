#!/usr/bin/env python3
"""
VERIDIAN Prompt Gateway - Machine-Language Query Interface
============================================================
OWNER DIRECTIVE 2026-07-25 (KE-20260725-061008-8423), point 3: "AI can
analyze the output prompt, ask this software query in machine language get
response, decide. Only if Software is not able to tell, than AI will ask
the OWNER specific query in english."

This module is that query mechanism. It answers a fixed set of close-ended
machine-language questions about a chat ALREADY processed by gateway.py
(identified by its VD- chat_id) using that chat's saved record on disk --
it never re-reads or re-classifies raw text. If gateway.py has not
processed a chat yet, there is nothing here to query; run gateway.py first.

Request/response envelope: ai-os/OWNER_ENGINE_MACHINE_LANGUAGE_CONTRACT_2026-07-25.yaml

Usage:
  python3 query.py --chat-id VD-20260725080900-0001 --q GET_CATEGORY
  python3 query.py --chat-id VD-20260725080900-0001 --q NEEDS_OWNER_CLARIFICATION
  python3 query.py --list-queries
"""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config import CHATS_DIR  # noqa: E402

# =============================================================================
# POLICY CONSTANTS -- the real, concrete "software could not tell" thresholds.
# These are the entire step_3 decision procedure: not prose, executable
# comparisons against values ChatClassifier already computed and gateway.py
# already saved to the chat record. No new AI judgment involved.
# =============================================================================
LOW_CONFIDENCE_THRESHOLD = 0.15  # ChatClassifier's own GENERAL-fallback confidence is
# fixed at 0.1 (see engine/classifier.py classify(): confidence < 0.05 -> forced
# GENERAL/0.1); 0.15 conservatively also catches real-but-weak non-fallback scores.
UNKNOWN_INTENT = "UNKNOWN"  # ChatClassifier.extract_intent()'s own literal fallback value.

# Every supported query code and what it reads from the saved chat record.
QUERY_CODES = {
    "GET_CATEGORY": "classification.category",
    "GET_INTENT": "classification.intent",
    "GET_CONFIDENCE": "classification.confidence",
    "GET_ENTITIES": "entities",
    "GET_MACHINE_PROMPT": "processing.machine_prompt",
    "GET_SNIPPETS": "snippets_used",
    "GET_TOKEN_REDUCTION_PCT": "processing.token_reduction_pct",
    "IS_CONFIDENT": "derived: classification.confidence >= LOW_CONFIDENCE_THRESHOLD",
    "NEEDS_OWNER_CLARIFICATION": "derived: step_3 insufficient-information decision procedure",
}


def _load_chat_record(chat_id: str):
    chat_file = Path(CHATS_DIR) / f"{chat_id}.json"
    if not chat_file.exists():
        return None
    with open(chat_file, "r") as f:
        return json.load(f)


def _envelope(chat_id, query, status, answer=None, reason_code=None):
    """The one and only response shape this module ever emits -- matches
    ai-os/OWNER_ENGINE_MACHINE_LANGUAGE_CONTRACT_2026-07-25.yaml response_envelope.
    status is always one of OK / INSUFFICIENT_INFO / NOT_FOUND / ERROR.
    answer is always a scalar, list, or flat object -- never a prose string."""
    return {
        "chat_id": chat_id,
        "query": query,
        "status": status,
        "answer": answer,
        "reason_code": reason_code,
    }


def run_query(chat_id: str, query: str) -> dict:
    if query not in QUERY_CODES:
        return _envelope(chat_id, query, "ERROR", None, "UNKNOWN_QUERY_CODE")

    record = _load_chat_record(chat_id)
    if record is None:
        return _envelope(chat_id, query, "NOT_FOUND", None, "CHAT_ID_NOT_FOUND")

    classification = record.get("classification", {})
    confidence = classification.get("confidence", 0.0)
    intent = classification.get("intent", UNKNOWN_INTENT)
    category = classification.get("category")
    # Document-mode chat records (gateway.py's _process_document_chat, used
    # for any multi-section input -- e.g. a full literal_template task spec)
    # save confidence=None deliberately: there is no single per-message
    # confidence score once classification runs per-section into a
    # category_histogram instead. None means "not applicable", not "low" --
    # treating it as < LOW_CONFIDENCE_THRESHOLD would force
    # NEEDS_OWNER_CLARIFICATION=true for every document-mode chat
    # unconditionally (and `confidence < LOW_CONFIDENCE_THRESHOLD` would
    # itself raise TypeError on None before task-20260726-101257).
    low_confidence = confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
    unknown_intent = intent == UNKNOWN_INTENT

    if query == "GET_CATEGORY":
        if low_confidence:
            return _envelope(chat_id, query, "INSUFFICIENT_INFO", {"category": category, "confidence": confidence},
                              "LOW_CLASSIFICATION_CONFIDENCE")
        return _envelope(chat_id, query, "OK", category)

    if query == "GET_INTENT":
        if unknown_intent:
            return _envelope(chat_id, query, "INSUFFICIENT_INFO", None, "INTENT_UNKNOWN")
        return _envelope(chat_id, query, "OK", intent)

    if query == "GET_CONFIDENCE":
        return _envelope(chat_id, query, "OK", confidence)

    if query == "GET_ENTITIES":
        return _envelope(chat_id, query, "OK", record.get("entities", []))

    if query == "GET_MACHINE_PROMPT":
        if low_confidence:
            return _envelope(chat_id, query, "INSUFFICIENT_INFO",
                              {"machine_prompt": record.get("processing", {}).get("machine_prompt")},
                              "LOW_CLASSIFICATION_CONFIDENCE")
        return _envelope(chat_id, query, "OK", record.get("processing", {}).get("machine_prompt"))

    if query == "GET_SNIPPETS":
        return _envelope(chat_id, query, "OK", record.get("snippets_used", []))

    if query == "GET_TOKEN_REDUCTION_PCT":
        return _envelope(chat_id, query, "OK", record.get("processing", {}).get("token_reduction_pct"))

    if query == "IS_CONFIDENT":
        return _envelope(chat_id, query, "OK", not low_confidence)

    if query == "NEEDS_OWNER_CLARIFICATION":
        # step_3: the ONLY code path in this system that decides "software is
        # not able to tell -> ask the OWNER". True iff either concrete signal
        # ChatClassifier itself already flags as unreliable is present.
        reasons = []
        if low_confidence:
            reasons.append("LOW_CLASSIFICATION_CONFIDENCE")
        if unknown_intent:
            reasons.append("INTENT_UNKNOWN")
        needs_clarification = bool(reasons)
        return _envelope(
            chat_id, query, "OK",
            {"needs_owner_clarification": needs_clarification, "reasons": reasons},
            reasons[0] if reasons else None,
        )

    return _envelope(chat_id, query, "ERROR", None, "UNHANDLED_QUERY_CODE")


def main():
    parser = argparse.ArgumentParser(
        description="Machine-language query interface for chats already processed by gateway.py"
    )
    parser.add_argument("--chat-id", dest="chat_id", default=None)
    parser.add_argument("--q", "--query", dest="query", default=None, choices=list(QUERY_CODES.keys()))
    parser.add_argument("--list-queries", action="store_true")
    args = parser.parse_args()

    if args.list_queries:
        print(json.dumps({"query_codes": QUERY_CODES}, indent=2))
        return

    if not args.chat_id or not args.query:
        print(json.dumps({"status": "ERROR", "reason_code": "MISSING_REQUIRED_ARGS"}, indent=2))
        sys.exit(1)

    result = run_query(args.chat_id, args.query)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["status"] in ("OK", "INSUFFICIENT_INFO") else 1)


if __name__ == "__main__":
    main()
