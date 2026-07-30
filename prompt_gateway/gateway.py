#!/usr/bin/env python3
"""
VERIDIAN Prompt Gateway - Raw Chat Pre-Processing Pipeline
============================================================
MAIN ENTRY POINT: python3 /opt/veridian/scripts/prompt_gateway/gateway.py

This is a PRE-PROCESSING FILTER for the real, production task lifecycle
dispatcher at /opt/veridian/scripts/task-gateway.py (submit/start/log/close/
register-automation/status), not a reimplementation of it. Two real,
distinct integration points exist between the two programs:
  - task-gateway.py cmd_submit's own run_owner_engine_gate() calls THIS
    module (--mode stdin --json-only) to gate every --source owner
    submission before its text reaches keyword extraction/dedup/search
    (task-20260725-080900). That direction predates this file's own
    task-lifecycle awareness.
  - --mode owner-dispatch (task-20260726-092433) is the reverse and, until
    this task, missing direction: THIS module classifies a raw Owner
    message and, when that classification is a real task-dispatch/status-
    query/completion-query request, calls task-gateway.py's submit/start/
    status directly (route_and_dispatch() / dispatch_to_task_lifecycle()
    below) -- so no AI turn or human has to read this module's classified
    output and hand-construct a task-gateway.py invocation in between.
    --mode stdin (used BY task-gateway.py's own gate, above) never calls
    into task-gateway.py -- only --mode owner-dispatch does, avoiding any
    recursion between the two integration points.

Orchestrates the complete chat processing pipeline:
  1. Receive raw chat input
  2. Generate unique chat ID (VD- namespace, distinct from task-/KE-/INS- ids)
  3. Classify chat (software-driven, not AI)
  4. Remove noise and convert to machine prompt
  5. Manage and prune context
  6. Tag related files with chat ID
  7. Output pruned, machine-ready prompt

ALL processing is done by SOFTWARE (deterministic Python code), NOT by AI.
This reduces input/output token usage by 50%+ before any AI sees the prompt.

Usage:
  # Process a single chat message (stdin):
  echo "Fix the auth error" | python3 gateway.py --mode stdin

  # Process a chat file:
  python3 gateway.py --mode file --input /path/to/chat.json

  # Interactive mode (read from stdin, output to stdout):
  python3 gateway.py --mode interactive

  # As a pipe filter:
  cat chat_input.txt | python3 gateway.py --mode pipe

  # Initialize the system:
  python3 gateway.py --mode init
"""

import os
import re
import sys
import json
import argparse
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure the engine modules are importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(SCRIPT_DIR, "engine")
# Parent of prompt_gateway/ -- the real scripts/ dir holding task-gateway.py and
# workflow_contract.py. Derived from __file__, not from config.py's VERIDIAN_BASE,
# so it resolves correctly both in a plain git checkout (tests) and at the live
# deployed location (/opt/veridian/scripts/prompt_gateway/gateway.py) without
# needing VERIDIAN_BASE set in either environment.
SCRIPTS_ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, SCRIPTS_ROOT_DIR)

from config import (
    VERIDIAN_BASE as BASE, DATA_DIR, CHATS_DIR, TAGS_DIR, LOGS_DIR,
    CONTEXT_DIR, SNIPPETS_DIR, SNIP_INDEX_FILE, LOG_LEVEL, LOG_FORMAT, LOG_FILE, get_config
)
from engine.id_generator import IDGenerator, FileTagger
from engine.classifier import ChatClassifier
from engine.prompt_engine import PromptEngine
from engine.context_engine import ContextManager
from engine.snip_engine import SnipEngine
from engine import document_engine
from engine.layer0_router import classify_layer0  # noqa: E402 -- task-20260730-owner-engine-layer0
from workflow_contract import has_all_required_sections  # noqa: E402
import gateway_persistence  # noqa: E402 -- task-20260730-owner-engine-layer0
# The real, single "software could not tell -> ask the Owner" decision
# procedure (OWNER_ENGINE_MANDATORY_GATE_IMPLEMENTATION_2026-07-25.yaml,
# step_3) -- imported and reused here, not re-implemented, so --mode
# owner-dispatch's real task-gateway.py calls are gated by the exact same
# NEEDS_OWNER_CLARIFICATION logic query.py already exposes (task-20260726-
# 101257 SCOPE item 1: this dispatch path previously skipped that gate
# entirely).
from query import run_query  # noqa: E402

# The real, production task lifecycle dispatcher this module's new --mode
# owner-dispatch calls into (task-20260726-092433 SCOPE item 1).
TASK_GATEWAY_PY = os.path.join(SCRIPTS_ROOT_DIR, "task-gateway.py")

# ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml system_definition's own GITHUB repo
# set -- reused here (not re-enumerated) as the deterministic --repo guess for a
# full task-spec message that doesn't name its target repo any more explicitly.
KNOWN_REPOS = ["claude-control", "compliance-tracker", "projexa"]
DEFAULT_REPO = "claude-control"

# Words that turn "a message naming an existing task_id" into a real status or
# completion query, as opposed to e.g. a message merely referencing a task_id
# in passing. Deliberately reuses ChatClassifier's own extract_entities() TASK_ID
# pattern for the id half of this check rather than a second one here.
STATUS_QUERY_WORDS_RE = re.compile(
    r"\b(status|progress|done|complete|completed|finished|finish)\b", re.IGNORECASE
)

# task-20260730-owner-engine-layer0: the real, justified confidence-gate
# threshold for step 3 of that task (do not silently drop a low-confidence
# classification that Layer 0 also failed to recognize).
#
# NOT the "60-70%" figure generic literature on production intent routers
# suggests -- that figure assumes a calibrated probability from a model
# trained/scored so that ~60-70% really does mean "usually right". This
# classifier (engine/classifier.py ChatClassifier.classify()) is a
# keyword-ratio + flat pattern-bonus score, not a calibrated probability:
# running its own __main__ self-test suite (six clearly-correct, humanly-
# unambiguous example messages) live on 2026-07-30 produced confidences of
# 0.0514, 0.2316, 0.0514, 0.1286, 0.0667, 0.2333 -- every one of them well
# under even 0.30, let alone 0.60. A 60-70% threshold on THIS scoring scheme
# would flag every genuine short Owner instruction as low-confidence, which
# is a worse regression than the bug being fixed.
# The real failing input this task exists to fix (gateway_output_20260730.
# json) scored 0.3263 -- already higher than every one of those six clearly-
# correct examples, despite being a completely unrelated governance
# paragraph that only won QUERY by incidental "how will..." keyword/pattern
# overlap. 0.40 is set just above that real failing value and comfortably
# above the real correct-classification ceiling observed above, so it
# catches exactly the "long, keyword-dense, wrong-category" failure shape
# without re-flagging genuine short instructions.
REVIEW_CONFIDENCE_THRESHOLD = 0.40
# Categories excluded from the confidence-gate: TASK already gets a real
# downstream destination via determine_lifecycle_route's submit/start
# action; GENERAL and GOVERNANCE are gateway.py's own designated buckets
# for "nothing further to dispatch" (GENERAL) and "already persisted as a
# directive by Layer 0 or this same gate below" (GOVERNANCE) -- flagging
# either for a SECOND review record would be pure noise, not a real gap.
REVIEW_GATE_EXCLUDED_CATEGORIES = {"TASK", "GENERAL", "GOVERNANCE"}


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging(log_file=None):
    """Configure logging for the gateway."""
    log_path = log_file or LOG_FILE
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stderr),
        ]
    )
    return logging.getLogger("VERIDIAN")


logger = setup_logging()


# =============================================================================
# TASK LIFECYCLE ROUTING (task-20260726-092433 SCOPE item 1)
# =============================================================================
def determine_lifecycle_route(category: str, entities: list, raw_text: str) -> dict:
    """
    Decide which task-gateway.py subcommand (if any) a classified Owner
    message should be routed to. Returns {"action": one of
    "status"/"start"/"submit"/"none", "task_id": str or None}.

    Order matters:
      1. A message naming an existing TASK_ID entity together with a
         status/completion word ("what's the status of task-...", "is
         task-... done yet") is a status-or-completion query even if it
         also scored into the TASK category -- checked first so a lookup
         against an EXISTING task is never misrouted as a new dispatch.
      2. A message that already carries every literal_template section
         header (## OBJECTIVE / ## SCOPE / ...) is a complete, ready-to-
         dispatch task spec. has_all_required_sections() is the exact same
         check task-gateway.py's cmd_start applies to a prompt-file
         (imported from workflow_contract.py, not re-implemented) --
         routes to start.
      3. Anything else classified into the TASK category with no existing
         task_id is a new, not-yet-tightened task idea -- routes to submit
         (the dedup/search/capability-lookup entrypoint; start is wrong
         here, it requires the fully-authored spec case 2 already covers).
      4. Everything else is not a task-lifecycle request -- action "none",
         left for normal chat processing.
    """
    task_ids = [e["value"] for e in entities if e.get("type") == "TASK_ID"]
    if task_ids and STATUS_QUERY_WORDS_RE.search(raw_text):
        return {"action": "status", "task_id": task_ids[0]}
    if has_all_required_sections(raw_text):
        return {"action": "start", "task_id": None}
    if category == "TASK" and not task_ids:
        return {"action": "submit", "task_id": None}
    return {"action": "none", "task_id": None}


def _derive_title_from_spec(raw_text: str, fallback: str) -> str:
    """First non-empty line of the ## OBJECTIVE section, truncated -- the
    same section cmd_start's own audit trail treats as the task's real
    intent. Falls back to `fallback` (the chat_id) if OBJECTIVE can't be
    isolated, so --title is always non-empty."""
    m = re.search(r"##\s*OBJECTIVE\s*\n+(.+)", raw_text)
    if not m:
        return fallback
    line = m.group(1).strip().splitlines()[0].strip().lstrip("#").strip()
    return line if len(line) <= 80 else line[:77] + "..."


def _derive_repo_from_spec(raw_text: str) -> str:
    """First of KNOWN_REPOS literally named in the spec text, else
    DEFAULT_REPO. A real, disclosed heuristic -- not a guess dressed up as
    certainty. Corrected 2026-07-26 (task-20260726-101257 SCOPE item 3):
    this function's guess feeds straight into the SAME dispatch_to_task_
    lifecycle() call that invokes `start` -- there is no downstream pause
    where a caller could inspect derived_repo and correct a wrong guess
    before anything runs; by the time a caller sees derived_repo in the
    return value, `start` has already executed against it. The real
    override checkpoint is dispatch_to_task_lifecycle()'s / route_and_
    dispatch()'s repo_override param (--repo on --mode owner-dispatch):
    supply it up front and this guess is never consulted at all."""
    for repo in KNOWN_REPOS:
        if repo in raw_text:
            return repo
    return DEFAULT_REPO


def _default_subprocess_runner(cmd, input_text=None):
    """Real invocation of task-gateway.py. Tests inject a fake runner with
    the same (cmd, input_text) -> dict signature that records the
    constructed argv instead of executing it -- see
    tests/test_gateway_task_integration.py -- so the integration test
    proves the command was built correctly without ever performing a live
    production dispatch (real systemd start, real gh calls, real credit
    spend)."""
    proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout) if proc.stdout else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "command": cmd, "returncode": proc.returncode,
        "stdout": proc.stdout, "stderr": proc.stderr, "parsed": parsed,
    }


def dispatch_to_task_lifecycle(route: dict, chat_result: dict, raw_text: str,
                                session_id: Optional[str], runner=_default_subprocess_runner,
                                repo_override: Optional[str] = None) -> dict:
    """
    The real, single trigger point from OWNER_ENGINE's own classification
    into the real task-gateway.py CLI -- no AI turn or human hand-
    constructs the command in between (task-20260726-092433 OBJECTIVE).

    Text passed to task-gateway.py is always chat_result["final_output"]
    (the ALREADY-gated machine prompt this same process just produced),
    never raw_text, and --source is always ai_agent: this call is software
    calling software, not a second raw-Owner-text gate. Using --source
    owner here would make task-gateway.py's own run_owner_engine_gate()
    re-run this exact classification a second time for no benefit, and
    risks recursion if this function were ever reached from inside that
    gate (it currently never is -- see gateway.py's module docstring).

    repo_override (task-20260726-101257 SCOPE item 3): the real override
    checkpoint for action "start"'s target repo. When given, takes
    precedence over _derive_repo_from_spec()'s literal-name guess entirely
    -- there is no later point in this same call where a wrong guess could
    otherwise be corrected before `start` runs against it.
    """
    action = route["action"]
    chat_id = chat_result["chat_id"]
    final_output = chat_result["final_output"]
    effective_session = session_id or chat_id

    if action == "status":
        cmd = ["python3", TASK_GATEWAY_PY, "status", "--task-id", route["task_id"]]
        return {"action": action, "task_id": route["task_id"], "result": runner(cmd)}

    if action == "submit":
        cmd = ["python3", TASK_GATEWAY_PY, "submit",
               "--text", final_output, "--source", "ai_agent",
               "--session-id", effective_session]
        return {"action": action, "task_id": None, "result": runner(cmd)}

    if action == "start":
        submit_cmd = ["python3", TASK_GATEWAY_PY, "submit",
                      "--text", final_output, "--source", "ai_agent",
                      "--session-id", effective_session]
        submit_outcome = runner(submit_cmd)
        submit_parsed = submit_outcome.get("parsed") or {}
        instruction_id = submit_parsed.get("instruction_id")
        if not instruction_id:
            return {
                "action": action, "task_id": None, "submit_result": submit_outcome,
                "result": None,
                "start_skipped_reason": "submit did not return an instruction_id",
            }

        # Real fix, task-20260726-101257 SCOPE item 2: submit's own
        # check-duplicate call (cmd_submit in task-gateway.py) already
        # reports duplicate_found/duplicate_evidence in its JSON response --
        # auto-firing `start` right after submit regardless was a real
        # duplicate-dispatch risk, directly opposite this whole initiative's
        # "zero duplication" principle. Surface the finding instead of
        # deciding on the Owner's/AI's behalf, matching how a manual
        # dispatch would behave when a human notices duplicate_found: true.
        if submit_parsed.get("duplicate_found"):
            return {
                "action": action, "task_id": None, "submit_result": submit_outcome,
                "result": None,
                "start_skipped_reason": "submit reported duplicate_found=true -- "
                                         "surfacing for human/AI review instead of "
                                         "auto-starting a possibly-duplicate task",
                "duplicate_evidence": submit_parsed.get("duplicate_evidence", []),
            }

        title = _derive_title_from_spec(raw_text, fallback=chat_id)
        repo = repo_override or _derive_repo_from_spec(raw_text)
        fd, prompt_path = tempfile.mkstemp(suffix=".txt", prefix=f"owner_dispatch_{chat_id}_")
        os.close(fd)
        with open(prompt_path, "w") as f:
            f.write(raw_text)

        try:
            start_cmd = ["python3", TASK_GATEWAY_PY, "start",
                         "--instruction-id", instruction_id,
                         "--title", title, "--repo", repo,
                         "--prompt-file", prompt_path]
            start_outcome = runner(start_cmd)
        finally:
            # veridian-task.py create (called inside cmd_start) persists the real,
            # permanent copy at ai-os/tasks/<task_id>/prompt.txt -- this is only a
            # transient CLI-argument staging file, same lifecycle as
            # run_owner_engine_gate()'s own tempfile above.
            if os.path.isfile(prompt_path):
                os.remove(prompt_path)

        return {
            "action": action, "task_id": None,
            "submit_result": submit_outcome, "result": start_outcome,
            "derived_title": title, "derived_repo": repo,
        }

    return {"action": "none", "task_id": None, "result": None}


# =============================================================================
# TASK GATEWAY CLASS
# =============================================================================
class TaskGateway:
    """
    Central orchestrator for the VERIDIAN Software Engine.
    Coordinates all sub-modules in the chat processing pipeline.
    """

    def __init__(self, base_dir=None):
        self.base_dir = Path(base_dir) if base_dir else Path(BASE)

        if base_dir:
            # Explicit override (e.g. --base-dir for testing): derive the same
            # relative layout under it rather than the namespaced config paths.
            data_dir = str(self.base_dir / "data")
            tags_dir = str(self.base_dir / "data" / "tags")
            context_dir = str(self.base_dir / "data" / "context")
            snippets_dir = str(self.base_dir / "snippets")
            snip_index_file = str(self.base_dir / "snippets" / "snip_index.json")
        else:
            # Default: use the real, namespaced prompt_gateway data paths from
            # config.py (/opt/veridian/data/prompt_gateway/...), not bare
            # /opt/veridian/data or /opt/veridian/snippets, so this module never
            # collides with other tools that write under /opt/veridian directly.
            data_dir = DATA_DIR
            tags_dir = TAGS_DIR
            context_dir = CONTEXT_DIR
            snippets_dir = SNIPPETS_DIR
            snip_index_file = SNIP_INDEX_FILE

        # Initialize all engine modules
        self.id_gen = IDGenerator(base_dir=data_dir)
        self.tagger = FileTagger(tags_dir=tags_dir)
        self.classifier = ChatClassifier()
        self.prompt_engine = PromptEngine()
        self.context_mgr = ContextManager(context_dir=context_dir)
        self.snip_engine = SnipEngine(
            snippets_dir=snippets_dir,
            index_file=snip_index_file,
        )

        logger.info("TaskGateway initialized")
        logger.info(f"Base directory: {self.base_dir}")

    def process_chat(self, raw_text: str, role: str = "user",
                     session_id: str = None) -> dict:
        """
        Process a single chat message through the full pipeline.
        
        Args:
            raw_text: The raw chat text from the user
            role: Message role (user/assistant/system)
            session_id: Optional session ID for context grouping
            
        Returns:
            Complete processing result dict.
        """
        pipeline_start = datetime.now(timezone.utc)

        logger.info(f"Processing chat ({len(raw_text)} chars)...")

        # === STEP 1: Generate Chat ID ===
        chat_id = self.id_gen.generate_id()
        logger.info(f"Generated chat ID: {chat_id}")

        # Document-scale input (multi-page structured text: headings, tables,
        # many sections) takes a dedicated path -- see _process_document_chat
        # for why: the single-sentence pipeline below forces one category
        # onto the whole input, caps entities at 5, and runs a template
        # compiler written for one imperative sentence.
        if document_engine.is_document_input(raw_text):
            return self._process_document_chat(raw_text, role, session_id, chat_id, pipeline_start)

        # === STEP 2: Classify (software, not AI) ===
        analysis = self.classifier.full_analysis(raw_text)

        # === STEP 2a: Layer 0 deterministic router (task-20260730-owner-engine-layer0) ===
        # Real root cause this closes: gateway_output_20260730.json / KE-20260730-
        # 041850-63cf -- a real governance instruction scored category=QUERY,
        # confidence=0.3263 under the scoring above, and had no lifecycle-route
        # branch to go to, so it was saved to CHATS_DIR and nowhere else.
        # classify_layer0() is a pure function of raw_text only (see
        # engine/layer0_router.py) -- called after full_analysis() purely so
        # entity/token/word-count extraction above runs exactly once regardless
        # of outcome; it does not consult or change WHAT Layer 0 decides.
        layer0_result = classify_layer0(raw_text)
        governance_record = None
        reuse_check = None
        status_lookup = None
        review_flag = None

        if layer0_result and layer0_result["layer0_category"] == "GOVERNANCE_DIRECTIVE":
            analysis["classification"]["category"] = "GOVERNANCE"
            analysis["classification"]["confidence"] = 1.0
            analysis["intent"] = self.classifier.extract_intent(raw_text)
            governance_record = gateway_persistence.insert_instruction(
                text=raw_text, source="owner", medium="owner_engine_layer0_governance_directive",
                campaign=session_id or "", session_id=session_id or chat_id,
                metadata={
                    "chat_id": chat_id, "layer0_category": "GOVERNANCE_DIRECTIVE",
                    "matched_pattern": layer0_result["matched_pattern"],
                    "matched_text": layer0_result["matched_text"],
                    "requires_protocol_incorporation": True,
                },
                response_summary="Captured by prompt_gateway Layer 0 as a governance/policy "
                                  "directive; not yet incorporated into PROTOCOL_OWNER_AI.yaml "
                                  "-- see PENDING_OWNER_REVIEW.md.",
            )
            gateway_persistence.append_pending_review(
                chat_id,
                f"GOVERNANCE DIRECTIVE captured by prompt_gateway Layer 0 "
                f"(matched: '{layer0_result['matched_text']}'). Needs a human/Claude session "
                f"to draft the next dated addendum in "
                f"ai-os/OWNER_DIRECTIVES/PROTOCOL_OWNER_AI.yaml. "
                f"instruction_id={governance_record.get('instruction_id')}",
            )
            logger.info(f"Layer0 GOVERNANCE_DIRECTIVE matched, persisted: {governance_record}")

        elif layer0_result and layer0_result["layer0_category"] == "TASK_DISPATCH":
            analysis["classification"]["category"] = "TASK"
            analysis["classification"]["confidence"] = 1.0
            analysis["intent"] = self.classifier.extract_intent(raw_text)
            # task step 4: real reuse-check BEFORE any new task record gets created.
            # Calls the same superboss-register.py lookup-capability primitive
            # plan_generator.py's own _lookup_capability() wraps -- see
            # gateway_persistence.py's module docstring for why this calls that
            # primitive directly rather than plan_generator.py's own CLI.
            reuse_check = gateway_persistence.lookup_capability_reuse(raw_text)
            logger.info(f"Layer0 TASK_DISPATCH matched, reuse_check found={reuse_check.get('found')}")

        elif layer0_result and layer0_result["layer0_category"] == "STATUS_QUERY":
            analysis["classification"]["category"] = "STATUS_QUERY_LAYER0"
            analysis["classification"]["confidence"] = 1.0
            analysis["intent"] = "QUERY"
            # task step 2b: zero-AI-cost read-only lookup, no task_id required
            # (gateway.py's existing STATUS_QUERY_WORDS_RE path in
            # determine_lifecycle_route only fires when a TASK_ID entity is
            # already present in the message).
            status_lookup = gateway_persistence.search_status(raw_text)
            logger.info("Layer0 STATUS_QUERY matched, zero-AI-cost lookup performed")

        else:
            # task step 3: real confidence gate on the EXISTING classifier's own
            # output. See REVIEW_CONFIDENCE_THRESHOLD's definition above for why
            # 0.40, not the generic 60-70% figure, is the real justified number
            # for THIS classifier's scoring scheme.
            _cat = analysis["classification"]["category"]
            _conf = analysis["classification"]["confidence"]
            if (_cat not in REVIEW_GATE_EXCLUDED_CATEGORIES
                    and _conf is not None and _conf < REVIEW_CONFIDENCE_THRESHOLD):
                review_flag = gateway_persistence.insert_instruction(
                    text=raw_text, source="owner", medium="owner_engine_low_confidence_queue",
                    campaign=session_id or "", session_id=session_id or chat_id,
                    metadata={
                        "chat_id": chat_id, "category": _cat, "confidence": _conf,
                        "intent": analysis["intent"], "review_needed": True,
                        "review_reason": "LOW_CLASSIFICATION_CONFIDENCE_NO_LAYER0_MATCH",
                    },
                    response_summary=f"confidence={_conf} < {REVIEW_CONFIDENCE_THRESHOLD} threshold, "
                                      f"category={_cat}; flagged for review instead of silently dropped.",
                )
                gateway_persistence.append_pending_review(
                    chat_id,
                    f"LOW-CONFIDENCE classification ({_cat}, confidence={_conf}) not recognized "
                    f"by Layer 0 and not auto-dispatchable -- needs Owner/AI review. "
                    f"instruction_id={review_flag.get('instruction_id')}",
                )
                logger.info(f"Confidence gate: flagged for review, {review_flag}")

        category = analysis["classification"]["category"]
        intent = analysis["intent"]
        confidence = analysis["classification"]["confidence"]
        logger.info(f"Classification: {category} (confidence: {confidence}, intent: {intent})")

        # === STEP 3: Noise Removal + Machine Prompt Conversion ===
        prompt_result = self.prompt_engine.process(raw_text, analysis["classification"])
        cleaned_text = prompt_result["cleaned_text"]
        machine_prompt = prompt_result["machine_prompt"]
        noise_reduction = prompt_result["noise_stats"]["reduction_pct"]
        token_reduction = prompt_result["token_reduction"]["reduction_pct"]
        logger.info(f"Noise reduction: {noise_reduction}% chars, Token reduction: {token_reduction}%")

        # === STEP 4: Get relevant Snippets for prompt building ===
        snippet_assists = self.snip_engine.get_prompt_assist_snippets(
            category=category, intent=intent
        )
        snippet_names = [s["name"] for s in snippet_assists] if snippet_assists else []
        logger.info(f"Found {len(snippet_assists)} relevant snippets: {snippet_names}")

        # === STEP 5: Context Management ===
        context_id = session_id or chat_id
        window = self.context_mgr.get_window(context_id)
        window.add_message(role, raw_text, msg_id=chat_id)
        
        prune_stats = self.context_mgr.prune_and_save(context_id, current_query=raw_text)
        context_messages = prune_stats.get("messages_after", 0) if isinstance(prune_stats, dict) else 0
        
        # Get pruned context text
        pruned_context = window.get_context_text()
        pruned_tokens = prune_stats.get("tokens_after", 0) if isinstance(prune_stats, dict) else 0
        
        logger.info(f"Context: {context_messages} msgs, ~{pruned_tokens} tokens after prune")

        # === STEP 6: Build Final Output ===
        # The final output is the pruned context + machine prompt
        # This is what gets sent to Claude/AI, saving 50%+ tokens
        final_output = self._build_final_output(
            machine_prompt=machine_prompt,
            pruned_context=pruned_context,
            category=category,
            intent=intent,
            chat_id=chat_id,
            entities=analysis.get("entities", []),
            snippet_assists=snippet_assists,
        )

        pipeline_end = datetime.now(timezone.utc)
        processing_ms = (pipeline_end - pipeline_start).total_seconds() * 1000

        result = {
            "chat_id": chat_id,
            "session_id": session_id,
            "classification": {
                "category": category,
                "intent": intent,
                "confidence": confidence,
            },
            "processing": {
                "original_chars": len(raw_text),
                "cleaned_chars": len(cleaned_text),
                "noise_reduction_pct": noise_reduction,
                "original_est_tokens": analysis["estimated_tokens"],
                "machine_prompt": machine_prompt,
                "token_reduction_pct": token_reduction,
                "context_messages": context_messages,
                "context_tokens": pruned_tokens,
                "processing_time_ms": round(processing_ms, 1),
            },
            "entities": analysis.get("entities", []),
            "snippets_used": snippet_names,
            "final_output": final_output,
            "pipeline_timestamp_utc": pipeline_end.isoformat(),
            # task-20260730-owner-engine-layer0: real, disclosed trace of what
            # Layer 0 / the confidence gate decided and persisted for THIS run,
            # in addition to the unconditional _save_chat_record() below.
            "layer0": {
                "matched": bool(layer0_result),
                "layer0_category": (layer0_result or {}).get("layer0_category"),
                "matched_pattern": (layer0_result or {}).get("matched_pattern"),
                "governance_directive_recorded": governance_record,
                "reuse_check": reuse_check,
                "status_lookup": status_lookup,
                "low_confidence_review_flagged": review_flag,
            },
        }

        logger.info(f"Pipeline complete in {processing_ms:.1f}ms. Token reduction: {token_reduction}%")

        # === STEP 7: Save chat record (existing, unconditional, per-chat JSON) ===
        self._save_chat_record(chat_id, result)

        # === STEP 7b: real, queryable decision-history record (task step 5) ===
        # The pre-existing per-chat JSON above already persists this run, but
        # only under CHATS_DIR -- disconnected from the same FTS-backed
        # `instructions` store the rest of the system searches/audits through
        # (superboss-register.py search / check-duplicate). This closes that
        # gap: one row per real gateway.py run, regardless of which path
        # above fired, so gateway.py's own decision history is traceable the
        # same way chat-service.ts's recordOrchestraExecution() makes every
        # AI reply traceable.
        gateway_persistence.insert_instruction(
            text=raw_text, source="ai_agent", medium="owner_engine_gateway_decision_log",
            campaign=session_id or "", session_id=session_id or chat_id,
            metadata={
                "chat_id": chat_id, "category": category, "intent": intent,
                "confidence": confidence, "layer0_matched": bool(layer0_result),
                "layer0_category": (layer0_result or {}).get("layer0_category"),
                "governance_directive_instruction_id": (governance_record or {}).get("instruction_id"),
                "low_confidence_review_instruction_id": (review_flag or {}).get("instruction_id"),
            },
            response_summary=f"gateway.py process_chat run for {chat_id}: category={category}, "
                              f"confidence={confidence}, layer0={bool(layer0_result)}",
        )

        return result

    def route_and_dispatch(self, raw_text: str, session_id: str = None,
                            runner=_default_subprocess_runner,
                            repo_override: Optional[str] = None) -> dict:
        """
        task-20260726-092433 SCOPE item 1's single entrypoint: classify
        raw_text via the same process_chat() pipeline every other mode
        uses, decide whether it's a task-dispatch/status-query/completion-
        query request (determine_lifecycle_route), and if so call the real
        task-gateway.py directly (dispatch_to_task_lifecycle) -- closing
        the manual "AI reads OWNER_ENGINE's output, then hand-constructs a
        task-gateway.py command" gap. Returns process_chat()'s own result
        dict with one added key, "lifecycle_dispatch".

        Real fix, task-20260726-101257 SCOPE item 1: once a route decides a
        real task-gateway.py action is actually warranted (status/submit/
        start -- action "none" was never going to dispatch anything, so
        there is nothing here for a clarifying question to protect against),
        this now runs the exact same NEEDS_OWNER_CLARIFICATION check
        query.py exposes (OWNER_ENGINE_MANDATORY_GATE_IMPLEMENTATION_2026-
        07-25.yaml step_3) against the chat record process_chat() just
        saved, BEFORE dispatch_to_task_lifecycle() is ever called.
        Previously this whole dispatch path never consulted that gate at
        all, so an ambiguous Owner message (low classification confidence
        or unknown intent -- the exact two signals step_3 defines as
        "software could not tell") could be auto-dispatched as a real
        task-gateway.py submit/start instead of surfacing a clarifying
        question first.
        """
        chat_result = self.process_chat(raw_text, role="user", session_id=session_id)
        category = chat_result["classification"]["category"]
        entities = chat_result.get("entities", [])
        route = determine_lifecycle_route(category, entities, raw_text)

        if route["action"] != "none":
            clarification = run_query(chat_result["chat_id"], "NEEDS_OWNER_CLARIFICATION")
            needs_clarification = bool(
                (clarification.get("answer") or {}).get("needs_owner_clarification")
            )
            if needs_clarification:
                chat_result["lifecycle_dispatch"] = {
                    "action": "clarification_needed",
                    "task_id": None,
                    "result": None,
                    "clarification": clarification,
                }
                return chat_result

        chat_result["lifecycle_dispatch"] = dispatch_to_task_lifecycle(
            route, chat_result, raw_text, session_id, runner=runner,
            repo_override=repo_override,
        )
        return chat_result

    def _build_final_output(self, machine_prompt: str, pruned_context: str,
                             category: str, intent: str, chat_id: str,
                             entities: list, snippet_assists: list,
                             entity_cap: Optional[int] = 5) -> str:
        """
        Build the final pruned output that gets sent to Claude/AI.
        This replaces the original noisy chat with a concise, structured prompt.

        entity_cap=5 is the original short-chat-message behavior (a single
        instruction has at most a handful of entities worth referencing
        up front). Document mode passes entity_cap=None: a 50-section
        architecture spec can legitimately carry hundreds of real named
        entities (engines, technologies, numeric requirements), and silently
        truncating to 5 discards the vast majority of the document's content.
        """
        parts = []

        # Header with machine-readable metadata
        parts.append(f"[VERIDIAN:{chat_id}]")
        parts.append(f"[CAT:{category}|INTENT:{intent}]")

        # Entity references (if any)
        if entities:
            ent_list = entities if entity_cap is None else entities[:entity_cap]
            entity_refs = [f"{e['type']}={e['value']}" for e in ent_list]
            parts.append(f"[ENTS:{','.join(entity_refs)}]")

        # Machine prompt (the core instruction)
        parts.append(f"\n{machine_prompt}")

        # Snippet references (if any)
        if snippet_assists:
            snip_names = [s["name"] for s in snippet_assists[:3]]
            parts.append(f"\n[SNIPS:{','.join(snip_names)}]")

        # Pruned context (only high-relevance messages)
        if pruned_context and len(pruned_context) > 10:
            parts.append(f"\n---\n[CONTEXT]\n{pruned_context}")

        return "\n".join(parts)

    def _process_document_chat(self, raw_text: str, role: str, session_id: Optional[str],
                                chat_id: str, pipeline_start) -> dict:
        """
        Document-mode pipeline: parses real structure (headings, tables)
        instead of forcing the input through the single-sentence compiler.

        Differences from process_chat's short-message path, and why:
          - Classification is per-section (classify_document), not one
            category for the whole document -- ChatClassifier.classify()
            picks a single argmax category, which is correct for one
            instruction and wrong for a document spanning CODE, ANALYSIS,
            OPS, and QUERY content simultaneously.
          - machine_prompt is a structural digest (document_engine.
            build_document_digest) that preserves every heading and table
            row verbatim, instead of PromptConverter's single greedy-regex
            template match, which was written for one imperative sentence
            and garbles/truncates anything longer.
          - entities use extract_document_entities (unbounded, plus numeric-
            fact patterns) and are NOT capped at 5 in the final output.
          - the raw document text is NOT added to the context window as a
            literal chat message. CONTEXT_MIN_MESSAGES protects a lone
            message from pruning regardless of size, so an oversized single
            message would otherwise survive untouched in every future turn's
            [CONTEXT] block -- inflating final_output back to ~original size
            and defeating the token-reduction the gateway exists to provide.
            The document's real content already lives in machine_prompt; the
            context window gets a short pointer instead.
        """
        logger.info(f"Processing DOCUMENT ({len(raw_text)} chars)...")

        blocks = document_engine.parse_document(raw_text)
        sections = document_engine.section_breakdown(blocks)

        doc_classification = self.classifier.classify_document(sections)
        category = doc_classification["primary_category"]
        intent = self.classifier.extract_intent(raw_text)
        entities = self.classifier.extract_document_entities(raw_text)
        logger.info(
            f"Document classification: {category} "
            f"(histogram: {doc_classification['category_histogram']}), "
            f"{len(sections)} sections, {len(entities)} entities"
        )

        machine_prompt = document_engine.build_document_digest(blocks)
        cleaned_text = machine_prompt

        original_length = len(raw_text)
        final_length = len(machine_prompt)
        noise_reduction_pct = round((1 - final_length / max(original_length, 1)) * 100, 1)
        token_reduction = self.prompt_engine.prompt_converter.estimate_token_reduction(
            raw_text, machine_prompt
        )
        logger.info(
            f"Document digest: {noise_reduction_pct}% chars, "
            f"Token reduction: {token_reduction['reduction_pct']}%"
        )

        snippet_assists = self.snip_engine.get_prompt_assist_snippets(
            category=category, intent=intent
        )
        snippet_names = [s["name"] for s in snippet_assists] if snippet_assists else []

        context_id = session_id or chat_id
        window = self.context_mgr.get_window(context_id)
        context_placeholder = (
            f"[document submitted: {chat_id}, {len(sections)} sections, "
            f"{original_length} chars -- full content in machine_prompt, not context]"
        )
        window.add_message(role, context_placeholder, msg_id=chat_id)
        prune_stats = self.context_mgr.prune_and_save(context_id, current_query=context_placeholder)
        context_messages = prune_stats.get("messages_after", 0) if isinstance(prune_stats, dict) else 0
        pruned_context = window.get_context_text()
        pruned_tokens = prune_stats.get("tokens_after", 0) if isinstance(prune_stats, dict) else 0

        final_output = self._build_final_output(
            machine_prompt=machine_prompt,
            pruned_context=pruned_context,
            category=category,
            intent=intent,
            chat_id=chat_id,
            entities=entities,
            snippet_assists=snippet_assists,
            entity_cap=None,
        )

        pipeline_end = datetime.now(timezone.utc)
        processing_ms = (pipeline_end - pipeline_start).total_seconds() * 1000

        result = {
            "chat_id": chat_id,
            "session_id": session_id,
            "document_mode": True,
            "classification": {
                "category": category,
                "intent": intent,
                "confidence": None,
                "category_histogram": doc_classification["category_histogram"],
                "section_classifications": doc_classification["section_classifications"],
            },
            "processing": {
                "original_chars": original_length,
                "cleaned_chars": final_length,
                "noise_reduction_pct": noise_reduction_pct,
                "original_est_tokens": len(raw_text.split()),
                "machine_prompt": machine_prompt,
                "token_reduction_pct": token_reduction["reduction_pct"],
                "context_messages": context_messages,
                "context_tokens": pruned_tokens,
                "processing_time_ms": round(processing_ms, 1),
                "sections_detected": len(sections),
            },
            "entities": entities,
            "snippets_used": snippet_names,
            "final_output": final_output,
            "pipeline_timestamp_utc": pipeline_end.isoformat(),
        }

        logger.info(f"Document pipeline complete in {processing_ms:.1f}ms.")
        self._save_chat_record(chat_id, result)
        return result

    def _save_chat_record(self, chat_id: str, result: dict):
        """Save the processing record to disk."""
        chat_file = Path(CHATS_DIR) / f"{chat_id}.json"
        chat_file.parent.mkdir(parents=True, exist_ok=True)
        with open(chat_file, "w") as f:
            json.dump(result, f, indent=2, default=str)

    def tag_files(self, chat_id: str, file_paths: list, metadata: dict = None):
        """Tag files with a chat ID."""
        for fp in file_paths:
            self.tagger.tag_file(chat_id, fp, metadata)

    def get_chat_record(self, chat_id: str) -> dict:
        """Retrieve a previously saved chat record."""
        chat_file = Path(CHATS_DIR) / f"{chat_id}.json"
        if chat_file.exists():
            with open(chat_file, "r") as f:
                return json.load(f)
        return {"error": f"No record found for {chat_id}"}

    def init_system(self):
        """
        Initialize the VERIDIAN system.
        Creates directories, seeds snippets, and validates installation.
        """
        logger.info("Initializing VERIDIAN system...")
        
        # Ensure all directories exist
        dirs = [DATA_DIR, CHATS_DIR, TAGS_DIR, LOGS_DIR, CONTEXT_DIR]
        for d in dirs:
            Path(d).mkdir(parents=True, exist_ok=True)
            logger.info(f"  Created directory: {d}")

        # Seed default snippets
        count = self.snip_engine.seed_defaults()
        logger.info(f"  Seeded {count} default snippets")

        # Verify all modules
        modules = [
            ("ID Generator", self.id_gen),
            ("File Tagger", self.tagger),
            ("Classifier", self.classifier),
            ("Prompt Engine", self.prompt_engine),
            ("Context Manager", self.context_mgr),
            ("Snip Engine", self.snip_engine),
        ]
        for name, module in modules:
            logger.info(f"  Module OK: {name}")

        # Generate a test ID to verify
        test_id = self.id_gen.generate_id()
        logger.info(f"  Test ID generated: {test_id}")

        # Test classification
        test_result = self.classifier.classify("write a python script to parse json")
        logger.info(f"  Test classification: {test_result['category']}")

        logger.info("VERIDIAN system initialized successfully!")

        return {
            "status": "initialized",
            "test_chat_id": test_id,
            "test_classification": test_result,
            "directories_created": [str(d) for d in dirs],
        }


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="VERIDIAN Prompt Gateway - Raw Chat Pre-Processing Pipeline "
                     "(feeds task-gateway.py --text, does not replace it)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  echo "Fix the auth error" | python3 gateway.py --mode stdin
  python3 gateway.py --mode file --input chat.json
  python3 gateway.py --mode init
  python3 gateway.py --mode interactive
        """
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=["stdin", "file", "interactive", "pipe", "init", "owner-dispatch"],
        default="stdin",
        help="Processing mode (default: stdin). owner-dispatch is the single "
             "entrypoint that also calls task-gateway.py submit/start/status "
             "directly when the classified message is a task-lifecycle request."
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        help="Input file path (for --mode file)"
    )
    parser.add_argument(
        "--session", "-s",
        type=str,
        default=None,
        help="Session ID for context grouping"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Output only the machine prompt (not the full JSON result)"
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default=None,
        help="Override VERIDIAN base directory"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="For --mode owner-dispatch's 'start' action only: explicit target "
             "repo, overriding _derive_repo_from_spec()'s literal-name guess "
             "entirely. This is the real override checkpoint (task-20260726-"
             "101257 SCOPE item 3) -- there is no later point after `start` "
             "runs where a wrong guess could otherwise be corrected."
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    gateway = TaskGateway(base_dir=args.base_dir)

    # === MODE: INIT ===
    if args.mode == "init":
        result = gateway.init_system()
        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
        else:
            print(json.dumps(result, indent=2))
        return

    # === MODE: FILE ===
    if args.mode == "file":
        if not args.input:
            print("Error: --input required for --mode file", file=sys.stderr)
            sys.exit(1)
        
        with open(args.input, "r") as f:
            input_data = json.load(f)
        
        raw_text = input_data.get("text", input_data.get("message", input_data.get("content", "")))
        role = input_data.get("role", "user")
        session = args.session or input_data.get("session_id")

        result = gateway.process_chat(raw_text, role=role, session_id=session)
        _output_result(result, args)
        return

    # === MODE: STDIN ===
    if args.mode == "stdin":
        raw_text = sys.stdin.read().strip()
        if not raw_text:
            print("Error: No input on stdin", file=sys.stderr)
            sys.exit(1)
        result = gateway.process_chat(raw_text, role="user", session_id=args.session)
        _output_result(result, args)
        return

    # === MODE: OWNER-DISPATCH ===
    # The single trigger point for a raw Owner message (task-20260726-092433):
    # classify + route + call task-gateway.py submit/start/status directly.
    if args.mode == "owner-dispatch":
        raw_text = sys.stdin.read().strip()
        if not raw_text:
            print("Error: No input on stdin", file=sys.stderr)
            sys.exit(1)
        result = gateway.route_and_dispatch(raw_text, session_id=args.session,
                                             repo_override=args.repo)
        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2, default=str)
        else:
            print(json.dumps(result, indent=2, default=str))
        return

    # === MODE: PIPE ===
    if args.mode == "pipe":
        for line in sys.stdin:
            raw_text = line.strip()
            if not raw_text:
                continue
            result = gateway.process_chat(raw_text, role="user", session_id=args.session)
            if args.json_only:
                print(result["final_output"])
            else:
                print(result["final_output"])
            print("---")
        return

    # === MODE: INTERACTIVE ===
    if args.mode == "interactive":
        session = args.session or f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        print(f"VERIDIAN Interactive Mode (session: {session})")
        print("Enter chat messages. Type 'exit' or 'quit' to stop.")
        print("=" * 60)
        
        while True:
            try:
                raw_text = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            
            if raw_text.lower() in ("exit", "quit"):
                break
            if not raw_text:
                continue
            
            result = gateway.process_chat(raw_text, role="user", session_id=session)
            print(f"\n[ID: {result['chat_id']}]")
            print(f"Category: {result['classification']['category']} | "
                  f"Intent: {result['classification']['intent']}")
            print(f"Token reduction: {result['processing']['token_reduction_pct']}%")
            print(f"Processing time: {result['processing']['processing_time_ms']}ms")
            print(f"\n--- MACHINE PROMPT ---")
            print(result["final_output"])
            print("=" * 60)
        
        print("\nSession ended. Context saved.")
        return


def _output_result(result: dict, args):
    """Output processing result."""
    if args.json_only:
        print(result["final_output"])
    else:
        output = result["final_output"]
        print(output)
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Full result saved to: {args.output}")


if __name__ == "__main__":
    main()
