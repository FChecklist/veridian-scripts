#!/usr/bin/env python3
"""
VERIDIAN Software Engine - Snip Engine
=======================================
Extends the existing Snip tool for snippet management.
Manages reusable code templates, command snippets, config patterns,
and response templates. Stores/Retrieves via JSON index.

100% free. Runs locally on the server. No API calls.
"""

import os
import re
import json
import hashlib
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SNIP_INDEX_FILE, SNIPPETS_DIR, SNIP_CATEGORIES


class SnipEngine:
    """
    Snippet management engine. Stores, retrieves, and indexes
    reusable code/text snippets by category, tags, and content hash.
    """

    def __init__(self, snippets_dir=None, index_file=None):
        self.snippets_dir = Path(snippets_dir) if snippets_dir else Path(SNIPPETS_DIR)
        self.snippets_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = Path(index_file) if index_file else Path(SNIP_INDEX_FILE)
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()

    def _load_index(self) -> dict:
        """Load snippet index from disk."""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"snippets": {}, "version": "1.0"}
        return {"snippets": {}, "version": "1.0"}

    def _save_index(self):
        """Persist snippet index to disk."""
        with open(self.index_file, "w") as f:
            json.dump(self._index, f, indent=2, default=str)

    def _content_hash(self, content: str) -> str:
        """Generate a short hash for content deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def add_snippet(self, name: str, content: str, category: str = "code_template",
                    tags: list = None, description: str = "", language: str = "") -> dict:
        """
        Add a new snippet to the store.
        
        Args:
            name: Unique snippet name (slug format: lowercase, hyphens)
            content: The actual snippet content (code, text, config)
            category: One of SNIP_CATEGORIES
            tags: List of tags for search
            description: Human-readable description
            language: Programming language (if code snippet)
            
        Returns:
            The stored snippet record.
        """
        if category not in SNIP_CATEGORIES:
            category = "code_template"

        content_hash = self._content_hash(content)

        snippet = {
            "name": name,
            "content": content,
            "category": category,
            "tags": tags or [],
            "description": description,
            "language": language,
            "content_hash": content_hash,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "usage_count": 0,
        }

        # Also save as individual file for easy browsing
        snippet_file = self.snippets_dir / f"{name}.snip"
        with open(snippet_file, "w") as f:
            json.dump(snippet, f, indent=2)

        self._index["snippets"][name] = {
            "file": str(snippet_file),
            "category": category,
            "tags": tags or [],
            "description": description,
            "language": language,
            "content_hash": content_hash,
        }

        self._save_index()
        return snippet

    def get_snippet(self, name: str) -> Optional[dict]:
        """Retrieve a snippet by name."""
        # Try individual file first
        snippet_file = self.snippets_dir / f"{name}.snip"
        if snippet_file.exists():
            try:
                with open(snippet_file, "r") as f:
                    snippet = json.load(f)
                snippet["usage_count"] = snippet.get("usage_count", 0) + 1
                # Update usage count on disk
                with open(snippet_file, "w") as f:
                    json.dump(snippet, f, indent=2, default=str)
                return snippet
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def search_snippets(self, query: str, category: str = None,
                        tags: list = None, language: str = None) -> list:
        """
        Search snippets by query string, category, tags, or language.
        Uses keyword matching (no AI, no embeddings).
        
        Returns: List of matching snippet records (without full content for performance).
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())
        results = []

        for name, meta in self._index["snippets"].items():
            score = 0.0
            
            # Category match
            if category and meta.get("category") == category:
                score += 2.0
            
            # Language match
            if language and meta.get("language") == language:
                score += 1.5
            
            # Tag match
            if tags:
                for t in tags:
                    if t.lower() in [tag.lower() for tag in meta.get("tags", [])]:
                        score += 1.5
            
            # Keyword search in name, description, tags
            search_text = f"{name} {' '.join(meta.get('tags', []))} {meta.get('description', '')}".lower()
            search_words = set(search_text.split())
            overlap = query_words & search_words
            score += len(overlap) * 1.0
            
            # Fuzzy: check if query is substring of name or description
            if query_lower in name.lower():
                score += 3.0
            if query_lower in meta.get("description", "").lower():
                score += 2.0

            if score > 0:
                # Load full snippet if good match
                snippet = self.get_snippet(name)
                if snippet:
                    snippet["_search_score"] = round(score, 2)
                    results.append(snippet)

        results.sort(key=lambda x: x.get("_search_score", 0), reverse=True)
        return results

    def list_categories(self) -> dict:
        """List all snippets grouped by category."""
        grouped = {cat: [] for cat in SNIP_CATEGORIES}
        for name, meta in self._index["snippets"].items():
            cat = meta.get("category", "code_template")
            if cat in grouped:
                grouped[cat].append({
                    "name": name,
                    "description": meta.get("description", ""),
                    "tags": meta.get("tags", []),
                    "language": meta.get("language", ""),
                })
        return grouped

    def delete_snippet(self, name: str) -> bool:
        """Delete a snippet by name."""
        if name in self._index["snippets"]:
            del self._index["snippets"][name]
            self._save_index()

        snippet_file = self.snippets_dir / f"{name}.snip"
        if snippet_file.exists():
            snippet_file.unlink()
            return True
        return False

    def get_prompt_assist_snippets(self, category: str = None,
                                    intent: str = None) -> list:
        """
        Get snippets that assist in prompt building for a given
        classification and intent. Used by the prompt engine.
        """
        results = []
        for name, meta in self._index["snippets"].items():
            # Match response_template category or workflow
            if meta.get("category") in ["response_template", "workflow", "pattern"]:
                if category and category.lower() in [t.lower() for t in meta.get("tags", [])]:
                    results.append(self.get_snippet(name))
                elif intent and intent.lower() in [t.lower() for t in meta.get("tags", [])]:
                    results.append(self.get_snippet(name))
        return [r for r in results if r]

    def seed_defaults(self):
        """
        Seed the snippet store with default useful snippets.
        Only adds if they don't already exist.
        """
        defaults = [
            {
                "name": "python-script-template",
                "content": '''#!/usr/bin/env python3
"""{description}"""
import os
import sys
import json
from pathlib import Path

def main():
    pass

if __name__ == "__main__":
    main()''',
                "category": "code_template",
                "tags": ["python", "script", "template", "boilerplate"],
                "description": "Standard Python script boilerplate",
                "language": "python",
            },
            {
                "name": "sql-connection-template",
                "content": '''import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()''',
                "category": "code_template",
                "tags": ["python", "sql", "database", "connection"],
                "description": "SQLite database connection context manager",
                "language": "python",
            },
            {
                "name": "error-handling-pattern",
                "content": '''try:
    result = operation()
except SpecificException as e:
    logger.error(f"Operation failed: {e}")
    raise
except Exception as e:
    logger.exception(f"Unexpected error: {e}")
    raise''',
                "category": "pattern",
                "tags": ["python", "error", "exception", "handling", "CODE"],
                "description": "Standard error handling pattern",
                "language": "python",
            },
            {
                "name": "systemd-service-template",
                "content": '''[Unit]
Description={service_name}
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script_path}
Restart=on-failure
RestartSec=5
User={user}
WorkingDirectory={work_dir}

[Install]
WantedBy=multi-user.target''',
                "category": "config",
                "tags": ["systemd", "service", "linux", "deploy", "OPS"],
                "description": "Systemd service unit file template",
                "language": "",
            },
            {
                "name": "prompt-machine-format",
                "content": '''# Machine prompt format reference:
# CODE_GEN:{type} - Generate code of specified type
# CODE_FIX:issue={issue}:target={target} - Fix issue in target
# CODE_ADD:feature={feature}:target={target} - Add feature to target
# ANALYZE:{subject} - Analyze a subject
# COMPARE:{a}:{b} - Compare two items
# REVIEW:{subject} - Review a subject
# QUERY:{question} - Answer a question
# LOOKUP:{subject} - Find information about subject
# OPS:{target} - Operations task
# TASK:{description} - Task management''',
                "category": "response_template",
                "tags": ["prompt", "machine", "format", "reference"],
                "description": "Machine prompt format reference for the prompt engine",
                "language": "",
            },
            {
                "name": "git-workflow",
                "content": '''# Standard git workflow:
git checkout -b feature/{name}
# ... make changes ...
git add -A
git commit -m "{message}"
git push origin feature/{name}
# Create PR via web interface or CLI''',
                "category": "workflow",
                "tags": ["git", "workflow", "version-control", "CODE"],
                "description": "Standard git feature branch workflow",
                "language": "",
            },
        ]

        for default in defaults:
            if default["name"] not in self._index["snippets"]:
                self.add_snippet(**default)

        return len(defaults)


if __name__ == "__main__":
    # Self-test
    engine = SnipEngine(snippets_dir="/tmp/veridian_test_snips",
                         index_file="/tmp/veridian_test_snips/snip_index.json")
    
    # Seed defaults
    count = engine.seed_defaults()
    print(f"Seeded {count} default snippets")

    # Search
    results = engine.search_snippets("python template")
    print(f"\nSearch 'python template': {len(results)} results")
    for r in results:
        print(f"  {r['name']} (score: {r.get('_search_score', 0)})")

    # Get specific
    snippet = engine.get_snippet("python-script-template")
    if snippet:
        print(f"\nGot snippet: {snippet['name']}")
        print(f"Usage count: {snippet.get('usage_count', 0)}")

    # Categories
    cats = engine.list_categories()
    print(f"\nCategories:")
    for cat, items in cats.items():
        if items:
            print(f"  {cat}: {[i['name'] for i in items]}")
