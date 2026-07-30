#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Prompt Engine
=========================================
Removes noise from raw chat text and converts natural English
into concise machine-language prompts.

By software, not by AI. Uses regex patterns, template matching,
and deterministic rules to achieve 50%+ token reduction.

Pipeline: Raw Chat → Noise Removal → Entity Extraction → 
          Template Matching → Machine Prompt Output
"""

import os
import re
import json
from typing import Optional, Tuple
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NOISE_PATTERNS, PROMPT_TEMPLATES


class NoiseRemover:
    """
    Deterministic noise removal from chat text.
    Removes filler words, redundant phrases, and meta-commentary
    using regex patterns. No AI, no API calls.
    """

    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]
        # Additional cleanup patterns
        self.cleanup_patterns = [
            # Multiple whitespace → single space
            (re.compile(r'\s{2,}'), ' '),
            # Spaces before punctuation
            (re.compile(r'\s+([,.!?;:])'), r'\1'),
            # Remove trailing punctuation repetition
            (re.compile(r'([.!?])\1{2,}'), r'\1'),
            # Remove empty parentheses
            (re.compile(r'\(\s*\)'), ''),
            # Remove leading/trailing whitespace from lines
            (re.compile(r'^\s+|\s+$', re.MULTILINE), ''),
        ]

    def remove_noise(self, text: str) -> Tuple[str, dict]:
        """
        Remove noise from text.
        Returns: (cleaned_text, stats_dict)
        """
        original_length = len(text)
        original_word_count = len(re.findall(r'\b\w+\b', text))

        cleaned = text
        removals = []

        for pattern in self.patterns:
            matches = pattern.findall(cleaned)
            if matches:
                for m in matches:
                    removals.append(m)
                cleaned = pattern.sub('', cleaned)

        # Apply cleanup patterns
        for pattern, replacement in self.cleanup_patterns:
            cleaned = pattern.sub(replacement, cleaned)

        cleaned = cleaned.strip()

        final_length = len(cleaned)
        final_word_count = len(re.findall(r'\b\w+\b', cleaned))

        stats = {
            "chars_removed": original_length - final_length,
            "words_removed": original_word_count - final_word_count,
            "original_chars": original_length,
            "final_chars": final_length,
            "original_words": original_word_count,
            "final_words": final_word_count,
            "reduction_pct": round((1 - final_length / max(original_length, 1)) * 100, 1),
            "noise_phrases_found": removals,
        }

        return cleaned, stats


class PromptConverter:
    """
    Converts cleaned natural English into concise machine-language prompts.
    Uses template matching and rule-based transformation.
    """

    def __init__(self):
        self.templates = {}
        for pattern_str, template in PROMPT_TEMPLATES.items():
            try:
                self.templates[re.compile(pattern_str, re.IGNORECASE)] = template
            except re.error:
                pass

    def convert(self, text: str, classification: dict = None) -> Tuple[str, dict]:
        """
        Convert text to machine prompt.
        
        Returns: (machine_prompt, conversion_info)
        """
        matched = False
        machine_prompt = ""
        matched_template = None

        # Try template matching first
        for pattern, template in self.templates.items():
            match = pattern.search(text)
            if match:
                try:
                    machine_prompt = template.format(**match.groupdict())
                    matched = True
                    matched_template = pattern.pattern
                    break
                except (KeyError, IndexError):
                    continue

        if not matched:
            # Fallback: compress text to machine prompt format
            machine_prompt = self._compress_to_prompt(text, classification)

        conversion_info = {
            "matched_template": matched_template,
            "was_template_match": matched,
            "machine_prompt": machine_prompt,
        }

        return machine_prompt, conversion_info

    def _compress_to_prompt(self, text: str, classification: dict = None) -> str:
        """
        Compress text into a concise prompt when no template matches.
        Strategy: Keep action verbs + key nouns, remove everything else.
        """
        if classification:
            category = classification.get("category", "GENERAL")
            intent = classification.get("intent", "UNKNOWN")
        else:
            category = "GENERAL"
            intent = "PROCESS"

        # Extract key terms: keep first/last words of each sentence, action verbs
        sentences = re.split(r'[.!?]+', text)
        key_terms = []

        # Action verbs to prioritize
        action_verbs = [
            "write", "create", "build", "fix", "debug", "analyze", "deploy",
            "update", "delete", "test", "configure", "install", "run", "execute",
            "review", "search", "find", "get", "set", "add", "remove", "check",
            "validate", "optimize", "refactor", "migrate", "convert", "parse",
            "extract", "transform", "load", "import", "export", "generate",
        ]

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            words = sentence.split()
            if not words:
                continue

            # Keep action verbs and their immediate object
            i = 0
            while i < len(words):
                w = words[i].lower()
                if w in action_verbs and i + 1 < len(words):
                    key_terms.append(words[i])
                    key_terms.append(words[i + 1])
                    i += 2
                i += 1

            # If no action verbs found, keep first and last word
            if not any(w.lower() in action_verbs for w in words):
                if len(words) >= 2:
                    key_terms.append(words[0])
                    key_terms.append(words[-1])
                elif words:
                    key_terms.append(words[0])

        # Deduplicate while preserving order
        seen = set()
        unique_terms = []
        for t in key_terms:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                unique_terms.append(t)

        prompt = f"{category}:{intent}:{'_'.join(unique_terms)}"
        return prompt

    def estimate_token_reduction(self, original: str, machine_prompt: str) -> dict:
        """
        Estimate token reduction between original text and machine prompt.
        """
        # Rough token estimation: ~1.3 tokens per word for English
        orig_tokens = int(len(original.split()) * 1.3)
        machine_tokens = int(len(machine_prompt.split()) * 1.1)

        return {
            "estimated_original_tokens": orig_tokens,
            "estimated_machine_tokens": machine_tokens,
            "estimated_savings_tokens": orig_tokens - machine_tokens,
            "reduction_pct": round(
                (1 - machine_tokens / max(orig_tokens, 1)) * 100, 1
            ),
        }


class PromptEngine:
    """
    Full prompt processing pipeline.
    Combines noise removal and prompt conversion.
    """

    def __init__(self):
        self.noise_remover = NoiseRemover()
        self.prompt_converter = PromptConverter()

    def process(self, text: str, classification: dict = None) -> dict:
        """
        Full pipeline: raw text → noise removal → machine prompt.
        
        Returns:
            {
                "original_text": str,
                "cleaned_text": str,
                "machine_prompt": str,
                "noise_stats": dict,
                "conversion_info": dict,
                "token_reduction": dict
            }
        """
        # Step 1: Remove noise
        cleaned, noise_stats = self.noise_remover.remove_noise(text)

        # Step 2: Convert to machine prompt
        machine_prompt, conversion_info = self.prompt_converter.convert(
            cleaned, classification
        )

        # Step 3: Estimate token reduction
        token_reduction = self.prompt_converter.estimate_token_reduction(
            text, machine_prompt
        )

        return {
            "original_text": text,
            "cleaned_text": cleaned,
            "machine_prompt": machine_prompt,
            "noise_stats": noise_stats,
            "conversion_info": conversion_info,
            "token_reduction": token_reduction,
        }


if __name__ == "__main__":
    # Self-test
    engine = PromptEngine()

    test_chats = [
        "I was wondering if you could basically write a Python script for me that connects to the database and extracts user data, you know what I mean?",
        "How do I deploy the Next.js application to production? I just wanted to know, as a matter of fact.",
        "Please fix the authentication error in the login module, it's literally completely broken",
        "Analyze the server performance logs from yesterday and tell me what you think, if you will",
        "Create a task to review the PR before Friday, very important",
    ]

    for chat in test_chats:
        result = engine.process(chat)
        print(f"\n{'='*60}")
        print(f"ORIGINAL: {chat}")
        print(f"CLEANED: {result['cleaned_text']}")
        print(f"MACHINE: {result['machine_prompt']}")
        print(f"NOISE reduction: {result['noise_stats']['reduction_pct']}% chars")
        print(f"TOKEN reduction: {result['token_reduction']['reduction_pct']}%")
