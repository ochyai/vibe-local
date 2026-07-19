#!/usr/bin/env python3
"""
Anthropic Messages API -> Ollama (OpenAI compatible) proxy
Claude Code -> proxy -> ollama:11434
"""

import json
import http.server
import urllib.request
import urllib.error
import urllib.parse
import sys
import time
import uuid
import threading
import os
import datetime
import re
import traceback
import shutil

OLLAMA_BASE = "http://localhost:11434"
PROXY_PORT = 8082
# [H3/L4 fix] Log to user-private directory with restricted permissions
LOG_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "vibe-local", "proxy-debug")
os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)

# --- Debug mode ---
DEBUG_MODE = os.environ.get("VIBE_LOCAL_DEBUG", "0") == "1"

# --- Session directory (created per proxy launch) ---
_session_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
SESSION_DIR = os.path.join(LOG_DIR, f"session_{_session_ts}")
os.makedirs(SESSION_DIR, mode=0o700, exist_ok=True)

# --- Thread-safe request counter ---
_request_counter = 0
_counter_lock = threading.Lock()


def _next_request_id():
    global _request_counter
    with _counter_lock:
        _request_counter += 1
        return _request_counter

# === Optimization settings for local LLM ===
# Max tokens cap (Claude Code sends 32000 which is way too much for 30B model)
MAX_TOKENS_CAP = 4096

# System prompt max length (chars). Claude Code sends ~15K which overwhelms 30B models.
SYSTEM_PROMPT_MAX_CHARS = 4000

# Essential tools only - drop tools that confuse local models
# (Task, TaskOutput, AskUserQuestion, EnterPlanMode, ExitPlanMode etc.)
ALLOWED_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "NotebookEdit",
}
# Set to None to disable tool filtering
# ALLOWED_TOOLS = None


def _log(tag, data, req_id=None):
    """Write metadata log (always active). Uses session directory with optional req_id prefix."""
    ts = datetime.datetime.now().strftime("%H%M%S")
    prefix = f"{req_id:04d}_{ts}" if req_id else ts
    path = os.path.join(SESSION_DIR, f"{prefix}_{tag}.json")
    try:
        with open(path, "w") as f:
            if isinstance(data, (dict, list)):
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(str(data))
        print(f"[proxy][log] {tag} -> {os.path.basename(path)}")
    except Exception as e:
        print(f"[proxy][log] ERROR writing {tag}: {e}")


def _debug_log(req_id, tag, data):
    """Write full content log (DEBUG_MODE only)."""
    if not DEBUG_MODE:
        return
    ts = datetime.datetime.now().strftime("%H%M%S")
    path = os.path.join(SESSION_DIR, f"{req_id:04d}_{ts}_{tag}.json")
    try:
        with open(path, "w") as f:
            if isinstance(data, (dict, list)):
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(str(data))
    except Exception as e:
        print(f"[proxy][debug] ERROR writing {tag}: {e}", file=sys.stderr)


def _debug_summary(req_id, model, msg_count, mode, elapsed_ms, ok, stop_reason=None, tools=None):
    """Print one-line debug summary to stderr (DEBUG_MODE only)."""
    if not DEBUG_MODE:
        return
    status = "OK" if ok else "FAIL"
    parts = [f"[debug] #{req_id:04d}", model, f"msgs={msg_count}", mode, f"{elapsed_ms}ms", status]
    if stop_reason:
        parts.append(f"stop={stop_reason}")
    if tools:
        parts.append(f"tools=[{','.join(tools)}]")
    print(" ".join(parts), file=sys.stderr)


def _save_replay(req_id, req, proxy_port):
    """Generate executable curl replay script (DEBUG_MODE only)."""
    if not DEBUG_MODE:
        return
    ts = datetime.datetime.now().strftime("%H%M%S")
    # Save request body
    body_filename = f"{req_id:04d}_{ts}_replay_body.json"
    body_path = os.path.join(SESSION_DIR, body_filename)
    try:
        with open(body_path, "w") as f:
            json.dump(req, f, ensure_ascii=False, indent=2)
    except Exception:
        return

    # Save curl script
    script_path = os.path.join(SESSION_DIR, f"{req_id:04d}_{ts}_replay.sh")
    try:
        with open(script_path, "w") as f:
            f.write(f"""#!/bin/bash
# Replay request #{req_id:04d}
curl -X POST \\
  http://127.0.0.1:{proxy_port}/v1/messages \\
  -H 'Content-Type: application/json' \\
  -H 'x-api-key: local' \\
  -H 'anthropic-version: 2023-06-01' \\
  -d @./{body_filename}
""")
        os.chmod(script_path, 0o755)
    except Exception as e:
        print(f"[proxy][debug] ERROR writing replay script: {e}", file=sys.stderr)


def _cleanup_old_sessions(max_age_days=7):
    """Remove session directories older than max_age_days."""
    now = time.time()
    cutoff = now - (max_age_days * 86400)
    cleaned = 0
    try:
        for entry in os.listdir(LOG_DIR):
            if not entry.startswith("session_"):
                continue
            entry_path = os.path.join(LOG_DIR, entry)
            if not os.path.isdir(entry_path):
                continue
            if os.path.getmtime(entry_path) < cutoff:
                shutil.rmtree(entry_path, ignore_errors=True)
                cleaned += 1
    except Exception as e:
        print(f"[proxy] cleanup error: {e}", file=sys.stderr)
    if cleaned:
        print(f"[proxy] Cleaned up {cleaned} old session(s) (>{max_age_days} days)", file=sys.stderr)


def _extract_tool_calls_from_text(text, known_tools=None):
    """Parse XML-style tool calls from text content.
    Returns (tool_calls_list, cleaned_text)."""
    tool_calls = []
    remaining_text = text

    # Pattern 1: <invoke name="ToolName"><parameter name="p">v</parameter></invoke>
    invoke_pat = re.compile(
        r'<invoke\s+name=\"([^\"]+)\">(.*?)</invoke>',
        re.DOTALL
    )
    param_pat = re.compile(
        r'<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>',
        re.DOTALL
    )

    for m in invoke_pat.finditer(text):
        tool_name = m.group(1)
        params_text = m.group(2)
        params = {}
        for pm in param_pat.finditer(params_text):
            params[pm.group(1)] = pm.group(2).strip()
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(params, ensure_ascii=False),
            },
        })
        remaining_text = remaining_text.replace(m.group(0), "")

    # Clean wrapper tags
    for tag in ["function_calls", "action"]:
        remaining_text = re.sub(r"</?%s[^>]*>" % re.escape(tag), "", remaining_text)

    if tool_calls:
        return tool_calls, remaining_text.strip()

    # Pattern 2: Qwen format: <function=ToolName><parameter=param>value</parameter></function>
    qwen_func_pat = re.compile(
        r'<function=([^>]+)>(.*?)</function>',
        re.DOTALL
    )
    qwen_param_pat = re.compile(
        r'<parameter=([^>]+)>(.*?)</parameter>',
        re.DOTALL
    )
    for m in qwen_func_pat.finditer(text):
        tool_name = m.group(1).strip()
        params_text = m.group(2)
        params = {}
        for pm in qwen_param_pat.finditer(params_text):
            params[pm.group(1).strip()] = pm.group(2).strip()
        if params:
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(params, ensure_ascii=False),
                },
            })
            remaining_text = remaining_text.replace(m.group(0), "")

    # Clean Qwen wrapper tags
    remaining_text = re.sub(r'</tool_call>', '', remaining_text)
    remaining_text = re.sub(r'<tool_call>', '', remaining_text)

    if tool_calls:
        return tool_calls, remaining_text.strip()

    # Pattern 3: <ToolName><param>val</param></ToolName>
    if known_tools:
        names_re = "|".join(re.escape(t) for t in known_tools)
        simple_pat = re.compile(r"<(%s)>(.*?)</\1>" % names_re, re.DOTALL)
        inner_pat = re.compile(r"<([a-zA-Z_]\w*)>(.*?)</\1>", re.DOTALL)
        for m in simple_pat.finditer(text):
            tool_name = m.group(1)
            inner = m.group(2)
            params = {}
            for pm in inner_pat.finditer(inner):
                params[pm.group(1)] = pm.group(2).strip()
            if params:
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(params, ensure_ascii=False),
                    },
                })
                remaining_text = remaining_text.replace(m.group(0), "")
        for tag in ["action", "function_calls"]:
            remaining_text = re.sub(r"</?%s[^>]*>" % tag, "", remaining_text)

    return tool_calls, remaining_text.strip()

class AnthropicToOllamaHandler(http.server.BaseHTTPRequestHandler):
    _current_tool_names = []

    def log_message(self, format, *args):
        print(f"[proxy] {args[0]}" if args else "")

    def _parse_path(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path

    def do_GET(self):
        path = self._parse_path()
        if path == "/":
            self._respond(200, {"status": "ok", "proxy": "anthropic-to-ollama"})
        elif path == "/v1/models":
            try:
                resp = urllib.request.urlopen(f"{OLLAMA_BASE}/v1/models", timeout=5)
                data = json.loads(resp.read())
                self._respond(200, data)
            except Exception as e:
                self._respond(502, {"error": str(e)})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        path = self._parse_path()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            req = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return
        if path == "/v1/messages":
            self._handle_messages(req)
        elif path == "/v1/messages/count_tokens":
            self._handle_count_tokens(req)
        else:
            self._respond(404, {"error": f"unknown path: {self.path}"})

    def _handle_messages(self, req):
        req_id = _next_request_id()
        t_start = time.time()

        model = req.get("model", "qwen3-coder:30b")
        messages = req.get("messages", [])
        system = req.get("system", "")
        max_tokens = min(req.get("max_tokens", 4096), MAX_TOKENS_CAP)
        temperature = req.get("temperature", 0.7)
        stream = req.get("stream", False)
        original_stream = stream
        tools_list = req.get("tools", [])

        # Filter tools to essential ones only
        if ALLOWED_TOOLS is not None and tools_list:
            original_tool_count = len(tools_list)
            tools_list = [t for t in tools_list if t.get("name", "") in ALLOWED_TOOLS]
            if len(tools_list) != original_tool_count:
                print(f"[proxy] Filtered tools: {original_tool_count} -> {len(tools_list)}")
            req["tools"] = tools_list

        has_tools = bool(tools_list)
        self._current_tool_names = [t.get("name", "") for t in tools_list]

        _log("req_meta", {
            "model": model, "stream": stream, "has_tools": has_tools,
            "tool_count": len(tools_list),
            "tool_names": self._current_tool_names[:20],
            "message_count": len(messages),
            "system_length": len(str(system)),
            "max_tokens": max_tokens,
            "max_tokens_original": req.get("max_tokens", 4096),
        }, req_id=req_id)

        # Debug: log full Anthropic request
        _debug_log(req_id, "anthropic_request_full", req)

        if has_tools and stream:
            stream = False

        oai_messages = []

        if system:
            if isinstance(system, list):
                sys_text = "\n".join(
                    b.get("text", "") for b in system if b.get("type") == "text"
                )
            else:
                sys_text = str(system)

            # Truncate overly long system prompts for local model
            original_sys_len = len(sys_text)
            if len(sys_text) > SYSTEM_PROMPT_MAX_CHARS:
                sys_text = sys_text[:SYSTEM_PROMPT_MAX_CHARS] + "\n...(truncated for local model)"
                print(f"[proxy] System prompt truncated: {original_sys_len} -> {len(sys_text)} chars")

            if has_tools:
                tool_names_str = ", ".join(self._current_tool_names[:15])
                fc_hint = (
                    "\n\n[IMPORTANT: FUNCTION CALLING]\n"
                    "You have tools available via function calling: " + tool_names_str + ".\n"
                    "When you need to perform any action, you MUST use function calls.\n"
                    "Do NOT write commands as plain text. Do NOT output XML tags.\n"
                    "Use the function calling mechanism provided by the API.\n"
                    "Always prefer Bash tool for system commands. Do NOT use Task or AskUserQuestion.\n"
                )
                sys_text += fc_hint

            oai_messages.append({"role": "system", "content": sys_text})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                text_parts = []
                tool_calls_out = []
                tool_results = []

                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "text":
                            text_parts.append(block.get("text", ""))
                        elif btype == "thinking":
                            pass
                        elif btype == "tool_use":
                            tool_calls_out.append({
                                "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                                },
                            })
                        elif btype == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                result_content = "\n".join(
                                    b.get("text", str(b)) for b in result_content
                                    if isinstance(b, dict)
                                )
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": str(result_content),
                            })
                    else:
                        text_parts.append(str(block))

                if tool_results:
                    for tr in tool_results:
                        oai_messages.append(tr)
                    continue

                if tool_calls_out:
                    assistant_msg = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
                    assistant_msg["tool_calls"] = tool_calls_out
                    oai_messages.append(assistant_msg)
                    continue

                content = "\n".join(text_parts)
                oai_messages.append({"role": role, "content": content})
            else:
                oai_messages.append({"role": role, "content": content})

        oai_req = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }

        if "tools" in req:
            oai_tools = []
            for tool in req["tools"]:
                tool_type = tool.get("type", "")
                if tool_type in ("custom", ""):
                    oai_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("input_schema", {}),
                        },
                    })
            if oai_tools:
                oai_req["tools"] = oai_tools
                oai_req["tool_choice"] = "auto"

        _log("req_to_ollama", {
            "model": oai_req.get("model"),
            "stream": oai_req.get("stream"),
            "message_count": len(oai_req.get("messages", [])),
            "tool_count": len(oai_req.get("tools", [])),
            "has_tool_choice": "tool_choice" in oai_req,
        }, req_id=req_id)

        # Debug: log full OAI-converted request
        _debug_log(req_id, "ollama_request_full", oai_req)
        # Debug: generate replay script
        _save_replay(req_id, req, PROXY_PORT)

        try:
            oai_body = json.dumps(oai_req).encode("utf-8")
            oai_request = urllib.request.Request(
                f"{OLLAMA_BASE}/v1/chat/completions",
                data=oai_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            if stream:
                self._handle_stream(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages))
            elif original_stream:
                self._handle_sync_as_sse(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages))
            else:
                self._handle_sync(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages))

        except urllib.error.URLError as e:
            elapsed_ms = int((time.time() - t_start) * 1000)
            _debug_summary(req_id, model, len(messages), "sync", elapsed_ms, False)
            self._respond(502, {
                "type": "error",
                "error": {"type": "api_error", "message": f"ollama connection failed: {e}"},
            })
        except Exception as e:
            elapsed_ms = int((time.time() - t_start) * 1000)
            _debug_summary(req_id, model, len(messages), "sync", elapsed_ms, False)
            traceback.print_exc()
            self._respond(500, {
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            })

    def _process_ollama_response(self, oai_resp):
        """Process ollama response, extract XML tool calls from text if needed."""
        choice = oai_resp.get("choices", [{}])[0]
        message = choice.get("message", {})
        content_text = message.get("content", "") or ""
        reasoning_text = message.get("reasoning", "") or ""
        tool_calls = message.get("tool_calls", [])
        finish_reason = choice.get("finish_reason", "end_turn")

        if not tool_calls and content_text and self._current_tool_names:
            extracted, cleaned = _extract_tool_calls_from_text(
                content_text, self._current_tool_names
            )
            if extracted:
                _log("xml_fallback", {
                    "extracted_count": len(extracted),
                    "tool_names": [tc["function"]["name"] for tc in extracted],
                    "original_len": len(content_text),
                    "cleaned_len": len(cleaned),
                })
                tool_calls = extracted
                content_text = cleaned
                finish_reason = "tool_calls"

        # [H3 fix] Do not log content preview - may contain sensitive code/data
        _log("resp_parsed", {
            "has_content": bool(content_text),
            "content_length": len(content_text),
            "has_reasoning": bool(reasoning_text),
            "has_tool_calls": bool(tool_calls),
            "tool_call_count": len(tool_calls),
            "tool_call_names": [tc.get("function", {}).get("name", "") for tc in tool_calls],
            "finish_reason": finish_reason,
        })

        return content_text, reasoning_text, tool_calls, finish_reason

    def _handle_sync(self, oai_request, model, req_id=0, t_start=None, msg_count=0):
        resp = urllib.request.urlopen(oai_request, timeout=300)
        oai_resp = json.loads(resp.read())
        # [H3 fix] Log metadata only, not full response content
        _log("resp_from_ollama_sync_meta", {
            "model": oai_resp.get("model"),
            "usage": oai_resp.get("usage"),
            "choices_count": len(oai_resp.get("choices", [])),
        }, req_id=req_id)

        # Debug: log full Ollama response
        _debug_log(req_id, "ollama_response_full", oai_resp)

        content_text, reasoning_text, tool_calls, finish_reason = self._process_ollama_response(oai_resp)

        content_blocks = []
        if reasoning_text:
            content_blocks.append({"type": "thinking", "thinking": reasoning_text})
        if content_text:
            content_blocks.append({"type": "text", "text": content_text})

        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                try:
                    tool_input = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_input = {"raw": func.get("arguments", "")}
                raw_id = tc.get("id", "")
                tool_use_id = raw_id if raw_id.startswith("toolu_") else f"toolu_{uuid.uuid4().hex[:24]}"
                content_blocks.append({
                    "type": "tool_use", "id": tool_use_id,
                    "name": func.get("name", ""), "input": tool_input,
                })

        if not content_blocks:
            content_blocks.append({"type": "text", "text": reasoning_text or ""})

        stop_reason = "tool_use" if finish_reason == "tool_calls" else ("end_turn" if finish_reason == "stop" else finish_reason)

        anthropic_resp = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message", "role": "assistant",
            "content": content_blocks, "model": model,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {
                "input_tokens": oai_resp.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": oai_resp.get("usage", {}).get("completion_tokens", 0),
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            },
        }

        # Debug: log full Anthropic response
        _debug_log(req_id, "anthropic_response_full", anthropic_resp)
        elapsed_ms = int((time.time() - t_start) * 1000) if t_start else 0
        tool_names = [tc.get("function", {}).get("name", "") for tc in tool_calls] if tool_calls else None
        _debug_summary(req_id, model, msg_count, "sync", elapsed_ms, True, stop_reason, tool_names)

        self._respond(200, anthropic_resp)

    def _handle_sync_as_sse(self, oai_request, model, req_id=0, t_start=None, msg_count=0):
        resp = urllib.request.urlopen(oai_request, timeout=300)
        oai_resp = json.loads(resp.read())
        _log("resp_from_ollama_sse_meta", {
            "model": oai_resp.get("model"),
            "usage": oai_resp.get("usage"),
            "choices_count": len(oai_resp.get("choices", [])),
        }, req_id=req_id)

        # Debug: log full Ollama response
        _debug_log(req_id, "ollama_response_full", oai_resp)

        content_text, reasoning_text, tool_calls, finish_reason = self._process_ollama_response(oai_resp)

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        stop_reason = "tool_use" if finish_reason == "tool_calls" else ("end_turn" if finish_reason == "stop" else finish_reason)
        input_tokens = oai_resp.get("usage", {}).get("prompt_tokens", 0)
        output_tokens = oai_resp.get("usage", {}).get("completion_tokens", 0)

        self._send_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        })

        block_index = 0

        if reasoning_text:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {"type": "thinking", "thinking": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {"type": "thinking_delta", "thinking": reasoning_text},
            })
            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
            block_index += 1

        if content_text:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {"type": "text", "text": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {"type": "text_delta", "text": content_text},
            })
            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
            block_index += 1

        for tc in tool_calls:
            func = tc.get("function", {})
            try:
                tool_input = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {"raw": func.get("arguments", "")}
            tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"

            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {"type": "tool_use", "id": tool_use_id, "name": func.get("name", ""), "input": {}},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input, ensure_ascii=False)},
            })
            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
            block_index += 1

        if block_index == 0:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "(empty response)"},
            })
            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": 0})

        self._send_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        })
        self._send_sse("message_stop", {"type": "message_stop"})
        self.wfile.flush()

        # Debug: log Anthropic SSE response summary and timing
        _debug_log(req_id, "anthropic_response_full", {
            "msg_id": msg_id, "stop_reason": stop_reason,
            "content_text_length": len(content_text) if content_text else 0,
            "reasoning_text_length": len(reasoning_text) if reasoning_text else 0,
            "tool_call_count": len(tool_calls),
            "tool_call_names": [tc.get("function", {}).get("name", "") for tc in tool_calls],
        })
        elapsed_ms = int((time.time() - t_start) * 1000) if t_start else 0
        tool_names = [tc.get("function", {}).get("name", "") for tc in tool_calls] if tool_calls else None
        _debug_summary(req_id, model, msg_count, "sync", elapsed_ms, True, stop_reason, tool_names)

    def _handle_stream(self, oai_request, model, req_id=0, t_start=None, msg_count=0):
        resp = urllib.request.urlopen(oai_request, timeout=300)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        self._send_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        })

        total_output_tokens = 0
        in_reasoning = False
        reasoning_started = False
        content_started = False
        content_index = 0
        buffer = b""
        # Debug: accumulate full stream content
        accumulated_reasoning = []
        accumulated_content = []
        for chunk in iter(lambda: resp.read(1), b""):
            buffer += chunk
            if chunk == b"\n":
                line = buffer.decode("utf-8", errors="replace").strip()
                buffer = b""
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    oai_chunk = json.loads(data_str)
                    delta = oai_chunk.get("choices", [{}])[0].get("delta", {})
                    reasoning = delta.get("reasoning", "")
                    text = delta.get("content", "")

                    if reasoning:
                        accumulated_reasoning.append(reasoning)
                        if not reasoning_started:
                            reasoning_started = True
                            in_reasoning = True
                            self._send_sse("content_block_start", {
                                "type": "content_block_start", "index": content_index,
                                "content_block": {"type": "thinking", "thinking": ""},
                            })
                        total_output_tokens += 1
                        self._send_sse("content_block_delta", {
                            "type": "content_block_delta", "index": content_index,
                            "delta": {"type": "thinking_delta", "thinking": reasoning},
                        })

                    if text:
                        accumulated_content.append(text)
                        if in_reasoning:
                            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": content_index})
                            content_index += 1
                            in_reasoning = False
                        if not content_started:
                            content_started = True
                            self._send_sse("content_block_start", {
                                "type": "content_block_start", "index": content_index,
                                "content_block": {"type": "text", "text": ""},
                            })
                        total_output_tokens += 1
                        self._send_sse("content_block_delta", {
                            "type": "content_block_delta", "index": content_index,
                            "delta": {"type": "text_delta", "text": text},
                        })
                except json.JSONDecodeError:
                    continue

        if in_reasoning:
            self._send_sse("content_block_stop", {"type": "content_block_stop", "index": content_index})
            content_index += 1

        if not content_started:
            if reasoning_started:
                self._send_sse("content_block_start", {
                    "type": "content_block_start", "index": content_index,
                    "content_block": {"type": "text", "text": ""},
                })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": content_index,
                "delta": {"type": "text_delta", "text": "(thinking limit reached)"},
            })

        self._send_sse("content_block_stop", {"type": "content_block_stop", "index": content_index})
        self._send_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": total_output_tokens},
        })
        self._send_sse("message_stop", {"type": "message_stop"})
        self.wfile.flush()

        # Debug: log assembled stream response
        _debug_log(req_id, "stream_assembled_response", {
            "reasoning": "".join(accumulated_reasoning),
            "content": "".join(accumulated_content),
            "output_tokens": total_output_tokens,
        })
        elapsed_ms = int((time.time() - t_start) * 1000) if t_start else 0
        _debug_summary(req_id, model, msg_count, "stream", elapsed_ms, True, "end_turn")

    def _handle_count_tokens(self, req):
        messages = req.get("messages", [])
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        total += len(block.get("text", "")) // 4
            else:
                total += len(str(content)) // 4
        system = req.get("system", "")
        if system:
            if isinstance(system, list):
                for block in system:
                    if isinstance(block, dict):
                        total += len(block.get("text", "")) // 4
            else:
                total += len(str(system)) // 4
        self._respond(200, {"input_tokens": total})

    def _send_sse(self, event_type, data):
        try:
            line = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


class ThreadedHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True

    def server_bind(self):
        """Override to skip slow reverse DNS lookup (getfqdn).
        On some networks, socket.getfqdn('127.0.0.1') hangs for minutes
        waiting for DNS reverse lookup, which makes the proxy appear frozen."""
        import socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        # Set server_name directly without DNS lookup
        host, port = self.server_address
        self.server_name = host
        self.server_port = port

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PROXY_PORT
    server = ThreadedHTTPServer(("127.0.0.1", port), AnthropicToOllamaHandler)
    print(f"[proxy] Anthropic -> Ollama proxy on http://127.0.0.1:{port}")
    print(f"[proxy] Ollama backend: {OLLAMA_BASE}")
    print(f"[proxy] Log dir: {LOG_DIR}")
    print(f"[proxy] Session: {os.path.basename(SESSION_DIR)}")
    print(f"[proxy] Debug mode: {'ON' if DEBUG_MODE else 'OFF'}")
    print(f"[proxy] XML tool call fallback: enabled")
    print(f"[proxy] Ctrl+C to stop")

    # Cleanup old sessions on startup
    _cleanup_old_sessions(7)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] stopped")
        server.server_close()


if __name__ == "__main__":
    main()
