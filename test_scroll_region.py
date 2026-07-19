#!/usr/bin/env python3
"""Standalone DECSTBM scroll region diagnostic — run outside vibe-coder.

Usage:
    python3 test_scroll_region.py

Tests the Reset-Draw-Restore pattern used by vibe-coder's ScrollRegion.
If this script works correctly, vibe-coder's footer should also work.
If it fails, set VIBE_NO_SCROLL=1 to disable scroll region mode.
"""
import os
import sys
import time
import shutil


def main():
    if not sys.stdout.isatty():
        print("ERROR: Not a TTY. Run in a real terminal.")
        return 1

    try:
        size = shutil.get_terminal_size((80, 24))
        rows, cols = size.lines, size.columns
    except (ValueError, OSError):
        print("ERROR: Cannot get terminal size.")
        return 1

    if rows < 10:
        print(f"ERROR: Terminal too small ({rows} rows, need >=10).")
        return 1

    DIM = "\033[38;5;240m"
    CYAN = "\033[38;5;51m"
    PINK = "\033[38;5;198m"
    GREEN = "\033[38;5;82m"
    RST = "\033[0m"

    STATUS_ROWS = 3
    scroll_end = rows - STATUS_ROWS
    sep_row = rows - 2
    status_row = rows - 1
    hint_row = rows

    print(f"{CYAN}{'=' * 50}{RST}")
    print(f"{CYAN}DECSTBM Scroll Region Test{RST}")
    print(f"{DIM}Terminal: {cols}x{rows}  TERM={os.environ.get('TERM', '?')}{RST}")
    print(f"{DIM}Scroll region: rows 1-{scroll_end}, footer: rows {sep_row}-{hint_row}{RST}")
    print(f"{CYAN}{'=' * 50}{RST}")
    print()

    # --- Test 1: Initial footer draw + DECSTBM setup ---
    print(f"{CYAN}[Test 1] Draw footer BEFORE DECSTBM, then set scroll region{RST}")
    time.sleep(0.5)

    footer = f"\033[{sep_row};1H\033[2K{DIM}{'═' * cols}{RST}"
    footer += f"\033[{status_row};1H\033[2K {GREEN}STATUS: Initial draw OK{RST}"
    footer += f"\033[{hint_row};1H\033[2K {DIM}HINT: Press nothing, just watch{RST}"

    buf = footer
    buf += f"\033[1;{scroll_end}r"
    buf += f"\033[{scroll_end};1H"
    sys.stdout.write(buf)
    sys.stdout.flush()

    print(f"  {GREEN}✓ Footer + DECSTBM set{RST}")
    time.sleep(1)

    # --- Test 2: Scrolling within region ---
    print(f"\n{CYAN}[Test 2] Scrolling within DECSTBM region{RST}")
    for i in range(8):
        print(f"  {DIM}Scroll line {i + 1}/8 — footer should stay fixed below{RST}")
        time.sleep(0.2)

    print(f"  {GREEN}✓ Scrolling complete{RST}")
    time.sleep(0.5)

    # --- Test 3: Reset-Draw-Restore status update ---
    print(f"\n{CYAN}[Test 3] Reset-Draw-Restore: update status mid-scroll{RST}")

    footer2 = f"\033[{sep_row};1H\033[2K{DIM}{'═' * cols}{RST}"
    footer2 += f"\033[{status_row};1H\033[2K {PINK}STATUS: Updated via Reset-Draw-Restore!{RST}"
    footer2 += f"\033[{hint_row};1H\033[2K {DIM}HINT: Status changed? Good!{RST}"

    buf2 = f"\033[1;{rows}r"                 # Reset to full screen (explicit)
    buf2 += footer2                          # Draw footer
    buf2 += f"\033[1;{scroll_end}r"         # Restore margins
    buf2 += f"\033[{scroll_end};1H"         # Cursor back
    sys.stdout.flush()
    os.write(sys.stdout.fileno(), buf2.encode("utf-8"))

    print(f"  {GREEN}✓ Status updated{RST}")
    time.sleep(1)

    # --- Test 4: More scrolling after update ---
    print(f"\n{CYAN}[Test 4] Continue scrolling after status update{RST}")
    for i in range(5):
        print(f"  {DIM}Post-update scroll {i + 1}/5{RST}")
        time.sleep(0.2)

    print(f"  {GREEN}✓ Post-update scrolling OK{RST}")
    time.sleep(0.5)

    # --- Cleanup ---
    buf3 = f"\033[1;{rows}r"
    buf3 += f"\033[{rows - 2};1H\033[J"
    buf3 += f"\033[{rows};1H"
    sys.stdout.flush()
    os.write(sys.stdout.fileno(), buf3.encode("utf-8"))

    print(f"\n{CYAN}{'=' * 50}{RST}")
    print(f"{CYAN}Results:{RST}")
    print(f"  {GREEN}✓{RST} Separator (═) visible at row {sep_row}  → DECSTBM footer works")
    print(f"  {GREEN}✓{RST} Scroll lines above separator    → DECSTBM scrolling works")
    print(f"  {GREEN}✓{RST} Status updated without '[' leak  → Reset-Draw-Restore works")
    print(f"  {GREEN}✓{RST} Post-update scrolling intact     → Margin restore works")
    print()
    print(f"  {DIM}If any test showed artifacts or missing footer:{RST}")
    print(f"  {PINK}→ Set VIBE_NO_SCROLL=1 to disable scroll region in vibe-coder{RST}")
    print(f"{CYAN}{'=' * 50}{RST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
