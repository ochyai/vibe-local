# vibe-local Improvement Roadmap

> Last updated: 2026-02-23

## vibe-coder: Claude Code Replacement (v0.3)

**Architecture change**: Claude Code CLI + proxy replaced with vibe-coder.py
```
OLD: vibe-local.sh → claude CLI (proprietary) → proxy.py → Ollama
NEW: vibe-local.sh → vibe-coder.py (OSS) → Ollama direct
```

**Benefits**: No login, no Node.js, no proxy process, fully OSS, Python+Ollama only

### vibe-coder.py Implementation Status

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Config (CLI args, config file, env vars) | DONE |
| 1 | OllamaClient (direct /v1/chat/completions) | DONE |
| 1 | BashTool (subprocess, timeout) | DONE |
| 1 | ReadTool (cat -n format, offset/limit) | DONE |
| 1 | WriteTool (absolute paths, mkdir -p) | DONE |
| 1 | EditTool (unique check, replace_all) | DONE |
| 1 | ToolRegistry + OpenAI function calling schemas | DONE |
| 1 | PermissionMgr (safe/ask/deny) | DONE |
| 1 | Session (in-memory, JSONL persistence) | DONE |
| 1 | Agent loop (LLM → tool → result → loop) | DONE |
| 1 | TUI (readline, ANSI colors, streaming) | DONE |
| 1 | XML tool call fallback (Qwen models) | DONE |
| 2 | GlobTool (os.walk + fnmatch) | DONE |
| 2 | GrepTool (os.walk + re, context lines) | DONE |
| 2 | WebFetchTool (urllib, HTML→text) | DONE |
| 2 | WebSearchTool (DuckDuckGo HTML) | DONE |
| 2 | NotebookEditTool (JSON .ipynb) | DONE |
| 2 | System prompt with OS-specific hints | DONE |
| 3 | Markdown rendering in TUI | DONE |
| 3 | Multi-line input (""") | DONE |
| 3 | Session persistence (JSONL) | DONE |
| 3 | Session resume (--resume, --session-id) | DONE |
| 3 | Context compaction | DONE |
| 4 | Permission system (safe/ask/network tiers) | DONE |
| 4 | -y flag (auto-approve) | DONE |
| 5 | Subagent/Task tools | TODO |
| 5 | Plan mode | TODO |

### Launcher Updates
- vibe-local.sh: Updated to use vibe-coder.py directly (no proxy)
- vibe-local.ps1: Updated to use vibe-coder.py directly (no proxy)
- install.sh: Node.js/Claude Code now optional
- install.ps1: Node.js/Claude Code now optional

---

## Current State (v0.2)

Working:
- Dual-model routing (qwen3-coder:30b + qwen3:8b sidecar)
- macOS environment injection (brew, /Users/, system_profiler)
- Windows environment injection (winget, %USERPROFILE%, PowerShell)
- Proxy auto-restart on code update (mtime detection)
- Tool filtering (20 → 9 essential tools)
- XML tool call fallback
- Zero crashes, zero errors in proxy
- Speed test via curl example works
- HTML preferred over pygame for GUI
- **Windows support** (install.ps1, vibe-local.ps1, vibe-local.cmd)
- **Init probe fast-path** (instant response for connectivity checks)
- **tool_choice forwarding** (Anthropic→OpenAI format conversion)
- **stop_sequences / top_p / top_k forwarding**
- **Image/Vision support** (Anthropic base64→OpenAI data URI)
- **Ollama tokenize** for accurate token counting
- **Security**: replay scripts 0o700, 50MB request size limit

## P0 — Critical (Breaks User Experience)

### P0-1: Response verbosity / TOOL FIRST not effective enough
**Status**: IN PROGRESS
**Impact**: Every response has unnecessary explanation
**Evidence**:
- "回線速度を測定するには、専用のツールが必要です。まずは、そのツールをインストールしてみますか？" (should just `brew install`)
- "はい、Gitにアクセスできます。現在の状態では..." (4 bullet points nobody asked for)
- "Python3がインストールされており..." (3 lines of explanation for `python3 --version`)

**Fix**: Strengthen system prompt: "ZERO preamble text before tool calls. After tool result, reply in 1 sentence MAX. NEVER ask follow-up questions."

### P0-2: Follow-up questions after every action
**Status**: IN PROGRESS
**Impact**: Model asks "何か特定の操作が必要ですか？" after EVERY response
**Evidence**: Appears 4 times in one session
**Fix**: Add rule "NEVER end with a question. Wait for user's next instruction."

### P0-3: Won't run commands for user
**Status**: IN PROGRESS
**Impact**: Tells user to run commands instead of using Bash tool
**Evidence**: "ゲームを実行するには、以下のコマンドをターミナルで実行してください" instead of `Bash(python3 minesweeper.py)`
**Fix**: Add rule "ALWAYS execute commands yourself using Bash. NEVER tell user to run commands."

### P0-4: curl URL quoting (zsh glob)
**Status**: IN PROGRESS
**Impact**: URLs with `?` fail on first try in zsh
**Evidence**: `no matches found: https://speed.cloudflare.com/__down?bytes=10000000`
**Fix**: Update speed test example with quoted URL. Add rule "ALWAYS quote URLs containing ? or & in Bash commands."

### P0-5: `\\n` literal in curl output
**Status**: IN PROGRESS
**Impact**: curl -w format shows `\\n` as text instead of newline
**Evidence**: `速度: 62497265 bytes/sec\\nダウンロード時間: 0.160007s\\n`
**Fix**: Fix speed test example to use `$'\n'` or avoid newlines entirely.

### P0-6: WebSearch returns fabricated URLs
**Status**: DONE ✅ (verified 2026-02-22)
**Impact**: WebSearch sidecar calls go to Ollama which can't search → model fabricates URLs
**Evidence**: Sidecar request with `tool_choice: {name: "web_search"}` gets proxied to Ollama, returns empty, model invents sources
**Fix**: Proxy intercepts WebSearch sidecar calls (`tool_choice.name == "web_search"`), performs real DuckDuckGo HTML search (`html.duckduckgo.com`), returns actual results as text. 8 results in ~1.3s. Offline fallback returns honest "not available" message.
**Key learning**: Detection must check `tool_choice.name` only (not `tools` array), because Claude Code sends `tools: [web_search_tool]` (1 element) not `tools: []`. Response uses simple text format (not `server_tool_use` + `web_search_tool_result` which requires opaque `encrypted_content`). "Did 0 searches" counter is cosmetic only — real results are used.

## P1 — High (Functional Issues)

### P1-1: Auto-install dependencies
**Status**: TODO
**Impact**: Won't install pygame before running, tells user to do it
**Evidence**: "Pygameライブラリがインストールされている必要があります" → should `pip3 install pygame` first
**Fix**: Add rule "Install missing dependencies with pip3/brew BEFORE running scripts."

### P1-2: stdin limitation awareness
**Status**: TODO
**Impact**: Runs scripts with `input()` via Bash → EOFError
**Evidence**: minesweeper.py with `input()` → `EOFError: EOF when reading a line`
**Fix**: Add rule "Scripts with input()/stdin CANNOT run in Bash. Use GUI (pygame/HTML) or rewrite to accept args."

### P1-3: Don't give up on errors
**Status**: TODO
**Impact**: Model suggests going back to browser version instead of fixing Python version
**Evidence**: "代わりに、ブラウザ版のマインスイーパを使用してください" after one error
**Fix**: Already partially in prompt ("If a tool fails, try alternative"), strengthen with "NEVER suggest user do something else. Fix the problem."

### P1-4: Mixed language output
**Status**: TODO
**Impact**: Chinese characters randomly appear in Japanese responses
**Evidence**: "是非" (Chinese context), "或者" (Chinese conjunction)
**Fix**: Add "Reply in the SAME language as the user. If user writes Japanese, reply in Japanese only."

## P2 — Medium (System/Proxy Improvements)

### P2-1: Sidecar cold start (18.5s)
**Status**: MITIGATED (init probe fast-path bypasses Ollama entirely for probe requests)
**Impact**: First sidecar request takes 18s while model loads into VRAM
**Fix**: Init probe fast-path added (A1). Warmup thread already exists in proxy startup.

### P2-2: Proxy log overwrite
**Status**: TODO
**Impact**: proxy.log loses previous session data on restart
**Fix**: Use append mode (`>>`) in vibe-local.sh instead of overwrite (`>`)

### P2-3: env_injected tracking in debug
**Status**: TODO
**Impact**: Cannot audit environment injection from debug files
**Fix**: Add `env_injected` field to req_meta log output. Estimated: 3 lines.

### P2-4: Prompt token growth unbounded
**Status**: TODO
**Impact**: In 94-request sessions, prompt grows from 8K to 12.6K tokens
**Risk**: May exceed model context window in very long sessions
**Fix**: Consider conversation truncation strategy (drop old tool results, keep system + last N turns)

### P2-5: Write tool latency (30-46s)
**Status**: ACCEPTED (inherent to 30B model)
**Impact**: Large file writes take 30-46 seconds
**Mitigation**: Could chunk writes, but complexity vs benefit is low

## P3 — Nice to Have

### P3-1: Tool variety
**Status**: TODO
**Impact**: Model only uses Bash/Write, rarely Read/Glob/Grep
**Fix**: Better tool guide examples in prompt

### P3-2: Native streaming for tool-use
**Status**: TODO
**Impact**: Currently forces sync mode for tool requests (sync-as-SSE)
**Fix**: Investigate if Ollama supports streaming with function calling

### P3-3: Empty session directory cleanup
**Status**: TODO
**Impact**: 6 empty dirs from rapid restarts
**Fix**: Don't create session dir until first request, or cleanup on shutdown

### P3-4: Legacy debug file migration
**Status**: TODO
**Impact**: ~189 files in root proxy-debug/ from before session dirs
**Fix**: One-time cleanup script

## Windows Support (v0.2)

**Status**: DONE
- `install.ps1` / `install.cmd`: Windows installer with winget-based dependency installation
- `vibe-local.ps1` / `vibe-local.cmd`: Windows launcher (PowerShell + CMD wrapper)
- Proxy cross-platform fixes: Windows log paths, chmod tolerance, Windows OS hints
- `install.sh` detects MINGW/MSYS/CYGWIN and redirects to PowerShell installer
- `.gitattributes` for correct line endings (LF for .py/.sh, CRLF for .ps1/.cmd)

## Proxy Improvements (v0.2)

| ID | Feature | Status |
|----|---------|--------|
| A1 | Init probe fast-path | DONE |
| A2 | tool_choice forwarding | DONE |
| A3 | stop_sequences forwarding | DONE |
| A4 | top_p / top_k forwarding | DONE |
| A5 | Token count via Ollama tokenize | DONE |
| A6 | Image/Vision support | DONE |
| A7 | Windows cross-platform | DONE |
| A8 | Security (0o700, 50MB limit) | DONE |

## Metrics to Track

| Metric | Session 1 (pre-fix) | Session 2 (post-fix) | Target |
|--------|---------------------|---------------------|--------|
| Text-only responses | 53% | ~40% | <20% |
| Linux-isms | 7 | 0 | 0 |
| "I cannot" patterns | 3 | 0 | 0 |
| Follow-up questions | N/A | 4 | 0 |
| Tool variety (unique) | 3/9 | 3/9 | 5/9 |
| Avg response length | ~200 chars | ~150 chars | <80 chars |
| Sidecar cold start | 18.5s | 18.5s | <2s |
