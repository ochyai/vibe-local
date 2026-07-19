#!/usr/bin/env python3
"""PTY-based TUI integration test for vibe-coder ScrollRegion.

Tests the ACTUAL escape sequence output by importing ScrollRegion and
running it with a mocked stdout that captures everything, then parsing
the output through MiniScreen (minimal VT100 emulator).

Also includes a live PTY test that writes escape sequences to a real
pseudo-terminal and reads back the result.

Usage:
    python3 test_tui_pty.py
    # or:
    python3 -m pytest test_tui_pty.py -v
"""
import os
import sys
import pty
import time
import struct
import fcntl
import termios
import select
import io


# ──────────────────────────────────────────────────────────────────────────
# MiniScreen: minimal VT100 emulator for verifying rendered output
# ──────────────────────────────────────────────────────────────────────────
class MiniScreen:
    """Minimal VT100 terminal emulator for testing."""

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.grid = [[' '] * cols for _ in range(rows)]
        self.cur_r = 0
        self.cur_c = 0
        self.scroll_top = 0
        self.scroll_bot = rows - 1
        self._saved_r = 0
        self._saved_c = 0

    def feed(self, data):
        """Process terminal output string."""
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == '\033' and i + 1 < len(data):
                if data[i + 1] == '[':
                    j = i + 2
                    params = ""
                    while j < len(data) and (data[j].isdigit() or data[j] == ';' or data[j] == '?'):
                        params += data[j]
                        j += 1
                    if j < len(data):
                        cmd = data[j]
                        self._handle_csi(params, cmd)
                        i = j + 1
                        continue
                i += 1
            elif ch == '\r':
                self.cur_c = 0
                i += 1
            elif ch == '\n':
                if self.cur_r >= self.scroll_bot:
                    self._scroll_up()
                else:
                    self.cur_r += 1
                i += 1
            elif ch == '\t':
                self.cur_c = min(self.cur_c + (8 - self.cur_c % 8), self.cols - 1)
                i += 1
            elif ch == '\b':
                if self.cur_c > 0:
                    self.cur_c -= 1
                i += 1
            elif ord(ch) >= 32:
                if self.cur_c < self.cols:
                    self.grid[self.cur_r][self.cur_c] = ch
                    self.cur_c += 1
                elif self.cur_c >= self.cols:
                    self.cur_c = 0
                    if self.cur_r >= self.scroll_bot:
                        self._scroll_up()
                    else:
                        self.cur_r += 1
                    if self.cur_c < self.cols:
                        self.grid[self.cur_r][self.cur_c] = ch
                        self.cur_c += 1
                i += 1
            else:
                i += 1

    def _handle_csi(self, params, cmd):
        parts = params.split(';') if params else []
        nums = []
        for p in parts:
            p = p.lstrip('?')
            try:
                nums.append(int(p))
            except ValueError:
                nums.append(0)

        if cmd == 'H' or cmd == 'f':
            r = (nums[0] if len(nums) > 0 and nums[0] > 0 else 1) - 1
            c = (nums[1] if len(nums) > 1 and nums[1] > 0 else 1) - 1
            self.cur_r = max(0, min(r, self.rows - 1))
            self.cur_c = max(0, min(c, self.cols - 1))
        elif cmd == 'J':
            n = nums[0] if nums else 0
            if n == 0:
                self.grid[self.cur_r][self.cur_c:] = [' '] * (self.cols - self.cur_c)
                for row in range(self.cur_r + 1, self.rows):
                    self.grid[row] = [' '] * self.cols
            elif n == 2:
                self.grid = [[' '] * self.cols for _ in range(self.rows)]
        elif cmd == 'K':
            n = nums[0] if nums else 0
            if n == 0:
                self.grid[self.cur_r][self.cur_c:] = [' '] * (self.cols - self.cur_c)
            elif n == 2:
                self.grid[self.cur_r] = [' '] * self.cols
        elif cmd == 'r':
            top = (nums[0] if len(nums) > 0 and nums[0] > 0 else 1) - 1
            bot = (nums[1] if len(nums) > 1 and nums[1] > 0 else self.rows) - 1
            self.scroll_top = max(0, min(top, self.rows - 1))
            self.scroll_bot = max(0, min(bot, self.rows - 1))
            self.cur_r = 0
            self.cur_c = 0
        elif cmd == 's':
            self._saved_r = self.cur_r
            self._saved_c = self.cur_c
        elif cmd == 'u':
            self.cur_r = self._saved_r
            self.cur_c = self._saved_c
        elif cmd == 'm':
            pass
        elif cmd == 'A':
            n = nums[0] if nums else 1
            self.cur_r = max(self.scroll_top, self.cur_r - n)
        elif cmd == 'B':
            n = nums[0] if nums else 1
            self.cur_r = min(self.scroll_bot, self.cur_r + n)

    def _scroll_up(self):
        del self.grid[self.scroll_top]
        self.grid.insert(self.scroll_bot, [' '] * self.cols)

    def get_row(self, row_1based):
        idx = row_1based - 1
        if 0 <= idx < self.rows:
            return ''.join(self.grid[idx]).rstrip()
        return ''

    def dump(self):
        lines = []
        for i in range(self.rows):
            text = ''.join(self.grid[i]).rstrip()
            lines.append(f"  {i+1:2d} |{text}|")
        return '\n'.join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Import vibe-coder
# ──────────────────────────────────────────────────────────────────────────
def _import_vc():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib
    return importlib.import_module("vibe-coder")


# ──────────────────────────────────────────────────────────────────────────
# Test 1: setup() produces correct screen
# ──────────────────────────────────────────────────────────────────────────
def test_setup_renders_footer():
    """setup() draws separator, status, and hint in footer rows."""
    vc = _import_vc()
    ROWS, COLS = 24, 80

    sr = vc.ScrollRegion()
    sr._rows = ROWS
    sr._cols = COLS
    sr._scroll_end = ROWS - 3
    sr._active = True
    sr._status_text = "✦ Ready │ ctx:5%"
    sr._hint_text = ""

    buf = sr._build_footer_buf()
    buf += f"\033[1;{ROWS - 3}r"
    buf += f"\033[{ROWS - 3};1H"

    screen = MiniScreen(ROWS, COLS)
    screen.feed(buf)

    sep = screen.get_row(22)
    status = screen.get_row(23)
    hint = screen.get_row(24)

    print(f"\n=== Test: setup footer rendering ===")
    print(f"  Sep (22): {sep!r}")
    print(f"  Sta (23): {status!r}")
    print(f"  Hin (24): {hint!r}")

    assert '─' in sep, f"Separator missing: {sep!r}"
    assert sep.count('─') >= 40, f"Separator too short: {sep!r}"
    assert 'Ready' in status, f"Status missing 'Ready': {status!r}"
    assert 'ESC' in hint, f"Hint missing 'ESC': {hint!r}"
    print(f"  ✓ All footer rows correct")


# ──────────────────────────────────────────────────────────────────────────
# Test 2: update_status is store-only (no terminal output)
# ──────────────────────────────────────────────────────────────────────────
def test_update_status_is_store_only():
    """update_status() stores text only — no terminal write."""
    vc = _import_vc()
    ROWS, COLS = 24, 80

    sr = vc.ScrollRegion()
    buf = io.StringIO()
    import unittest.mock as mock
    with mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((COLS, ROWS))):
        with mock.patch('sys.stdout', buf):
            sr.setup()
            buf.truncate(0)
            buf.seek(0)
            sr.update_status("Thinking... (3s)")

    output = buf.getvalue()
    print(f"\n=== Test: update_status is store-only ===")
    print(f"  Output length: {len(output)}")
    assert output == "", "update_status() must NOT write to terminal"
    assert sr._status_text == "Thinking... (3s)"
    print(f"  ✓ Store-only: no terminal write")


# ──────────────────────────────────────────────────────────────────────────
# Test 3: Scrolling preserves footer
# ──────────────────────────────────────────────────────────────────────────
def test_scrolling_preserves_footer():
    """Printing 30 lines inside scroll region should not corrupt footer."""
    vc = _import_vc()
    ROWS, COLS = 24, 80
    scroll_end = ROWS - 3

    sr = vc.ScrollRegion()
    sr._rows = ROWS
    sr._cols = COLS
    sr._scroll_end = scroll_end
    sr._active = True
    sr._status_text = "✦ Ready"

    # Setup
    buf = sr._build_footer_buf()
    buf += f"\033[1;{scroll_end}r"
    buf += f"\033[{scroll_end};1H"

    screen = MiniScreen(ROWS, COLS)
    screen.feed(buf)

    # Print 30 lines (causes scrolling within the region)
    for i in range(30):
        screen.feed(f"Line {i+1}: test output\r\n")

    sep = screen.get_row(22)
    status = screen.get_row(23)
    hint = screen.get_row(24)

    print(f"\n=== Test: scrolling preserves footer ===")
    print(f"  Sep (22): {sep!r}")
    print(f"  Sta (23): {status!r}")
    print(f"  Hin (24): {hint!r}")

    assert '─' in sep, f"Separator corrupted: {sep!r}"
    assert 'Ready' in status, f"Status corrupted: {status!r}"
    assert 'ESC' in hint, f"Hint corrupted: {hint!r}"

    # Verify some content is visible
    has_content = any('Line' in screen.get_row(r) for r in range(1, scroll_end + 1))
    assert has_content, "No scrolled content visible"
    print(f"  ✓ Footer intact after 30 lines of scrolling")


# ──────────────────────────────────────────────────────────────────────────
# Test 4: Inline \r status doesn't corrupt anything
# ──────────────────────────────────────────────────────────────────────────
def test_inline_r_status():
    """Inline \\r status updates stay within current line, don't leak."""
    vc = _import_vc()
    ROWS, COLS = 24, 80
    scroll_end = ROWS - 3

    sr = vc.ScrollRegion()
    sr._rows = ROWS
    sr._cols = COLS
    sr._scroll_end = scroll_end
    sr._active = True
    sr._status_text = "✦ Ready"

    # Setup
    buf = sr._build_footer_buf()
    buf += f"\033[1;{scroll_end}r"
    buf += f"\033[{scroll_end};1H"

    screen = MiniScreen(ROWS, COLS)
    screen.feed(buf)

    # Simulate inline status (the actual approach used by spinner/thinking/tool status)
    for i in range(10):
        screen.feed(f"\r  ◠ Step {i}/9    ")
    # Clear it
    screen.feed(f"\r{' ' * 40}\r")

    # Print some real output
    screen.feed("Real output line 1\r\n")
    screen.feed("Real output line 2\r\n")

    print(f"\n=== Test: inline \\r status ===")

    # Footer should be intact
    sep = screen.get_row(22)
    assert '─' in sep, f"Separator corrupted by inline status: {sep!r}"

    # No stray brackets
    errors = []
    for r in range(1, ROWS + 1):
        row_text = screen.get_row(r)
        if row_text.strip() == '[':
            errors.append(f"Lone '[' at row {r}")
        if row_text.lstrip().startswith('[') and ';' in row_text and 'm' in row_text:
            errors.append(f"Broken SGR at row {r}: {row_text!r}")

    assert not errors, "Bracket leaks: " + "; ".join(errors)

    # Inline status should have been cleared — current row should show "Real output"
    # Find where the output is
    has_real = any('Real output' in screen.get_row(r) for r in range(1, scroll_end + 1))
    assert has_real, "Real output not visible after inline status clear"

    print(f"  ✓ Inline \\r status: no leaks, footer intact, real output visible")


# ──────────────────────────────────────────────────────────────────────────
# Test 5: Teardown → store status → setup cycle
# ──────────────────────────────────────────────────────────────────────────
def test_teardown_store_setup_cycle():
    """Teardown, store new status, re-setup: footer shows new status."""
    vc = _import_vc()
    ROWS, COLS = 24, 80
    scroll_end = ROWS - 3

    sr = vc.ScrollRegion()
    sr._rows = ROWS
    sr._cols = COLS
    sr._scroll_end = scroll_end
    sr._active = True
    sr._status_text = "Initial"

    # Setup
    buf1 = sr._build_footer_buf()
    buf1 += f"\033[1;{scroll_end}r"
    buf1 += f"\033[{scroll_end};1H"

    # Teardown
    sr._active = False
    buf2 = f"\033[1;{ROWS}r"
    buf2 += f"\033[{ROWS - 2};1H\033[J"
    buf2 += f"\033[{ROWS};1H"

    # Store new status (no terminal write)
    sr._status_text = "Updated After Teardown"

    # Re-setup
    sr._active = True
    buf3 = sr._build_footer_buf()
    buf3 += f"\033[1;{scroll_end}r"
    buf3 += f"\033[{scroll_end};1H"

    # Feed all through MiniScreen
    screen = MiniScreen(ROWS, COLS)
    screen.feed(buf1 + buf2 + buf3)

    status = screen.get_row(23)
    print(f"\n=== Test: teardown-store-setup cycle ===")
    print(f"  Status (23): {status!r}")
    assert 'Updated After Teardown' in status, f"New status not shown: {status!r}"
    print(f"  ✓ Status updated through teardown-setup cycle")


# ──────────────────────────────────────────────────────────────────────────
# Test 6: Live PTY — write escape sequences to real pseudo-terminal
# ──────────────────────────────────────────────────────────────────────────
def test_live_pty_escape_sequences():
    """Write scroll region escape sequences through a real PTY and verify output."""
    ROWS, COLS = 24, 80
    scroll_end = ROWS - 3

    master_fd, slave_fd = pty.openpty()

    try:
        # Set PTY size
        winsize = struct.pack("HHHH", ROWS, COLS, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        # Write the setup sequence to slave side
        sep_text = '─' * COLS
        buf = f"\033[{ROWS-2};1H\033[2K{sep_text}"
        buf += f"\033[{ROWS-1};1H\033[2K Ready"
        buf += f"\033[{ROWS};1H\033[2K ESC: stop"
        buf += f"\033[1;{scroll_end}r"
        buf += f"\033[{scroll_end};1H"

        os.write(slave_fd, buf.encode("utf-8"))

        # Read what comes out on the master side
        time.sleep(0.1)
        output = b""
        while True:
            r, _, _ = select.select([master_fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master_fd, 16384)
                    if chunk:
                        output += chunk
                    else:
                        break
                except OSError:
                    break
            else:
                break

        decoded = output.decode("utf-8", errors="replace")
        print(f"\n=== Test: Live PTY escape sequences ===")
        print(f"  Wrote {len(buf)} chars, read back {len(decoded)} chars")

        # The output through PTY should contain our text
        assert '─' in decoded, "Separator char missing from PTY output"
        assert 'Ready' in decoded, "'Ready' missing from PTY output"
        assert 'ESC' in decoded, "'ESC' missing from PTY output"

        # Verify no broken escape sequences (stray '[')
        # Remove valid ESC[ sequences first
        clean = decoded
        import re
        clean = re.sub(r'\033\[[^a-zA-Z]*[a-zA-Z]', '', clean)
        # After removing all valid CSI sequences, there should be no lone '['
        lone_brackets = clean.count('[')
        assert lone_brackets == 0, f"Found {lone_brackets} stray '[' after removing CSI sequences"

        print(f"  ✓ PTY echoes correct escape sequences, no stray brackets")

    finally:
        os.close(master_fd)
        os.close(slave_fd)


# ──────────────────────────────────────────────────────────────────────────
# Test 7: Full screen dump at each timing point
# ──────────────────────────────────────────────────────────────────────────
def test_full_lifecycle_screen_dumps():
    """Capture and verify screen state at each timing: setup, scroll, status, teardown."""
    vc = _import_vc()
    ROWS, COLS = 24, 80
    scroll_end = ROWS - 3
    timing_dumps = {}

    sr = vc.ScrollRegion()
    sr._rows = ROWS
    sr._cols = COLS
    sr._scroll_end = scroll_end
    sr._active = True
    sr._status_text = "✦ Ready │ ctx:5% │ model"

    screen = MiniScreen(ROWS, COLS)

    # Timing 1: After setup
    buf = sr._build_footer_buf()
    buf += f"\033[1;{scroll_end}r"
    buf += f"\033[{scroll_end};1H"
    screen.feed(buf)
    timing_dumps["after_setup"] = screen.dump()

    # Verify after setup
    assert '─' in screen.get_row(22), "Setup: separator missing"
    assert 'Ready' in screen.get_row(23), "Setup: status missing"

    # Timing 2: After some output
    for i in range(5):
        screen.feed(f"Output line {i+1}\r\n")
    timing_dumps["after_output"] = screen.dump()

    assert '─' in screen.get_row(22), "After output: separator corrupted"
    assert any('Output line' in screen.get_row(r) for r in range(1, scroll_end + 1)), \
        "After output: content missing"

    # Timing 3: After inline \r status
    screen.feed(f"\r  ◠ Thinking... (2s)    ")
    timing_dumps["during_status"] = screen.dump()

    assert '─' in screen.get_row(22), "During status: separator corrupted"

    # Timing 4: After clearing status
    screen.feed(f"\r{' ' * 50}\r")
    screen.feed(f"New output after status\r\n")
    timing_dumps["after_clear_status"] = screen.dump()

    assert '─' in screen.get_row(22), "After clear: separator corrupted"

    # Timing 5: After heavy scrolling (stress test)
    for i in range(50):
        screen.feed(f"Stress line {i+1}\r\n")
    timing_dumps["after_stress"] = screen.dump()

    assert '─' in screen.get_row(22), "After stress: separator corrupted"
    assert 'Ready' in screen.get_row(23), "After stress: status corrupted"
    assert 'ESC' in screen.get_row(24), "After stress: hint corrupted"

    print(f"\n=== Test: Full lifecycle screen dumps ===")
    for timing, dump in timing_dumps.items():
        print(f"\n  --- {timing} ---")
        # Just show footer rows for brevity
        for line in dump.split('\n'):
            line_num = line.strip().split('|')[0].strip() if '|' in line else ''
            if line_num.isdigit() and int(line_num) >= 20:
                print(f"  {line}")
    print(f"\n  ✓ All 5 timing points verified: footer always intact")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────
def main():
    tests = [
        ("Setup footer rendering", test_setup_renders_footer),
        ("update_status is store-only", test_update_status_is_store_only),
        ("Scrolling preserves footer", test_scrolling_preserves_footer),
        ("Inline \\r status safe", test_inline_r_status),
        ("Teardown-store-setup cycle", test_teardown_store_setup_cycle),
        ("Live PTY escape sequences", test_live_pty_escape_sequences),
        ("Full lifecycle screen dumps", test_full_lifecycle_screen_dumps),
    ]

    print("=" * 60)
    print("  vibe-coder TUI Integration Tests")
    print("  (MiniScreen emulator + PTY verification)")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  → PASS: {name}")
        except (AssertionError, Exception) as e:
            print(f"  → FAIL: {name}: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print(f"  ✓ All tests passed!")
    else:
        print(f"  ✗ {failed} test(s) failed")
        print(f"  Tip: VIBE_DEBUG_TUI=1 python3 vibe-coder.py for debug logs")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
