#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Chat Classifier
===========================================
First-level analysis of incoming chats using SOFTWARE (not AI).
Uses keyword matching, pattern recognition, and scoring rules.
Zero API calls. Zero token usage for classification.
"""

import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CHAT_CATEGORIES
from collections import Counter
import document_engine


class ChatClassifier:
    """
    Classifies incoming chat messages into categories using software-driven
    keyword matching and regex pattern analysis. No AI, no API calls.
    """

    def __init__(self):
        self.categories = CHAT_CATEGORIES
        # Compile regex patterns once for performance
        self._compiled_patterns = {}
        for cat, info in self.categories.items():
            compiled = []
            for p in info.get("patterns", []):
                try:
                    compiled.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    pass
            self._compiled_patterns[cat] = compiled

    def classify(self, text: str) -> dict:
        """
        Classify a text message into a category.
        
        Returns:
            {
                "category": str,        # Primary category
                "confidence": float,    # 0.0-1.0 confidence score
                "scores": dict,         # All category scores
                "keywords_found": list, # Keywords that matched
                "patterns_matched": list # Regex patterns that matched
            }
        """
        text_lower = text.lower().strip()
        words = re.findall(r'\b\w+\b', text_lower)
        scores = {}
        all_keywords = []
        all_patterns = []

        for cat, info in self.categories.items():
            keywords = info.get("keywords", [])
            patterns = self._compiled_patterns.get(cat, [])
            
            # Keyword score: ratio of matched keywords to total keywords (weighted)
            keyword_matches = []
            for kw in keywords:
                if kw in text_lower:
                    keyword_matches.append(kw)
                    if kw not in all_keywords:
                        all_keywords.append(kw)
            
            keyword_score = len(keyword_matches) / max(len(keywords), 1)
            
            # Pattern score: any pattern match gives a significant boost
            pattern_matches = []
            pattern_score = 0.0
            for p in patterns:
                if p.search(text):
                    pattern_matches.append(p.pattern)
                    if p.pattern not in all_patterns:
                        all_patterns.append(p.pattern)
                    pattern_score = 0.5  # one match = significant
            
            # Combined score: keywords weighted 60%, patterns weighted 40%
            combined = (keyword_score * 0.6) + (pattern_score * 0.4)
            scores[cat] = round(combined, 4)

        # Determine primary category
        primary = max(scores, key=scores.get)
        confidence = scores[primary]
        
        # If top score is very low (< 0.05), default to GENERAL
        if confidence < 0.05:
            primary = "GENERAL"
            confidence = 0.1
            scores["GENERAL"] = 0.1

        return {
            "category": primary,
            "confidence": round(confidence, 4),
            "scores": scores,
            "keywords_found": all_keywords,
            "patterns_matched": all_patterns,
        }

    def extract_intent(self, text: str) -> str:
        """
        Extract the primary intent/action verb from the text.
        Returns a normalized intent string.
        """
        intent_patterns = {
            "WRITE": [r"\bwrite\b", r"\bcreate\b", r"\bgenerate\b", r"\bcompose\b", r"\bbuild\b", r"\bmake\b"],
            "FIX": [r"\bfix\b", r"\bdebug\b", r"\bresolve\b", r"\brepair\b", r"\bcorrect\b", r"\bsolve\b"],
            "ANALYZE": [r"\banalyze\b", r"\breview\b", r"\bassess\b", r"\bevaluate\b", r"\bexamine\b"],
            "DEPLOY": [r"\bdeploy\b", r"\binstall\b", r"\bsetup\b", r"\bconfigure\b", r"\bset\s+up\b"],
            "QUERY": [r"\bwhat\b", r"\bhow\b", r"\bwhy\b", r"\bfind\b", r"\bsearch\b", r"\bget\b", r"\btell\b"],
            "UPDATE": [r"\bupdate\b", r"\bmodify\b", r"\bchange\b", r"\bedit\b", r"\balter\b", r"\badjust\b"],
            "DELETE": [r"\bdelete\b", r"\bremove\b", r"\berase\b", r"\bclean\b", r"\bpurge\b"],
            "TEST": [r"\btest\b", r"\brun\b", r"\bexecute\b", r"\bverify\b", r"\bvalidate\b"],
            "TASK": [r"\btask\b", r"\btodo\b", r"\bassign\b", r"\bschedule\b", r"\btrack\b"],
        }

        text_lower = text.lower()
        for intent, patterns in intent_patterns.items():
            for p in patterns:
                if re.search(p, text_lower):
                    return intent
        return "UNKNOWN"

    def extract_entities(self, text: str) -> list:
        """
        Extract named entities from text using pattern matching.
        Returns list of {"type": str, "value": str} dicts.
        """
        entities = []
        
        # File paths
        for match in re.finditer(r'[\w./\-]+\.(?:py|js|ts|json|yaml|yml|toml|cfg|ini|sh|bash|sql|md|txt|html|css)', text):
            entities.append({"type": "FILE_PATH", "value": match.group()})

        # URLs
        for match in re.finditer(r'https?://[^\s<>"{}|\\^`]+', text):
            entities.append({"type": "URL", "value": match.group()})

        # Code references (ClassName, function_name, etc.)
        for match in re.finditer(r'\b[A-Z][a-zA-Z_]+(?:\.[A-Z][a-zA-Z_]*)*\b', text):
            if match.group() not in ["The", "This", "That", "Then", "There", "These"]:
                entities.append({"type": "CODE_REF", "value": match.group()})

        # Numbers with units
        for match in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(?:MB|GB|KB|TB|ms|s|sec|min|hr|hours?|days?|%)\b', text):
            entities.append({"type": "MEASUREMENT", "value": match.group()})

        # Version numbers
        for match in re.finditer(r'\bv?\d+\.\d+(?:\.\d+)*(?:-\w+)?\b', text):
            entities.append({"type": "VERSION", "value": match.group()})

        # Task lifecycle IDs (task-YYYYMMDD-HHMMSS-slug, the id format
        # ai-os/tasks/<id>/ and every worker/<id> branch use -- distinct
        # from this module's own VD-/KE-/INS- id namespaces). Added for
        # task-20260726-092433 so a raw Owner status/completion query
        # ("what's the status of task-20260726-092433-...") can be routed
        # to task-gateway.py status without a second, separate regex living
        # in gateway.py's dispatch logic.
        for match in re.finditer(r'\btask-\d{8}-\d{6}(?:-+[a-z0-9]+)*\b', text):
            entities.append({"type": "TASK_ID", "value": match.group()})

        return entities

    def classify_document(self, sections: list) -> dict:
        """
        Classify a document section-by-section instead of forcing one
        category onto the whole input. A 50-section architecture document
        spans CODE, ANALYSIS, OPS and QUERY content simultaneously; picking
        the single highest-scoring category for the entire text (the
        short-message behavior in classify()) silently discards that.

        Returns: {"primary_category": str, "category_histogram": dict,
                   "section_classifications": [{"heading","category","confidence"}]}
        """
        per_section = []
        cat_counter = Counter()
        for sec in sections:
            text = sec.get("text", "").strip()
            if not text:
                continue
            result = self.classify(text)
            per_section.append({
                "heading": sec.get("heading", ""),
                "category": result["category"],
                "confidence": result["confidence"],
            })
            cat_counter[result["category"]] += 1

        primary = cat_counter.most_common(1)[0][0] if cat_counter else "GENERAL"
        return {
            "primary_category": primary,
            "category_histogram": dict(cat_counter),
            "section_classifications": per_section,
        }

    def extract_document_entities(self, text: str) -> list:
        """
        Entity extraction for document-scale input: reuses the existing
        pattern-based extract_entities() (file paths, URLs, code refs,
        measurements, versions) across the FULL text -- not just a 5-entity
        slice of it -- and adds numeric/structural claims ("74 engines",
        "L0-L9") that the chat-tuned patterns never looked for.
        """
        entities = self.extract_entities(text)
        for fact in document_engine.extract_numeric_facts(text):
            entities.append({"type": "NUMERIC_FACT", "value": fact})

        # De-duplicate by (type, value) while preserving first-seen order.
        seen = set()
        unique = []
        for e in entities:
            key = (e["type"], e["value"])
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique

    def full_analysis(self, text: str) -> dict:
        """
        Perform complete first-level analysis on a chat message.
        Combines classification, intent extraction, and entity extraction.
        
        Returns:
            {
                "classification": {...},
                "intent": str,
                "entities": [...],
                "word_count": int,
                "char_count": int,
                "estimated_tokens": int,
                "analysis_time_utc": str
            }
        """
        classification = self.classify(text)
        intent = self.extract_intent(text)
        entities = self.extract_entities(text)
        words = re.findall(r'\b\w+\b', text)
        
        return {
            "classification": classification,
            "intent": intent,
            "entities": entities,
            "word_count": len(words),
            "char_count": len(text),
            "estimated_tokens": len(text.split()) + len(re.findall(r'[^\w\s]', text)),  # rough estimate
            "analysis_time_utc": datetime.now(timezone.utc).isoformat(),
        }


if __name__ == "__main__":
    # Self-test
    classifier = ChatClassifier()
    
    test_messages = [
        "Write a Python script that connects to the database and extracts user data",
        "How do I deploy the Next.js application to production?",
        "Fix the authentication error in the login module",
        "Hello, thanks for your help!",
        "Analyze the server performance logs from yesterday",
        "Create a task to review the PR before Friday",
    ]
    
    for msg in test_messages:
        result = classifier.full_analysis(msg)
        print(f"\nMessage: {msg}")
        print(f"  Category: {result['classification']['category']} "
              f"(confidence: {result['classification']['confidence']})")
        print(f"  Intent: {result['intent']}")
        print(f"  Entities: {[e['value'] for e in result['entities']]}")
        print(f"  Est. tokens: {result['estimated_tokens']}")
