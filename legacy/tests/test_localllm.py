"""
Comprehensive test suite for localllm.py.

Tests cover:
  - extract_tool_calls() — all XML/JSON patterns
  - anthropic_to_oai_messages() — format conversion
  - anthropic_to_oai_tools() — tool definition conversion
  - MLXBackend._inject_tools_into_system() — system prompt injection
  - MLXBackend._format_prompt() — chat template with tools fallback
  - MLXBackend._make_sampler() — sampler creation
  - LocalLLMHandler — HTTP integration tests (SSE format)
  - Edge cases — unicode, long content, invalid JSON, BrokenPipeError
"""

import io
import json
import re
import socket
import sys
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from unittest import mock

import pytest

# Import the module under test
sys.path.insert(0, "/Users/yoichiochiai/vibe-local")
import localllm


# ═══════════════════════════════════════════════════════════════
# extract_tool_calls()
# ═══════════════════════════════════════════════════════════════

class TestExtractToolCallsAnthropicXML:
    """Pattern 1: <invoke name="..."><parameter name="...">...</parameter></invoke>"""

    def test_single_invoke(self):
        text = '<invoke name="Bash"><parameter name="command">ls -la</parameter></invoke>'
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        tc = calls[0]
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "Bash"
        args = json.loads(tc["function"]["arguments"])
        assert args["command"] == "ls -la"
        assert tc["id"].startswith("call_")
        assert cleaned == ""

    def test_multiple_params(self):
        text = (
            '<invoke name="Edit">'
            '<parameter name="file_path">/tmp/a.py</parameter>'
            '<parameter name="old_string">foo</parameter>'
            '<parameter name="new_string">bar</parameter>'
            '</invoke>'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["file_path"] == "/tmp/a.py"
        assert args["old_string"] == "foo"
        assert args["new_string"] == "bar"

    def test_invoke_with_surrounding_text(self):
        text = (
            'I will run the command.\n'
            '<invoke name="Bash"><parameter name="command">echo hello</parameter></invoke>\n'
            'Done.'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert "I will run the command." in cleaned
        assert "Done." in cleaned
        assert "<invoke" not in cleaned

    def test_invoke_wrapped_in_function_calls_tags(self):
        text = (
            '<function_calls>\n'
            '<invoke name="Read"><parameter name="file_path">/etc/hosts</parameter></invoke>\n'
            '</function_calls>'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "Read"
        # function_calls wrapper tags should be stripped
        assert "<function_calls>" not in cleaned
        assert "</function_calls>" not in cleaned

    def test_multiple_invokes(self):
        text = (
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'
            '<invoke name="Read"><parameter name="file_path">/tmp/x</parameter></invoke>'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["function"]["name"] == "Bash"
        assert calls[1]["function"]["name"] == "Read"
        # Each call should have a unique ID
        assert calls[0]["id"] != calls[1]["id"]

    def test_multiline_param_value(self):
        text = (
            '<invoke name="Write">'
            '<parameter name="content">line1\nline2\nline3</parameter>'
            '<parameter name="file_path">/tmp/out.txt</parameter>'
            '</invoke>'
        )
        calls, _ = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert "line1\nline2\nline3" == args["content"]


class TestExtractToolCallsQwenXML:
    """Pattern 2: <function=ToolName><parameter=param>value</parameter></function>"""

    def test_single_qwen_call(self):
        text = '<function=Bash><parameter=command>ls -la</parameter></function>'
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "Bash"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == "ls -la"
        assert cleaned == ""

    def test_qwen_with_surrounding_text(self):
        text = (
            'Let me check.\n'
            '<function=Grep><parameter=pattern>TODO</parameter><parameter=path>/src</parameter></function>\n'
            'Searching...'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "Grep"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["pattern"] == "TODO"
        assert args["path"] == "/src"
        assert "Let me check." in cleaned
        assert "Searching..." in cleaned

    def test_qwen_wrapped_in_tool_call_tags(self):
        text = (
            '<tool_call>\n'
            '<function=Bash><parameter=command>pwd</parameter></function>\n'
            '</tool_call>'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert "<tool_call>" not in cleaned
        assert "</tool_call>" not in cleaned

    def test_multiple_qwen_calls(self):
        text = (
            '<function=Bash><parameter=command>ls</parameter></function>'
            '<function=Read><parameter=file_path>/tmp/x</parameter></function>'
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 2


class TestExtractToolCallsSimpleXML:
    """Pattern 3: <ToolName><param>val</param></ToolName> (requires known_tools)."""

    def test_simple_nested_xml(self):
        text = '<Bash><command>ls -la</command></Bash>'
        calls, cleaned = localllm.extract_tool_calls(text, known_tools={"Bash"})
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "Bash"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == "ls -la"

    def test_simple_xml_not_in_known_tools(self):
        text = '<Bash><command>ls</command></Bash>'
        calls, cleaned = localllm.extract_tool_calls(text, known_tools={"Read"})
        assert len(calls) == 0

    def test_simple_xml_without_known_tools(self):
        """Without known_tools, pattern 3 is not tried."""
        text = '<Bash><command>ls</command></Bash>'
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 0

    def test_simple_xml_multiple_params(self):
        text = '<Edit><file_path>/tmp/a.py</file_path><old_string>x</old_string><new_string>y</new_string></Edit>'
        calls, _ = localllm.extract_tool_calls(text, known_tools={"Edit"})
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["file_path"] == "/tmp/a.py"


class TestExtractToolCallsJSON:
    """Pattern 4: JSON tool call objects."""

    def test_json_tool_call(self):
        text = '{"name": "Bash", "arguments": {"command": "ls -la"}}'
        calls, cleaned = localllm.extract_tool_calls(text, known_tools={"Bash"})
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "Bash"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == "ls -la"

    def test_json_tool_call_not_in_known_tools(self):
        text = '{"name": "Unknown", "arguments": {"x": "y"}}'
        calls, cleaned = localllm.extract_tool_calls(text, known_tools={"Bash"})
        assert len(calls) == 0

    def test_json_tool_call_without_known_tools(self):
        """Without known_tools, JSON pattern is not tried."""
        text = '{"name": "Bash", "arguments": {"command": "ls"}}'
        calls, _ = localllm.extract_tool_calls(text)
        assert len(calls) == 0

    def test_json_invalid_arguments(self):
        text = '{"name": "Bash", "arguments": {invalid json}}'
        calls, _ = localllm.extract_tool_calls(text, known_tools={"Bash"})
        assert len(calls) == 0


class TestExtractToolCallsNoMatch:
    """No tool calls in text."""

    def test_plain_text(self):
        text = "Hello, I can help you with that. Let me explain."
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 0
        assert cleaned == text

    def test_empty_string(self):
        calls, cleaned = localllm.extract_tool_calls("")
        assert len(calls) == 0
        assert cleaned == ""

    def test_xml_like_but_not_tool_call(self):
        text = "<p>This is HTML</p>"
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 0


class TestExtractToolCallsMixed:
    """Mixed text + tool calls."""

    def test_text_before_and_after_invoke(self):
        text = (
            "I'll list the files.\n"
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>\n'
            "Here are the results."
        )
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        assert "I'll list the files." in cleaned
        assert "Here are the results." in cleaned
        assert "<invoke" not in cleaned


# ═══════════════════════════════════════════════════════════════
# anthropic_to_oai_messages()
# ═══════════════════════════════════════════════════════════════

class TestAnthropicToOaiMessages:

    def test_simple_user_assistant(self):
        req = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 2
        assert oai[0] == {"role": "user", "content": "Hello"}
        assert oai[1] == {"role": "assistant", "content": "Hi there"}

    def test_system_prompt_string(self):
        req = {
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 2
        assert oai[0]["role"] == "system"
        assert "You are helpful." in oai[0]["content"]

    def test_system_prompt_list_format(self):
        req = {
            "system": [
                {"type": "text", "text": "First part."},
                {"type": "text", "text": "Second part."},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert oai[0]["role"] == "system"
        assert "First part." in oai[0]["content"]
        assert "Second part." in oai[0]["content"]

    def test_system_prompt_truncation(self):
        long_system = "x" * 5000
        req = {
            "system": long_system,
            "messages": [{"role": "user", "content": "Hi"}],
        }
        oai = localllm.anthropic_to_oai_messages(req)
        sys_content = oai[0]["content"]
        assert len(sys_content) < 5000
        assert "...(truncated)" in sys_content

    def test_system_prompt_with_tools_appends_tool_note(self):
        req = {
            "system": "Be helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{"name": "Bash"}],
        }
        oai = localllm.anthropic_to_oai_messages(req, tool_names=["Bash"])
        sys_content = oai[0]["content"]
        assert "Bash" in sys_content
        assert "FUNCTION CALLING" in sys_content

    def test_tool_use_blocks_in_assistant(self):
        req = {
            "messages": [
                {"role": "user", "content": "List files"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll list files."},
                        {
                            "type": "tool_use",
                            "id": "call_abc123",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ],
                },
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 2
        assistant_msg = oai[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "I'll list files."
        assert len(assistant_msg["tool_calls"]) == 1
        tc = assistant_msg["tool_calls"][0]
        assert tc["id"] == "call_abc123"
        assert tc["function"]["name"] == "Bash"

    def test_tool_result_blocks_in_user(self):
        req = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_abc123",
                            "content": "file1.txt\nfile2.txt",
                        },
                    ],
                },
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 1
        assert oai[0]["role"] == "tool"
        assert oai[0]["tool_call_id"] == "call_abc123"
        assert "file1.txt" in oai[0]["content"]

    def test_tool_result_with_list_content(self):
        req = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_xyz",
                            "content": [
                                {"type": "text", "text": "Result line 1"},
                                {"type": "text", "text": "Result line 2"},
                            ],
                        },
                    ],
                },
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert oai[0]["role"] == "tool"
        assert "Result line 1" in oai[0]["content"]
        assert "Result line 2" in oai[0]["content"]

    def test_thinking_blocks_skipped(self):
        req = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Let me think..."},
                        {"type": "text", "text": "Answer here."},
                    ],
                },
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 1
        # thinking content should not appear
        assert "Let me think" not in oai[0]["content"]
        assert "Answer here." in oai[0]["content"]

    def test_mixed_content_blocks(self):
        req = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hmm"},
                        {"type": "text", "text": "Here is my answer."},
                        {
                            "type": "tool_use",
                            "id": "call_t1",
                            "name": "Grep",
                            "input": {"pattern": "TODO"},
                        },
                    ],
                },
            ]
        }
        oai = localllm.anthropic_to_oai_messages(req)
        msg = oai[0]
        assert msg["role"] == "assistant"
        assert "Here is my answer." in msg["content"]
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "Grep"

    def test_empty_messages_list(self):
        req = {"messages": []}
        oai = localllm.anthropic_to_oai_messages(req)
        assert oai == []

    def test_no_system(self):
        req = {"messages": [{"role": "user", "content": "Hi"}]}
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai) == 1
        assert oai[0]["role"] == "user"


# ═══════════════════════════════════════════════════════════════
# anthropic_to_oai_tools()
# ═══════════════════════════════════════════════════════════════

class TestAnthropicToOaiTools:

    def test_standard_conversion(self):
        tools = [
            {
                "name": "Bash",
                "description": "Execute a bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
        ]
        oai = localllm.anthropic_to_oai_tools(tools)
        assert len(oai) == 1
        assert oai[0]["type"] == "function"
        func = oai[0]["function"]
        assert func["name"] == "Bash"
        assert func["description"] == "Execute a bash command"
        assert func["parameters"]["type"] == "object"
        assert "command" in func["parameters"]["properties"]

    def test_empty_tools_list(self):
        oai = localllm.anthropic_to_oai_tools([])
        assert oai == []

    def test_tool_with_type_custom(self):
        tools = [
            {
                "type": "custom",
                "name": "Read",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        oai = localllm.anthropic_to_oai_tools(tools)
        assert len(oai) == 1
        assert oai[0]["function"]["name"] == "Read"

    def test_tool_with_unknown_type_skipped(self):
        tools = [
            {
                "type": "computer_20241022",
                "name": "computer",
                "description": "Computer tool",
                "input_schema": {},
            },
        ]
        oai = localllm.anthropic_to_oai_tools(tools)
        assert len(oai) == 0

    def test_complex_input_schema(self):
        tools = [
            {
                "name": "Edit",
                "description": "Edit a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to file"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["file_path", "old_string", "new_string"],
                },
            },
        ]
        oai = localllm.anthropic_to_oai_tools(tools)
        params = oai[0]["function"]["parameters"]
        assert len(params["properties"]) == 4
        assert params["required"] == ["file_path", "old_string", "new_string"]

    def test_multiple_tools(self):
        tools = [
            {"name": "Bash", "description": "Run bash", "input_schema": {}},
            {"name": "Read", "description": "Read file", "input_schema": {}},
            {"name": "Write", "description": "Write file", "input_schema": {}},
        ]
        oai = localllm.anthropic_to_oai_tools(tools)
        assert len(oai) == 3
        names = [t["function"]["name"] for t in oai]
        assert names == ["Bash", "Read", "Write"]


# ═══════════════════════════════════════════════════════════════
# MLXBackend._inject_tools_into_system()
# ═══════════════════════════════════════════════════════════════

class TestMLXBackendInjectTools:

    def setup_method(self):
        self.backend = localllm.MLXBackend()
        self.tools = [
            {
                "function": {
                    "name": "Bash",
                    "description": "Execute a command",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                }
            },
        ]

    def test_inject_when_system_exists(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result = self.backend._inject_tools_into_system(messages, self.tools)
        assert result[0]["role"] == "system"
        assert "You are helpful." in result[0]["content"]
        assert "Bash" in result[0]["content"]
        assert "Execute a command" in result[0]["content"]
        # Original messages should not be modified
        assert "Bash" not in messages[0]["content"]

    def test_inject_when_no_system(self):
        messages = [
            {"role": "user", "content": "Hi"},
        ]
        result = self.backend._inject_tools_into_system(messages, self.tools)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Bash" in result[0]["content"]
        assert result[1]["role"] == "user"
        # Original messages should not be modified
        assert len(messages) == 1

    def test_inject_multiple_tools(self):
        tools = [
            {"function": {"name": "Bash", "description": "Run cmd", "parameters": {}}},
            {"function": {"name": "Read", "description": "Read file", "parameters": {}}},
        ]
        messages = [{"role": "system", "content": "System."}]
        result = self.backend._inject_tools_into_system(messages, tools)
        assert "### Bash" in result[0]["content"]
        assert "### Read" in result[0]["content"]

    def test_inject_includes_format_instruction(self):
        messages = [{"role": "system", "content": "System."}]
        result = self.backend._inject_tools_into_system(messages, self.tools)
        assert "<function=ToolName>" in result[0]["content"]


# ═══════════════════════════════════════════════════════════════
# MLXBackend._format_prompt()
# ═══════════════════════════════════════════════════════════════

class TestMLXBackendFormatPrompt:
    """Test _format_prompt with mocked tokenizer."""

    def setup_method(self):
        self.backend = localllm.MLXBackend()
        self.backend.tokenizer = mock.MagicMock()

    def test_no_tools_uses_chat_template(self):
        messages = [{"role": "user", "content": "Hi"}]
        self.backend.tokenizer.apply_chat_template.return_value = "formatted"
        result = self.backend._format_prompt(messages, tools=None)
        assert result == "formatted"
        self.backend.tokenizer.apply_chat_template.assert_called_once_with(
            messages, add_generation_prompt=True, tokenize=False
        )

    def test_tools_passed_to_tokenizer_if_supported(self):
        messages = [{"role": "user", "content": "Hi"}]
        tools = [{"function": {"name": "Bash"}}]
        self.backend.tokenizer.apply_chat_template.return_value = "with_tools"
        result = self.backend._format_prompt(messages, tools=tools)
        assert result == "with_tools"
        self.backend.tokenizer.apply_chat_template.assert_called_once_with(
            messages, tools=tools, add_generation_prompt=True, tokenize=False
        )

    def test_tools_fallback_to_inject_when_tokenizer_rejects(self):
        """If tokenizer raises TypeError for tools param, falls back to injection."""
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Hi"},
        ]
        tools = [{"function": {"name": "Bash", "description": "Run", "parameters": {}}}]

        call_count = [0]
        def fake_apply(msgs, **kwargs):
            call_count[0] += 1
            if "tools" in kwargs:
                raise TypeError("unexpected keyword argument 'tools'")
            # Second call (without tools) succeeds
            return f"injected:{msgs[0]['content'][:20]}"

        self.backend.tokenizer.apply_chat_template.side_effect = fake_apply
        result = self.backend._format_prompt(messages, tools=tools)
        # Should have called twice: once with tools (fails), once without
        assert call_count[0] == 2
        assert "injected:" in result

    def test_empty_tools_list_treated_as_no_tools(self):
        """Empty tools list should be falsy, so no tools param passed."""
        messages = [{"role": "user", "content": "Hi"}]
        self.backend.tokenizer.apply_chat_template.return_value = "no_tools"
        result = self.backend._format_prompt(messages, tools=[])
        assert result == "no_tools"
        # Should be called without tools param
        self.backend.tokenizer.apply_chat_template.assert_called_once_with(
            messages, add_generation_prompt=True, tokenize=False
        )


# ═══════════════════════════════════════════════════════════════
# MLXBackend._make_sampler()
# ═══════════════════════════════════════════════════════════════

class TestMLXBackendMakeSampler:
    """Test the _make_sampler static method."""

    def test_make_sampler_calls_make_sampler(self):
        fake_make_sampler = mock.MagicMock(return_value="sampler_obj")
        with mock.patch.dict(sys.modules, {
            "mlx_lm": mock.MagicMock(),
            "mlx_lm.sample_utils": mock.MagicMock(make_sampler=fake_make_sampler),
        }):
            result = localllm.MLXBackend._make_sampler(0.5)
            fake_make_sampler.assert_called_once_with(temp=0.5)
            assert result == "sampler_obj"


# ═══════════════════════════════════════════════════════════════
# LocalLLMHandler integration tests (real HTTP server)
# ═══════════════════════════════════════════════════════════════

def _find_free_port():
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_sse_events(raw_bytes):
    """Parse SSE events from raw response bytes."""
    text = raw_bytes.decode("utf-8", errors="replace") if isinstance(raw_bytes, bytes) else raw_bytes
    events = []
    for m in re.finditer(r'event:\s*(\S+)\ndata:\s*(\{.*?\})\n', text, re.DOTALL):
        event_type = m.group(1)
        try:
            data = json.loads(m.group(2))
        except json.JSONDecodeError:
            data = m.group(2)
        events.append((event_type, data))
    return events


def _start_test_server(backend, port):
    """Start a localllm server on a given port with a mock backend."""
    server = localllm.ThreadedServer(("127.0.0.1", port), localllm.LocalLLMHandler)
    server.backend = backend
    server.backend_name = "mock"
    server.model_name = "test-model"
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _http_request(port, method, path, body=None):
    """Send an HTTP request and return (status, body_text)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


class TestLocalLLMHandlerGET:

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = mock.MagicMock()
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def test_root_endpoint(self):
        status, body = _http_request(self.port, "GET", "/")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["backend"] == "mock"

    def test_models_endpoint(self):
        status, body = _http_request(self.port, "GET", "/v1/models")
        assert status == 200
        data = json.loads(body)
        assert data["data"][0]["id"] == "test-model"

    def test_unknown_path_404(self):
        status, body = _http_request(self.port, "GET", "/unknown")
        assert status == 404


class TestLocalLLMHandlerPOST:

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = mock.MagicMock()
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def test_count_tokens_simple(self):
        body = {
            "messages": [
                {"role": "user", "content": "Hello world test message"},
            ]
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages/count_tokens", body)
        assert status == 200
        data = json.loads(resp)
        assert "input_tokens" in data

    def test_count_tokens_with_system(self):
        body = {
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages/count_tokens", body)
        assert status == 200
        data = json.loads(resp)
        assert data["input_tokens"] > 0

    def test_count_tokens_with_list_content(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello world"},
                    ],
                },
            ]
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages/count_tokens", body)
        assert status == 200
        data = json.loads(resp)
        assert "input_tokens" in data

    def test_count_tokens_system_list_format(self):
        body = {
            "system": [{"type": "text", "text": "System prompt here."}],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        status, resp = _http_request(self.port, "POST", "/v1/messages/count_tokens", body)
        assert status == 200
        data = json.loads(resp)
        assert "input_tokens" in data

    def test_unknown_post_path_404(self):
        status, resp = _http_request(self.port, "POST", "/v1/unknown", {"messages": []})
        assert status == 404

    def test_invalid_json_body(self):
        status, resp = _http_request(self.port, "POST", "/v1/messages", b"not json{{{")
        assert status == 400


class TestLocalLLMHandlerSSEFormat:
    """Test that SSE responses follow the Anthropic message event sequence."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        self.port = _find_free_port()
        self.backend = mock.MagicMock()
        self.server = _start_test_server(self.backend, self.port)
        yield
        self.server.shutdown()

    def test_sync_response_sse_sequence(self):
        """Verify message_start -> content_block_start -> delta -> stop -> message_delta -> message_stop."""
        self.backend.generate.return_value = "Hello, I can help!"

        status, resp = _http_request(self.port, "POST", "/v1/messages",
            {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100})

        assert status == 200
        events = _parse_sse_events(resp)
        event_types = [e[0] for e in events]

        assert event_types[0] == "message_start"
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "content_block_stop" in event_types
        assert event_types[-2] == "message_delta"
        assert event_types[-1] == "message_stop"

    def test_sync_response_message_start_structure(self):
        self.backend.generate.return_value = "Response text"

        status, resp = _http_request(self.port, "POST", "/v1/messages",
            {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 50})

        events = _parse_sse_events(resp)
        msg_start = events[0][1]
        assert msg_start["type"] == "message_start"
        msg = msg_start["message"]
        assert msg["role"] == "assistant"
        assert msg["type"] == "message"
        assert "id" in msg
        assert msg["id"].startswith("msg_")
        assert "usage" in msg

    def test_tool_call_response_format(self):
        """Verify tool_use blocks appear in SSE when tool calls are extracted."""
        self.backend.generate.return_value = (
            'Let me check.\n'
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'
        )

        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "List files"}],
            "tools": [{"name": "Bash", "description": "Run cmd", "input_schema": {}}],
            "max_tokens": 200,
        })

        events = _parse_sse_events(resp)

        # Should have tool_use content block
        tool_use_starts = [
            e[1] for e in events
            if e[0] == "content_block_start"
            and isinstance(e[1], dict)
            and e[1].get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_use_starts) == 1
        assert tool_use_starts[0]["content_block"]["name"] == "Bash"

        # message_delta should have stop_reason = tool_use
        msg_delta = [e[1] for e in events if e[0] == "message_delta"]
        assert len(msg_delta) == 1
        assert msg_delta[0]["delta"]["stop_reason"] == "tool_use"

    def test_streaming_response_sse_sequence(self):
        """Test true token-by-token streaming SSE format."""
        self.backend.stream.return_value = iter(["Hello", " world", "!"])

        status, resp = _http_request(self.port, "POST", "/v1/messages", {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "max_tokens": 100,
        })

        events = _parse_sse_events(resp)
        event_types = [e[0] for e in events]

        assert event_types[0] == "message_start"
        assert event_types[1] == "content_block_start"
        # Should have 3 content_block_delta events (one per token)
        deltas = [e for e in events if e[0] == "content_block_delta"]
        assert len(deltas) == 3
        assert deltas[0][1]["delta"]["text"] == "Hello"
        assert deltas[1][1]["delta"]["text"] == " world"
        assert deltas[2][1]["delta"]["text"] == "!"
        assert event_types[-3] == "content_block_stop"
        assert event_types[-2] == "message_delta"
        assert event_types[-1] == "message_stop"

    def test_empty_response_fallback(self):
        """Empty content should produce (empty response) text."""
        self.backend.generate.return_value = ""

        status, resp = _http_request(self.port, "POST", "/v1/messages",
            {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 50})

        events = _parse_sse_events(resp)
        deltas = [e[1] for e in events if e[0] == "content_block_delta"]
        assert any("(empty response)" in d.get("delta", {}).get("text", "") for d in deltas)


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_unicode_content(self):
        text = '日本語テスト <invoke name="Bash"><parameter name="command">echo こんにちは</parameter></invoke>'
        calls, cleaned = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == "echo こんにちは"
        assert "日本語テスト" in cleaned

    def test_unicode_in_messages(self):
        req = {
            "system": "あなたは親切なアシスタントです。",
            "messages": [{"role": "user", "content": "日本語で答えてください"}],
        }
        oai = localllm.anthropic_to_oai_messages(req)
        assert "あなたは親切なアシスタントです。" in oai[0]["content"]

    def test_very_long_content_tool_call(self):
        long_value = "x" * 100000
        text = f'<invoke name="Write"><parameter name="content">{long_value}</parameter></invoke>'
        calls, _ = localllm.extract_tool_calls(text)
        assert len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert len(args["content"]) == 100000

    def test_very_long_message_conversion(self):
        long_text = "y" * 50000
        req = {"messages": [{"role": "user", "content": long_text}]}
        oai = localllm.anthropic_to_oai_messages(req)
        assert len(oai[0]["content"]) == 50000

    def test_empty_messages(self):
        req = {"messages": []}
        oai = localllm.anthropic_to_oai_messages(req)
        assert oai == []

    def test_make_tool_call_structure(self):
        tc = localllm._make_tool_call("Bash", {"command": "ls"})
        assert tc["id"].startswith("call_")
        assert len(tc["id"]) == len("call_") + 8
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "Bash"
        assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}

    def test_make_tool_call_unicode_args(self):
        tc = localllm._make_tool_call("Write", {"content": "こんにちは世界"})
        args = json.loads(tc["function"]["arguments"])
        assert args["content"] == "こんにちは世界"

    def test_broken_pipe_handling_in_send_sse(self):
        """_send_sse should swallow BrokenPipeError."""
        handler = localllm.LocalLLMHandler.__new__(localllm.LocalLLMHandler)
        handler.wfile = mock.MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError()
        # Should not raise
        handler._send_sse("test", {"type": "test"})

    def test_broken_pipe_in_messages_handler(self):
        """BrokenPipeError during message handling should be caught gracefully.
        _handle_messages wraps everything in try/except BrokenPipeError."""
        mock_backend = mock.MagicMock()
        mock_backend.generate.side_effect = BrokenPipeError()

        port = _find_free_port()
        server = _start_test_server(mock_backend, port)
        try:
            # The server should handle the BrokenPipeError internally
            # and not crash. The client may see a connection error,
            # which is expected.
            try:
                _http_request(port, "POST", "/v1/messages",
                    {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 50})
            except Exception:
                pass  # Client-side errors are expected
            # Server should still be alive — send another request
            status, resp = _http_request(port, "GET", "/")
            assert status == 200
        finally:
            server.shutdown()

    def test_invalid_json_in_tool_arguments_emit(self):
        """_emit_sse_full handles invalid JSON in tool call arguments gracefully."""
        handler = localllm.LocalLLMHandler.__new__(localllm.LocalLLMHandler)
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"

        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()

        tool_calls = [
            {
                "id": "call_test",
                "type": "function",
                "function": {
                    "name": "Bash",
                    "arguments": "not valid json{{{",
                },
            }
        ]
        # Should not raise — invalid JSON falls back to {"raw": ...}
        handler._emit_sse_full("test-model", "", "", tool_calls, "tool_calls", {})

        events = _parse_sse_events(handler.wfile.getvalue())
        # Find the input_json_delta
        deltas = [
            e[1] for e in events
            if e[0] == "content_block_delta"
            and isinstance(e[1], dict)
            and e[1].get("delta", {}).get("type") == "input_json_delta"
        ]
        assert len(deltas) == 1
        partial = json.loads(deltas[0]["delta"]["partial_json"])
        assert "raw" in partial

    def test_max_tokens_cap(self):
        """max_tokens should be capped at MAX_TOKENS_CAP."""
        mock_backend = mock.MagicMock()
        mock_backend.generate.return_value = "ok"

        port = _find_free_port()
        server = _start_test_server(mock_backend, port)
        try:
            status, resp = _http_request(port, "POST", "/v1/messages",
                {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 999999})
            assert status == 200
            # Backend should have been called with capped value
            call_args = mock_backend.generate.call_args
            assert call_args is not None
            # Check the max_tokens positional arg (index 1 in positional args)
            actual_max_tokens = call_args[0][1]
            assert actual_max_tokens <= localllm.MAX_TOKENS_CAP
        finally:
            server.shutdown()

    def test_allowed_tools_filtering(self):
        """Tools not in ALLOWED_TOOLS should be filtered out."""
        mock_backend = mock.MagicMock()
        mock_backend.generate.return_value = "ok"

        port = _find_free_port()
        server = _start_test_server(mock_backend, port)
        try:
            status, resp = _http_request(port, "POST", "/v1/messages", {
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [
                    {"name": "Bash", "description": "ok", "input_schema": {}},
                    {"name": "EvilTool", "description": "bad", "input_schema": {}},
                ],
                "max_tokens": 50,
            })
            assert status == 200
            # Backend's generate was called. The tool_names list passed to
            # anthropic_to_oai_messages should only include "Bash", not "EvilTool".
            # We verify by checking the SSE response doesn't mention EvilTool in
            # the system prompt injection.
            assert "EvilTool" not in resp
        finally:
            server.shutdown()


class TestCountTokens:
    """Dedicated tests for _handle_count_tokens."""

    def test_basic_counting(self):
        handler = localllm.LocalLLMHandler.__new__(localllm.LocalLLMHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.request_version = "HTTP/1.1"

        req = {
            "messages": [{"role": "user", "content": "Hello"}],  # 5 chars // 4 = 1
        }
        handler._handle_count_tokens(req)
        output = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["input_tokens"] == 1  # 5 // 4 = 1

    def test_counting_with_system_string(self):
        handler = localllm.LocalLLMHandler.__new__(localllm.LocalLLMHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.request_version = "HTTP/1.1"

        req = {
            "system": "Be helpful.",  # 11 chars // 4 = 2
            "messages": [{"role": "user", "content": "Hello world test"}],  # 16 // 4 = 4
        }
        handler._handle_count_tokens(req)
        output = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["input_tokens"] == 6  # 2 + 4

    def test_counting_empty_messages(self):
        handler = localllm.LocalLLMHandler.__new__(localllm.LocalLLMHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.request_version = "HTTP/1.1"

        req = {"messages": []}
        handler._handle_count_tokens(req)
        output = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert data["input_tokens"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
