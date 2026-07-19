#!/usr/bin/env python3
"""
localllm.py — Direct local LLM server for vibe-local.
Speaks Anthropic Messages API, runs inference via MLX on Apple Silicon.
No Ollama. No proxy chain. No pip. One process.

Usage:
    python3 localllm.py --model mlx-community/Qwen2.5-Coder-32B-Instruct-4bit

Architecture:
    vibe-local --HTTP--> localllm.py --function call--> MLX/Metal
                         ^^^^^^^^^^^
                      This is the ONLY process.
"""

import os
import subprocess
import sys

# ═══════════════════════════════════════════════════════════════
# Auto-bootstrap: create venv and install mlx-lm on first run
# ═══════════════════════════════════════════════════════════════

VENV_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "localllm", "venv")


def _ensure_deps():
    """Auto-install mlx-lm into a local venv if not available. Zero pip needed."""
    try:
        import mlx_lm  # noqa: F401
        return  # Already available
    except ImportError:
        pass

    # Are we inside our venv but mlx-lm not installed yet?
    if sys.prefix == VENV_DIR:
        print("[localllm] Installing mlx-lm into venv...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "mlx-lm"],
        )
        return

    # Create venv if it doesn't exist
    if not os.path.isdir(VENV_DIR):
        print("[localllm] First run — creating virtual environment...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    # Install mlx-lm
    venv_pip = os.path.join(VENV_DIR, "bin", "pip")
    print("[localllm] Installing mlx-lm (first run only, may take a minute)...")
    subprocess.check_call([venv_pip, "install", "-q", "mlx-lm"])

    # Re-exec this script under the venv's Python
    venv_python = os.path.join(VENV_DIR, "bin", "python3")
    os.execv(venv_python, [venv_python] + sys.argv)


# _ensure_deps() is called in __main__ block, not at import time.
# This allows tests to import localllm without triggering bootstrap.

# ── Imports (mlx-lm is lazy-imported inside class methods) ────

import argparse
import json
import http.server
import urllib.parse
import time
import uuid
import threading
import re
import traceback

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

DEFAULT_PORT = 8082
MAX_TOKENS_CAP = 16384
SYSTEM_PROMPT_MAX_CHARS = 4000

ALLOWED_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "NotebookEdit",
}


# ═══════════════════════════════════════════════════════════════
# Inference Backends
# ═══════════════════════════════════════════════════════════════

class MLXBackend:
    """Apple Silicon native inference via mlx-lm. Zero-copy Unified Memory."""

    def __init__(self):
        self.model = None
        self.tokenizer = None

    def load(self, model_id: str):
        import mlx.core as mx
        from mlx_lm import load as mlx_load

        # Set Metal cache limit to 80% of system RAM for optimal buffer reuse
        try:
            import os
            mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            cache_bytes = int(mem_bytes * 0.8)
            mx.metal.set_cache_limit(cache_bytes)
            print(f"[localllm] Metal cache limit: {cache_bytes / (1024**3):.0f} GB")
        except Exception:
            pass

        print(f"[localllm] Loading MLX model: {model_id}")
        t0 = time.time()
        self.model, self.tokenizer = mlx_load(model_id)
        elapsed = time.time() - t0
        print(f"[localllm] Model loaded in {elapsed:.1f}s")

    @staticmethod
    def _make_sampler(temperature: float):
        """Create an mlx-lm sampler with the given temperature."""
        from mlx_lm.sample_utils import make_sampler
        return make_sampler(temp=temperature)

    def generate(self, messages: list, max_tokens: int, temperature: float,
                 tools: list = None) -> str:
        """Full generation, returns complete text."""
        from mlx_lm import generate as mlx_generate
        prompt = self._format_prompt(messages, tools)
        sampler = self._make_sampler(temperature)
        return mlx_generate(
            self.model, self.tokenizer, prompt,
            max_tokens=max_tokens, sampler=sampler,
        )

    def stream(self, messages: list, max_tokens: int, temperature: float):
        """Token-by-token generation, yields text chunks."""
        from mlx_lm import stream_generate
        prompt = self._format_prompt(messages, None)
        sampler = self._make_sampler(temperature)
        for resp in stream_generate(
            self.model, self.tokenizer, prompt,
            max_tokens=max_tokens, sampler=sampler,
            prefill_step_size=4096,
        ):
            yield resp.text

    def _format_prompt(self, messages: list, tools: list = None) -> str:
        """Apply chat template. Handles tools if tokenizer supports them."""
        kwargs = {"add_generation_prompt": True, "tokenize": False}
        if tools:
            try:
                return self.tokenizer.apply_chat_template(
                    messages, tools=tools, **kwargs
                )
            except (TypeError, Exception):
                # Tokenizer doesn't support tools param — inject into system prompt
                messages = self._inject_tools_into_system(messages, tools)
        return self.tokenizer.apply_chat_template(messages, **kwargs)

    def _inject_tools_into_system(self, messages: list, tools: list) -> list:
        """Fallback: format tools as text in system prompt."""
        tools_desc = "You have these tools available via function calling:\n\n"
        for t in tools:
            func = t.get("function", t)
            name = func.get("name", "")
            desc = func.get("description", "")
            params = json.dumps(func.get("parameters", {}), indent=2)
            tools_desc += f"### {name}\n{desc}\nParameters: {params}\n\n"
        tools_desc += (
            "When you need to use a tool, output a function call in this format:\n"
            '<function=ToolName>\n<parameter=param_name>value</parameter>\n</function>\n'
        )
        messages = list(messages)
        if messages and messages[0]["role"] == "system":
            messages[0] = {
                "role": "system",
                "content": messages[0]["content"] + "\n\n" + tools_desc,
            }
        else:
            messages.insert(0, {"role": "system", "content": tools_desc})
        return messages


# ═══════════════════════════════════════════════════════════════
# Tool Call Extraction (XML fallback for local models)
# ═══════════════════════════════════════════════════════════════

def extract_tool_calls(text, known_tools=None):
    """Parse XML-style tool calls from model output text.
    Returns (tool_calls_list, cleaned_text).
    Handles: Anthropic XML, Qwen XML, simple nested XML."""
    tool_calls = []
    remaining = text

    # Pattern 1: <invoke name="ToolName"><parameter name="p">v</parameter></invoke>
    invoke_pat = re.compile(
        r'<invoke\s+name="([^"]+)">(.*?)</invoke>', re.DOTALL
    )
    param_pat = re.compile(
        r'<parameter\s+name="([^"]+)">(.*?)</parameter>', re.DOTALL
    )
    for m in invoke_pat.finditer(text):
        params = {}
        for pm in param_pat.finditer(m.group(2)):
            params[pm.group(1)] = pm.group(2).strip()
        tool_calls.append(_make_tool_call(m.group(1), params))
        remaining = remaining.replace(m.group(0), "")

    for tag in ["function_calls", "action"]:
        remaining = re.sub(rf"</?{re.escape(tag)}[^>]*>", "", remaining)

    if tool_calls:
        return tool_calls, remaining.strip()

    # Pattern 2: Qwen format: <function=ToolName><parameter=p>v</parameter></function>
    qwen_func = re.compile(r'<function=([^>]+)>(.*?)</function>', re.DOTALL)
    qwen_param = re.compile(r'<parameter=([^>]+)>(.*?)</parameter>', re.DOTALL)
    for m in qwen_func.finditer(text):
        params = {}
        for pm in qwen_param.finditer(m.group(2)):
            params[pm.group(1).strip()] = pm.group(2).strip()
        if params:
            tool_calls.append(_make_tool_call(m.group(1).strip(), params))
            remaining = remaining.replace(m.group(0), "")

    remaining = re.sub(r'</?tool_call>', '', remaining)

    if tool_calls:
        return tool_calls, remaining.strip()

    # Pattern 3: <ToolName><param>val</param></ToolName>
    if known_tools:
        names_re = "|".join(re.escape(t) for t in known_tools)
        simple_pat = re.compile(rf"<({names_re})>(.*?)</\1>", re.DOTALL)
        inner_pat = re.compile(r"<([a-zA-Z_]\w*)>(.*?)</\1>", re.DOTALL)
        for m in simple_pat.finditer(text):
            params = {}
            for pm in inner_pat.finditer(m.group(2)):
                params[pm.group(1)] = pm.group(2).strip()
            if params:
                tool_calls.append(_make_tool_call(m.group(1), params))
                remaining = remaining.replace(m.group(0), "")

    # Pattern 4: JSON tool calls (some models output JSON directly)
    json_pat = re.compile(
        r'\{[^{}]*"name"\s*:\s*"(\w+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}',
        re.DOTALL,
    )
    if not tool_calls and known_tools:
        for m in json_pat.finditer(text):
            name = m.group(1)
            if name in known_tools:
                try:
                    args = json.loads(m.group(2))
                    tool_calls.append(_make_tool_call(name, args))
                    remaining = remaining.replace(m.group(0), "")
                except json.JSONDecodeError:
                    pass

    return tool_calls, remaining.strip()


def _make_tool_call(name, params):
    return {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(params, ensure_ascii=False),
        },
    }


# ═══════════════════════════════════════════════════════════════
# Message Format Conversion (Anthropic <-> OpenAI)
# ═══════════════════════════════════════════════════════════════

def anthropic_to_oai_messages(req, tool_names=None):
    """Convert Anthropic Messages API request body to OpenAI-style messages."""
    messages = req.get("messages", [])
    system = req.get("system", "")
    has_tools = bool(req.get("tools"))
    oai = []

    # System prompt
    if system:
        if isinstance(system, list):
            sys_text = "\n".join(
                b.get("text", "") for b in system if b.get("type") == "text"
            )
        else:
            sys_text = str(system)

        original_len = len(sys_text)
        if len(sys_text) > SYSTEM_PROMPT_MAX_CHARS:
            sys_text = sys_text[:SYSTEM_PROMPT_MAX_CHARS] + "\n...(truncated)"
            print(f"[localllm] System prompt truncated: {original_len} -> {len(sys_text)}")

        if has_tools and tool_names:
            names_str = ", ".join(tool_names[:15])
            sys_text += (
                "\n\n[IMPORTANT: FUNCTION CALLING]\n"
                f"You have tools: {names_str}.\n"
                "Use function calls for actions. Do NOT output XML tags.\n"
            )
        oai.append({"role": "system", "content": sys_text})

    # Messages
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts = []
            tc_out = []
            tool_results = []

            for block in content:
                if not isinstance(block, dict):
                    text_parts.append(str(block))
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    pass
                elif btype == "tool_use":
                    tc_out.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(
                                block.get("input", {}), ensure_ascii=False
                            ),
                        },
                    })
                elif btype == "tool_result":
                    rc = block.get("content", "")
                    if isinstance(rc, list):
                        rc = "\n".join(
                            b.get("text", str(b))
                            for b in rc if isinstance(b, dict)
                        )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(rc),
                    })

            if tool_results:
                for tr in tool_results:
                    oai.append(tr)
                continue

            if tc_out:
                assistant_msg = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                    "tool_calls": tc_out,
                }
                oai.append(assistant_msg)
                continue

            oai.append({"role": role, "content": "\n".join(text_parts)})
        else:
            oai.append({"role": role, "content": content})

    return oai


def anthropic_to_oai_tools(tools_list):
    """Convert Anthropic tool definitions to OpenAI function format."""
    oai_tools = []
    for tool in tools_list:
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
    return oai_tools


# ═══════════════════════════════════════════════════════════════
# HTTP Handler — Anthropic Messages API
# ═══════════════════════════════════════════════════════════════

class LocalLLMHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if args:
            print(f"[localllm] {args[0]}")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._respond(200, {"status": "ok", "server": "localllm",
                                "backend": self.server.backend_name})
        elif path == "/v1/models":
            self._respond(200, {
                "data": [{"id": self.server.model_name, "object": "model"}],
                "object": "list",
            })
        elif path == "/api/tags":
            # Ollama-compatible model list (vibe-coder checks this)
            self._respond(200, {
                "models": [{"name": self.server.model_name, "size": 0}],
            })
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/messages":
            self._handle_messages(req)
        elif path == "/v1/messages/count_tokens":
            self._handle_count_tokens(req)
        elif path == "/v1/chat/completions":
            self._handle_chat_completions(req)
        else:
            self._respond(404, {"error": f"unknown: {path}"})

    # ── Core request handler ──────────────────────────────────

    def _handle_messages(self, req):
        t0 = time.time()
        backend = self.server.backend
        model = req.get("model", "local")
        stream = req.get("stream", False)
        tools_list = req.get("tools", [])

        # Filter tools
        if ALLOWED_TOOLS is not None and tools_list:
            tools_list = [t for t in tools_list if t.get("name", "") in ALLOWED_TOOLS]
            req["tools"] = tools_list

        has_tools = bool(tools_list)
        tool_names = [t.get("name", "") for t in tools_list]
        max_tokens = min(req.get("max_tokens", 4096), MAX_TOKENS_CAP)
        temperature = req.get("temperature", 0.7)

        # Convert message formats
        oai_messages = anthropic_to_oai_messages(req, tool_names)
        oai_tools = anthropic_to_oai_tools(tools_list) if tools_list else None

        print(f"[localllm] #{threading.current_thread().name} "
              f"msgs={len(oai_messages)} tools={len(tool_names)} "
              f"stream={stream} max_tokens={max_tokens}")

        try:
            if has_tools:
                # Tool calls: sync mode, parse tool calls from output
                self._handle_with_tools(
                    backend, oai_messages, oai_tools, tool_names,
                    max_tokens, temperature, model, t0,
                )
            elif stream:
                # True token-by-token streaming
                self._handle_stream(
                    backend, oai_messages, max_tokens, temperature, model, t0,
                )
            else:
                # Sync text generation
                self._handle_sync(
                    backend, oai_messages, max_tokens, temperature, model, t0,
                )
        except BrokenPipeError:
            print(f"[localllm] Client disconnected")
        except Exception as e:
            traceback.print_exc()
            elapsed = int((time.time() - t0) * 1000)
            print(f"[localllm] ERROR after {elapsed}ms: {e}")
            try:
                self._respond(500, {
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)},
                })
            except Exception:
                pass

    # ── Streaming (no tools) ──────────────────────────────────

    def _handle_stream(self, backend, oai_messages, max_tokens, temperature,
                       model, t0):
        """True token-by-token streaming directly from inference engine."""
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        self._send_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0},
            },
        })

        self._send_sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        })

        token_count = 0
        for text in backend.stream(oai_messages, max_tokens, temperature):
            if not text:
                continue
            token_count += 1
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text},
            })

        self._send_sse("content_block_stop", {
            "type": "content_block_stop", "index": 0,
        })
        self._send_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": token_count},
        })
        self._send_sse("message_stop", {"type": "message_stop"})
        self.wfile.flush()

        elapsed = int((time.time() - t0) * 1000)
        print(f"[localllm] Stream done: {token_count} tokens, {elapsed}ms")

    # ── Sync generation (no tools) ────────────────────────────

    def _handle_sync(self, backend, oai_messages, max_tokens, temperature,
                     model, t0):
        """Non-streaming generation, returned as SSE for Claude Code."""
        content = backend.generate(oai_messages, max_tokens, temperature)

        self._emit_sse_full(model, content, "", [], "end_turn", {})

        elapsed = int((time.time() - t0) * 1000)
        print(f"[localllm] Sync done: {len(content)} chars, {elapsed}ms")

    # ── Tool calling (always sync) ────────────────────────────

    def _handle_with_tools(self, backend, oai_messages, oai_tools, tool_names,
                           max_tokens, temperature, model, t0):
        """Generate with tools: sync, then parse tool calls from output."""
        full_text = backend.generate(
            oai_messages, max_tokens, temperature, oai_tools
        )
        tool_calls, content = extract_tool_calls(full_text, tool_names)
        if not tool_calls:
            content = full_text
        finish = "tool_calls" if tool_calls else "stop"

        self._emit_sse_full(model, content, "", tool_calls, finish, {})

        elapsed = int((time.time() - t0) * 1000)
        tc_names = [tc["function"]["name"] for tc in tool_calls]
        print(f"[localllm] Tools done: {elapsed}ms, "
              f"tools={tc_names or 'none'}")

    # ── SSE emission helpers ──────────────────────────────────

    def _emit_sse_full(self, model, content, reasoning, tool_calls,
                       finish_reason, usage):
        """Emit a complete Anthropic SSE response from collected parts."""
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        stop_reason = (
            "tool_use" if finish_reason == "tool_calls"
            else "end_turn" if finish_reason == "stop"
            else finish_reason or "end_turn"
        )
        input_tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0
        output_tokens = usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # message_start
        self._send_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens, "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        })

        block_index = 0

        # Reasoning block (if present)
        if reasoning:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {"type": "thinking", "thinking": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {"type": "thinking_delta", "thinking": reasoning},
            })
            self._send_sse("content_block_stop", {
                "type": "content_block_stop", "index": block_index,
            })
            block_index += 1

        # Text content block
        if content:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {"type": "text", "text": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {"type": "text_delta", "text": content},
            })
            self._send_sse("content_block_stop", {
                "type": "content_block_stop", "index": block_index,
            })
            block_index += 1

        # Tool use blocks
        for tc in tool_calls:
            func = tc.get("function", {})
            try:
                tool_input = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {"raw": func.get("arguments", "")}
            tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"

            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": block_index,
                "content_block": {
                    "type": "tool_use", "id": tool_use_id,
                    "name": func.get("name", ""), "input": {},
                },
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": block_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input, ensure_ascii=False),
                },
            })
            self._send_sse("content_block_stop", {
                "type": "content_block_stop", "index": block_index,
            })
            block_index += 1

        # Empty response fallback
        if block_index == 0:
            self._send_sse("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            self._send_sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "(empty response)"},
            })
            self._send_sse("content_block_stop", {
                "type": "content_block_stop", "index": 0,
            })

        # message_delta + message_stop
        self._send_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        })
        self._send_sse("message_stop", {"type": "message_stop"})
        self.wfile.flush()

    # ── Token counting ────────────────────────────────────────

    def _handle_count_tokens(self, req):
        total = 0
        for msg in req.get("messages", []):
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

    # ── OpenAI-compatible /v1/chat/completions ──────────────

    def _handle_chat_completions(self, req):
        """OpenAI-compatible chat completions endpoint.
        This allows vibe-coder.py to talk to localllm directly via --ollama-host."""
        t0 = time.time()
        backend = self.server.backend
        model = req.get("model", "local")
        messages = req.get("messages", [])
        max_tokens = min(req.get("max_tokens", 4096), MAX_TOKENS_CAP)
        temperature = req.get("temperature", 0.7)
        stream = req.get("stream", False)
        tools = req.get("tools", [])

        print(f"[localllm] /v1/chat/completions msgs={len(messages)} "
              f"stream={stream} tools={len(tools)}")

        try:
            if stream and not tools:
                self._oai_stream(backend, messages, max_tokens, temperature, model, t0)
            else:
                self._oai_sync(backend, messages, max_tokens, temperature, model,
                               tools, t0)
        except BrokenPipeError:
            pass
        except Exception as e:
            traceback.print_exc()
            self._respond(500, {"error": {"message": str(e), "type": "server_error"}})

    def _oai_sync(self, backend, messages, max_tokens, temperature, model,
                  tools, t0):
        """Sync OpenAI-format response."""
        oai_tools = tools if tools else None
        full_text = backend.generate(messages, max_tokens, temperature, oai_tools)

        # Parse tool calls from output if tools were requested
        tool_calls_out = []
        content = full_text
        if tools:
            tool_names = [t.get("function", {}).get("name", "") for t in tools]
            extracted, cleaned = extract_tool_calls(full_text, tool_names)
            if extracted:
                tool_calls_out = extracted
                content = cleaned

        finish = "tool_calls" if tool_calls_out else "stop"
        message = {"role": "assistant", "content": content or ""}
        if tool_calls_out:
            message["tool_calls"] = tool_calls_out

        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        self._respond(200, resp)
        elapsed = int((time.time() - t0) * 1000)
        print(f"[localllm] OAI sync done: {elapsed}ms")

    def _oai_stream(self, backend, messages, max_tokens, temperature, model, t0):
        """Streaming OpenAI-format SSE response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

        # First chunk: role
        self._oai_sse(chunk_id, model, {"role": "assistant", "content": ""}, None)

        # Stream tokens
        token_count = 0
        for text in backend.stream(messages, max_tokens, temperature):
            if not text:
                continue
            token_count += 1
            self._oai_sse(chunk_id, model, {"content": text}, None)

        # Final chunk
        self._oai_sse(chunk_id, model, {}, "stop")

        # [DONE]
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

        elapsed = int((time.time() - t0) * 1000)
        print(f"[localllm] OAI stream done: {token_count} tokens, {elapsed}ms")

    def _oai_sse(self, chunk_id, model, delta, finish_reason):
        """Send one OpenAI SSE chunk."""
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        try:
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except BrokenPipeError:
            pass

    # ── Low-level helpers ─────────────────────────────────────

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


# ═══════════════════════════════════════════════════════════════
# Threaded HTTP Server
# ═══════════════════════════════════════════════════════════════

class ThreadedServer(http.server.HTTPServer):
    allow_reuse_address = True

    def server_bind(self):
        import socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.bind(self.server_address)
        except OSError as e:
            if e.errno == 48:  # Address already in use
                port = self.server_address[1]
                print(f"\n[localllm] ERROR: Port {port} is already in use.")
                print(f"  Check with: lsof -i :{port}")
                print(f"  Kill it:    kill $(lsof -ti :{port})")
                print(f"  Or use:     python3 localllm.py --port {port + 1} ...")
                sys.exit(1)
            raise
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


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Local LLM server for Claude Code (Anthropic Messages API, MLX)"
    )
    parser.add_argument(
        "--model", required=True,
        help="HuggingFace model ID (e.g. mlx-community/Qwen2.5-Coder-32B-Instruct-4bit)"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})"
    )
    args = parser.parse_args()

    # Create and load backend (mlx-lm guaranteed by _ensure_deps)
    backend = MLXBackend()
    backend.load(args.model)

    # Start server
    server = ThreadedServer(("127.0.0.1", args.port), LocalLLMHandler)
    server.backend = backend
    server.backend_name = "mlx"
    server.model_name = args.model

    print()
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │  localllm — MLX local inference server      │")
    print(f"  ├─────────────────────────────────────────────┤")
    print(f"  │  Model:    {args.model[:33]:<33}│")
    print(f"  │  Endpoint: http://127.0.0.1:{args.port:<18}│")
    print(f"  ├─────────────────────────────────────────────┤")
    print(f"  │  claude --base-url http://127.0.0.1:{args.port:<7}│")
    print(f"  └─────────────────────────────────────────────┘")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[localllm] Shutting down.")
        server.server_close()


if __name__ == "__main__":
    _ensure_deps()
    main()
