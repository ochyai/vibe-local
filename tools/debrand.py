#!/usr/bin/env python3
"""opencodeバイナリの可視ブランディングを vibe code に差し替えるパッチツール。

opencode は Bun でコンパイルされた単一バイナリで、JSソースがそのまま
埋め込まれている。文字列リテラルを「同一バイト長」で置換する限り、
モジュールオフセットを壊さず安全にパッチできる。

置換対象 (画面に見えるものだけ):
  1. CLIヘルプ等の平置きロゴ  var O=["⠀...","█▀▀█ ...", ...]   "opencode"
  2. TUIホームの分割ロゴ      {left:[...4行...],right:[...]}    "open"+"code"
  3. 縮小ロゴ                 t={left:[...],right:[...]}        2文字版

機能的な文字列 (設定パス、URL、環境変数名、opencode.json 等) には触れない。
ウィンドウタイトルはパッチではなく OPENCODE_DISABLE_TERMINAL_TITLE=1 で対応
(ランチャー側)。

使い方:
  python3 debrand.py <入力opencode> <出力パス>

終了コード: 0=成功 / 1=失敗(パターン不一致など。呼び出し側は素のopencodeに
フォールバックすること)
"""
import re
import shutil
import subprocess
import sys


def esc(s: str) -> bytes:
    """非ASCII文字を \\uXXXX にエスケープした JS 文字列(内容のみ)を bytes で返す"""
    out = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
        else:
            out.append("\\u%04x" % ord(ch))
    return "".join(out).encode()


def js_array(rows, pad_to: int) -> bytes:
    """["r1","r2",...] を組み立て、pad_to バイトに満たない分は ] の前に空白を詰める"""
    body = b"[" + b",".join(b'"' + esc(r) + b'"' for r in rows) + b"]"
    if len(body) > pad_to:
        raise ValueError(f"replacement too long: {len(body)} > {pad_to}")
    return body[:-1] + b" " * (pad_to - len(body)) + b"]"


# --- 新ロゴアート ------------------------------------------------------
# 平置きロゴ "vibecode" (旧 "opencode"、8文字・幅39・4行)
FLAT = [
    "⠀     ▀   ▄                      ▄     ",
    "█  █  █   █▀▀█ █▀▀█ █▀▀▀ █▀▀█ █▀▀█ █▀▀█",
    "█  █  █   █  █ █▀▀▀ █    █  █ █  █ █▀▀▀",
    " ▀▀   ▀   ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀",
]

# TUIホーム分割ロゴの左側 "vibe" (旧 "open"、幅19・4行)
# レンダラの文字マップ: '_'→空白, '^'→▀(メイン色), '~'→▀(アクセント色)
SPLIT_LEFT = [
    "      ▀   ▄        ",
    "█  █  █   █▀▀█ █▀▀█",
    "█__█  █   █__█ █^^^",
    " ~~   ▀   ▀▀▀▀ ▀▀▀▀",
]

# 縮小ロゴ "v"+"c" (旧2文字版、幅4・4行 ×2ブロック)
SMALL_LEFT = ["    ", "█  █", "█__█", "_~~_"]
SMALL_RIGHT = ["    ", "█▀▀▀", "█___", "▀▀▀▀"]


def patch(data: bytes) -> tuple[bytes, list[str]]:
    log = []

    # 1. 平置きロゴ: ["⠀ ... 4要素 ...] を丸ごと置換
    flat_pat = re.compile(
        rb'\["\\u2800[^\]]{0,2000}?\\u2580\\u2580\\u2580\\u2580"\]'
    )
    def flat_sub(m):
        return js_array(FLAT, len(m.group(0)))
    data, n = flat_pat.subn(flat_sub, data)
    log.append(f"flat logo: {n}")
    if n == 0:
        raise ValueError("flat logo pattern not found")

    # 2. TUI分割ロゴの左ブロック: {left:[...4行...],right: の left 配列を置換
    #    行2 "█__█ █__█ █^^^ █__█" が "open" 固有のシグネチャ
    left_pat = re.compile(
        rb'left:(\[[^\]]{0,800}?'
        rb'\\u2588__\\u2588 \\u2588__\\u2588 \\u2588\^\^\^ \\u2588__\\u2588'
        rb'[^\]]{0,800}?\]),right:'
    )
    def left_sub(m):
        return b"left:" + js_array(SPLIT_LEFT, len(m.group(1))) + b",right:"
    data, n = left_pat.subn(left_sub, data)
    log.append(f"split logo left: {n}")
    if n == 0:
        raise ValueError("split logo pattern not found")

    # 3. 縮小ロゴ: {left:["    ","█▀▀▀","█_^█","▀▀▀▀"],right:["    ","█▀▀█","█__█","▀▀▀▀"]}
    small_pat = re.compile(
        rb'\{left:(\["    ","\\u2588\\u2580\\u2580\\u2580","\\u2588_\^\\u2588",'
        rb'"\\u2580\\u2580\\u2580\\u2580"\]),right:(\["    ","\\u2588\\u2580\\u2580\\u2588",'
        rb'"\\u2588__\\u2588","\\u2580\\u2580\\u2580\\u2580"\])\}'
    )
    def small_sub(m):
        return (b"{left:" + js_array(SMALL_LEFT, len(m.group(1)))
                + b",right:" + js_array(SMALL_RIGHT, len(m.group(2))) + b"}")
    data, n = small_pat.subn(small_sub, data)
    log.append(f"small logo: {n}")   # 見つからなくても致命ではない

    # 4. ホーム画面のTip: /share は vibe-local では無効化しているうえ
    #    opencode.ai に言及するため、ローカル動作の紹介文に差し替える
    old_tip = b"Run {highlight}/share{/highlight} to create a public opencode.ai link"
    new_tip = b"No internet needed: {highlight}vibe{/highlight} runs on this machine"
    if len(new_tip) > len(old_tip):
        raise ValueError("tip replacement too long")
    new_tip += b" " * (len(old_tip) - len(new_tip))
    n = data.count(old_tip)
    data = data.replace(old_tip, new_tip)
    log.append(f"share tip: {n}")

    return data, log


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    orig_len = len(data)
    try:
        data, log = patch(data)
    except ValueError as e:
        print(f"debrand: パターン不一致: {e}", file=sys.stderr)
        return 1
    assert len(data) == orig_len, "byte length changed!"
    with open(dst, "wb") as f:
        f.write(data)
    shutil.copymode(src, dst)
    # macOS arm64 は署名必須。ad-hoc で再署名する
    if sys.platform == "darwin":
        r = subprocess.run(["codesign", "--force", "-s", "-", dst],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"debrand: codesign失敗: {r.stderr}", file=sys.stderr)
            return 1
    print("debrand: " + ", ".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
