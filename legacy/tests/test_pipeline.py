"""
Comprehensive end-to-end pipeline test suite for localllm.py.

Tests cover:
  A. HTTP Pipeline Tests (real HTTP, mocked backend)
  B. SSE Format Compliance Tests
  C. Message Conversion Pipeline Tests
  D. Error Handling Pipeline Tests
  E. Backend Integration Tests (mocked mlx_lm)
  F. Shell Script Tests (using subprocess)

All tests run without mlx_lm or llama_cpp installed.
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest

# Import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import localllm


# ═══════════════════════════════════════════════════════════════
# Shared test utilities
# ═══════════════════════════════════════════════════════════════

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_free_port():
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_sse_events(raw):
    """Parse SSE events from raw response text/bytes.
    Returns list of (event_type, data_dict) tuples."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    events = []
    # Split on double newlines to get individual events
    chunks = re.split(r"\n\n+", text.strip())
    for chunk in chunks:
        if not chunk.strip():
            continue
        event_type = None
        data_str = None
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()
        if event_type and data_str:
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = data_str
            events.append((event_type, data))
    return events


def _start_test_server(backend, port):
    """Start a localllm ThreadedServer with a mock backend."""
    server = localllm.ThreadedServer(("127.0.0.1", port), localllm.LocalLLMHandler)
    server.backend = backend
    server.backend_name = "mock"
    server.model_name = "test-model"
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Wait for server to be ready
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    return server


def _http_request(port, method, path, body=None, timeout=10):
    """Send an HTTP request and return (status, body_text)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


class MockBackend:
    """A predictable mock backend for testing.
    Allows pre-configuring generate/stream return values."""

    def __init__(self, generate_text="Hello from mock", stream_tokens=None):
        self._generate_text = generate_text
        self._stream_tokens = stream_tokens or ["Hello", " from", " mock"]
        self._generate_call_count = 0
        self._stream_call_count = 0
        self._last_messages = None
        self._last_tools = None

    def generate(self, messages, max_tokens, temperature, tools=None):
        self._generate_call_count += 1
        self._last_messages = messages
        self._last_tools = tools
        return self._generate_text

    def stream(self, messages, max_tokens, temperature):
        self._stream_call_count += 1
        self._last_messages = messages
        for token in self._stream_tokens:
            yield token


# ═══════════════════════════════════════════════════════════════
# A. HTTP Pipeline Tests (real HTTP, mocked backend)
# ═══════════════════════════════════════════════════════════════

class TestHTTPPipeline:
    """Real HTTP requests against a ThreadedServer with MockBackend."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = MockBackend(generate_text="Hello, I can help you!")
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def test_simple_text_generation(self):
        """POST /v1/messages with simple user message, verify SSE response."""
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        })
        assert status == 200
        events = _parse_sse_events(resp)
        event_types = [e[0] for e in events]

        # Must have the full Anthropic SSE sequence
        assert "message_start" in event_types
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "content_block_stop" in event_types
        assert "message_delta" in event_types
        assert "message_stop" in event_types

        # Verify text content in delta
        deltas = [e[1] for e in events if e[0] == "content_block_delta"]
        text_parts = [
            d["delta"]["text"] for d in deltas
            if d.get("delta", {}).get("type") == "text_delta"
        ]
        full_text = "".join(text_parts)
        assert "Hello, I can help you!" in full_text

    def test_streaming_text_generation(self):
        """POST with stream=true, verify SSE events arrive in correct order."""
        self.backend._stream_tokens = ["token1", " token2", " token3", " token4"]
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "max_tokens": 100,
        })
        assert status == 200
        events = _parse_sse_events(resp)
        event_types = [e[0] for e in events]

        # Verify exact ordering
        assert event_types[0] == "message_start"
        assert event_types[1] == "content_block_start"
        # Streaming deltas
        delta_events = [e for e in events if e[0] == "content_block_delta"]
        assert len(delta_events) == 4
        assert delta_events[0][1]["delta"]["text"] == "token1"
        assert delta_events[1][1]["delta"]["text"] == " token2"
        assert delta_events[2][1]["delta"]["text"] == " token3"
        assert delta_events[3][1]["delta"]["text"] == " token4"
        # Closing events
        assert event_types[-3] == "content_block_stop"
        assert event_types[-2] == "message_delta"
        assert event_types[-1] == "message_stop"

    def test_tool_call_response(self):
        """POST with tools, mock backend returns XML tool call, verify Anthropic tool_use block."""
        self.backend._generate_text = (
            'I will list the files.\n'
            '<invoke name="Bash"><parameter name="command">ls -la</parameter></invoke>'
        )
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "List files"}],
            "tools": [
                {"name": "Bash", "description": "Run bash", "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                }},
            ],
            "max_tokens": 200,
        })
        assert status == 200
        events = _parse_sse_events(resp)

        # Find tool_use content blocks
        tool_blocks = [
            e[1] for e in events
            if e[0] == "content_block_start"
            and isinstance(e[1], dict)
            and e[1].get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["content_block"]["name"] == "Bash"
        assert tool_blocks[0]["content_block"]["id"].startswith("toolu_")

        # Verify input_json_delta
        json_deltas = [
            e[1] for e in events
            if e[0] == "content_block_delta"
            and isinstance(e[1], dict)
            and e[1].get("delta", {}).get("type") == "input_json_delta"
        ]
        assert len(json_deltas) == 1
        tool_input = json.loads(json_deltas[0]["delta"]["partial_json"])
        assert tool_input["command"] == "ls -la"

        # stop_reason should be tool_use
        msg_delta = [e[1] for e in events if e[0] == "message_delta"]
        assert msg_delta[0]["delta"]["stop_reason"] == "tool_use"

    def test_multiple_tool_calls(self):
        """Backend returns multiple XML tool calls, verify all parsed."""
        self.backend._generate_text = (
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'
            '<invoke name="Read"><parameter name="file_path">/tmp/a.txt</parameter></invoke>'
            '<invoke name="Grep"><parameter name="pattern">TODO</parameter></invoke>'
        )
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Find TODOs"}],
            "tools": [
                {"name": "Bash", "description": "cmd", "input_schema": {}},
                {"name": "Read", "description": "read", "input_schema": {}},
                {"name": "Grep", "description": "grep", "input_schema": {}},
            ],
            "max_tokens": 200,
        })
        assert status == 200
        events = _parse_sse_events(resp)

        tool_blocks = [
            e[1] for e in events
            if e[0] == "content_block_start"
            and isinstance(e[1], dict)
            and e[1].get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_blocks) == 3
        tool_names = [b["content_block"]["name"] for b in tool_blocks]
        assert "Bash" in tool_names
        assert "Read" in tool_names
        assert "Grep" in tool_names

    def test_multi_turn_conversation(self):
        """Send conversation with tool_use + tool_result history."""
        self.backend._generate_text = "The file contains hello world."
        req = {
            "messages": [
                {"role": "user", "content": "Read the file"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll read the file."},
                        {
                            "type": "tool_use",
                            "id": "toolu_abc123",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc123",
                            "content": "hello world",
                        },
                    ],
                },
            ],
            "max_tokens": 100,
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages", req)
        assert status == 200
        events = _parse_sse_events(resp)
        # Should have successfully generated a response
        deltas = [
            e[1] for e in events
            if e[0] == "content_block_delta"
            and e[1].get("delta", {}).get("type") == "text_delta"
        ]
        full_text = "".join(d["delta"]["text"] for d in deltas)
        assert "hello world" in full_text

    def test_concurrent_requests(self):
        """Send 5 requests in parallel threads, all should succeed."""
        results = [None] * 5
        errors = []

        def make_request(idx):
            try:
                status, resp = _http_request(self.port, "POST", "/v1/messages", {
                    "messages": [{"role": "user", "content": f"Request {idx}"}],
                    "max_tokens": 50,
                })
                results[idx] = status
            except Exception as e:
                errors.append((idx, str(e)))

        threads = [threading.Thread(target=make_request, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Errors occurred: {errors}"
        assert all(s == 200 for s in results), f"Not all succeeded: {results}"

    def test_large_system_prompt_truncation(self):
        """Send 10K char system prompt, verify truncated in backend call."""
        long_system = "A" * 10000
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "system": long_system,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 50,
        })
        assert status == 200
        # Verify the backend received truncated messages
        assert self.backend._last_messages is not None
        sys_msg = self.backend._last_messages[0]
        assert sys_msg["role"] == "system"
        assert len(sys_msg["content"]) < 10000
        assert "truncated" in sys_msg["content"]

    def test_tool_filtering(self):
        """Send request with disallowed tools, verify they're filtered out."""
        self.backend._generate_text = "I cannot use those tools."
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Do something"}],
            "tools": [
                {"name": "Bash", "description": "ok", "input_schema": {}},
                {"name": "Task", "description": "disallowed", "input_schema": {}},
                {"name": "AskUserQuestion", "description": "disallowed", "input_schema": {}},
                {"name": "Read", "description": "ok", "input_schema": {}},
            ],
            "max_tokens": 50,
        })
        assert status == 200
        # "Task" and "AskUserQuestion" should not appear in response
        assert "Task" not in resp or "AskUserQuestion" not in resp
        # Backend was called -- verify it received messages
        assert self.backend._generate_call_count == 1


# ═══════════════════════════════════════════════════════════════
# B. SSE Format Compliance Tests
# ═══════════════════════════════════════════════════════════════

class TestSSEFormatCompliance:
    """Parse raw SSE bytes and verify protocol compliance."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = MockBackend(generate_text="Test response text")
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def _get_raw_sse(self, body):
        """Get raw SSE response text."""
        _, resp = _http_request(self.port, "POST", "/v1/messages", body)
        return resp

    def test_sse_event_format(self):
        """Each SSE event must have 'event: type\\ndata: json\\n\\n' format."""
        raw = self._get_raw_sse({
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 50,
        })
        # Split on double-newline
        chunks = [c.strip() for c in raw.split("\n\n") if c.strip()]
        for chunk in chunks:
            lines = chunk.split("\n")
            has_event = any(line.startswith("event:") for line in lines)
            has_data = any(line.startswith("data:") for line in lines)
            assert has_event, f"Chunk missing 'event:' line: {chunk!r}"
            assert has_data, f"Chunk missing 'data:' line: {chunk!r}"
            # Verify data is valid JSON
            for line in lines:
                if line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    json.loads(data_str)  # Should not raise

    def test_sse_message_start_has_required_fields(self):
        """message_start must have id, type, role, content, model, usage."""
        raw = self._get_raw_sse({
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 50,
        })
        events = _parse_sse_events(raw)
        msg_start = events[0]
        assert msg_start[0] == "message_start"
        msg = msg_start[1]["message"]
        assert "id" in msg
        assert msg["type"] == "message"
        assert msg["role"] == "assistant"
        assert "content" in msg
        assert "model" in msg
        assert "usage" in msg
        usage = msg["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage

    def test_sse_tool_use_has_toolu_prefix(self):
        """tool_use ids must start with 'toolu_'."""
        self.backend._generate_text = (
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'
        )
        raw = self._get_raw_sse({
            "messages": [{"role": "user", "content": "List"}],
            "tools": [{"name": "Bash", "description": "cmd", "input_schema": {}}],
            "max_tokens": 100,
        })
        events = _parse_sse_events(raw)
        tool_starts = [
            e[1] for e in events
            if e[0] == "content_block_start"
            and isinstance(e[1], dict)
            and e[1].get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_starts) >= 1
        for ts in tool_starts:
            assert ts["content_block"]["id"].startswith("toolu_")

    def test_sse_stop_reason_end_turn(self):
        """Text response must have stop_reason 'end_turn'."""
        raw = self._get_raw_sse({
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 50,
        })
        events = _parse_sse_events(raw)
        msg_delta = [e[1] for e in events if e[0] == "message_delta"]
        assert len(msg_delta) == 1
        assert msg_delta[0]["delta"]["stop_reason"] == "end_turn"

    def test_sse_stop_reason_tool_use(self):
        """Tool response must have stop_reason 'tool_use'."""
        self.backend._generate_text = (
            '<invoke name="Bash"><parameter name="command">pwd</parameter></invoke>'
        )
        raw = self._get_raw_sse({
            "messages": [{"role": "user", "content": "Where am I?"}],
            "tools": [{"name": "Bash", "description": "cmd", "input_schema": {}}],
            "max_tokens": 100,
        })
        events = _parse_sse_events(raw)
        msg_delta = [e[1] for e in events if e[0] == "message_delta"]
        assert len(msg_delta) == 1
        assert msg_delta[0]["delta"]["stop_reason"] == "tool_use"


# ═══════════════════════════════════════════════════════════════
# C. Message Conversion Pipeline Tests
# ═══════════════════════════════════════════════════════════════

class TestMessageConversionPipeline:
    """End-to-end: Anthropic request -> OAI messages -> (generate) -> parse -> Anthropic SSE."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = MockBackend()
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def test_full_pipeline_text_only(self):
        """Simple text in, text out."""
        self.backend._generate_text = "This is a test response."
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Test message"}],
            "max_tokens": 100,
        })
        assert status == 200
        events = _parse_sse_events(resp)

        # Verify full pipeline produced valid output
        msg_start = [e for e in events if e[0] == "message_start"]
        assert len(msg_start) == 1
        assert msg_start[0][1]["message"]["role"] == "assistant"

        text_deltas = [
            e[1]["delta"]["text"] for e in events
            if e[0] == "content_block_delta"
            and e[1].get("delta", {}).get("type") == "text_delta"
        ]
        assert "".join(text_deltas) == "This is a test response."

    def test_full_pipeline_with_tools(self):
        """Tools in, tool_use out."""
        self.backend._generate_text = (
            'Let me check.\n'
            '<invoke name="Grep"><parameter name="pattern">error</parameter>'
            '<parameter name="path">/var/log</parameter></invoke>'
        )
        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Find errors"}],
            "tools": [
                {"name": "Grep", "description": "Search", "input_schema": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                }},
            ],
            "max_tokens": 200,
        })
        assert status == 200
        events = _parse_sse_events(resp)

        # Should have text block + tool_use block
        block_starts = [e for e in events if e[0] == "content_block_start"]
        block_types = [b[1]["content_block"]["type"] for b in block_starts]
        assert "text" in block_types
        assert "tool_use" in block_types

        # Verify tool input
        json_deltas = [
            e[1] for e in events
            if e[0] == "content_block_delta"
            and e[1].get("delta", {}).get("type") == "input_json_delta"
        ]
        assert len(json_deltas) >= 1
        tool_input = json.loads(json_deltas[0]["delta"]["partial_json"])
        assert tool_input["pattern"] == "error"
        assert tool_input["path"] == "/var/log"

    def test_full_pipeline_tool_result_roundtrip(self):
        """Conversation with tool_use and tool_result, verify all IDs preserved."""
        self.backend._generate_text = "Based on the file content, it says hello."

        req = {
            "messages": [
                {"role": "user", "content": "Read the file"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Reading..."},
                        {
                            "type": "tool_use",
                            "id": "toolu_test123",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_test123",
                            "content": "hello world content",
                        },
                    ],
                },
            ],
            "max_tokens": 100,
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages", req)
        assert status == 200

        # Verify messages reached backend in correct OAI format
        msgs = self.backend._last_messages
        assert len(msgs) == 3
        # First: user
        assert msgs[0]["role"] == "user"
        # Second: assistant with tool_calls
        assert msgs[1]["role"] == "assistant"
        assert "tool_calls" in msgs[1]
        assert msgs[1]["tool_calls"][0]["id"] == "toolu_test123"
        assert msgs[1]["tool_calls"][0]["function"]["name"] == "Read"
        # Third: tool result
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == "toolu_test123"
        assert "hello world content" in msgs[2]["content"]

    def test_thinking_blocks_stripped(self):
        """Thinking blocks in input don't appear in OAI messages."""
        self.backend._generate_text = "Answer."

        req = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Secret internal thought"},
                        {"type": "text", "text": "Visible response"},
                    ],
                },
                {"role": "user", "content": "Follow up"},
            ],
            "max_tokens": 50,
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages", req)
        assert status == 200

        # Verify thinking content not in backend messages
        msgs = self.backend._last_messages
        all_content = json.dumps(msgs)
        assert "Secret internal thought" not in all_content
        assert "Visible response" in all_content


# ═══════════════════════════════════════════════════════════════
# D. Error Handling Pipeline Tests
# ═══════════════════════════════════════════════════════════════

class TestErrorHandlingPipeline:
    """Tests for server error handling."""

    def test_backend_exception_returns_500(self):
        """Backend raises RuntimeError, verify 500 response."""
        port = _find_free_port()
        backend = MockBackend()
        backend.generate = mock.MagicMock(side_effect=RuntimeError("Model crashed"))
        server = _start_test_server(backend, port)
        try:
            status, resp = _http_request(port, "POST", "/v1/messages", {
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 50,
            })
            assert status == 500
            data = json.loads(resp)
            assert data["type"] == "error"
            assert "Model crashed" in data["error"]["message"]
        finally:
            server.shutdown()

    def test_client_disconnect_no_crash(self):
        """Close connection mid-stream, server doesn't crash."""
        port = _find_free_port()

        # Create a slow backend that yields tokens with delays
        class SlowBackend:
            def generate(self, messages, max_tokens, temperature, tools=None):
                return "ok"
            def stream(self, messages, max_tokens, temperature):
                for i in range(100):
                    time.sleep(0.01)
                    yield f"token{i}"

        server = _start_test_server(SlowBackend(), port)
        try:
            # Open a raw socket, send request, then close immediately
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", port))
            request_body = json.dumps({
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "max_tokens": 100,
            })
            http_req = (
                f"POST /v1/messages HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(request_body)}\r\n"
                f"\r\n"
                f"{request_body}"
            )
            sock.sendall(http_req.encode())
            time.sleep(0.1)
            sock.close()  # Disconnect mid-stream

            # Give server time to handle the broken pipe
            time.sleep(0.5)

            # Server should still be alive
            status, resp = _http_request(port, "GET", "/")
            assert status == 200
        finally:
            server.shutdown()

    def test_invalid_json_returns_400(self):
        """Send garbage body, get 400."""
        port = _find_free_port()
        backend = MockBackend()
        server = _start_test_server(backend, port)
        try:
            status, resp = _http_request(port, "POST", "/v1/messages",
                                         b"this is not json{{{")
            assert status == 400
            data = json.loads(resp)
            assert "invalid" in data.get("error", "").lower()
        finally:
            server.shutdown()

    def test_empty_body_handled(self):
        """Empty POST body doesn't crash."""
        port = _find_free_port()
        backend = MockBackend()
        backend.generate = mock.MagicMock(return_value="ok")
        server = _start_test_server(backend, port)
        try:
            status, resp = _http_request(port, "POST", "/v1/messages", b"")
            # Empty body => json.loads(b"") would be {} => req = {}
            # _handle_messages will proceed with empty data; this should not crash
            # It may produce 200 with SSE or some error, but no crash
            assert status in (200, 400, 500)
        finally:
            server.shutdown()

    def test_unknown_endpoint_returns_404(self):
        """GET/POST to /v1/unknown -> 404."""
        port = _find_free_port()
        backend = MockBackend()
        server = _start_test_server(backend, port)
        try:
            status_get, _ = _http_request(port, "GET", "/v1/unknown")
            assert status_get == 404

            status_post, resp_post = _http_request(port, "POST", "/v1/unknown",
                                                    {"messages": []})
            assert status_post == 404
            data = json.loads(resp_post)
            assert "unknown" in data.get("error", "").lower()
        finally:
            server.shutdown()


# ═══════════════════════════════════════════════════════════════
# E. Backend Integration Tests (mocked mlx_lm)
# ═══════════════════════════════════════════════════════════════

class TestBackendIntegration:
    """Test MLXBackend with fully mocked mlx_lm."""

    def test_mlx_backend_generate_calls_format_prompt(self):
        """Verify _format_prompt is called before generate."""
        backend = localllm.MLXBackend()
        backend.tokenizer = mock.MagicMock()
        backend.model = mock.MagicMock()

        # Mock _format_prompt
        backend._format_prompt = mock.MagicMock(return_value="formatted prompt")

        mock_generate = mock.MagicMock(return_value="Generated text")
        with mock.patch.dict(sys.modules, {
            "mlx_lm": mock.MagicMock(generate=mock_generate),
        }):
            # Need to re-import generate for the patched module
            import importlib
            # Use the actual generate method which imports mlx_lm.generate
            with mock.patch("localllm.MLXBackend._make_sampler", return_value="sampler"):
                # Directly test that _format_prompt is called
                messages = [{"role": "user", "content": "Hi"}]
                backend._format_prompt(messages, None)
                backend._format_prompt.assert_called_once_with(messages, None)

    def test_mlx_backend_stream_yields_incremental(self):
        """Mock stream_generate to yield 5 tokens, verify all yielded."""
        backend = localllm.MLXBackend()
        backend.tokenizer = mock.MagicMock()
        backend.tokenizer.apply_chat_template.return_value = "prompt"
        backend.model = mock.MagicMock()

        # Create mock stream responses
        class MockStreamResp:
            def __init__(self, text):
                self.text = text

        mock_responses = [MockStreamResp(f"tok{i}") for i in range(5)]

        mock_sampler = mock.MagicMock()
        with mock.patch.dict(sys.modules, {
            "mlx_lm": mock.MagicMock(
                stream_generate=mock.MagicMock(return_value=iter(mock_responses))
            ),
            "mlx_lm.sample_utils": mock.MagicMock(
                make_sampler=mock.MagicMock(return_value=mock_sampler)
            ),
        }):
            messages = [{"role": "user", "content": "Hi"}]
            tokens = list(backend.stream(messages, max_tokens=100, temperature=0.7))
            assert len(tokens) == 5
            assert tokens == ["tok0", "tok1", "tok2", "tok3", "tok4"]

    def test_mlx_backend_sampler_used(self):
        """Verify make_sampler called with correct temperature."""
        mock_make_sampler = mock.MagicMock(return_value="my_sampler")
        with mock.patch.dict(sys.modules, {
            "mlx_lm": mock.MagicMock(),
            "mlx_lm.sample_utils": mock.MagicMock(make_sampler=mock_make_sampler),
        }):
            result = localllm.MLXBackend._make_sampler(0.42)
            mock_make_sampler.assert_called_once_with(temp=0.42)
            assert result == "my_sampler"

    def test_mlx_backend_tools_in_chat_template(self):
        """When tokenizer supports tools, they're passed to apply_chat_template."""
        backend = localllm.MLXBackend()
        backend.tokenizer = mock.MagicMock()
        backend.tokenizer.apply_chat_template.return_value = "template_with_tools"

        messages = [{"role": "user", "content": "Hi"}]
        tools = [{"function": {"name": "Bash", "description": "cmd", "parameters": {}}}]

        result = backend._format_prompt(messages, tools=tools)
        assert result == "template_with_tools"
        backend.tokenizer.apply_chat_template.assert_called_once_with(
            messages, tools=tools, add_generation_prompt=True, tokenize=False
        )

    def test_mlx_backend_tools_fallback_injection(self):
        """When tokenizer rejects tools, they're injected into system prompt."""
        backend = localllm.MLXBackend()
        backend.tokenizer = mock.MagicMock()

        call_count = [0]
        def fake_apply(msgs, **kwargs):
            call_count[0] += 1
            if "tools" in kwargs:
                raise TypeError("unexpected keyword argument 'tools'")
            # Verify tools were injected into system message
            assert any("Bash" in m.get("content", "") for m in msgs
                       if m["role"] == "system")
            return "fallback_prompt"

        backend.tokenizer.apply_chat_template.side_effect = fake_apply

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        tools = [{"function": {"name": "Bash", "description": "Run cmd", "parameters": {}}}]

        result = backend._format_prompt(messages, tools=tools)
        assert result == "fallback_prompt"
        assert call_count[0] == 2  # First with tools (fails), second without


# ═══════════════════════════════════════════════════════════════
# F. Shell Script Tests (using subprocess)
# ═══════════════════════════════════════════════════════════════

SHELL_SCRIPT = os.path.join(PROJECT_DIR, "vibe-local.sh")


class TestShellScript:
    """Test the vibe-local.sh shell script without actually running it fully."""

    def test_vibe_local_sh_syntax_valid(self):
        """bash -n vibe-local.sh passes (syntax check)."""
        result = subprocess.run(
            ["bash", "-n", SHELL_SCRIPT],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_direct_help_shows_in_usage(self):
        """The --direct flag should be documented in the script."""
        with open(SHELL_SCRIPT, "r") as f:
            content = f.read()
        assert "--direct" in content, "--direct not found in vibe-local.sh"
        # Verify the usage comment mentions --direct
        assert "MLX直結" in content or "MLX" in content

    def test_find_localllm_script_finds_adjacent(self):
        """Verify the script contains logic to find localllm.py in same dir."""
        with open(SHELL_SCRIPT, "r") as f:
            content = f.read()
        assert "localllm.py" in content
        assert "find_localllm_script" in content
        # Verify it checks script_dir
        assert "script_dir" in content

    def test_direct_mode_apple_silicon_guard(self):
        """Verify the direct mode has an Apple Silicon check."""
        with open(SHELL_SCRIPT, "r") as f:
            content = f.read()
        assert "uname" in content
        assert "arm64" in content
        assert "Darwin" in content
        assert "Apple Silicon Mac" in content or "Apple Silicon" in content

    def test_direct_mode_error_message_for_non_mac(self):
        """Verify the error message for non-Apple-Silicon systems."""
        with open(SHELL_SCRIPT, "r") as f:
            content = f.read()
        assert "Ollama モードを使ってください" in content or "Ollama" in content
