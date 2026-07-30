#!/usr/bin/env python3
"""External AI work state machine -- auto-save + resume for chunked work
delegated to external AI models (z.ai/others) that never touch this server.

Architecture: the external AI produces text in its own session. A human (or
Claude Code on the human's behalf) pastes that complete text back here via
`save-chunk`. This script owns all disk/DB writes -- the external AI is
never trusted with file paths, chunk sequencing, or write authority; it is
only trusted to produce content, and even that is validated before saving.

Idiot-proofing principles applied throughout (see VERIDIAN task
task-20260728-065751-ext-state-machine, Owner directive: "idiotproof,
assuming ai makes mistakes"):
  1. The CALLER (not the AI's self-reported status line) is the source of
     truth for session_id/chunk_number. The AI's echoed values are used
     only for cross-check warnings, never to redirect a write.
  2. Every session_id/task_id/filename that reaches the filesystem is
     validated against a strict safe-character allowlist first -- these
     values ultimately originate from pasted AI/user text, so path
     traversal and injection are treated as expected attack surface, not
     edge cases.
  3. Writes are atomic (tmp file + os.replace) -- a crash mid-write can
     never leave a corrupted chunk file.
  4. Re-saving identical content is a safe no-op (hash-compared). Re-saving
     DIFFERENT content to an already-SAVED chunk is never silently
     overwritten -- it is versioned aside and flagged NEEDS_HUMAN_REVIEW.
     (This mirrors a real incident this same session where a different
     script silently dropped historical content on conflict.)
  5. `resume` never trusts the DB's SAVED status at face value -- it
     re-verifies the file exists on disk AND its hash still matches before
     reporting a chunk as genuinely done. Disk truth wins over DB claims.
  6. If the AI doesn't follow the expected status-line format at all, we
     still save whatever content we can extract (never lose real work) but
     mark it NEEDS_HUMAN_REVIEW instead of pretending it succeeded cleanly.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

BASE_DIR = Path("/opt/veridian/external_users")
DB_PATH = "/opt/veridian/ai-os/memory/external_ai_state.sqlite"
KEY_PATH = BASE_DIR / ".encryption_key"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
VALID_CHUNK_STATUSES = {"COMPLETE", "INCOMPLETE"}


class StateMachineError(Exception):
    pass


def _require_safe_id(value, field_name):
    if not value or not SAFE_ID_RE.match(value):
        raise StateMachineError(
            f"{field_name} failed safety validation: {value!r} "
            f"(must match ^[A-Za-z0-9_-]{{1,128}}$ -- no slashes, dots, or spaces)"
        )
    return value


def _require_safe_filename(value):
    if (
        not value
        or not SAFE_FILENAME_RE.match(value)
        or ".." in value
        or value.startswith("/")
        or value.startswith(".")
    ):
        raise StateMachineError(
            f"filename failed safety validation: {value!r} "
            f"(no path traversal, no leading dot/slash, safe charset only)"
        )
    return value


def hash_email(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_or_create_key() -> bytes:
    BASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    os.chmod(KEY_PATH, 0o600)
    return key


def encrypt_email(email: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(_get_or_create_key()).encrypt(email.strip().encode()).decode()


def decrypt_email(token: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(_get_or_create_key()).decrypt(token.encode()).decode()


def get_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_ai_sessions (
            id TEXT PRIMARY KEY,
            user_email_hash TEXT NOT NULL,
            user_email_encrypted TEXT NOT NULL,
            task_id TEXT NOT NULL,
            original_prompt TEXT,
            base_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK(status IN ('ACTIVE','COMPLETE','ABANDONED')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_email_hash
            ON external_ai_sessions(user_email_hash);

        CREATE TABLE IF NOT EXISTS external_ai_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES external_ai_sessions(id) ON DELETE CASCADE,
            chunk_number INTEGER NOT NULL,
            total_chunks INTEGER,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','AWAITING_HANDOVER','SAVED','FAILED','NEEDS_HUMAN_REVIEW')),
            saved_file_path TEXT,
            content_sha256 TEXT,
            ai_reported_status_line TEXT,
            error_log TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id, chunk_number)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_session ON external_ai_chunks(session_id);
        """
    )
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "db_path": DB_PATH}))


def create_session(email: str, task_id: str, original_prompt: str = "") -> dict:
    _require_safe_id(task_id, "task_id")
    email_hash = hash_email(email)
    session_id = f"EXT-{__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}"
    base_path = BASE_DIR / email_hash / "tasks" / task_id
    base_path.mkdir(parents=True, exist_ok=True, mode=0o700)

    conn = get_db()
    conn.execute(
        """INSERT INTO external_ai_sessions
           (id, user_email_hash, user_email_encrypted, task_id, original_prompt, base_path)
           VALUES (?,?,?,?,?,?)""",
        (session_id, email_hash, encrypt_email(email), task_id, original_prompt, str(base_path)),
    )
    conn.commit()
    conn.close()
    result = {"session_id": session_id, "base_path": str(base_path), "email_hash": email_hash}
    print(json.dumps(result))
    return result


_STATUS_FIELD_RE = re.compile(
    r"^\s*([A-Z_]+)\s*:\s*(.*?)\s*$", re.MULTILINE
)


def parse_status_line(raw_text: str) -> dict:
    """Lenient extraction of the mandated status block. Never raises -- a
    field that can't be found is simply absent from the returned dict, and
    the caller (save_chunk) decides what's mandatory. AI models WILL get
    the format slightly wrong (extra spaces, missing fields, wrong case on
    the COMPLETE/INCOMPLETE value) -- this must survive all of that."""
    fields = {}
    for m in _STATUS_FIELD_RE.finditer(raw_text):
        key, val = m.group(1).strip().upper(), m.group(2).strip()
        if key and val and key not in fields:
            fields[key] = val
    return fields


_CODE_BLOCK_RE = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.DOTALL)


def extract_code_block(raw_text: str) -> str | None:
    """Extract the LAST fenced code block (the deliverable is expected to
    be the final block; a status-line explanation may itself accidentally
    contain earlier example fences). Returns None if no fence found at
    all -- caller must handle that as a format violation, not a crash."""
    blocks = _CODE_BLOCK_RE.findall(raw_text)
    return blocks[-1] if blocks else None


def save_chunk(session_id: str, chunk_number: int, raw_ai_response: str, filename_override: str = None) -> dict:
    """The one function that actually writes to disk. session_id and
    chunk_number are CALLER-PROVIDED (the human/Claude-Code operator knows
    which chunk this is) -- the AI's self-reported SESSION_ID/CHUNK_NUMBER
    in its status line are cross-checked for a warning only, never trusted
    to redirect where data lands. This is the core idiot-proofing move:
    the AI cannot mis-file its own output even if it hallucinates."""
    _require_safe_id(session_id, "session_id")
    if not isinstance(chunk_number, int) or chunk_number < 1:
        raise StateMachineError(f"chunk_number must be a positive int, got {chunk_number!r}")

    conn = get_db()
    session = conn.execute(
        "SELECT * FROM external_ai_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        conn.close()
        raise StateMachineError(f"No session found for session_id={session_id!r}. Create it first with create-session.")

    warnings = []
    fields = parse_status_line(raw_ai_response)

    ai_session_id = fields.get("SESSION_ID")
    if ai_session_id and ai_session_id != session_id:
        warnings.append(
            f"AI echoed SESSION_ID={ai_session_id!r} but caller specified {session_id!r} -- "
            f"trusting the CALLER value, saving under {session_id!r} regardless."
        )

    ai_chunk_number = fields.get("CHUNK_NUMBER")
    if ai_chunk_number and ai_chunk_number.strip() != str(chunk_number):
        warnings.append(
            f"AI echoed CHUNK_NUMBER={ai_chunk_number!r} but caller specified {chunk_number!r} -- "
            f"trusting the CALLER value."
        )

    raw_status = (fields.get("CHUNK_STATUS") or "").upper()
    if raw_status not in VALID_CHUNK_STATUSES:
        warnings.append(
            f"CHUNK_STATUS missing or unrecognized ({fields.get('CHUNK_STATUS')!r}) -- "
            f"defaulting to INCOMPLETE (safer than assuming success)."
        )
        ai_declared_complete = False
    else:
        ai_declared_complete = raw_status == "COMPLETE"

    filename = filename_override or fields.get("FILE_SAVED_AS")
    format_violation = False
    if not filename:
        filename = f"chunk_{chunk_number:02d}.txt"
        warnings.append("No FILE_SAVED_AS found in AI output -- falling back to a generic name; review recommended.")
        format_violation = True
    else:
        try:
            filename = _require_safe_filename(filename)
        except StateMachineError as e:
            warnings.append(f"FILE_SAVED_AS rejected by safety check ({e}) -- falling back to a generic name.")
            filename = f"chunk_{chunk_number:02d}.txt"
            format_violation = True

    content = extract_code_block(raw_ai_response)
    if content is None:
        warnings.append(
            "No fenced code block found in AI output -- saving the ENTIRE raw response "
            "as-is so nothing is lost, but this needs human review; the AI did not follow "
            "the required output format."
        )
        content = raw_ai_response
        format_violation = True

    content_bytes = content.encode("utf-8")
    new_hash = sha256_of(content_bytes)

    base_path = Path(session["base_path"])
    target_path = base_path / filename

    existing = conn.execute(
        "SELECT * FROM external_ai_chunks WHERE session_id = ? AND chunk_number = ?",
        (session_id, chunk_number),
    ).fetchone()

    final_status = "NEEDS_HUMAN_REVIEW"
    saved_path_str = None

    if target_path.exists():
        existing_hash = sha256_of(target_path.read_bytes())
        if existing_hash == new_hash:
            warnings.append(f"Identical content already saved at {target_path} -- treating as a safe no-op re-paste.")
            final_status = "SAVED" if not format_violation else "NEEDS_HUMAN_REVIEW"
            saved_path_str = str(target_path)
        else:
            versioned = base_path / f"{Path(filename).stem}.CONFLICT-{uuid.uuid4().hex[:8]}{Path(filename).suffix}"
            _atomic_write(versioned, content_bytes)
            warnings.append(
                f"CONFLICT: {target_path} already exists with DIFFERENT content. "
                f"Refused to overwrite. New content saved separately at {versioned} for human comparison."
            )
            final_status = "NEEDS_HUMAN_REVIEW"
            saved_path_str = str(versioned)
    else:
        _atomic_write(target_path, content_bytes)
        saved_path_str = str(target_path)
        final_status = "SAVED" if (ai_declared_complete and not format_violation) else (
            "NEEDS_HUMAN_REVIEW" if format_violation else "SAVED"
        )
        if not ai_declared_complete and not format_violation:
            warnings.append("AI marked this chunk INCOMPLETE -- saved as-is but flagged; you likely need to re-request this chunk.")
            final_status = "NEEDS_HUMAN_REVIEW"

    total_chunks_val = None
    tc_raw = fields.get("TOTAL_CHUNKS")
    if tc_raw and tc_raw.strip().lower() not in ("final", "unknown", ""):
        try:
            total_chunks_val = int(re.sub(r"[^0-9]", "", tc_raw) or 0) or None
        except ValueError:
            total_chunks_val = None

    conn.execute(
        """INSERT INTO external_ai_chunks
           (session_id, chunk_number, total_chunks, status, saved_file_path,
            content_sha256, ai_reported_status_line, error_log, updated_at)
           VALUES (?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(session_id, chunk_number) DO UPDATE SET
             total_chunks=excluded.total_chunks,
             status=excluded.status,
             saved_file_path=excluded.saved_file_path,
             content_sha256=excluded.content_sha256,
             ai_reported_status_line=excluded.ai_reported_status_line,
             error_log=excluded.error_log,
             updated_at=datetime('now')""",
        (
            session_id, chunk_number, total_chunks_val, final_status, saved_path_str,
            new_hash, json.dumps(fields), " | ".join(warnings) if warnings else None,
        ),
    )
    conn.execute(
        "UPDATE external_ai_sessions SET updated_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()

    result = {
        "session_id": session_id,
        "chunk_number": chunk_number,
        "status": final_status,
        "saved_file_path": saved_path_str,
        "content_sha256": new_hash,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))
    return result


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.parent / f".{path.name}.tmp.{uuid.uuid4().hex[:8]}"
    tmp.write_bytes(data)
    os.replace(tmp, path)


def resume(email: str) -> dict:
    """Never trusts the DB alone. Re-verifies every SAVED chunk's file
    still exists on disk with a matching hash before reporting it as
    genuinely done -- disk is ground truth, DB is a cache of claims."""
    email_hash = hash_email(email)
    conn = get_db()
    session = conn.execute(
        """SELECT * FROM external_ai_sessions
           WHERE user_email_hash = ? AND status = 'ACTIVE'
           ORDER BY updated_at DESC LIMIT 1""",
        (email_hash,),
    ).fetchone()

    if session is None:
        conn.close()
        result = {"found": False, "message": "No active session found for this email."}
        print(json.dumps(result))
        return result

    chunks = conn.execute(
        """SELECT * FROM external_ai_chunks WHERE session_id = ?
           ORDER BY chunk_number ASC""",
        (session["id"],),
    ).fetchall()

    verified_saved = []
    needs_attention = []
    reconciliation_notes = []

    for ch in chunks:
        row = dict(ch)
        if row["status"] == "SAVED" and row["saved_file_path"]:
            p = Path(row["saved_file_path"])
            if not p.exists():
                reconciliation_notes.append(
                    f"Chunk {row['chunk_number']}: DB says SAVED but file is MISSING on disk "
                    f"({row['saved_file_path']}) -- downgrading to FAILED, must be regenerated."
                )
                conn.execute(
                    "UPDATE external_ai_chunks SET status='FAILED', error_log=? WHERE id=?",
                    (f"resume() found file missing at {row['saved_file_path']}", row["id"]),
                )
                row["status"] = "FAILED"
                needs_attention.append(row)
                continue
            actual_hash = sha256_of(p.read_bytes())
            if actual_hash != row["content_sha256"]:
                reconciliation_notes.append(
                    f"Chunk {row['chunk_number']}: file on disk has changed since it was saved "
                    f"(hash mismatch) -- downgrading to NEEDS_HUMAN_REVIEW."
                )
                conn.execute(
                    "UPDATE external_ai_chunks SET status='NEEDS_HUMAN_REVIEW', error_log=? WHERE id=?",
                    (f"resume() found hash mismatch: db={row['content_sha256']} disk={actual_hash}", row["id"]),
                )
                row["status"] = "NEEDS_HUMAN_REVIEW"
                needs_attention.append(row)
                continue
            verified_saved.append(row)
        elif row["status"] != "SAVED":
            needs_attention.append(row)

    conn.commit()
    conn.close()

    highest_saved = max((c["chunk_number"] for c in verified_saved), default=0)
    next_chunk_number = highest_saved + 1
    known_total = next(
        (c["total_chunks"] for c in reversed(chunks) if c["total_chunks"]), None
    )

    result = {
        "found": True,
        "session_id": session["id"],
        "task_id": session["task_id"],
        "base_path": session["base_path"],
        "verified_saved_chunks": [c["chunk_number"] for c in verified_saved],
        "chunks_needing_attention": [
            {"chunk_number": c["chunk_number"], "status": c["status"], "error_log": c.get("error_log")}
            for c in needs_attention
        ],
        "reconciliation_notes": reconciliation_notes,
        "next_chunk_number_to_work_on": next_chunk_number,
        "known_total_chunks": known_total,
    }
    print(json.dumps(result, indent=2))
    return result


def list_sessions(email: str) -> dict:
    email_hash = hash_email(email)
    conn = get_db()
    rows = conn.execute(
        """SELECT id, task_id, status, base_path, created_at, updated_at
           FROM external_ai_sessions WHERE user_email_hash = ?
           ORDER BY updated_at DESC""",
        (email_hash,),
    ).fetchall()
    conn.close()
    result = {"sessions": [dict(r) for r in rows]}
    print(json.dumps(result, indent=2))
    return result


def mark_complete(session_id: str) -> dict:
    _require_safe_id(session_id, "session_id")
    conn = get_db()
    conn.execute(
        "UPDATE external_ai_sessions SET status='COMPLETE', updated_at=datetime('now') WHERE id=?",
        (session_id,),
    )
    conn.commit()
    changed = conn.total_changes
    conn.close()
    result = {"session_id": session_id, "marked_complete": changed > 0}
    print(json.dumps(result))
    return result


def verify_integrity() -> dict:
    """Health-check across ALL sessions -- reconciles every SAVED chunk
    against disk. Intended for periodic manual runs, not per-chunk use."""
    conn = get_db()
    chunks = conn.execute(
        "SELECT * FROM external_ai_chunks WHERE status = 'SAVED'"
    ).fetchall()
    broken = []
    for row in chunks:
        p = Path(row["saved_file_path"]) if row["saved_file_path"] else None
        if not p or not p.exists():
            broken.append({"session_id": row["session_id"], "chunk_number": row["chunk_number"], "issue": "file missing"})
        elif sha256_of(p.read_bytes()) != row["content_sha256"]:
            broken.append({"session_id": row["session_id"], "chunk_number": row["chunk_number"], "issue": "hash mismatch"})
    conn.close()
    result = {"checked": len(chunks), "broken": broken}
    print(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Phase 2: delegation-loop prompt generation
# ---------------------------------------------------------------------------

CHUNK_PROMPT_TEMPLATE = """\
═══════════════════════════════════════════════════════════════
EXTERNAL AI WORK PACKET — READ FULLY BEFORE STARTING
═══════════════════════════════════════════════════════════════

SESSION_ID: {session_id}
OWNER_EMAIL: {owner_email}
TASK_ID: {task_id}
CHUNK: {chunk_number} of {total_chunks}

── ROLE ──
You are producing ONE self-contained deliverable for a human who will copy
your ENTIRE response, verbatim, into a separate system that saves it to
disk. You have no access to that system. You cannot verify anything landed
correctly. Your only job is to produce complete, correct, directly-usable
output in THIS response — never "I'll continue in the next message."

── OBJECTIVE FOR THIS CHUNK ──
{objective}

── CONTEXT FROM PRIOR CHUNKS ──
{prior_context}

── HARD OUTPUT RULES ──
1. Output EXACTLY ONE file's complete content per response, wrapped in a
   single fenced code block. If the task genuinely needs multiple files,
   say so in your status line and STOP — do not produce file 2 until asked.
2. Never truncate, abbreviate, or write "... rest omitted" / "// continued"
   anywhere in the code block. If you are running low on room, finish the
   current logical unit (function/class) cleanly and report it as an
   INCOMPLETE chunk in your status line rather than cutting off mid-token.
3. No prose commentary inside the code block — comments only where a real
   reader would need them (non-obvious logic), same as normal code style.
4. Assume nothing you output will be re-read by you. Any assumption you
   made (a library version, a function signature you invented, a file path)
   must be listed explicitly in the status line below, not left implicit.

── MANDATORY STATUS LINE (end every response with exactly this block) ──
---
CHUNK_STATUS: COMPLETE | INCOMPLETE
SESSION_ID: {session_id}
OWNER_EMAIL: {owner_email}
CHUNK_NUMBER: {chunk_number}
TOTAL_CHUNKS: {total_chunks}
FILE_SAVED_AS: {{the exact filename this chunk should be saved as}}
ASSUMPTIONS_MADE: {{list, or "none"}}
NEXT_CHUNK_NEEDED: {{one-sentence description of what chunk N+1 must do,
                    or "none — task complete"}}
---
═══════════════════════════════════════════════════════════════
"""


def generate_chunk_prompt(session_id: str, chunk_number: int, objective: str,
                           total_chunks="unknown", prior_context: str = None) -> dict:
    """Fills the standard chunk-prompt template with real, looked-up
    session data so the Owner never has to hand-fill placeholders (a hand-
    edited placeholder is exactly the kind of human error this whole
    system exists to route around). prior_context defaults to a summary
    auto-built from already-SAVED chunks in this session if not given
    explicitly, so continuity survives even if the Owner forgets to paste
    it in themselves."""
    _require_safe_id(session_id, "session_id")
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM external_ai_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        conn.close()
        raise StateMachineError(f"No session found for session_id={session_id!r}.")

    if prior_context is None:
        saved = conn.execute(
            """SELECT chunk_number, saved_file_path FROM external_ai_chunks
               WHERE session_id = ? AND status = 'SAVED' ORDER BY chunk_number""",
            (session_id,),
        ).fetchall()
        if saved:
            prior_context = "Already saved in this session:\n" + "\n".join(
                f"  - chunk {r['chunk_number']}: {r['saved_file_path']}" for r in saved
            )
        else:
            prior_context = "N/A — fresh start, no prior chunks saved yet."

    owner_email = decrypt_email(session["user_email_encrypted"])
    conn.close()

    prompt = CHUNK_PROMPT_TEMPLATE.format(
        session_id=session_id,
        owner_email=owner_email,
        task_id=session["task_id"],
        chunk_number=chunk_number,
        total_chunks=total_chunks,
        objective=objective,
        prior_context=prior_context,
    )
    result = {"session_id": session_id, "chunk_number": chunk_number, "prompt": prompt}
    print(prompt)
    return result


# ---------------------------------------------------------------------------
# Phase 4: final cross-user audit
# ---------------------------------------------------------------------------

def audit_all() -> dict:
    """Owner-wide integrity + isolation + duplication audit across every
    session/user. Three independent checks, each answering a different
    real question:
      - integrity: does every SAVED chunk's file still exist with a
        matching hash? (same check as verify_integrity, included here too
        so a single command gives the full picture)
      - isolation: does every chunk's saved path actually live inside its
        OWN session's base_path? This should be structurally impossible
        given how save_chunk builds paths, but the audit re-verifies it
        independently rather than assuming the write path was never
        bypassed by a future code change.
      - cross_user_duplicates: any identical content (by hash) saved under
        more than one session_id. Not auto-resolved -- flagged for a human
        to judge whether it's legitimate shared boilerplate or a real
        mis-filing bug."""
    conn = get_db()
    sessions = {s["id"]: dict(s) for s in conn.execute("SELECT * FROM external_ai_sessions")}
    all_chunks = conn.execute("SELECT * FROM external_ai_chunks").fetchall()
    conn.close()

    integrity_broken = []
    isolation_violations = []
    hash_to_sessions = {}

    for row in all_chunks:
        if row["status"] != "SAVED" or not row["saved_file_path"]:
            continue
        p = Path(row["saved_file_path"])
        session = sessions.get(row["session_id"])

        if not p.exists():
            integrity_broken.append({"session_id": row["session_id"], "chunk_number": row["chunk_number"], "issue": "file missing"})
            continue
        actual_hash = sha256_of(p.read_bytes())
        if actual_hash != row["content_sha256"]:
            integrity_broken.append({"session_id": row["session_id"], "chunk_number": row["chunk_number"], "issue": "hash mismatch"})
            continue

        if session:
            try:
                p.resolve().relative_to(Path(session["base_path"]).resolve())
            except ValueError:
                isolation_violations.append({
                    "session_id": row["session_id"], "chunk_number": row["chunk_number"],
                    "saved_path": str(p), "expected_base": session["base_path"],
                })

        hash_to_sessions.setdefault(row["content_sha256"], set()).add(row["session_id"])

    cross_user_duplicates = [
        {"content_sha256": h, "session_ids": sorted(sids)}
        for h, sids in hash_to_sessions.items()
        if len(sids) > 1
    ]

    result = {
        "sessions_checked": len(sessions),
        "chunks_checked": sum(1 for r in all_chunks if r["status"] == "SAVED"),
        "integrity_broken": integrity_broken,
        "isolation_violations": isolation_violations,
        "cross_user_duplicates": cross_user_duplicates,
        "clean": not integrity_broken and not isolation_violations and not cross_user_duplicates,
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    cs = sub.add_parser("create-session")
    cs.add_argument("--email", required=True)
    cs.add_argument("--task-id", required=True)
    cs.add_argument("--prompt", default="")

    sc = sub.add_parser("save-chunk")
    sc.add_argument("--session-id", required=True)
    sc.add_argument("--chunk-number", required=True, type=int)
    sc.add_argument("--input-file", required=True, help="Path to a file containing the FULL raw AI response text")
    sc.add_argument("--filename", default=None, help="Override the target filename instead of trusting FILE_SAVED_AS")

    rs = sub.add_parser("resume")
    rs.add_argument("--email", required=True)

    ls = sub.add_parser("list-sessions")
    ls.add_argument("--email", required=True)

    mc = sub.add_parser("mark-complete")
    mc.add_argument("--session-id", required=True)

    sub.add_parser("verify-integrity")

    gp = sub.add_parser("generate-chunk-prompt")
    gp.add_argument("--session-id", required=True)
    gp.add_argument("--chunk-number", required=True, type=int)
    gp.add_argument("--objective", required=True, help="What this specific chunk must produce")
    gp.add_argument("--total-chunks", default="unknown")
    gp.add_argument("--prior-context", default=None, help="Override the auto-built prior-chunks summary")

    sub.add_parser("audit-all")

    args = p.parse_args()
    try:
        if args.command == "init-db":
            init_db()
        elif args.command == "create-session":
            create_session(args.email, args.task_id, args.prompt)
        elif args.command == "save-chunk":
            raw = Path(args.input_file).read_text(encoding="utf-8")
            save_chunk(args.session_id, args.chunk_number, raw, args.filename)
        elif args.command == "resume":
            resume(args.email)
        elif args.command == "list-sessions":
            list_sessions(args.email)
        elif args.command == "mark-complete":
            mark_complete(args.session_id)
        elif args.command == "verify-integrity":
            verify_integrity()
        elif args.command == "generate-chunk-prompt":
            generate_chunk_prompt(args.session_id, args.chunk_number, args.objective, args.total_chunks, args.prior_context)
        elif args.command == "audit-all":
            audit_all()
    except StateMachineError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
