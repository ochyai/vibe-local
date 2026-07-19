# v1.3.2 — DECSTBM Scroll Region, PowerShell Fix

> **Fixed footer display** — Separator, status line, and hint bar now stay pinned to the bottom of your terminal while AI output scrolls above.

## Highlights

### Fixed Footer with DECSTBM Scroll Region

The terminal now uses VT100 DECSTBM (Set Top and Bottom Margins) to create a scroll region. AI output scrolls within the upper area while a 3-row footer (separator, status, hints) stays fixed at the bottom.

```
┌─────────────────────────────────────┐
│  AI output scrolls here             │  ← Scroll region (rows 1..N-3)
│  ...                                │
│  ...                                │
├─────────────────────────────────────┤  ← Separator (row N-2)
│  ✦ Ready                            │  ← Status line (row N-1)
│  /help ∙ """ multi-line ∙ Ctrl+C    │  ← Hint bar (row N)
└─────────────────────────────────────┘
```

**Key design decisions:**
- **Store-only pattern** — `update_status()` / `update_hint()` only store text. No terminal writes. Footer is drawn atomically during `setup()` and `resize()`.
- **Real-time feedback** — Spinner, thinking indicator, tool status, and heartbeat use `\r` within the scroll region (not the footer area).
- **Thread-safe** — All `_active` checks inside lock. `resize()` uses non-blocking lock to prevent SIGWINCH deadlock.
- **Atomic writes** — `os.write(fd, bytes)` for single-syscall terminal output (< PIPE_BUF = 4096).

### ESC Interrupt (v1.3.0)

Press `ESC` during AI response to interrupt generation immediately. Faster than `Ctrl+C` with no delay.

### Type-Ahead Input (v1.3.1)

Start typing your next prompt while the AI is still responding. Input is buffered and ready when the prompt appears.

### `/debug-scroll` Command

Interactive diagnostic for DECSTBM behavior. Tests scroll region setup, footer drawing, and cursor positioning. Reports environment info (OS, TERM, terminal size).

```
> /debug-scroll
```

### Debug Logging

Set `VIBE_DEBUG_TUI=1` to log all escape sequences to `~/.vibe-tui-debug.log`. Essential for diagnosing terminal rendering issues.

```bash
VIBE_DEBUG_TUI=1 python3 vibe-coder.py
```

### Fallback Mode

If your terminal doesn't support DECSTBM properly, disable the scroll region:

```bash
VIBE_NO_SCROLL=1 vibe-local
```

## Stats

| Metric | Value |
|--------|-------|
| Lines of code | ~7,400 |
| Unit tests | 780 |
| PTY integration tests | 7 |
| Total tests | 787 |
| External dependencies | 0 |
| Files | 1 (vibe-coder.py) |

## Changes since v1.2.0

### New Features
- DECSTBM scroll region with fixed footer (separator + status + hints)
- ESC key interrupt during AI response
- Type-ahead input buffer
- `/debug-scroll` diagnostic command
- `VIBE_DEBUG_TUI=1` escape sequence logging
- `VIBE_NO_SCROLL=1` fallback mode
- Compact tool display (icons instead of full names)
- Heartbeat progress indicator during long operations
- Error checkmark display improvements

### Bug Fixes
- Fixed PowerShell parameter binding — `-p` no longer conflicts with `-ProgressAction` (PR #10 by @kn1cht)
- Fixed `[` character leaking into output from broken CSI sequences
- Fixed cursor position after DECSTBM setup on macOS Terminal.app
- Fixed status line update during streaming (store-only pattern)
- Fixed SIGWINCH resize deadlock (non-blocking lock)
- Fixed stale `_sr_active` cache in stream response
- Fixed teardown crash when `_rows <= 0`
- Fixed WebSearch HTML entity decoding
- Fixed spinner clear length for short labels

### Thread Safety
- All `_active` checks moved inside lock
- `resize()` uses `lock.acquire(blocking=False)` for signal handler safety
- Direct `_sr._active` query replaces stale boolean cache
- Teardown guards against zero-dimension terminals

### Testing
- 780 unit tests + 7 PTY integration tests = 787 total
- Added thread safety tests (non-blocking lock, zero-rows guard, double-activation)
- Added store-only behavior verification tests
- All tests pass on macOS, Linux, and Windows (WSL)

## Compatibility

| Terminal | DECSTBM | Notes |
|----------|:-------:|-------|
| macOS Terminal.app | Yes | Tested, works with store-only pattern |
| iTerm2 | Yes | Full support |
| Alacritty | Yes | Full support |
| Windows Terminal | Yes | Full support |
| Linux VT (TTY) | Yes | Full support |
| VS Code Terminal | Yes | Full support |
| tmux / screen | Yes | Works within multiplexer |

If you encounter display issues, use `VIBE_NO_SCROLL=1` to disable the scroll region.

## Full Changelog

- `56a1f8f` fix: PowerShell parameter binding (PR #10 by @kn1cht)
- `bc491ab` docs: update README for v1.3.1 + add release notes
- `7a8f81f` feat: DECSTBM scroll region — fixed footer with thread-safe store-only pattern
- `9f8fce6` fix: error checkmark, status line guards, tool icons, heartbeat (UX audit)
- `9725859` feat: v1.3.1 — type-ahead input, WebSearch HTML entity fix, UX improvements
- `05fc6fd` feat: v1.3.0 — ESC interrupt, status line, compact tool display

---

**Full diff**: https://github.com/ochyai/vibe-local/compare/v1.3.1...v1.3.2
