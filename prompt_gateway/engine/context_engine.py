#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Context Engine
==========================================
Manages conversation context: pruning, relevance scoring,
and maintaining a sliding window of high-relevance messages.

By software, not by AI. Uses TF-IDF-like scoring, recency decay,
and deterministic rules to prune 50%+ of context tokens.
"""

import os
import re
import json
import math
from typing import Optional
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MAX_CONTEXT_TOKENS_ESTIMATE,
    CONTEXT_DECAY_FACTOR,
    CONTEXT_PRUNE_THRESHOLD,
    CONTEXT_MAX_MESSAGES,
    CONTEXT_MIN_MESSAGES,
    CONTEXT_DIR,
)


class RelevanceScorer:
    """
    Scores messages by relevance to a current query.
    Uses TF-IDF-like keyword matching and recency decay.
    All computation is deterministic, no AI calls.
    """

    def __init__(self, decay_factor=CONTEXT_DECAY_FACTOR):
        self.decay_factor = decay_factor
        # Stop words to ignore in scoring
        self.stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further",
            "then", "once", "here", "there", "when", "where", "why", "how",
            "all", "each", "every", "both", "few", "more", "most", "other",
            "some", "such", "no", "nor", "not", "only", "own", "same", "so",
            "than", "too", "very", "just", "because", "but", "and", "or",
            "if", "while", "about", "up", "down", "it", "its", "this", "that",
            "these", "those", "i", "me", "my", "myself", "we", "you", "your",
            "he", "she", "him", "her", "they", "them", "what", "which", "who",
            "am", "please", "thanks", "thank", "ok", "okay",
        }

    def tokenize(self, text: str) -> list:
        """Tokenize text into meaningful words (lowercased, no stop words)."""
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text.lower())
        return [w for w in words if w not in self.stop_words and len(w) > 1]

    def compute_tf(self, tokens: list) -> dict:
        """Compute term frequency."""
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counts.items()}

    def compute_relevance(self, message: str, current_query: str,
                          message_index: int, total_messages: int) -> float:
        """
        Compute relevance score for a message relative to the current query.
        Combines keyword overlap with recency decay.
        """
        msg_tokens = self.tokenize(message)
        query_tokens = self.tokenize(current_query)

        if not query_tokens or not msg_tokens:
            # If no overlap possible, score based on recency alone
            return self._recency_score(message_index, total_messages)

        # TF-IDF-like scoring: term frequency overlap
        msg_tf = self.compute_tf(msg_tokens)
        query_tf = self.compute_tf(query_tokens)

        overlap_score = 0.0
        for token in set(msg_tokens) & set(query_tokens):
            overlap_score += msg_tf.get(token, 0) * query_tf.get(token, 0)

        # Normalize overlap score
        overlap_score = min(overlap_score, 1.0)

        # Recency score: more recent messages get higher scores
        recency = self._recency_score(message_index, total_messages)

        # Combined: 70% keyword relevance + 30% recency
        combined = (overlap_score * 0.7) + (recency * 0.3)

        return round(combined, 4)

    def _recency_score(self, index: int, total: int) -> float:
        """
        Compute recency score. Index 0 = oldest, total-1 = newest.
        Uses exponential decay from newest to oldest.
        """
        if total <= 1:
            return 1.0
        position_ratio = index / (total - 1)  # 0 = oldest, 1 = newest
        return self.decay_factor ** (1 - position_ratio)


class ContextWindow:
    """
    Manages a sliding context window with pruning.
    Maintains conversation history within token budget.
    """

    def __init__(self,
                 max_tokens=MAX_CONTEXT_TOKENS_ESTIMATE,
                 max_messages=CONTEXT_MAX_MESSAGES,
                 min_messages=CONTEXT_MIN_MESSAGES,
                 prune_threshold=CONTEXT_PRUNE_THRESHOLD):
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self.min_messages = min_messages
        self.prune_threshold = prune_threshold
        self.scorer = RelevanceScorer()
        self.messages = []  # List of {"role": str, "content": str, "id": str}

    def add_message(self, role: str, content: str, msg_id: str = None) -> dict:
        """
        Add a message to the context window.
        Returns the message record.
        """
        msg = {
            "role": role,
            "content": content,
            "id": msg_id or f"msg_{len(self.messages)}",
            "added_utc": datetime.now(timezone.utc).isoformat(),
            "est_tokens": self._estimate_tokens(content),
        }
        self.messages.append(msg)
        return msg

    def prune(self, current_query: str = "") -> dict:
        """
        Prune the context window to fit within budget.
        Strategy:
        1. Score each message by relevance to current_query
        2. Remove messages below threshold (but keep min_messages)
        3. If still over budget, remove oldest messages
        
        Returns pruning statistics.
        """
        stats = {
            "messages_before": len(self.messages),
            "tokens_before": sum(m["est_tokens"] for m in self.messages),
            "removed_by_relevance": 0,
            "removed_by_budget": 0,
            "messages_after": 0,
            "tokens_after": 0,
            "reduction_pct": 0.0,
        }

        if not self.messages:
            return stats

        total = len(self.messages)

        # Step 1: Score all messages
        scored = []
        for i, msg in enumerate(self.messages):
            score = self.scorer.compute_relevance(
                msg["content"], current_query or self.messages[-1]["content"],
                i, total
            )
            scored.append((i, msg, score))

        # Sort by score ascending (lowest first = most pruneable)
        scored.sort(key=lambda x: x[2])

        # Step 2: Remove low-relevance messages (below threshold)
        to_remove = set()
        keep_count = total - len(to_remove)
        
        for idx, msg, score in scored:
            if keep_count <= self.min_messages:
                break
            if score < self.prune_threshold:
                to_remove.add(idx)
                keep_count -= 1

        self.messages = [m for i, m in enumerate(self.messages) if i not in to_remove]
        stats["removed_by_relevance"] = len(to_remove)

        # Step 3: If still over budget, remove oldest
        current_tokens = sum(m["est_tokens"] for m in self.messages)
        
        while (len(self.messages) > self.min_messages and
               current_tokens > self.max_tokens):
            removed = self.messages.pop(0)
            current_tokens -= removed["est_tokens"]
            stats["removed_by_budget"] += 1

        # Step 4: If still over max_messages, trim
        while len(self.messages) > self.max_messages:
            self.messages.pop(0)
            stats["removed_by_budget"] += 1

        stats["messages_after"] = len(self.messages)
        stats["tokens_after"] = sum(m["est_tokens"] for m in self.messages)
        stats["reduction_pct"] = round(
            (1 - stats["tokens_after"] / max(stats["tokens_before"], 1)) * 100, 1
        )

        return stats

    def get_context(self) -> list:
        """Return the current context messages."""
        return self.messages.copy()

    def get_context_text(self) -> str:
        """Return all context as a single text block."""
        parts = []
        for msg in self.messages:
            parts.append(f"[{msg['role']}] {msg['content']}")
        return "\n".join(parts)

    def estimate_tokens(self) -> int:
        """Estimate total tokens in current context."""
        return sum(m["est_tokens"] for m in self.messages)

    def clear(self):
        """Clear the entire context window."""
        self.messages = []

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation for a single text."""
        words = len(re.findall(r'\b\w+\b', text))
        special = len(re.findall(r'[^\w\s]', text))
        return int((words * 1.3) + (special * 0.5))


class ContextManager:
    """
    High-level context manager that persists context per chat ID.
    Saves and loads context from disk.
    """

    def __init__(self, context_dir=None):
        self.context_dir = Path(context_dir) if context_dir else Path(CONTEXT_DIR)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self._active_windows = {}

    def get_window(self, chat_id: str) -> ContextWindow:
        """Get or create a context window for a chat ID."""
        if chat_id not in self._active_windows:
            self._load_window(chat_id)
        return self._active_windows[chat_id]

    def _load_window(self, chat_id: str):
        """Load a context window from disk."""
        ctx_file = self.context_dir / f"{chat_id}.json"
        window = ContextWindow()
        if ctx_file.exists():
            try:
                with open(ctx_file, "r") as f:
                    data = json.load(f)
                window.messages = data.get("messages", [])
            except (json.JSONDecodeError, IOError):
                pass
        self._active_windows[chat_id] = window

    def save_window(self, chat_id: str):
        """Persist a context window to disk."""
        window = self._active_windows.get(chat_id)
        if not window:
            return
        ctx_file = self.context_dir / f"{chat_id}.json"
        with open(ctx_file, "w") as f:
            json.dump({"messages": window.messages, "chat_id": chat_id}, f, indent=2)

    def prune_and_save(self, chat_id: str, current_query: str = "") -> dict:
        """Prune a window and save to disk. Returns pruning stats."""
        window = self.get_window(chat_id)
        stats = window.prune(current_query)
        self.save_window(chat_id)
        return stats

    def list_contexts(self) -> list:
        """List all stored context files."""
        return [f.stem for f in self.context_dir.glob("*.json")]

    def delete_context(self, chat_id: str):
        """Delete a stored context."""
        ctx_file = self.context_dir / f"{chat_id}.json"
        if ctx_file.exists():
            ctx_file.unlink()
        self._active_windows.pop(chat_id, None)


if __name__ == "__main__":
    # Self-test
    window = ContextWindow(max_messages=10, max_tokens=200)

    # Simulate a conversation
    msgs = [
        ("user", "I was wondering if you could basically write a Python script for me"),
        ("assistant", "Sure, I can help with that. What kind of script do you need?"),
        ("user", "Something that connects to the database and extracts user data"),
        ("assistant", "I'll create a script with proper error handling and logging"),
        ("user", "Make sure it uses parameterized queries to prevent SQL injection"),
        ("assistant", "Good point. I'll use prepared statements throughout the script."),
        ("user", "Also add retry logic for transient database connection failures"),
        ("assistant", "I'll implement exponential backoff for the retry mechanism."),
        ("user", "Now fix the authentication error in the login module please"),
        ("assistant", "Can you share the error message and stack trace?"),
        ("user", "Getting a 401 Unauthorized when calling the /api/auth endpoint"),
        ("assistant", "That usually means the JWT token is expired or invalid."),
        ("user", "How do I refresh the token before it expires?"),
        ("assistant", "Implement a token refresh interceptor in your HTTP client."),
    ]

    for role, content in msgs:
        window.add_message(role, content)

    print(f"Messages before pruning: {len(window.messages)}")
    print(f"Estimated tokens before: {window.estimate_tokens()}")

    stats = window.prune(current_query="How do I refresh the token before it expires?")
    print(f"\nMessages after pruning: {stats['messages_after']}")
    print(f"Estimated tokens after: {stats['tokens_after']}")
    print(f"Token reduction: {stats['reduction_pct']}%")
    print(f"Removed by relevance: {stats['removed_by_relevance']}")
    print(f"Removed by budget: {stats['removed_by_budget']}")
    print(f"\nRemaining messages:")
    for msg in window.get_context():
        print(f"  [{msg['role']}] {msg['content'][:80]}...")
