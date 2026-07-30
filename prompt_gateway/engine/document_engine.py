#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Document Engine
=============================================
Handles document-scale input (multi-page structured text: headings, tables,
many distinct sections) as a genuinely different processing path from the
single-sentence chat message path the rest of the pipeline was built and
tested for.

Root problem this exists to fix: ChatClassifier forces ONE category onto an
entire input regardless of length, PromptConverter's template regexes are
written to match one imperative sentence and garble/truncate anything longer,
and the final-output entity list is hard-capped at 5 -- all fine for "fix the
auth error", all silently destructive for a 50-section architecture document.

By software, not by AI. Deterministic parsing (headings, tables, numeric
claims) and deterministic digest assembly -- no API calls, no LLM calls.
"""

import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NOISE_PATTERNS

# =============================================================================
# DOCUMENT DETECTION
# =============================================================================
DOCUMENT_SIZE_THRESHOLD_CHARS = 1500
DOCUMENT_HEADING_MIN_COUNT = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")


def is_document_input(text: str) -> bool:
    """
    True if `text` looks like a multi-section document rather than a single
    chat message: either it is long, or it contains multiple markdown-style
    headings. Either signal alone is enough -- a long single paragraph with
    no headings still overwhelms the single-template compiler, and a short
    but clearly multi-section outline still needs structural preservation.
    """
    if len(text) >= DOCUMENT_SIZE_THRESHOLD_CHARS:
        return True
    heading_count = sum(1 for line in text.splitlines() if _HEADING_RE.match(line.strip()))
    return heading_count >= DOCUMENT_HEADING_MIN_COUNT


# =============================================================================
# STRUCTURE PARSING
# =============================================================================
def parse_document(text: str) -> list:
    """
    Parse raw text into an ordered list of structural blocks:
      {"type": "heading", "level": int, "text": str}
      {"type": "table", "rows": [[cell, ...], ...]}
      {"type": "paragraph", "text": str}

    Recognizes two table conventions so this works both on our own
    [TABLE]/[/TABLE]-delimited docx extraction and on plain markdown-style
    pipe tables that don't use those explicit markers:
      1. Explicit [TABLE] ... [/TABLE] markers wrapping pipe-delimited rows.
      2. 2+ consecutive lines each containing at least one '|' (a bare
         markdown table with no explicit markers).
    """
    blocks = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    paragraph_buf = []

    def flush_paragraph():
        if paragraph_buf:
            joined = " ".join(paragraph_buf).strip()
            if joined:
                blocks.append({"type": "paragraph", "text": joined})
            paragraph_buf.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == "[TABLE]":
            flush_paragraph()
            i += 1
            rows = []
            while i < n and lines[i].strip() != "[/TABLE]":
                row_line = lines[i].strip()
                if row_line:
                    rows.append([c.strip() for c in row_line.split("|")])
                i += 1
            i += 1  # skip [/TABLE]
            if rows:
                blocks.append({"type": "table", "rows": rows})
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            flush_paragraph()
            blocks.append({"type": "heading", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        # Bare markdown pipe-table fallback (no [TABLE] marker): 2+
        # consecutive lines containing '|' with roughly consistent column counts.
        if "|" in stripped:
            j = i
            pipe_lines = []
            while j < n and "|" in lines[j] and lines[j].strip():
                pipe_lines.append([c.strip() for c in lines[j].strip().split("|")])
                j += 1
            if len(pipe_lines) >= 2:
                flush_paragraph()
                blocks.append({"type": "table", "rows": pipe_lines})
                i = j
                continue

        paragraph_buf.append(stripped)
        i += 1

    flush_paragraph()
    return blocks


# =============================================================================
# NUMERIC FACT EXTRACTION
# =============================================================================
_NUMERIC_UNIT_WORDS = (
    "engines?|stages?|fields?|specs?|specifications?|levels?|categories?|"
    "layers?|tiers?|ms|milliseconds?|seconds?|minutes?|hours?|days?|"
    "gb|mb|kb|tb|%|percent"
)
_NUMERIC_FACT_RE = re.compile(
    rf"\b\d+\+?\s*(?:{_NUMERIC_UNIT_WORDS})\b", re.IGNORECASE
)


def extract_numeric_facts(text: str) -> list:
    """
    Extract quantitative/structural claims like "74 engines", "41 stages",
    "200+ fields", "L0-L9", "4-layer" -- the class of gold-standard content
    that a chat-tuned entity extractor (built for file paths and URLs) never
    looked for, but that a real architecture document leans on heavily.
    """
    facts = []
    seen = set()
    for m in _NUMERIC_FACT_RE.finditer(text):
        val = m.group().strip()
        key = val.lower()
        if key not in seen:
            seen.add(key)
            facts.append(val)
    # Range-style facts: "L0-L9", "0.85 and 0.95", "MAJOR.MINOR.PATCH" style not needed;
    # explicit level ranges (L0 through L9 / L0-L9) are common in this doc:
    for m in re.finditer(r"\bL\d+(?:\s*(?:-|through)\s*L\d+)\b", text, re.IGNORECASE):
        val = m.group().strip()
        key = val.lower()
        if key not in seen:
            seen.add(key)
            facts.append(val)
    return facts


# =============================================================================
# DIGEST ASSEMBLY (replaces single-template PromptConverter for doc-mode)
# =============================================================================
_NOISE_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]


def _denoise_paragraph(text: str) -> str:
    """Light filler-word strip on prose paragraphs only -- headings and table
    cells are structural/entity-bearing content and must survive verbatim."""
    cleaned = text
    for pattern in _NOISE_PATTERNS_COMPILED:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def build_document_digest(blocks: list) -> str:
    """
    Build the document-mode machine prompt: a structural digest that
    preserves every heading and every table row verbatim (this is where the
    document's real entities/requirements live) while lightly de-noising
    prose paragraphs. This deliberately does NOT collapse the document into
    a single CATEGORY:INTENT:terms line the way the short-message compiler
    does -- that compression strategy is correct for one sentence and
    destructive for a multi-section document.
    """
    lines = []
    for block in blocks:
        if block["type"] == "heading":
            lines.append(f"{'#' * block['level']} {block['text']}")
        elif block["type"] == "table":
            for row in block["rows"]:
                lines.append(" | ".join(row))
        else:
            denoised = _denoise_paragraph(block["text"])
            if denoised:
                lines.append(denoised)
    return "\n".join(lines)


def section_breakdown(blocks: list) -> list:
    """
    Group blocks under their nearest heading, returning a list of
    {"heading": str, "level": int, "text": str} used to classify per-section
    instead of forcing one category onto the whole document.
    """
    sections = []
    current = {"heading": "(preamble)", "level": 0, "text": ""}
    for block in blocks:
        if block["type"] == "heading":
            if current["text"].strip():
                sections.append(current)
            current = {"heading": block["text"], "level": block["level"], "text": ""}
        elif block["type"] == "paragraph":
            current["text"] += " " + block["text"]
        elif block["type"] == "table":
            current["text"] += " " + " ".join(" ".join(row) for row in block["rows"])
    if current["text"].strip():
        sections.append(current)
    return sections
