#!/usr/bin/env python3
"""
Minimal local proxy: translates Anthropic Messages API requests (what Claude
Code CLI sends via ANTHROPIC_BASE_URL) into OpenRouter's OpenAI-compatible
chat-completions calls, and translates the response back to Anthropic's
Messages format. Lets Claude Code CLI's real tool-use loop (file edit, bash,
git) run against GLM-5.2 via OpenRouter instead of Anthropic's own models.

Deliberately non-streaming end-to-end: always requests stream=false from
OpenRouter, buffers the full response, then re-emits it as either a plain
JSON response or a minimal single-shot SSE stream (matching whatever the
incoming request asked for). This trades away real-time token streaming for
a much smaller, more auditable translation surface -- the actual thing that
matters for headless `claude -p` worker usage, which already just waits for
the final result.

Standard library only. No third-party dependencies.
"""
import http.server
import json
import os
import socketserver
import urllib.request
import urllib.error
import uuid
import sys
import threading

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "8787"))


def get_openrouter_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # fall back to reading the shared .env directly
    env_path = "/opt/veridian/shared/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.strip().split("=", 1)[1]
    raise RuntimeError("OPENROUTER_API_KEY not found in env or /opt/veridian/shared/.env")


def anthropic_content_to_openai_messages(system, messages):
    """Flatten Anthropic's block-array message format into OpenAI's flat
    per-role message list, including separate 'tool' role messages for
    tool_result blocks."""
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

        # content is a list of blocks
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
            # tool_result blocks live on a "user" turn in Anthropic's format;
            # each becomes its own separate "tool" role message in OpenAI's.
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
    """Re-emit a complete Anthropic response as a minimal, valid single-shot
    SSE stream (one text delta containing the whole content, or one
    input_json_delta per tool_use block)."""
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


REAL_COST_LOG = "/opt/veridian/ai-os/logs/glm-proxy-calls.jsonl"
_log_lock = threading.Lock()


def log_real_cost(oa_resp, model):
    """Append one JSONL record with the REAL cost OpenRouter charged for this
    call (from usage.include=true), so worker-entrypoint.sh can enforce a
    budget cap against real spend instead of the CLI's own inflated self-
    report. Never raises -- a logging failure must not break the actual
    proxied call."""
    try:
        usage = oa_resp.get("usage", {})
        record = {
            "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "model": model,
            "provider": oa_resp.get("provider"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "real_cost_usd": usage.get("cost"),
        }
        import os as _os
        _os.makedirs(_os.path.dirname(REAL_COST_LOG), exist_ok=True)
        with _log_lock:
            with open(REAL_COST_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")
    except Exception as e:
        sys.stderr.write(f"[proxy] real-cost logging failed (non-fatal): {e}\n")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {self.address_string()} - {fmt % args}\n")

    def do_POST(self):
        if not self.path.startswith("/v1/messages"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        wants_stream = bool(body.get("stream", False))

        try:
            # Always force the configured model -- the incoming request's own
            # "model" field is whatever Claude Code CLI's own default is
            # (e.g. claude-opus-4-8), which means nothing to OpenRouter and
            # was the exact cause of a real 400 error found during the
            # 2026-07-19 smoke test. This proxy exists specifically to
            # redirect ALL calls to PROXY_MODEL regardless of what the
            # client asked for.
            model = os.environ.get("PROXY_MODEL", "z-ai/glm-5.2")
            oa_payload = {
                "model": model,
                "messages": anthropic_content_to_openai_messages(body.get("system"), body["messages"]),
                "max_tokens": body.get("max_tokens", 4096),
                "stream": False,
                # Owner directive 2026-07-19: always route to the lowest-cost
                # real provider currently serving this model, not a fixed one.
                "provider": {"sort": "price"},
                # Real cost accounting -- Claude Code CLI's own total_cost_usd
                # is meaningless once proxied (it prices against whatever
                # model it THOUGHT it called, e.g. claude-opus-4-8 rates, not
                # GLM-5.2's real OpenRouter cost). This makes OpenRouter
                # return the actual usage.cost in USD so it can be logged.
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

            oa_resp = call_openrouter(oa_payload)
            anth_resp = openai_response_to_anthropic(oa_resp, model)
            log_real_cost(oa_resp, model)

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
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    print(f"[proxy] listening on 127.0.0.1:{LISTEN_PORT}, forwarding to OpenRouter", flush=True)
    server.serve_forever()
