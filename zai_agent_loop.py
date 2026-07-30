#!/usr/bin/env python3
"""z.ai autonomous agent loop -- real tool-calling integration, NOT browser
automation. Owner provides ONE task description; z.ai's model (via its real
Anthropic-compatible API, https://api.z.ai/api/anthropic) autonomously
decides when to call the save_chunk/mark_task_complete tools; THIS script
is the only thing that ever touches disk -- it executes the tool calls the
model requests and reports results back into the conversation. No browser,
no DOM scraping, no login, no courier step of any kind after launch.

Architecture (see VERIDIAN task task-20260728-065751-ext-state-machine,
Owner directive 2026-07-28: "Owner only pastes one prompt... everything is
between that ai model, SERVER and Claude Code session... non negotiable"):

  Owner/Claude Code launches this script once with --objective "<task>"
    -> script creates/resumes a session via external_ai_state_machine.py
    -> script calls z.ai with the objective + current resume state + tools
    -> z.ai's model decides, on its own, to call save_chunk(...) as it
       produces each piece of work
    -> THIS script executes that call for real (through the same
       idiot-proofed save_chunk() already built and tested), appends a
       tool_result, and calls z.ai again
    -> loop continues until the model calls mark_task_complete or a safety
       cap is hit
    -> if the process/laptop/session dies mid-task, re-running this script
       with the SAME --task-id resumes: it re-derives what's already SAVED
       (via the state machine's own disk-truth reconciliation) and tells
       the model exactly that before asking it to continue, so completed
       work is never regenerated.

Every tool call from the model is executed via the EXISTING, already
idiot-proofed external_ai_state_machine.py functions (path safety, atomic
writes, conflict versioning, hash verification) -- this script adds the
agentic loop on top, it does not re-implement or bypass any of those
protections.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, "/opt/veridian/scripts")
import external_ai_state_machine as sm  # noqa: E402

ENV_PATH = "/opt/veridian/shared/.env"
ZAI_API_URL = "https://api.z.ai/api/anthropic/v1/messages"
ZAI_MODEL = "glm-4.7"
MAX_TOOL_ROUNDS = 60  # hard safety cap -- never loop forever regardless of what the model does
MAX_TOKENS_PER_CALL = 8000

TOOLS = [
    {
        "name": "save_chunk",
        "description": (
            "Save ONE complete file/deliverable to the server. Call this every time "
            "you finish a discrete, complete piece of work -- do not wait until the "
            "entire task is done. The server independently verifies your content "
            "(conflict detection, path safety) before accepting it, so call this as "
            "often as needed; duplicate/identical re-saves are safely ignored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target filename, e.g. 'service.py'. No paths, no '..'."},
                "content": {"type": "string", "description": "The COMPLETE file content. Never truncate or abbreviate."},
                "chunk_status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
                "notes": {"type": "string", "description": "Assumptions made, or what remains to be done next."},
            },
            "required": ["filename", "content", "chunk_status"],
        },
    },
    {
        "name": "mark_task_complete",
        "description": "Call this ONCE, only when the ENTIRE objective has been fully delivered via save_chunk calls. This ends the session.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "Brief summary of everything delivered."}},
            "required": ["summary"],
        },
    },
]


def load_env():
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def zai_call(api_key: str, system: str, messages: list) -> dict:
    body = json.dumps({
        "model": ZAI_MODEL,
        "max_tokens": MAX_TOKENS_PER_CALL,
        "system": system,
        "tools": TOOLS,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(
        ZAI_API_URL,
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise sm.StateMachineError(f"z.ai API error {e.code}: {error_body}")


def build_resume_context(session_id: str) -> str:
    """Reuses the state machine's own disk-truth resume() logic so the
    model is told EXACTLY what's really saved, never what the DB merely
    claims. This is the mechanism that makes 'paste the same task again
    and it continues where it left off' actually true rather than
    aspirational."""
    conn = sm.get_db()
    session = conn.execute("SELECT * FROM external_ai_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    if session is None:
        return "This is a fresh session -- nothing has been saved yet."

    email = sm.decrypt_email(session["user_email_encrypted"])
    state = sm.resume(email)
    if not state.get("found"):
        return "This is a fresh session -- nothing has been saved yet."

    lines = [f"Resuming session {session_id}. Already genuinely saved on disk (verified by hash, not just DB claim):"]
    if state["verified_saved_chunks"]:
        for n in state["verified_saved_chunks"]:
            lines.append(f"  - chunk {n}: done, do not regenerate.")
    else:
        lines.append("  - nothing verified-saved yet.")
    if state["chunks_needing_attention"]:
        lines.append("Chunks that need re-doing (failed, missing, or flagged for review):")
        for c in state["chunks_needing_attention"]:
            lines.append(f"  - chunk {c['chunk_number']}: {c['status']} -- {c.get('error_log') or ''}")
    lines.append(f"Continue from here. Next chunk number to use: {state['next_chunk_number_to_work_on']}.")
    return "\n".join(lines)


def run(email: str, task_id: str, objective: str):
    conn = sm.get_db()
    existing = conn.execute(
        "SELECT id FROM external_ai_sessions WHERE user_email_hash = ? AND task_id = ?",
        (sm.hash_email(email), task_id),
    ).fetchone()
    conn.close()

    if existing:
        session_id = existing["id"] if isinstance(existing, dict) else existing[0]
        print(json.dumps({"event": "resuming_existing_session", "session_id": session_id}))
    else:
        created = sm.create_session(email, task_id, objective)
        session_id = created["session_id"]
        print(json.dumps({"event": "created_new_session", "session_id": session_id}))

    env = load_env()
    api_key = env.get("ZAI_API_KEY")
    if not api_key:
        raise sm.StateMachineError("ZAI_API_KEY not found in /opt/veridian/shared/.env")

    resume_context = build_resume_context(session_id)

    system_prompt = (
        "You are completing a real, bounded software/content task autonomously. "
        "You have two tools: save_chunk (call it every time you finish a complete, "
        "self-contained deliverable) and mark_task_complete (call it once, when "
        "everything is genuinely done). Never describe work in prose instead of "
        "calling save_chunk -- prose is not saved anywhere; only tool calls persist. "
        "Never claim a chunk is COMPLETE if you truncated or abbreviated it. "
        f"\n\nOBJECTIVE:\n{objective}\n\nRESUME STATE:\n{resume_context}"
    )

    messages = [{"role": "user", "content": "Begin. Call save_chunk for each deliverable as you complete it."}]
    conn = sm.get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(chunk_number), 0) as m FROM external_ai_chunks WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    chunk_counter = (row["m"] if isinstance(row, dict) else row[0]) + 1

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        print(json.dumps({"event": "api_call", "round": round_num, "session_id": session_id}))
        response = zai_call(api_key, system_prompt, messages)

        if "error" in response:
            print(json.dumps({"event": "api_error", "detail": response["error"]}))
            return {"status": "ERROR", "session_id": session_id, "detail": response["error"]}

        content_blocks = response.get("content", [])
        messages.append({"role": "assistant", "content": content_blocks})

        tool_results = []
        task_done = False
        completion_summary = None

        for block in content_blocks:
            if block.get("type") != "tool_use":
                continue
            name = block["name"]
            tool_input = block.get("input", {})
            tool_use_id = block["id"]

            if name == "save_chunk":
                try:
                    raw_response_text = (
                        f"```\n{tool_input.get('content', '')}\n```\n\n"
                        f"---\nCHUNK_STATUS: {tool_input.get('chunk_status', 'INCOMPLETE')}\n"
                        f"FILE_SAVED_AS: {tool_input.get('filename', '')}\n"
                        f"ASSUMPTIONS_MADE: {tool_input.get('notes', 'none')}\n---\n"
                    )
                    result = sm.save_chunk(session_id, chunk_counter, raw_response_text, tool_input.get("filename"))
                    chunk_counter += 1
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use_id,
                        "content": json.dumps({"saved": True, "status": result["status"], "warnings": result["warnings"]}),
                    })
                    print(json.dumps({"event": "chunk_saved", "session_id": session_id, "result": result}))
                except sm.StateMachineError as e:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use_id,
                        "content": json.dumps({"saved": False, "error": str(e)}), "is_error": True,
                    })

            elif name == "mark_task_complete":
                task_done = True
                completion_summary = tool_input.get("summary", "")
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tool_use_id,
                    "content": json.dumps({"acknowledged": True}),
                })

        if task_done:
            sm.mark_complete(session_id)
            print(json.dumps({"event": "task_complete", "session_id": session_id, "summary": completion_summary}))
            return {"status": "COMPLETE", "session_id": session_id, "summary": completion_summary}

        if not tool_results:
            print(json.dumps({"event": "model_stopped_without_tool_call", "session_id": session_id}))
            return {"status": "STOPPED_NO_TOOL_CALL", "session_id": session_id}

        messages.append({"role": "user", "content": tool_results})
        time.sleep(1)

    print(json.dumps({"event": "max_rounds_reached", "session_id": session_id}))
    return {"status": "MAX_ROUNDS_REACHED", "session_id": session_id}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--objective", required=True)
    args = p.parse_args()

    sm._require_safe_id(args.task_id, "task_id")
    try:
        result = run(args.email, args.task_id, args.objective)
        print(json.dumps({"final_result": result}))
    except sm.StateMachineError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
