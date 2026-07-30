#!/usr/bin/env python3
"""
v2 (2026-07-20, Owner directive "zero credit wastage"): adds two hard,
code-enforced controls on top of the v1 translation proxy. Both are chokepoint
controls -- every GLM-5.2 call from every worker on this box passes through
this single process, so this is the one place a guarantee is actually
possible, not just hoped for via per-task discipline.

1. RESPONSE CACHE (Q8): exact-match cache keyed by a hash of the full
   outgoing request (model + messages + tools + tool_choice). A cache hit
   returns instantly, costs $0, and is logged as such. This directly targets
   the pathological-retry case found in the RCA (BYOB-AI-model: 12 restarts,
   most of them re-sending an identical resume-context because nothing had
   actually changed since the last failed attempt) -- when a retry's first
   call is byte-identical to a prior call, it now costs nothing instead of
   the same real 4-figure token bill.
   Honest scope: this is exact-match, not semantic. It does NOT help two
   genuinely different/progressing calls -- only literal repeats. That is
   the correct, safe tradeoff (a semantic/fuzzy cache risks serving a stale
   or wrong response to a real tool-use loop, which is worse than no cache).

2. HARD BUDGET CEILING (Q7): once real cumulative spend (from this proxy's
   own real-cost log, counted from BUDGET_WINDOW_START) reaches
   BUDGET_CAP_USD, every subsequent request is rejected immediately with a
   clear 402 before any OpenRouter call is made -- i.e. it costs nothing to
   enforce. This makes "finish inside $10" a mechanical constraint the code
   guarantees, not a target workers are asked to remember.

Standard library only (unchanged from v1) -- sqlite3 is stdlib.
"""
import hashlib
import http.server
import json
import os
import socketserver
import sqlite3
import sys
import threading
import urllib.request
import urllib.error
import uuid
import datetime

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "8787"))
REAL_COST_LOG = "/opt/veridian/ai-os/logs/glm-proxy-calls.jsonl"
CACHE_DB = os.environ.get("PROXY_CACHE_DB", "/opt/veridian/ai-os/logs/glm-response-cache.sqlite")

# Budget ceiling config. BUDGET_WINDOW_START marks "now" for the purposes of
# the $10 target -- it deliberately does NOT count the $19.76 already spent
# before this control existed (that money is already spent, a retroactive
# cap on it is meaningless). Set via env; defaults keep the gate OFF
# (cap=None) so this file is inert until deliberately turned on.
BUDGET_CAP_USD = os.environ.get("PROXY_BUDGET_CAP_USD")
BUDGET_CAP_USD = float(BUDGET_CAP_USD) if BUDGET_CAP_USD else None
BUDGET_WINDOW_START = os.environ.get("PROXY_BUDGET_WINDOW_START")  # ISO8601

_log_lock = threading.Lock()
_cache_lock = threading.Lock()
_budget_lock = threading.Lock()
_budget_spent_usd = 0.0  # running total, initialized at startup from the real log


def get_openrouter_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    env_path = "/opt/veridian/shared/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.strip().split("=", 1)[1]
    raise RuntimeError("OPENROUTER_API_KEY not found in env or /opt/veridian/shared/.env")


def anthropic_content_to_openai_messages(system, messages):
    out = []
    if system:
        sys_text = system if isinstance(system, str) else "\n".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"
        )
        if sys_text:
            out.append({"role": "system", "content": sys_text})

    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        text_parts = []
        tool_calls = []
        tool_results = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = "\n".join(
                        b.get("text", "") for b in result_content if isinstance(b, dict)
                    )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": result_content if isinstance(result_content, str) else json.dumps(result_content),
                })

        if role == "assistant":
            am = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
            if tool_calls:
                am["tool_calls"] = tool_calls
            out.append(am)
        else:
            if text_parts:
                out.append({"role": role, "content": "\n".join(text_parts)})
            out.extend(tool_results)

    return out


def anthropic_tools_to_openai(tools):
    if not tools:
        return None
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


STOP_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


def openai_response_to_anthropic(oa_resp, model):
    choice = oa_resp["choices"][0]
    message = choice["message"]
    finish_reason = choice.get("finish_reason", "stop")

    content_blocks = []
    if message.get("content"):
        content_blocks.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
            "name": tc["function"]["name"],
            "input": args,
        })

    usage = oa_resp.get("usage", {})

    return {
        "id": oa_resp.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": STOP_REASON_MAP.get(finish_reason, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def call_openrouter(payload):
    key = get_openrouter_key()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://veridian-dev.internal",
            "X-Title": "veridian-claude-code-proxy",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def sse_frame_full_response(anth_resp):
    lines = []

    def emit(event, data):
        lines.append(f"event: {event}\ndata: {json.dumps(data)}\n\n")

    msg_id = anth_resp["id"]
    emit("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "content": [], "model": anth_resp["model"],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": anth_resp["usage"]["input_tokens"], "output_tokens": 0},
        },
    })

    for idx, block in enumerate(anth_resp["content"]):
        if block["type"] == "text":
            emit("content_block_start", {"type": "content_block_start", "index": idx,
                                          "content_block": {"type": "text", "text": ""}})
            emit("content_block_delta", {"type": "content_block_delta", "index": idx,
                                          "delta": {"type": "text_delta", "text": block["text"]}})
            emit("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "tool_use":
            emit("content_block_start", {"type": "content_block_start", "index": idx,
                                          "content_block": {"type": "tool_use", "id": block["id"],
                                                             "name": block["name"], "input": {}}})
            emit("content_block_delta", {"type": "content_block_delta", "index": idx,
                                          "delta": {"type": "input_json_delta",
                                                     "partial_json": json.dumps(block["input"])}})
            emit("content_block_stop", {"type": "content_block_stop", "index": idx})

    emit("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": anth_resp["stop_reason"], "stop_sequence": None},
        "usage": {"output_tokens": anth_resp["usage"]["output_tokens"]},
    })
    emit("message_stop", {"type": "message_stop"})
    return "".join(lines)


def log_real_cost(oa_resp, model, cache_hit=False, cache_key=None):
    try:
        usage = oa_resp.get("usage", {})
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model": model,
            "provider": oa_resp.get("provider"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "real_cost_usd": 0.0 if cache_hit else usage.get("cost"),
            "cache_hit": cache_hit,
        }
        if cache_key:
            record["cache_key"] = cache_key[:16]
        os.makedirs(os.path.dirname(REAL_COST_LOG), exist_ok=True)
        with _log_lock:
            with open(REAL_COST_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")
        return record["real_cost_usd"] or 0.0
    except Exception as e:
        sys.stderr.write(f"[proxy] real-cost logging failed (non-fatal): {e}\n")
        return 0.0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_conn():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            saved_usd_estimate REAL NOT NULL DEFAULT 0
        )
    """)
    return conn


def cache_key_for(oa_payload):
    """Hash the exact request content that determines the response: model +
    messages + tools + tool_choice. Deliberately excludes nothing else --
    an exact match here means OpenRouter would see byte-identical input."""
    material = json.dumps({
        "model": oa_payload["model"],
        "messages": oa_payload["messages"],
        "tools": oa_payload.get("tools"),
        "tool_choice": oa_payload.get("tool_choice"),
    }, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def cache_get(key):
    with _cache_lock:
        conn = _cache_conn()
        try:
            row = conn.execute("SELECT response FROM cache WHERE key = ?", (key,)).fetchone()
            if row:
                conn.execute("UPDATE cache SET hit_count = hit_count + 1 WHERE key = ?", (key,))
                conn.commit()
                return json.loads(row[0])
            return None
        finally:
            conn.close()


def cache_put(key, oa_resp, model, cost_usd):
    with _cache_lock:
        conn = _cache_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, response, model, created_at, hit_count, saved_usd_estimate) "
                "VALUES (?, ?, ?, ?, COALESCE((SELECT hit_count FROM cache WHERE key=?), 0), ?)",
                (key, json.dumps(oa_resp), model,
                 datetime.datetime.now(datetime.timezone.utc).isoformat(), key, cost_usd or 0.0),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Budget ceiling
# ---------------------------------------------------------------------------

def _init_budget_spent():
    global _budget_spent_usd
    if BUDGET_CAP_USD is None:
        return
    total = 0.0
    try:
        with open(REAL_COST_LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("ts", "")
                if BUDGET_WINDOW_START and ts < BUDGET_WINDOW_START:
                    continue
                total += r.get("real_cost_usd") or 0.0
    except FileNotFoundError:
        pass
    with _budget_lock:
        _budget_spent_usd = total
    sys.stderr.write(f"[proxy] budget ceiling ON: cap=${BUDGET_CAP_USD:.4f} window_start={BUDGET_WINDOW_START} "
                      f"already_spent_in_window=${total:.4f}\n")


def budget_check():
    """Returns (allowed: bool, spent: float)."""
    if BUDGET_CAP_USD is None:
        return True, 0.0
    with _budget_lock:
        return (_budget_spent_usd < BUDGET_CAP_USD), _budget_spent_usd


def budget_record_spend(amount_usd):
    global _budget_spent_usd
    if BUDGET_CAP_USD is None:
        return
    with _budget_lock:
        _budget_spent_usd += (amount_usd or 0.0)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {self.address_string()} - {fmt % args}\n")

    def do_GET(self):
        if self.path == "/healthz":
            allowed, spent = budget_check()
            body = json.dumps({
                "status": "ok",
                "budget_cap_usd": BUDGET_CAP_USD,
                "budget_spent_usd": round(spent, 4),
                "budget_allowed": allowed,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self.path.startswith("/v1/messages"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        wants_stream = bool(body.get("stream", False))

        # Hard budget ceiling -- checked BEFORE any OpenRouter call, so a
        # rejection costs nothing.
        allowed, spent = budget_check()
        if not allowed:
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {
                    "type": "budget_exceeded",
                    "message": f"Proxy budget ceiling reached: spent ${spent:.4f} >= cap ${BUDGET_CAP_USD:.4f}. "
                                f"No OpenRouter call was made for this request. Raise PROXY_BUDGET_CAP_USD or "
                                f"start a new window to continue.",
                },
            }).encode())
            return

        try:
            model = os.environ.get("PROXY_MODEL", "z-ai/glm-5.2")
            oa_payload = {
                "model": model,
                "messages": anthropic_content_to_openai_messages(body.get("system"), body["messages"]),
                "max_tokens": body.get("max_tokens", 4096),
                "stream": False,
                "provider": {"sort": "price"},
                "usage": {"include": True},
            }
            tools = anthropic_tools_to_openai(body.get("tools"))
            if tools:
                oa_payload["tools"] = tools
                if body.get("tool_choice"):
                    tc = body["tool_choice"]
                    if tc.get("type") == "auto":
                        oa_payload["tool_choice"] = "auto"
                    elif tc.get("type") == "any":
                        oa_payload["tool_choice"] = "required"
                    elif tc.get("type") == "tool":
                        oa_payload["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}

            key = cache_key_for(oa_payload)
            cached = cache_get(key)
            if cached is not None:
                oa_resp = cached
                anth_resp = openai_response_to_anthropic(oa_resp, model)
                log_real_cost(oa_resp, model, cache_hit=True, cache_key=key)
                # no budget_record_spend -- cache hits are free
            else:
                oa_resp = call_openrouter(oa_payload)
                anth_resp = openai_response_to_anthropic(oa_resp, model)
                cost = log_real_cost(oa_resp, model, cache_hit=False, cache_key=key)
                budget_record_spend(cost)
                cache_put(key, oa_resp, model, cost)

        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {"type": "api_error", "message": f"OpenRouter error {e.code}: {err_body[:500]}"},
            }).encode())
            return
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {"type": "api_error", "message": f"Proxy error: {e}"},
            }).encode())
            return

        if wants_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(sse_frame_full_response(anth_resp).encode())
        else:
            body_out = json.dumps(anth_resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    get_openrouter_key()  # fail fast if missing
    _init_budget_spent()
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    cap_msg = f"cap=${BUDGET_CAP_USD}" if BUDGET_CAP_USD is not None else "cap=OFF"
    print(f"[proxy v2] listening on 127.0.0.1:{LISTEN_PORT}, forwarding to OpenRouter, "
          f"cache={CACHE_DB}, budget {cap_msg}", flush=True)
    server.serve_forever()
