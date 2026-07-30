#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Configuration
==========================================
Central configuration for the VERIDIAN chat processing pipeline.
All paths and settings are software-controlled, not AI-controlled.
"""

import os
import json
from pathlib import Path
from datetime import datetime

# =============================================================================
# BASE PATHS (auto-detected, override via env vars)
# =============================================================================
VERIDIAN_BASE = os.environ.get("VERIDIAN_BASE", "/opt/veridian")
# This module lives at /opt/veridian/scripts/prompt_gateway/ -- its own namespaced
# subdirectory under the real scripts/ dir, NOT the scripts/ dir itself (which holds
# the unrelated production task-gateway.py). Keep SCRIPTS_DIR pointed at this
# namespace so ENGINE_DIR/LIB_DIR resolve to prompt_gateway/engine and
# prompt_gateway/lib, not the top-level scripts/ tree.
SCRIPTS_DIR = os.path.join(VERIDIAN_BASE, "scripts", "prompt_gateway")
ENGINE_DIR = os.path.join(SCRIPTS_DIR, "engine")
# All writable data lives under its own /opt/veridian/data/prompt_gateway namespace
# so it never collides with existing /opt/veridian/ai-os/ or /opt/veridian/chatgpt-audit/
# directories, or with any other tool that later wants /opt/veridian/data/ for itself.
DATA_DIR = os.path.join(VERIDIAN_BASE, "data", "prompt_gateway")
SNIPPETS_DIR = os.path.join(DATA_DIR, "snippets")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
CHATS_DIR = os.path.join(DATA_DIR, "chats")
TAGS_DIR = os.path.join(DATA_DIR, "tags")
CONTEXT_DIR = os.path.join(DATA_DIR, "context")
LIB_DIR = os.path.join(SCRIPTS_DIR, "lib")

# Ensure directories exist
for d in [VERIDIAN_BASE, SCRIPTS_DIR, ENGINE_DIR, DATA_DIR, SNIPPETS_DIR,
          LOGS_DIR, CHATS_DIR, TAGS_DIR, CONTEXT_DIR, LIB_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# =============================================================================
# CHAT ID FORMAT
# =============================================================================
ID_PREFIX = os.environ.get("VERIDIAN_ID_PREFIX", "VD")
ID_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
ID_COUNTER_WIDTH = 4  # zero-padded counter width

# =============================================================================
# CLASSIFICATION CATEGORIES (software-driven, keyword + pattern matching)
# =============================================================================
CHAT_CATEGORIES = {
    "CODE": {
        "description": "Software development, code writing, debugging, refactoring",
        "keywords": ["code", "function", "class", "module", "script", "debug", "error",
                     "bug", "fix", "refactor", "implement", "build", "compile", "deploy",
                     "api", "endpoint", "database", "server", "test", "unit test",
                     "git", "commit", "branch", "merge", "python", "javascript", "typescript",
                     "react", "next.js", "node", "sql", "docker", "kubernetes", "css", "html"],
        "patterns": [r"write\s+(a\s+)?(script|function|class|module|code)",
                     r"(create|build|make|implement|add)\s+(a\s+)?(api|endpoint|feature|service)",
                     r"(fix|debug|resolve|solve)\s+(the\s+)?(bug|error|issue|problem)",
                     r"(run|execute|test)\s+(the\s+)?(script|code|test|command)"]
    },
    "ANALYSIS": {
        "description": "Data analysis, review, assessment, evaluation",
        "keywords": ["analyze", "analysis", "review", "assess", "evaluate", "compare",
                     "measure", "metric", "report", "data", "statistics", "trend",
                     "performance", "benchmark", "audit", "inspect", "check", "verify"],
        "patterns": [r"analyze\s+(the\s+)?(data|results|performance|logs)",
                     r"(review|audit|assess)\s+(the\s+)?(code|system|process)",
                     r"(compare|benchmark)\s+(the\s+)?(results|performance|data)"]
    },
    "OPS": {
        "description": "System operations, deployment, infrastructure, maintenance",
        "keywords": ["deploy", "server", "infrastructure", "monitor", "log", "restart",
                     "configure", "setup", "install", "update", "upgrade", "migrate",
                     "backup", "restore", "container", "vm", "cloud", "aws", "gcp", "azure",
                     "linux", "systemd", "nginx", "apache", "ssl", "certificate"],
        "patterns": [r"(deploy|install|setup|configure)\s+(the\s+)?(server|system|service)",
                     r"(restart|stop|start)\s+(the\s+)?(server|service|container)",
                     r"(monitor|check)\s+(the\s+)?(server|system|logs|status)"]
    },
    "QUERY": {
        "description": "Questions, information requests, knowledge lookup",
        "keywords": ["what", "how", "why", "when", "where", "which", "who", "tell",
                     "explain", "describe", "show", "find", "search", "lookup", "get",
                     "information", "help", "question", "understand"],
        "patterns": [r"(what|how|why|when|where|which|who)\s+(is|are|was|were|do|does|did|can|should|will)",
                     r"(tell|explain|describe|show)\s+(me\s+)?(the|about|how)",
                     r"(find|search|lookup|get)\s+(the\s+)?(information|details|data)"]
    },
    "TASK": {
        "description": "Action items, to-dos, assignments, workflow management",
        "keywords": ["task", "todo", "assign", "schedule", "priority", "deadline",
                     "reminder", "follow-up", "track", "progress", "milestone", "sprint",
                     "plan", "roadmap", "backlog", "story", "ticket", "issue"],
        "patterns": [r"(create|add|make)\s+(a\s+)?(task|todo|ticket|issue)",
                     r"(assign|schedule|prioritize)\s+(the\s+)?(task|work|job)",
                     r"(update|track|check)\s+(the\s+)?(progress|status|task)"]
    },
    "GENERAL": {
        "description": "General conversation, greetings, misc",
        "keywords": ["hello", "hi", "hey", "thanks", "thank", "bye", "goodbye",
                     "ok", "okay", "sure", "yes", "no", "please", "welcome"],
        "patterns": []
    },
    # task-20260730-owner-engine-layer0: defense-in-depth alongside
    # engine/layer0_router.py's dedicated GOVERNANCE_PATTERNS pre-classifier
    # (which runs BEFORE this scoring and normally decides governance-shaped
    # text first). This category exists so that if Layer 0 ever misses a
    # governance-shaped phrasing, the regular argmax scoring below still has
    # a real bucket to land in, instead of the message defaulting into QUERY
    # purely because of incidental "how"/"what" keyword overlap (the real
    # root cause of gateway_output_20260730.json's QUERY/0.3263 result).
    "GOVERNANCE": {
        "description": "Policy directives, standing-rule corrections, root-cause "
                        "governance statements from the Owner about how the system "
                        "itself must operate going forward",
        "keywords": ["going forward", "root cause", "policy", "protocol", "directive",
                     "governance", "standing rule", "from now on", "single point of entry",
                     "versioning", "traceability", "audited", "end to end", "not assumption"],
        "patterns": [r"\bgoing\s+forward\b", r"\broot\s+cause\b", r"\bwe\s+identified\s+(this|the)\s+issue\b",
                     r"\bensure\s+that\b", r"\bfrom\s+now\s+on\b", r"\bsingle\s+point\s+of\s+entry\b"]
    }
}

# =============================================================================
# NOISE REMOVAL RULES (software-driven, regex-based)
# =============================================================================
NOISE_PATTERNS = [
    # Filler phrases
    r"\b(you know|i mean|basically|actually|literally|honestly|frankly|to be honest)\b",
    r"\b(like i said|as mentioned|as i was saying|by the way|anyway|anyhow)\b",
    # Redundant qualifiers
    r"\b(very|really|quite|rather|somewhat|just|simply|merely|pretty much)\b",
    r"\b(in fact|as a matter of fact|it goes without saying|needless to say)\b",
    # Repeated words
    r"\b(\w+)\s+\1\b",
    # Overly polite padding
    r"\b(i would like to kindly|i just wanted to|i was wondering if)\b",
    # Empty intensifiers
    r"\b(absolutely|totally|completely|entirely|utterly|extremely)\b",
    # Meta-commentary
    r"\b(so to speak|if you will|as it were|in a sense|in other words)\b",
]

# =============================================================================
# PROMPT TEMPLATE MAPPINGS (English → machine prompt)
# =============================================================================
# These map natural language patterns to concise machine-language prompts
PROMPT_TEMPLATES = {
    # Code requests
    r"write (a |the )?(?P<type>script|function|class|module|program)\b": 
        "CODE_GEN:{type}",
    r"create (a |the )?(?P<type>script|function|class|module|program)\b":
        "CODE_GEN:{type}",
    r"(fix|debug|resolve)\s+(?P<issue>.+?)\s+(in|for|on)\s+(?P<target>.+)":
        "CODE_FIX:issue={issue}:target={target}",
    r"(add|implement|build)\s+(?P<feature>.+?)\s+(to|in|for)\s+(?P<target>.+)":
        "CODE_ADD:feature={feature}:target={target}",
    
    # Analysis requests
    r"analyze\s+(?P<subject>.+)":
        "ANALYZE:{subject}",
    r"(compare|benchmark)\s+(?P<a>.+?)\s+(with|against|to)\s+(?P<b>.+)":
        "COMPARE:{a}:{b}",
    r"(review|audit)\s+(?P<subject>.+)":
        "REVIEW:{subject}",
    
    # Query requests (how-do-I patterns)
    r"how (?:do|can|should|to) (?:i |we |you )?(?P<q>.+)\??":
        "QUERY:{q}",
    r"(what|why|when|where|which|who)\s+(?P<q>.+)\??":
        "QUERY:{q}",
    r"(find|search|lookup|get)\s+(?P<subject>.+)":
        "LOOKUP:{subject}",
    
    # Ops requests
    r"(deploy|install|setup|configure)\s+(?:the\s+)?(?P<target>.+)":
        "OPS:{target}",
    r"(restart|stop|start)\s+(?P<service>.+)":
        "OPS_{service}",
    
    # Describe/explain requests
    r"(describe|explain)\s+(?P<subject>.+)":
        "EXPLAIN:{subject}",
    
    # Compare requests
    r"(compare|difference between)\s+(?P<a>.+?)\s+(?:and|vs|versus|to)\s+(?P<b>.+)":
        "COMPARE:{a}:{b}",
    
    # Task requests
    r"(create|add|make)\s+(a\s+)?(task|todo)\s*[:=]?\s*(?P<desc>.+)":
        "TASK:{desc}",
}

# =============================================================================
# CONTEXT ENGINE SETTINGS
# =============================================================================
MAX_CONTEXT_TOKENS_ESTIMATE = 4000  # estimated token ceiling (reduced from typical 8000+)
CONTEXT_DECAY_FACTOR = 0.85  # older messages get progressively lower relevance scores
CONTEXT_PRUNE_THRESHOLD = 0.3  # messages below this relevance score are pruned
CONTEXT_MAX_MESSAGES = 20  # hard cap on context messages
CONTEXT_MIN_MESSAGES = 5  # always keep at least this many messages

# =============================================================================
# SNIP ENGINE SETTINGS
# =============================================================================
SNIP_INDEX_FILE = os.path.join(SNIPPETS_DIR, "snip_index.json")
SNIP_CATEGORIES = ["code_template", "command", "config", "pattern", "response_template", "workflow"]

# =============================================================================
# FILE TAGGING SETTINGS
# =============================================================================
TAG_FILE_PREFIX = "tag_"
TAG_FILE_EXT = ".json"

# =============================================================================
# LOGGING
# =============================================================================
LOG_LEVEL = os.environ.get("VERIDIAN_LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
LOG_FILE = os.path.join(LOGS_DIR, f"veridian_{datetime.now().strftime('%Y%m%d')}.log")


def get_config():
    """Return full config dict for external consumers."""
    return {
        "paths": {
            "base": VERIDIAN_BASE,
            "scripts": SCRIPTS_DIR,
            "engine": ENGINE_DIR,
            "data": DATA_DIR,
            "snippets": SNIPPETS_DIR,
            "logs": LOGS_DIR,
            "chats": CHATS_DIR,
            "tags": TAGS_DIR,
            "context": CONTEXT_DIR,
            "lib": LIB_DIR,
        },
        "id": {
            "prefix": ID_PREFIX,
            "counter_width": ID_COUNTER_WIDTH,
        },
        "context": {
            "max_tokens_estimate": MAX_CONTEXT_TOKENS_ESTIMATE,
            "decay_factor": CONTEXT_DECAY_FACTOR,
            "prune_threshold": CONTEXT_PRUNE_THRESHOLD,
            "max_messages": CONTEXT_MAX_MESSAGES,
            "min_messages": CONTEXT_MIN_MESSAGES,
        },
        "snip": {
            "index_file": SNIP_INDEX_FILE,
            "categories": SNIP_CATEGORIES,
        },
    }
