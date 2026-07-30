#!/usr/bin/env python3
"""
VERIDIAN Software Engine - ID Generator
=======================================
Generates unique chat IDs and manages ID-to-file tagging.
ID format: VD-YYYYMMDDHHMMSS-XXXX (timestamp + zero-padded counter)
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ID_PREFIX, ID_TIMESTAMP_FORMAT, ID_COUNTER_WIDTH,
    TAGS_DIR, TAG_FILE_PREFIX, TAG_FILE_EXT,
    DATA_DIR, CHATS_DIR, LOGS_DIR
)


class IDGenerator:
    """
    Generates unique chat IDs with the format: VD-YYYYMMDDHHMMSS-XXXX
    The counter resets each day. Thread-safe via file locking.
    """

    def __init__(self, base_dir=None):
        self.base_dir = Path(base_dir) if base_dir else DATA_DIR
        self.counter_file = self.base_dir / "_id_counter.json"
        self._load_counter()

    def _load_counter(self):
        """Load counter state from disk."""
        if self.counter_file.exists():
            try:
                with open(self.counter_file, "r") as f:
                    state = json.load(f)
                self._last_date = state.get("date", "")
                self._counter = state.get("counter", 0)
            except (json.JSONDecodeError, KeyError):
                self._last_date = ""
                self._counter = 0
        else:
            self._last_date = ""
            self._counter = 0

    def _save_counter(self):
        """Persist counter state to disk."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with open(self.counter_file, "w") as f:
            json.dump({"date": self._last_date, "counter": self._counter}, f)

    def generate_id(self) -> str:
        """
        Generate a new unique chat ID.
        Format: VD-YYYYMMDDHHMMSS-XXXX
        Counter resets to 0001 at the start of each new UTC day.
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime(ID_TIMESTAMP_FORMAT)

        if self._last_date != date_str:
            self._counter = 0
            self._last_date = date_str

        self._counter += 1
        counter_str = str(self._counter).zfill(ID_COUNTER_WIDTH)

        self._save_counter()

        return f"{ID_PREFIX}-{time_str}-{counter_str}"

    def parse_id(self, chat_id: str) -> dict:
        """
        Parse a chat ID back into its components.
        Returns: {"prefix": str, "timestamp": str, "counter": int, "datetime": datetime}
        """
        parts = chat_id.split("-")
        if len(parts) != 3:
            return {"error": f"Invalid ID format: {chat_id}"}
        
        prefix, timestamp, counter = parts
        try:
            dt = datetime.strptime(timestamp, ID_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
            return {
                "prefix": prefix,
                "timestamp": timestamp,
                "counter": int(counter),
                "datetime": dt,
            }
        except ValueError:
            return {"error": f"Invalid timestamp in ID: {chat_id}"}


class FileTagger:
    """
    Manages file tagging — associates files with chat IDs.
    Each tag records: chat_id, file_path, tag_time, and optional metadata.
    Tags are stored as JSON files in the tags directory.
    """

    def __init__(self, tags_dir=None):
        self.tags_dir = Path(tags_dir) if tags_dir else Path(TAGS_DIR)
        self.tags_dir.mkdir(parents=True, exist_ok=True)

    def tag_file(self, chat_id: str, file_path: str, metadata: dict = None) -> dict:
        """
        Tag a file with a chat ID.
        Returns the tag record.
        """
        tag_record = {
            "chat_id": chat_id,
            "file_path": str(file_path),
            "tag_time": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }

        tag_filename = f"{TAG_FILE_PREFIX}{chat_id}{TAG_FILE_EXT}"
        tag_file = self.tags_dir / tag_filename

        # Load existing tags for this chat_id or create new
        existing_tags = []
        if tag_file.exists():
            try:
                with open(tag_file, "r") as f:
                    existing_tags = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_tags = []

        existing_tags.append(tag_record)

        with open(tag_file, "w") as f:
            json.dump(existing_tags, f, indent=2, default=str)

        return tag_record

    def get_files_for_chat(self, chat_id: str) -> list:
        """Get all files tagged with a specific chat ID."""
        tag_file = self.tags_dir / f"{TAG_FILE_PREFIX}{chat_id}{TAG_FILE_EXT}"
        if not tag_file.exists():
            return []
        
        try:
            with open(tag_file, "r") as f:
                tags = json.load(f)
            return [t["file_path"] for t in tags]
        except (json.JSONDecodeError, IOError):
            return []

    def get_all_tags(self) -> dict:
        """Get all tag records grouped by chat_id."""
        all_tags = {}
        for tag_file in self.tags_dir.glob(f"{TAG_FILE_PREFIX}*{TAG_FILE_EXT}"):
            try:
                with open(tag_file, "r") as f:
                    tags = json.load(f)
                chat_id = tag_file.stem.replace(TAG_FILE_PREFIX, "")
                all_tags[chat_id] = tags
            except (json.JSONDecodeError, IOError):
                continue
        return all_tags

    def remove_tag(self, chat_id: str, file_path: str) -> bool:
        """Remove a specific file tag for a chat ID."""
        tag_file = self.tags_dir / f"{TAG_FILE_PREFIX}{chat_id}{TAG_FILE_EXT}"
        if not tag_file.exists():
            return False
        
        try:
            with open(tag_file, "r") as f:
                tags = json.load(f)
            
            original_len = len(tags)
            tags = [t for t in tags if t.get("file_path") != file_path]
            
            if len(tags) == 0:
                tag_file.unlink()
            elif len(tags) < original_len:
                with open(tag_file, "w") as f:
                    json.dump(tags, f, indent=2)
                return True
        except (json.JSONDecodeError, IOError):
            return False
        
        return False


def generate_chat_id() -> str:
    """Quick function to generate a new chat ID."""
    gen = IDGenerator()
    return gen.generate_id()


def tag_files_for_chat(chat_id: str, file_paths: list, metadata: dict = None) -> list:
    """Quick function to tag multiple files for a chat ID."""
    tagger = FileTagger()
    records = []
    for fp in file_paths:
        records.append(tagger.tag_file(chat_id, fp, metadata))
    return records


if __name__ == "__main__":
    # Self-test
    gen = IDGenerator(base_dir="/tmp/veridian_test")
    for i in range(3):
        cid = gen.generate_id()
        print(f"Generated ID: {cid}")
        parsed = gen.parse_id(cid)
        print(f"  Parsed: {parsed}")

    tagger = FileTagger(tags_dir="/tmp/veridian_test/tags")
    tagger.tag_file(cid, "/tmp/test_script.py", {"type": "output"})
    files = tagger.get_files_for_chat(cid)
    print(f"Files tagged for {cid}: {files}")
