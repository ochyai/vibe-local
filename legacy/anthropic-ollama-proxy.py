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

# Force unbuffered stdout so print() appears immediately in log files
if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, write_through=True, line_buffering=True)

print("[proxy] === PROXY MODULE LOADED (with WebSearch interception) ===", file=sys.stderr)

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
PROXY_PORT = int(os.environ.get("VIBE_LOCAL_PROXY_PORT", "8082"))

# [SEC] Validate OLLAMA_HOST to prevent SSRF - only allow localhost targets
def _validate_ollama_host(url):
    """Ensure OLLAMA_HOST points to localhost only (SSRF prevention)."""
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    allowed = {"localhost", "127.0.0.1", "::1", "[::1]"}
    if hostname not in allowed:
        print(f"[proxy] SECURITY: OLLAMA_HOST '{hostname}' is not localhost. "
              f"Only localhost/127.0.0.1/::1 are allowed.", file=sys.stderr)
        print(f"[proxy] SECURITY: Falling back to http://localhost:11434", file=sys.stderr)
        return "http://localhost:11434"
    return url

OLLAMA_BASE = _validate_ollama_host(OLLAMA_BASE)

# --- Model routing ---
# Map Claude model patterns to local Ollama models
# Main model: for full coding tasks (tool use, long context)
MAIN_MODEL = os.environ.get("VIBE_LOCAL_MODEL", "qwen3-coder:30b")
# Sidecar model: for lightweight tasks (permission checks, summaries, init probes)
SIDECAR_MODEL = os.environ.get("VIBE_LOCAL_SIDECAR_MODEL", "qwen3:8b")

# Model routing rules: (pattern_substring -> ollama_model)
# Checked in order; first match wins. Default: MAIN_MODEL.
MODEL_ROUTES = [
    ("haiku", SIDECAR_MODEL),        # claude-haiku-* -> fast local
    ("flash", SIDECAR_MODEL),        # gemini-flash style -> fast local
    ("mini", SIDECAR_MODEL),         # gpt-4o-mini style -> fast local
]


def _resolve_model(requested_model, has_tools=False, message_count=0, max_tokens=4096):
    """Route a Claude/API model name to a local Ollama model.
    Lightweight requests (no tools, few messages, low max_tokens) use sidecar."""
    # Check explicit model routes first
    req_lower = requested_model.lower()
    for pattern, target in MODEL_ROUTES:
        if pattern in req_lower:
            return target, True  # (model, is_sidecar)

    # Heuristic: init probes (max_tokens==1, no tools, 0-1 messages) -> sidecar
    if max_tokens == 1 and not has_tools and message_count <= 1:
        return SIDECAR_MODEL, True

    # Default: main model for everything else
    return MAIN_MODEL, False
# [H3/L4 fix] Log to user-private directory with restricted permissions
if os.name == "nt":
    # Windows: use %LOCALAPPDATA%\vibe-local\proxy-debug
    _appdata = os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    LOG_DIR = os.path.join(_appdata, "vibe-local", "proxy-debug")
else:
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
MAX_TOKENS_CAP = 8192
# Sidecar model max tokens (permission checks etc. need very short responses)
SIDECAR_MAX_TOKENS = 1024
# Sidecar timeout (seconds) - shorter since these should be fast
SIDECAR_TIMEOUT = 60

# System prompt max length (chars). Claude Code sends ~15K which overwhelms 30B models.
SYSTEM_PROMPT_MAX_CHARS = 4000

# Curated local model system prompt (replaces truncated Claude Code system prompt)
LOCAL_SYSTEM_PROMPT = """You are a helpful coding assistant. You EXECUTE tasks using tools and explain results clearly.

CORE RULES:
1. TOOL FIRST. Call a tool immediately — no explanation before the tool call.
2. After tool result: give a clear, concise summary (2-3 sentences). No bullet points or numbered lists.
3. NEVER end with a question like "何か必要ですか？". Just finish and wait.
4. NEVER say "I cannot" — always try with a tool first.
5. NEVER tell the user to run a command. YOU run it with Bash.
6. If a tool fails, try a different approach. NEVER give up.
7. Install dependencies BEFORE running: Bash(pip3 install X) first, THEN Bash(python3 script.py).
8. Scripts using input()/stdin CANNOT run in Bash (gets EOFError). Write GUI versions (HTML/JS or pygame) instead.
9. For GUI apps, prefer HTML/JS (open in browser) over pygame/tkinter.
10. NEVER use sudo unless the user explicitly asks.
11. Reply in the SAME language as the user's message. Never mix languages.
12. In Bash, ALWAYS quote URLs with single quotes: curl 'https://example.com/path?key=val'
13. NEVER fabricate URLs, search results, or sources. If a search returns no results, say so honestly.
14. For large downloads/installs (MacTeX, Xcode, etc.), warn the user about size and time BEFORE starting.
15. If a task is taking long, keep the user informed with progress updates.

WRONG: "回線速度を測定するには専用のツールが必要です。インストールしてみますか？"
RIGHT: [immediately call Bash(speedtest --simple) or curl speed test]

WRONG: "以下のコマンドをターミナルで実行してください: python3 game.py"
RIGHT: [call Bash(python3 /absolute/path/game.py)]

WRONG: "何か特定の操作が必要ですか？"
RIGHT: [finish your response, wait silently]

WRONG: "調べた結果、以下のトレンドがあります：1. ... 2. ... Sources: https://fake-url.org"
RIGHT: "検索結果が取得できませんでした。オフライン環境ではWeb検索が制限されます。"

Tools:
- Bash: Run commands (ls, git, npm, pip3, python3, curl, brew, open...)
- Read: Read files (NOT cat/head/tail)
- Write: Create files (ALWAYS use absolute paths)
- Edit: Modify existing files (old_string must match exactly)
- Glob: Find files by pattern (NOT find command)
- Grep: Search file contents (NOT grep/rg command)
- WebFetch: Fetch a specific URL's content
- WebSearch: Search the web (may not work offline)

Research tips: If WebSearch fails (offline), use Bash(curl -s 'URL') to fetch specific pages, or search local files with Grep/Glob.
Speed test: Bash(curl -o /dev/null -s -w '%{speed_download}' 'https://speed.cloudflare.com/__down?bytes=10000000')
"""


def _extract_environment_info(sys_text):
    """Extract # Environment section from Claude Code's system prompt."""
    env_info = {}
    # Look for environment section
    env_start = sys_text.find("# Environment")
    if env_start < 0:
        return env_info
    env_block = sys_text[env_start:]
    # Extract key fields
    for line in env_block.split("\n"):
        line = line.strip().lstrip("- ")
        if "working directory:" in line.lower():
            env_info["cwd"] = line.split(":", 1)[1].strip()
        elif "platform:" in line.lower():
            env_info["platform"] = line.split(":", 1)[1].strip()
        elif "shell:" in line.lower():
            env_info["shell"] = line.split(":", 1)[1].strip()
        elif "os version:" in line.lower():
            env_info["os_version"] = line.split(":", 1)[1].strip()
    return env_info

# Essential tools only - drop tools that confuse local models
# (Task, TaskOutput, AskUserQuestion, EnterPlanMode, ExitPlanMode etc.)
ALLOWED_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "NotebookEdit",
}
# Set to None to disable tool filtering
# ALLOWED_TOOLS = None


def _open_private(path, mode="w"):
    """Open a file with restricted permissions (owner-only on Unix)."""
    if os.name != "nt":
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        return os.fdopen(fd, mode)
    return open(path, mode)


def _log(tag, data, req_id=None):
    """Write metadata log (always active). Uses session directory with optional req_id prefix."""
    ts = datetime.datetime.now().strftime("%H%M%S")
    prefix = f"{req_id:04d}_{ts}" if req_id else ts
    path = os.path.join(SESSION_DIR, f"{prefix}_{tag}.json")
    try:
        with _open_private(path) as f:
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
        with _open_private(path) as f:
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
        with _open_private(body_path) as f:
            json.dump(req, f, ensure_ascii=False, indent=2)
    except Exception:
        return

    # Save curl script
    script_path = os.path.join(SESSION_DIR, f"{req_id:04d}_{ts}_replay.sh")
    try:
        with _open_private(script_path) as f:
            f.write(f"""#!/bin/bash
# Replay request #{req_id:04d}
curl -X POST \\
  http://127.0.0.1:{proxy_port}/v1/messages \\
  -H 'Content-Type: application/json' \\
  -H 'x-api-key: local' \\
  -H 'anthropic-version: 2023-06-01' \\
  -d @./{body_filename}
""")
        try:
            os.chmod(script_path, 0o700)
        except OSError:
            pass  # Windows may not support Unix permissions
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


def _ddg_search(query, max_results=8):
    """Search DuckDuckGo HTML endpoint. Zero dependencies (stdlib only).
    Returns list of {"title": str, "url": str, "snippet": str}."""
    search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(search_url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[proxy][websearch] DuckDuckGo fetch failed: {e}", file=sys.stderr)
        return None  # None = network error (distinct from empty results [])

    results = []
    # DuckDuckGo HTML results: <a class="result__a" href="...">Title</a>
    # Snippets: <a class="result__snippet" ...>Snippet text</a>
    # URLs may be DDG redirects: //duckduckgo.com/l/?uddg=ENCODED_URL&...
    link_pat = re.compile(
        r'<a\s+[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pat = re.compile(
        r'<a\s+[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pat.findall(html)
    snippets = snippet_pat.findall(html)

    for i, (raw_url, raw_title) in enumerate(links[:max_results + 5]):
        # Clean HTML tags from title
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        if not title:
            continue

        # Extract real URL from DDG redirect
        url = raw_url
        if "uddg=" in url:
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                url = urllib.parse.unquote(m.group(1))
        elif url.startswith("//"):
            url = "https:" + url

        # Skip ad results (DDG ad tracker URLs)
        if "/y.js?" in url or "ad_provider" in url or "duckduckgo.com/y.js" in url:
            continue

        # Get snippet if available
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    print(f"[proxy][websearch] DDG returned {len(results)} results for: {query}")
    return results


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
    # NOTE: _current_tool_names is set per-request in _handle_messages()
    # to avoid thread-safety issues with concurrent requests

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
                # [SEC] Log full error internally, return generic message
                print(f"[proxy] /v1/models error: {e}", file=sys.stderr)
                self._respond(502, {"error": "failed to fetch models from ollama"})
        else:
            # [SEC] Don't reflect user-supplied path in response (XSS prevention)
            self._respond(404, {"error": "not found"})

    # Max request body size (50 MB) to prevent abuse
    MAX_REQUEST_BYTES = 50 * 1024 * 1024

    def do_POST(self):
        path = self._parse_path()
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0
        if content_length < 0:
            content_length = 0
        if content_length > self.MAX_REQUEST_BYTES:
            self._respond(413, {"error": f"request too large: {content_length} bytes (max {self.MAX_REQUEST_BYTES})"})
            return
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
            # [SEC] Don't reflect user-supplied path in response
            self._respond(404, {"error": "unknown endpoint"})

    def _handle_web_search(self, req, req_id, t_start):
        """Handle WebSearch sidecar call: search DuckDuckGo, return results
        in Anthropic web_search_tool_result format so CLI counts searches."""
        # Extract query from user message
        messages = req.get("messages", [])
        query = ""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text.startswith("Perform a web search for the query: "):
                                query = text[len("Perform a web search for the query: "):].strip()
                            else:
                                query = text.strip()
                elif isinstance(content, str):
                    if content.startswith("Perform a web search for the query: "):
                        query = content[len("Perform a web search for the query: "):].strip()
                    else:
                        query = content.strip()

        if not query:
            query = "test"

        print(f"[proxy][websearch] === INTERCEPTED WebSearch sidecar ===", file=sys.stderr)
        print(f"[proxy][websearch] Query: {query}", file=sys.stderr)
        _log("websearch_intercept", {"query": query}, req_id=req_id)

        # Perform DuckDuckGo search
        try:
            results = _ddg_search(query)
        except Exception as e:
            print(f"[proxy][websearch] DDG search exception: {e}", file=sys.stderr)
            traceback.print_exc()
            results = None

        result_count = len(results) if results else 0

        elapsed_ms = int((time.time() - t_start) * 1000)
        _log("websearch_response", {
            "query": query,
            "result_count": result_count,
            "elapsed_ms": elapsed_ms,
        }, req_id=req_id)

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        model = req.get("model", "claude-haiku-4-5-20251001")
        srvtool_id = f"srvtoolu_{uuid.uuid4().hex[:24]}"

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
                "usage": {"input_tokens": 100, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        })

        # Block 0: server_tool_use (tells CLI a web search was performed)
        self._send_sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {
                "type": "server_tool_use", "id": srvtool_id,
                "name": "web_search", "input": {"query": query},
            },
        })
        self._send_sse("content_block_stop", {"type": "content_block_stop", "index": 0})

        # Block 1: web_search_tool_result (actual search results)
        if results is None:
            # Offline / error
            tool_result_content = [{
                "type": "web_search_result",
                "url": "", "title": "Search unavailable (offline)",
                "page_age": "",
                "snippet": f'Web search failed for "{query}". Try Bash(curl "URL") instead.',
            }]
        elif result_count == 0:
            tool_result_content = [{
                "type": "web_search_result",
                "url": "", "title": "No results",
                "page_age": "",
                "snippet": f'No web search results found for "{query}".',
            }]
        else:
            tool_result_content = []
            for r in results:
                tool_result_content.append({
                    "type": "web_search_result",
                    "url": r["url"],
                    "title": r["title"],
                    "page_age": "",
                    "snippet": r.get("snippet", ""),
                    "encrypted_content": "",
                })

        self._send_sse("content_block_start", {
            "type": "content_block_start", "index": 1,
            "content_block": {
                "type": "web_search_tool_result",
                "tool_use_id": srvtool_id,
                "content": tool_result_content,
            },
        })
        self._send_sse("content_block_stop", {"type": "content_block_stop", "index": 1})

        self._send_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": result_count * 20},
        })
        self._send_sse("message_stop", {"type": "message_stop"})
        self.wfile.flush()

        print(f"[proxy][websearch] Done in {elapsed_ms}ms, {result_count} results (web_search_tool_result format)", file=sys.stderr)

    def _handle_messages(self, req):
        req_id = _next_request_id()
        t_start = time.time()

        # Log every request to stderr (unbuffered) for debugging
        _tc = req.get("tool_choice", None)
        _tl = req.get("tools", None)
        print(f"[proxy] REQ#{req_id} model={req.get('model','?')} "
              f"tools={'None' if _tl is None else len(_tl)} "
              f"tc_name={_tc.get('name','') if isinstance(_tc, dict) else str(_tc)} "
              f"stream={req.get('stream', False)}", file=sys.stderr)

        # --- WebSearch sidecar interception ---
        # Detect: tool_choice.name == "web_search" (regardless of tools array)
        tool_choice = req.get("tool_choice")
        if isinstance(tool_choice, dict) and tool_choice.get("name") == "web_search":
            print(f"[proxy] >>> WEBSEARCH INTERCEPTED req#{req_id} <<<", file=sys.stderr)
            return self._handle_web_search(req, req_id, t_start)

        # --- A1: Init probe fast-path ---
        # Claude Code sends a probe with max_tokens=1, <=1 message, no tools to test connectivity.
        # Respond instantly without hitting Ollama to save 2-5 seconds.
        messages = req.get("messages", [])
        tools_list_raw = req.get("tools", [])
        if (req.get("max_tokens", 4096) == 1
                and len(messages) <= 1
                and not tools_list_raw):
            print(f"[proxy] >>> INIT PROBE FAST-PATH req#{req_id} <<<", file=sys.stderr)
            _log("init_probe", {"req_id": req_id}, req_id=req_id)
            stream = req.get("stream", False)
            probe_model = req.get("model", "qwen3-coder:30b")
            msg_id = f"msg_{uuid.uuid4().hex[:24]}"
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self._send_sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": msg_id, "type": "message", "role": "assistant",
                        "content": [], "model": probe_model,
                        "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0,
                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                    },
                })
                self._send_sse("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                self._send_sse("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": ""},
                })
                self._send_sse("content_block_stop", {"type": "content_block_stop", "index": 0})
                self._send_sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 0},
                })
                self._send_sse("message_stop", {"type": "message_stop"})
                self.wfile.flush()
            else:
                self._respond(200, {
                    "id": msg_id, "type": "message", "role": "assistant",
                    "content": [{"type": "text", "text": ""}],
                    "model": probe_model,
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                })
            elapsed_ms = int((time.time() - t_start) * 1000)
            print(f"[proxy] Init probe responded in {elapsed_ms}ms", file=sys.stderr)
            return

        requested_model = req.get("model", "qwen3-coder:30b")
        # messages and tools_list_raw already extracted above for init probe check
        system = req.get("system", "")
        max_tokens = min(req.get("max_tokens", 4096), MAX_TOKENS_CAP)
        temperature = req.get("temperature", 0.7)
        stop_sequences = req.get("stop_sequences", None)
        top_p = req.get("top_p", None)
        top_k = req.get("top_k", None)
        stream = req.get("stream", False)
        original_stream = stream
        tools_list = tools_list_raw

        # Resolve model: route to appropriate local model
        model, is_sidecar = _resolve_model(
            requested_model,
            has_tools=bool(tools_list),
            message_count=len(messages),
            max_tokens=max_tokens,
        )
        if model != requested_model:
            print(f"[proxy] Model routed: {requested_model} -> {model} {'(sidecar)' if is_sidecar else ''}")

        # Sidecar optimization: cap max_tokens lower for fast responses
        if is_sidecar:
            max_tokens = min(max_tokens, SIDECAR_MAX_TOKENS)

        # Filter tools to essential ones only
        if ALLOWED_TOOLS is not None and tools_list:
            original_tool_count = len(tools_list)
            tools_list = [t for t in tools_list if t.get("name", "") in ALLOWED_TOOLS]
            if len(tools_list) != original_tool_count:
                print(f"[proxy] Filtered tools: {original_tool_count} -> {len(tools_list)}")
            req["tools"] = tools_list

        has_tools = bool(tools_list)
        current_tool_names = [t.get("name", "") for t in tools_list]

        _log("req_meta", {
            "requested_model": requested_model,
            "resolved_model": model,
            "is_sidecar": is_sidecar,
            "stream": stream, "has_tools": has_tools,
            "tool_count": len(tools_list),
            "tool_names": current_tool_names[:20],
            "message_count": len(messages),
            "system_length": len(str(system)),
            "max_tokens": max_tokens,
            "max_tokens_original": req.get("max_tokens", 4096),
            "env_injected": False,  # updated below if injected
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

            # Replace overly long system prompts with curated local version
            original_sys_len = len(sys_text)
            if original_sys_len > SYSTEM_PROMPT_MAX_CHARS:
                # Extract environment info BEFORE replacing (critical for OS awareness)
                env_info = _extract_environment_info(sys_text)

                # Extract any CLAUDE.md / user instructions from the original prompt
                user_instructions = ""
                for marker in ["# claudeMd", "Contents of", "CLAUDE.md"]:
                    idx = sys_text.find(marker)
                    if idx >= 0:
                        end_idx = len(sys_text)
                        for end_marker in ["\n# Environment", "\n# System", "\n<fast_mode"]:
                            eidx = sys_text.find(end_marker, idx)
                            if eidx > idx:
                                end_idx = min(end_idx, eidx)
                        user_instructions = sys_text[idx:end_idx].strip()
                        break

                sys_text = LOCAL_SYSTEM_PROMPT

                # Inject environment info (prevents Linux assumption on macOS)
                if env_info:
                    env_block = "\n# Environment\n"
                    if env_info.get("cwd"):
                        env_block += f"- Working directory: {env_info['cwd']}\n"
                    if env_info.get("platform"):
                        env_block += f"- Platform: {env_info['platform']}\n"
                    if env_info.get("os_version"):
                        env_block += f"- OS: {env_info['os_version']}\n"
                    if env_info.get("shell"):
                        env_block += f"- Shell: {env_info['shell']}\n"
                    # Add explicit OS guidance with command mapping
                    platform = env_info.get("platform", "").lower()
                    if "darwin" in platform:
                        env_block += "\nIMPORTANT — This is macOS (NOT Linux):\n"
                        env_block += "- Home directory: /Users/ (NEVER /home/)\n"
                        env_block += "- Package manager: brew (NEVER apt, yum, apt-get)\n"
                        env_block += "- USB devices: system_profiler SPUSBDataType (NEVER lsusb)\n"
                        env_block += "- Hardware info: system_profiler (NEVER lshw, lspci)\n"
                        env_block += "- Network speed: brew install speedtest-cli && speedtest (NEVER sudo apt)\n"
                        env_block += "- Disk info: diskutil list (NEVER fdisk, lsblk)\n"
                        env_block += "- Process: ps aux, Activity Monitor (NEVER /proc/)\n"
                        env_block += "- FORBIDDEN on macOS: apt, yum, dmesg, lsusb, lshw, lspci, /proc/, /home/\n"
                    elif "linux" in platform:
                        env_block += "- This is Linux. Home directory: /home/\n"
                    elif "win" in platform or "windows" in env_info.get("os_version", "").lower():
                        env_block += "\nIMPORTANT — This is Windows (NOT Linux/macOS):\n"
                        env_block += "- Home directory: %USERPROFILE% (e.g. C:\\Users\\username)\n"
                        env_block += "- Package manager: winget (NEVER apt, brew, yum)\n"
                        env_block += "- Shell: PowerShell or cmd.exe\n"
                        env_block += "- Use: Get-ChildItem (dir), Get-Content (type), Remove-Item (del)\n"
                        env_block += "- Paths use backslash: C:\\Users\\... (NEVER /home/)\n"
                        env_block += "- FORBIDDEN on Windows: apt, brew, /home/, /proc/, chmod\n"
                    sys_text += env_block

                if user_instructions:
                    max_user = SYSTEM_PROMPT_MAX_CHARS - len(sys_text) - 50
                    if len(user_instructions) > max_user > 0:
                        user_instructions = user_instructions[:max_user] + "\n...(truncated)"
                    sys_text += "\n# Project Instructions\n" + user_instructions + "\n"
                # Update req_meta with env injection status
                _log("env_status", {
                    "env_injected": bool(env_info),
                    "platform": env_info.get("platform", ""),
                    "cwd": env_info.get("cwd", ""),
                    "prompt_len_before": original_sys_len,
                    "prompt_len_after": len(sys_text),
                    "has_user_instructions": bool(user_instructions),
                }, req_id=req_id)
                print(f"[proxy] System prompt replaced: {original_sys_len} -> {len(sys_text)} chars (env: {bool(env_info)})")

            if has_tools:
                tool_names_str = ", ".join(current_tool_names[:15])
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
                        elif btype == "image":
                            # A6: Convert Anthropic image format to OpenAI image_url data URI
                            source = block.get("source", {})
                            src_type = source.get("type", "")
                            if src_type == "base64":
                                media_type = source.get("media_type", "image/png")
                                data = source.get("data", "")
                                if data:
                                    text_parts.append({
                                        "type": "image_url",
                                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                                    })
                            elif src_type == "url":
                                url = source.get("url", "")
                                if url:
                                    text_parts.append({
                                        "type": "image_url",
                                        "image_url": {"url": url},
                                    })
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
                    text_only = [p for p in text_parts if isinstance(p, str)]
                    assistant_msg = {"role": "assistant", "content": "\n".join(text_only) if text_only else None}
                    assistant_msg["tool_calls"] = tool_calls_out
                    oai_messages.append(assistant_msg)
                    continue

                # A6: If any part is a dict (image), use OpenAI multimodal content array
                has_images = any(isinstance(p, dict) for p in text_parts)
                if has_images:
                    oai_content = []
                    for p in text_parts:
                        if isinstance(p, str):
                            if p:
                                oai_content.append({"type": "text", "text": p})
                        else:
                            oai_content.append(p)  # already in OpenAI format
                    oai_messages.append({"role": role, "content": oai_content})
                else:
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
                # A2: Convert Anthropic tool_choice format to OpenAI format
                tc = req.get("tool_choice")
                if isinstance(tc, dict):
                    tc_type = tc.get("type", "auto")
                    if tc_type == "any":
                        oai_req["tool_choice"] = "required"
                    elif tc_type == "none":
                        oai_req["tool_choice"] = "none"
                    elif tc_type == "tool":
                        oai_req["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}
                    else:
                        oai_req["tool_choice"] = "auto"
                else:
                    oai_req["tool_choice"] = "auto"

        # A3: Forward stop_sequences as "stop"
        if stop_sequences:
            oai_req["stop"] = stop_sequences

        # A4: Forward top_p and top_k
        if top_p is not None:
            oai_req["top_p"] = top_p
        if top_k is not None:
            oai_req["top_k"] = top_k

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

        timeout = SIDECAR_TIMEOUT if is_sidecar else 300

        try:
            oai_body = json.dumps(oai_req).encode("utf-8")
            oai_request = urllib.request.Request(
                f"{OLLAMA_BASE}/v1/chat/completions",
                data=oai_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            if stream:
                self._handle_stream(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages), timeout=timeout)
            elif original_stream:
                self._handle_sync_as_sse(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages), timeout=timeout, tool_names=current_tool_names)
            else:
                self._handle_sync(oai_request, model, req_id=req_id, t_start=t_start, msg_count=len(messages), timeout=timeout, tool_names=current_tool_names)

        except urllib.error.URLError as e:
            elapsed_ms = int((time.time() - t_start) * 1000)
            _debug_summary(req_id, model, len(messages), "sync", elapsed_ms, False)
            # [SEC] Log full error internally, return generic message to client
            print(f"[proxy] URLError: {e}", file=sys.stderr)
            self._respond(502, {
                "type": "error",
                "error": {"type": "api_error", "message": "ollama connection failed"},
            })
        except Exception as e:
            elapsed_ms = int((time.time() - t_start) * 1000)
            _debug_summary(req_id, model, len(messages), "sync", elapsed_ms, False)
            traceback.print_exc()
            # [SEC] Don't leak internal error details to client
            self._respond(500, {
                "type": "error",
                "error": {"type": "api_error", "message": "internal proxy error"},
            })

    def _process_ollama_response(self, oai_resp, current_tool_names=None):
        """Process ollama response, extract XML tool calls from text if needed."""
        choice = oai_resp.get("choices", [{}])[0]
        message = choice.get("message", {})
        content_text = message.get("content", "") or ""
        reasoning_text = message.get("reasoning", "") or ""
        tool_calls = message.get("tool_calls", [])
        finish_reason = choice.get("finish_reason", "end_turn")

        if not tool_calls and content_text and current_tool_names:
            extracted, cleaned = _extract_tool_calls_from_text(
                content_text, current_tool_names
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

    def _handle_sync(self, oai_request, model, req_id=0, t_start=None, msg_count=0, timeout=300, tool_names=None):
        resp = urllib.request.urlopen(oai_request, timeout=timeout)
        oai_resp = json.loads(resp.read())
        # [H3 fix] Log metadata only, not full response content
        _log("resp_from_ollama_sync_meta", {
            "model": oai_resp.get("model"),
            "usage": oai_resp.get("usage"),
            "choices_count": len(oai_resp.get("choices", [])),
        }, req_id=req_id)

        # Debug: log full Ollama response
        _debug_log(req_id, "ollama_response_full", oai_resp)

        content_text, reasoning_text, tool_calls, finish_reason = self._process_ollama_response(oai_resp, current_tool_names=tool_names)

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

    def _handle_sync_as_sse(self, oai_request, model, req_id=0, t_start=None, msg_count=0, timeout=300, tool_names=None):
        resp = urllib.request.urlopen(oai_request, timeout=timeout)
        oai_resp = json.loads(resp.read())
        _log("resp_from_ollama_sse_meta", {
            "model": oai_resp.get("model"),
            "usage": oai_resp.get("usage"),
            "choices_count": len(oai_resp.get("choices", [])),
        }, req_id=req_id)

        # Debug: log full Ollama response
        _debug_log(req_id, "ollama_response_full", oai_resp)

        content_text, reasoning_text, tool_calls, finish_reason = self._process_ollama_response(oai_resp, current_tool_names=tool_names)

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

    def _handle_stream(self, oai_request, model, req_id=0, t_start=None, msg_count=0, timeout=300):
        resp = urllib.request.urlopen(oai_request, timeout=timeout)
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
        # Debug: accumulate full stream content
        accumulated_reasoning = []
        accumulated_content = []

        # Buffered SSE reading (much faster than byte-by-byte)
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    buf = b""  # signal done
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
            else:
                continue
            break  # [DONE] was received

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
        # Collect all text for tokenization
        all_text = []
        messages = req.get("messages", [])
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        all_text.append(block.get("text", ""))
            else:
                all_text.append(str(content))
        system = req.get("system", "")
        if system:
            if isinstance(system, list):
                for block in system:
                    if isinstance(block, dict):
                        all_text.append(block.get("text", ""))
            else:
                all_text.append(str(system))

        combined = "\n".join(all_text)

        # A5: Try Ollama /api/tokenize for accurate count, fallback to len//4
        total = None
        try:
            tok_body = json.dumps({"model": MAIN_MODEL, "text": combined}).encode("utf-8")
            tok_req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/tokenize",
                data=tok_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            tok_resp = urllib.request.urlopen(tok_req, timeout=5)
            tok_data = json.loads(tok_resp.read())
            tokens = tok_data.get("tokens", None)
            if tokens is not None:
                total = len(tokens)
                print(f"[proxy] Token count via Ollama tokenize: {total}")
        except Exception:
            pass  # Ollama may not support /api/tokenize

        if total is None:
            total = len(combined) // 4
            print(f"[proxy] Token count via len//4 fallback: {total}")

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
    print(f"[proxy] Main model: {MAIN_MODEL}")
    print(f"[proxy] Sidecar model: {SIDECAR_MODEL}")
    print(f"[proxy] Model routes: {len(MODEL_ROUTES)} rules")
    print(f"[proxy] Log dir: {LOG_DIR}")
    print(f"[proxy] Session: {os.path.basename(SESSION_DIR)}")
    print(f"[proxy] Debug mode: {'ON' if DEBUG_MODE else 'OFF'}")
    print(f"[proxy] XML tool call fallback: enabled")
    print(f"[proxy] Ctrl+C to stop")

    # Cleanup old sessions on startup
    _cleanup_old_sessions(7)

    # Warmup: preload both models into VRAM to avoid cold-start latency
    def _warmup():
        for m in set([MAIN_MODEL, SIDECAR_MODEL]):
            try:
                body = json.dumps({"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1, "stream": False}).encode()
                req = urllib.request.Request(f"{OLLAMA_BASE}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=120)
                print(f"[proxy] Warmup OK: {m}")
            except Exception as e:
                print(f"[proxy] Warmup failed for {m}: {e}")
    warmup_thread = threading.Thread(target=_warmup, daemon=True)
    warmup_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] stopped")
        server.server_close()


if __name__ == "__main__":
    main()
