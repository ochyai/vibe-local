#!/bin/bash
# vibe-local.sh (v2)
# ローカルLLM (Ollama) で AIコーディング環境を起動するランチャー
#
# v2: 自作プロキシを廃止。
#   - TUI: OpenCode (マルチプロバイダネイティブ対応, MIT)
#   - 互換: --classic で Claude Code CLI + Ollamaネイティブ /v1/messages
#   - モデル: RAM自動判定で 2026年世代 (qwen3.6 / gpt-oss / qwen3-coder-next / qwen3.5)
#   - コンテキスト: RAM段階に応じた num_ctx を焼き込んだ別名モデル
#     (vibe-coder / vibe-fast) を ollama create で管理 (weightsは共有、容量増なし)
#
# NOTE: This project is NOT affiliated with, endorsed by, or associated with
#       Anthropic, Alibaba, OpenAI, Mistral, or the OpenCode project.
#
# 使い方:
#   vibe-local                    # vibe TUI + ローカルLLM (推奨)
#   vibe-local --auto             # ネットありならClaude Code(クラウド)、なければローカル
#   vibe-local -p "質問"          # ワンショット (run)
#   vibe-local --fast             # 小型モデルを主役にしてサクサク動かす
#   vibe-local --no-router        # 自動振り分けを切って大型モデル直結
#   vibe-local --model TAG        # Ollamaモデル手動指定 (例: --model devstral)
#   vibe-local -y                 # ツール自動許可 (上級者向け・自己責任)
#   vibe-local --theme NAME       # テーマ切替 (null=黒灰 / vaporwave)
#   vibe-local --classic          # Claude Code CLI をローカルLLMで使う (互換モード)
#   vibe-local --vibe-coder ...    # 内蔵Pythonエンジン vibe-coder.py で起動 (RAG対応)
#                                  #   例: vibe-local --vibe-coder --rag --rag-path .
#   vibe-local --serve            # 教室モード: LANにサーバー公開 (実験的)
#   vibe-local --attach URL       # 教室モード: 先生のサーバーに接続 (実験的)
#   vibe-local --doctor           # 環境診断
#   vibe-local --rebuild          # モデル別名とパッチ済みTUIを作り直す
#
#   VIBE_LOCAL_NO_SPLASH=1 vibe-local   # 起動アニメーションを省略

set -uo pipefail

VIBE_VERSION="2.2.0"

# --- パス ---
CONFIG_DIR="${HOME}/.config/vibe-local"
CONFIG_FILE="${CONFIG_DIR}/v2.conf"            # v1/vibe-coder の config とは別ファイル (設定ドリフト対策)
OPENCODE_CONFIG_FILE="${CONFIG_DIR}/opencode.json"
AGENTS_FILE="${CONFIG_DIR}/AGENTS.md"
STATE_DIR="${HOME}/.local/state/vibe-local"
LIB_DIR="${HOME}/.local/lib/vibe-local"
THEME_DIR="${HOME}/.config/opencode/themes"
TUI_JSON="${HOME}/.config/opencode/tui.json"
DEFAULT_THEME="vibe-null"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$THEME_DIR" "${LIB_DIR}/bin" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"

# --- 設定読み込み (既知キーのみ、sourceしない) ---
BASE_MODEL=""
SMALL_BASE=""
NUM_CTX=""
if [ -f "$CONFIG_FILE" ]; then
    _val() { grep -E "^${1}=" "$CONFIG_FILE" 2>/dev/null | head -1 | sed "s/^${1}=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}/\1/" || true; }
    _b="$(_val BASE_MODEL)";  [ -n "$_b" ] && BASE_MODEL="$_b"
    _s="$(_val SMALL_BASE)";  [ -n "$_s" ] && SMALL_BASE="$_s"
    _c="$(_val NUM_CTX)";     [ -n "$_c" ] && NUM_CTX="$_c"
    _h="$(_val OLLAMA_HOST)"; [ -n "$_h" ] && OLLAMA_HOST="$_h"
    unset _val _b _s _c _h
fi

# --- RAM 判定 ---
ram_gb() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo $(( $(sysctl -n hw.memsize) / 1073741824 ))
    else
        echo $(( $(grep MemTotal /proc/meminfo | awk '{print $2}') / 1048576 ))
    fi
}

# --- RAM に応じたモデルマトリクス (2026-07世代) ---
apply_model_matrix() {
    local ram; ram="$(ram_gb)"
    if [ "$ram" -ge 80 ]; then
        BASE_MODEL="${BASE_MODEL:-qwen3-coder-next}";  NUM_CTX="${NUM_CTX:-131072}"
        SMALL_BASE="${SMALL_BASE:-qwen3.5:4b}"
    elif [ "$ram" -ge 32 ]; then
        BASE_MODEL="${BASE_MODEL:-qwen3.6:35b-a3b}";   NUM_CTX="${NUM_CTX:-65536}"
        SMALL_BASE="${SMALL_BASE:-qwen3.5:4b}"
    elif [ "$ram" -ge 16 ]; then
        BASE_MODEL="${BASE_MODEL:-gpt-oss:20b}";       NUM_CTX="${NUM_CTX:-65536}"
        SMALL_BASE=""   # 16GBでは2モデル同時ロードはスワップ地獄になるので main と共用
    elif [ "$ram" -ge 8 ]; then
        BASE_MODEL="${BASE_MODEL:-qwen3.5:4b}";        NUM_CTX="${NUM_CTX:-32768}"
        SMALL_BASE=""
    else
        echo "エラー: メモリが不足しています (${ram}GB)。最低8GB必要です。"
        exit 1
    fi
}

# 小型モデルの焼き込みコンテキスト。TUIのシステムプロンプト+ツール定義が
# 1万トークン弱あるため、16384だとあふれてコンテキストシフト地獄になる
SMALL_CTX=32768

ROUTER_PORT=11435

# --- ollama 起動確認 ---
ensure_ollama() {
    if curl -s --max-time 2 "$OLLAMA_HOST/api/tags" &>/dev/null; then
        return 0
    fi
    echo "🦙 ollama を起動中..."
    # 自前で起動する時はモデル常駐(2h)とflash attentionを効かせる (サクサク対策)
    if [[ "$(uname)" == "Darwin" ]]; then
        launchctl setenv OLLAMA_KEEP_ALIVE 2h 2>/dev/null || true
        open -a Ollama 2>/dev/null || \
            (OLLAMA_KEEP_ALIVE=2h OLLAMA_FLASH_ATTENTION=1 ollama serve &>/dev/null &)
    else
        OLLAMA_KEEP_ALIVE=2h OLLAMA_FLASH_ATTENTION=1 ollama serve &>/dev/null &
    fi
    for i in $(seq 1 15); do
        printf "\r  🦙 ollama 起動待ち... %ds " "$((i * 2))"
        sleep 2
        if curl -s --max-time 2 "$OLLAMA_HOST/api/tags" &>/dev/null; then
            printf "\r%-40s\n" ""
            return 0
        fi
    done
    printf "\r%-40s\n" ""
    echo "❌ エラー: ollama が起動できませんでした"
    echo "  macOS: Ollama アプリを手動で起動 / Linux: ollama serve"
    return 1
}

# --- ollama バージョン確認 (/v1/messages ネイティブ対応 = 0.14+) ---
check_ollama_version() {
    local v major minor
    v="$(curl -s --max-time 3 "$OLLAMA_HOST/api/version" 2>/dev/null | grep -oE '"version":"[0-9.]+"' | grep -oE '[0-9.]+' || true)"
    [ -z "$v" ] && return 0   # 取得できない場合は続行
    major="${v%%.*}"; minor="$(echo "$v" | cut -d. -f2)"
    if [ "$major" -eq 0 ] && [ "$minor" -lt 14 ]; then
        echo "⚠️  Ollama $v は古すぎます (0.14以上を推奨)。brew upgrade ollama を実行してください。"
        [ "$1" = "strict" ] && return 1
    fi
    return 0
}

# --- モデル存在確認 & pull 案内 ---
model_exists() {
    ollama show "$1" &>/dev/null
}

ensure_base_model() {
    local m="$1"
    model_exists "$m" && return 0
    echo ""
    echo "📦 モデル $m がまだダウンロードされていません。"
    if ! curl -s --max-time 3 https://ollama.com &>/dev/null; then
        echo "❌ オフラインのためダウンロードできません。"
        echo "   ネットがある場所で: ollama pull $m"
        echo "   もしくは手元にあるモデルを指定: vibe-local --model <モデル名>"
        ollama list 2>/dev/null | head -10
        return 1
    fi
    printf "   今ダウンロードしますか？ (数GB〜50GB) [Y/n]: "
    local reply=""
    if [ -t 0 ]; then read -r reply; elif [ -e /dev/tty ]; then read -r reply </dev/tty; fi
    case "$reply" in
        [nN]*) echo "   中止しました。"; return 1 ;;
        *) ollama pull "$m" || return 1 ;;
    esac
}

# --- num_ctx 焼き込み別名モデルの作成 (vibe-coder / vibe-fast) ---
# 現行OllamaはモデルネイティブMAXのコンテキストでロードするため、
# 低RAM機ではKVキャッシュがメモリを食い潰す。RAM相応のnum_ctxを別名に焼き込む。
ensure_alias() {
    local alias_name="$1" base="$2" ctx="$3"
    local sig="${base}|${ctx}"
    local sig_file="${STATE_DIR}/${alias_name}.sig"
    if model_exists "$alias_name" && [ -f "$sig_file" ] && [ "$(cat "$sig_file" 2>/dev/null)" = "$sig" ]; then
        return 0
    fi
    echo "🔧 ${alias_name} を構成中 (FROM ${base}, num_ctx ${ctx})..."
    # 注意: 既存の古いエイリアスが残っていると「存在チェック」では失敗を検知できない。
    # 必ず ollama create の終了コードで判定する。
    if ! printf 'FROM %s\nPARAMETER num_ctx %s\n' "$base" "$ctx" | ollama create "$alias_name" -f - >/dev/null 2>&1; then
        # 一部バージョンは -f - (stdin) 非対応。テンポラリファイルで再試行
        local tmp; tmp="$(mktemp "${STATE_DIR}/Modelfile.XXXXXX")"
        printf 'FROM %s\nPARAMETER num_ctx %s\n' "$base" "$ctx" > "$tmp"
        if ! ollama create "$alias_name" -f "$tmp" >/dev/null; then
            rm -f "$tmp"
            echo "❌ ${alias_name} の作成に失敗 (FROM ${base})"
            rm -f "$sig_file"
            return 1
        fi
        rm -f "$tmp"
    fi
    echo "$sig" > "$sig_file"
    return 0
}

# --- テーマ設置 ---
# 同梱テーマ (vibe-null=黒灰・既定 / vibe-vaporwave) を配置し、tui.json の
# theme を設定する。ユーザーが --theme で明示指定した場合はそれを優先。
# 旧既定の vibe-vaporwave のままなら新既定 vibe-null へ一度だけ移行する。
install_theme() {
    local name src
    for name in vibe-null vibe-vaporwave; do
        for src in "${SCRIPT_DIR}/themes/${name}.json" "${LIB_DIR}/${name}.json"; do
            [ -f "$src" ] && cp "$src" "${THEME_DIR}/${name}.json" 2>/dev/null && break
        done
    done
    local want="${THEME_OVERRIDE:-}"
    if [ ! -f "$TUI_JSON" ]; then
        printf '{\n  "$schema": "https://opencode.ai/tui.json",\n  "theme": "%s"\n}\n' \
            "${want:-$DEFAULT_THEME}" > "$TUI_JSON"
        return 0
    fi
    python3 - "$TUI_JSON" "${want}" "$DEFAULT_THEME" <<'EOF' 2>/dev/null || true
import json, sys
p, want, default = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(p))
cur = d.get("theme")
if want:
    d["theme"] = want
elif cur is None or cur == "vibe-vaporwave":   # 未設定 or 旧既定 → 新既定へ
    d["theme"] = default
if d.get("theme") != cur:
    json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
EOF
}

# --- opencodeバイナリのブランディング除去 (vibe-tui) ---
# tools/debrand.py で可視ロゴを "vibe code" に差し替えた専用バイナリを生成。
# opencode のバージョンが変わったら自動で作り直す。失敗時は素の opencode に
# フォールバック (機能は同一、ロゴ表示だけの違い)。
OPENCODE_BIN="opencode"
ensure_patched_tui() {
    command -v opencode &>/dev/null || return 1
    local src patched sig_file sig ver debrand
    src="$(command -v opencode)"
    src="$(readlink -f "$src" 2>/dev/null || echo "$src")"
    patched="${LIB_DIR}/bin/vibe-tui"
    sig_file="${STATE_DIR}/vibe-tui.sig"
    ver="$(opencode --version 2>/dev/null | head -1)"
    sig="${ver}|${src}"
    if [ -x "$patched" ] && [ "$(cat "$sig_file" 2>/dev/null)" = "$sig" ]; then
        OPENCODE_BIN="$patched"
        return 0
    fi
    for debrand in "${SCRIPT_DIR}/tools/debrand.py" "${LIB_DIR}/debrand.py"; do
        [ -f "$debrand" ] || continue
        echo "🔧 TUI を vibe 仕様に調整中..."
        if python3 "$debrand" "$src" "$patched" >/dev/null 2>&1; then
            echo "$sig" > "$sig_file"
            OPENCODE_BIN="$patched"
            return 0
        fi
        break
    done
    rm -f "$patched" "$sig_file" 2>/dev/null
    return 1
}

# --- モデルの先読み (ウォームアップ) ---
# TUIが立ち上がる裏でモデルをロードしておくと、最初の応答が数十秒速くなる。
# keep_alive=2h でしばらく常駐させる。
warmup_model() {
    curl -s --max-time 600 "$OLLAMA_HOST/api/generate" \
        -d "{\"model\":\"$1\",\"keep_alive\":\"2h\"}" &>/dev/null &
}

# --- vibe-router (モデル自動振り分け) ---
# 雑談・短い質問 → 小型モデル / コーディング・ツール使用中 → 大型モデル。
# 加えて小型モデル宛の全リクエストのthinkingを無効化する
# (タイトル生成の思考ループが数十秒キューを塞ぐ実測バグ対策)。
# 失敗してもOllama直結で動くので致命ではない。
ROUTER_OK=0
ensure_router() {
    local script="" sig health
    for c in "${SCRIPT_DIR}/tools/vibe-router.py" "${LIB_DIR}/vibe-router.py"; do
        [ -f "$c" ] && script="$c" && break
    done
    [ -z "$script" ] && return 1
    command -v python3 &>/dev/null || return 1
    sig="v${VIBE_VERSION}|${MAIN_ALIAS}|${SMALL_ALIAS}|${OLLAMA_HOST}"
    health="$(curl -s --max-time 1 "http://127.0.0.1:${ROUTER_PORT}/vibe-router/health" 2>/dev/null || true)"
    if echo "$health" | grep -qF "\"sig\": \"$sig\""; then
        ROUTER_OK=1
        return 0
    fi
    # 設定が変わった/落ちている → 旧デーモンを終了して起動し直す
    if [ -f "${STATE_DIR}/router.pid" ]; then
        kill "$(cat "${STATE_DIR}/router.pid")" 2>/dev/null
        rm -f "${STATE_DIR}/router.pid"
    fi
    nohup python3 "$script" --port "$ROUTER_PORT" --upstream "$OLLAMA_HOST" \
        --main "$MAIN_ALIAS" --fast "$SMALL_ALIAS" --sig "$sig" \
        >> "${STATE_DIR}/router.log" 2>&1 < /dev/null &
    echo $! > "${STATE_DIR}/router.pid"
    disown 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if curl -s --max-time 1 "http://127.0.0.1:${ROUTER_PORT}/vibe-router/health" 2>/dev/null | grep -qF "\"sig\": \"$sig\""; then
            ROUTER_OK=1
            return 0
        fi
        sleep 0.2
    done
    return 1
}

# --- 初学者向けの共通指示 (OpenCodeのinstructionsとして読み込ませる) ---
write_agents_md() {
    [ -f "$AGENTS_FILE" ] && return 0
    cat > "$AGENTS_FILE" <<'EOF'
# vibe-local 共通ルール

- ユーザーが日本語で話したら日本語で、英語なら英語で答える。
- ユーザーは初学者の可能性がある。専門用語には短い説明を添える。
- 破壊的なコマンド (rm, sudo, chmod, dd, --force など) を実行する前に、
  それが何をするのかを一行で説明してから実行の許可を求める。
- コードを書いたら、実行方法を必ず最後に示す。
EOF
}

# --- OpenCode 設定生成 ---
# use_router=1 のとき baseURL をルーターに向け、既定モデルを vibe-auto にする。
# vibe-auto = ルーターが内容を見て main/small に自動振り分けする仮想モデル。
write_opencode_config() {
    local main_model="$1" small_model="$2" main_ctx="$3" perm="$4" use_router="${5:-0}"
    local perm_json base_url default_model auto_entry=""
    if [ "$perm" = "allow" ]; then
        perm_json='{ "edit": "allow", "bash": "allow", "webfetch": "allow" }'
    else
        perm_json='{ "edit": "ask", "bash": "ask", "webfetch": "ask" }'
    fi
    if [ "$use_router" = "1" ]; then
        base_url="http://127.0.0.1:${ROUTER_PORT}/v1"
        default_model="vibe-auto"
        auto_entry="\"vibe-auto\": {
          \"name\": \"vibe-auto (おまかせ)\",
          \"limit\": { \"context\": ${main_ctx}, \"output\": 32000 }
        },
        "
    else
        base_url="${OLLAMA_HOST}/v1"
        default_model="${main_model}"
    fi
    cat > "$OPENCODE_CONFIG_FILE" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "ollama/${default_model}",
  "small_model": "ollama/${small_model}",
  "instructions": ["${AGENTS_FILE}"],
  "autoupdate": false,
  "share": "disabled",
  "permission": ${perm_json},
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "${base_url}" },
      "models": {
        ${auto_entry}"${main_model}": {
          "name": "${main_model} (local)",
          "limit": { "context": ${main_ctx}, "output": 32000 }
        },
        "${small_model}": {
          "name": "${small_model} (local, fast)",
          "limit": { "context": ${SMALL_CTX}, "output": 8000 }
        }
      }
    }
  }
}
EOF
}

# --- ネットワーク接続チェック ---
check_network() {
    curl -s --max-time 3 https://api.anthropic.com/ &>/dev/null
}

# --- 起動画面「零位相→物化」 ---
# 真っ黒な虚空から白銀の太陽が昇り、ノイズからロゴが物化する (黒灰モノクロ)。
# 24bitカラー。非TTY/狭い端末/VIBE_LOCAL_NO_SPLASH=1 では静かなテキストに落ちる。
show_splash() {
    local mode_label="$1" model_label="$2" small_label="$3" ctx_label="$4" perm_label="$5"
    if [ ! -t 1 ] || [ "${VIBE_LOCAL_NO_SPLASH:-0}" = "1" ] || ! command -v python3 &>/dev/null; then
        echo ""
        echo "============================================"
        echo " 🌴 vibe-local v${VIBE_VERSION} — ${mode_label}"
        echo " Model: ${model_label}   Small: ${small_label}"
        echo " Context: ${ctx_label}   Permissions: ${perm_label}"
        echo "============================================"
        echo ""
        return 0
    fi
    VIBE_SP_MODE="$mode_label" VIBE_SP_MODEL="$model_label" VIBE_SP_SMALL="$small_label" \
    VIBE_SP_CTX="$ctx_label" VIBE_SP_PERM="$perm_label" VIBE_SP_VER="$VIBE_VERSION" \
    python3 - <<'PYSPLASH' || true
import os, sys, time, math, shutil

W = 72
cols = shutil.get_terminal_size().columns
if cols < W + 2:
    print(f"\n 🌴 vibe-local v{os.environ.get('VIBE_SP_VER','2')} — {os.environ.get('VIBE_SP_MODE','')}")
    print(f"    Model: {os.environ.get('VIBE_SP_MODEL','')}  ({os.environ.get('VIBE_SP_CTX','')})\n")
    sys.exit(0)

WHITE    = (245, 245, 247)
SILVER   = (201, 201, 210)
ASH      = (168, 168, 178)
GRAY     = (128, 128, 138)
DIMGRAY  = (88, 88, 98)
CHARCOAL = (48, 48, 56)
INK      = (22, 22, 26)

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def fg(c):
    return f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m"

def bg(c):
    return f"\x1b[48;2;{c[0]};{c[1]};{c[2]}m"

RST = "\x1b[0m"

LOGO = [
    "██╗   ██╗██╗██████╗ ███████╗  ██╗      ██████╗  ██████╗ █████╗ ██╗     ",
    "██║   ██║██║██╔══██╗██╔════╝  ██║     ██╔═══██╗██╔════╝██╔══██╗██║     ",
    "╚██╗ ██╔╝██║██████╔╝█████╗    ██║     ██║   ██║██║     ███████║██║     ",
    " ╚████╔╝ ██║██╔══██╗██╔══╝    ██║     ██║   ██║██║     ██╔══██║██║     ",
    "  ╚═══╝  ╚═╝██████╔╝███████╗  ███████╗╚██████╔╝╚██████╗██║  ██║███████╗",
    "            ╚═════╝ ╚══════╝   ╚═════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝",
]
LW = max(len(r) for r in LOGO)
SCALE = 1
if LW > W:
    # 幅が足りない時は詰める (全角スペース調整不要のASCIIアートなのでそのまま中央寄せ切替)
    pass

SUN_R = 7          # 太陽の行数
GRID_ROWS = 4      # 手前グリッド行数
FRAMES = 6

def hash01(x, y):
    return ((x * 2654435761 + y * 40503) & 0xFFFF) / 0xFFFF

def sun_row(i, t):
    """i: 0=太陽の頂点。t: 0..1 出現率。返り値: (文字列, 太陽の幅)"""
    risen = int(SUN_R * t + 0.999)
    if i < SUN_R - risen:
        return " " * W
    yy = (SUN_R - i) / SUN_R
    half = int(SUN_R * 2.9 * math.sqrt(max(0.0, 1.0 - yy * yy)))
    if half <= 0:
        half = 1
    col = lerp(WHITE, GRAY, i / max(1, SUN_R - 1))
    # 下に行くほど太いスキャンライン (outrun sun)
    if i >= SUN_R - 3 and i % 2 == 0:
        ch = "▄"
    else:
        ch = "█"
    pad = (W - half * 2) // 2
    return " " * pad + fg(col) + ch * (half * 2) + RST + " " * (W - pad - half * 2)

def grid_row(j, f):
    """遠近グリッド。jが大きいほど手前。fでスクロール位相。"""
    chars = [" "] * W
    cx = W // 2
    # 横線
    hc = lerp(CHARCOAL, DIMGRAY, j / max(1, GRID_ROWS - 1))
    for x in range(W):
        chars[x] = fg(hc) + "─"
    # 縦線 (消失点から放射)
    spread = (j + 1 + (f % 2)) * 4
    for k in range(-5, 6):
        if k == 0:
            x = cx
            glyph = "│"
        else:
            x = cx + k * spread // 2
            glyph = "╱" if k < 0 else "╲"
        if 0 <= x < W:
            chars[x] = fg(SILVER) + glyph
    return "".join(chars) + RST

def logo_rows(t):
    out = []
    pad = (W - LW) // 2
    for y, row in enumerate(LOGO):
        line = " " * max(0, pad)
        for x, ch in enumerate(row):
            if ch == " ":
                line += " "
                continue
            g = x / max(1, LW - 1)
            col = lerp(WHITE, GRAY, g)
            if hash01(x, y) > t:
                # まだ物化していない: 零位相のノイズ
                noise = "·" if hash01(x + 7, y + 3) > 0.5 else " "
                line += fg(lerp(col, INK, 0.8)) + noise
            else:
                line += fg(col) + ch
        out.append(line + RST)
    return out

def chip(label, value, c):
    return f"{bg(c)}\x1b[38;2;18;18;22m {label} {RST}{fg(c)} {value}{RST}"

def frame_lines(f):
    t = (f + 1) / FRAMES
    lines = []
    for i in range(SUN_R):
        lines.append(sun_row(i, t))
    for row in logo_rows(t):
        lines.append(row)
    for j in range(GRID_ROWS):
        lines.append(grid_row(j, f))
    return lines

total = SUN_R + len(LOGO) + GRID_ROWS
try:
    sys.stdout.write("\x1b[?25l")  # カーソル非表示
    print()
    for f in range(FRAMES):
        for ln in frame_lines(f):
            print(" " + ln)
        sys.stdout.flush()
        time.sleep(0.045)
        if f < FRAMES - 1:
            sys.stdout.write(f"\x1b[{total}A")
    tag = "インターネットが なくても、ヴァイブは ある。 ── no net, still vibe."
    ver = f"v{os.environ.get('VIBE_SP_VER','2')}"
    print()
    print(" " + fg(DIMGRAY) + "─" * W + RST)
    print(" " + chip("VIBE LOCAL", ver, WHITE) + "  " +
          chip("MODE", os.environ.get("VIBE_SP_MODE", ""), SILVER))
    print(" " + chip("MODEL", os.environ.get("VIBE_SP_MODEL", ""), ASH) + "  " +
          chip("CTX", os.environ.get("VIBE_SP_CTX", ""), GRAY))
    print(" " + chip("PERM", os.environ.get("VIBE_SP_PERM", ""), GRAY))
    print(" " + fg(DIMGRAY) + "─" * W + RST)
    print(" " + fg(GRAY) + tag + RST + "\n")
finally:
    sys.stdout.write("\x1b[?25h")  # カーソル再表示
    sys.stdout.flush()
PYSPLASH
}

# --- 診断 ---
doctor() {
    echo "vibe-local v${VIBE_VERSION} — 環境診断"
    echo "----------------------------------------"
    printf "RAM:        %s GB\n" "$(ram_gb)"
    if command -v ollama &>/dev/null; then
        printf "ollama:     %s\n" "$(ollama --version 2>/dev/null | head -1)"
    else
        echo "ollama:     ❌ 未インストール (brew install ollama)"
    fi
    if curl -s --max-time 2 "$OLLAMA_HOST/api/tags" &>/dev/null; then
        echo "ollama API: ✅ 稼働中 ($OLLAMA_HOST)"
    else
        echo "ollama API: ⚠️  停止中"
    fi
    if command -v opencode &>/dev/null; then
        printf "opencode:   %s\n" "$(opencode --version 2>/dev/null | head -1)"
    else
        echo "opencode:   ❌ 未インストール (brew install opencode)"
    fi
    if command -v claude &>/dev/null; then
        printf "claude:     %s (--classic 用)\n" "$(claude --version 2>/dev/null | head -1)"
    else
        echo "claude:     - 未インストール (--classic を使う場合のみ必要)"
    fi
    apply_model_matrix
    echo "モデル:     main=${BASE_MODEL} (num_ctx=${NUM_CTX}) small=${SMALL_BASE:-同上}"
    for m in vibe-coder vibe-fast "$BASE_MODEL"; do
        if model_exists "$m"; then echo "  ✅ $m"; else echo "  ⬜ $m (未作成/未DL)"; fi
    done
    if check_network; then echo "ネット:     ✅ オンライン"; else echo "ネット:     📡 オフライン"; fi
    if [ -x "${LIB_DIR}/bin/vibe-tui" ]; then
        echo "vibe-tui:   ✅ パッチ済みTUIあり ($(cat "${STATE_DIR}/vibe-tui.sig" 2>/dev/null | cut -d'|' -f1))"
    else
        echo "vibe-tui:   ⬜ 未生成 (初回起動時に自動生成)"
    fi
    if curl -s --max-time 1 "http://127.0.0.1:${ROUTER_PORT}/vibe-router/health" 2>/dev/null | grep -q '"sig"'; then
        echo "router:     ✅ 稼働中 (:${ROUTER_PORT}, 雑談→small / 作業→main)"
    else
        echo "router:     ⬜ 停止中 (起動時に自動開始)"
    fi
    if [ -f "$TUI_JSON" ]; then
        echo "テーマ:     $(grep -o '"theme"[^,}]*' "$TUI_JSON" 2>/dev/null | head -1 | sed 's/.*: *"\(.*\)"/\1/')"
    fi
    echo "設定:       $CONFIG_FILE"
    echo "opencode設定: $OPENCODE_CONFIG_FILE"
}

# --- 引数パース ---
AUTO_MODE=0
YES_FLAG=0
MODEL_OVERRIDE=0
FAST_MODE=0
CLASSIC=0
VIBE_CODER=0
SERVE=0
ATTACH_URL=""
PROMPT=""
PRINT_CONFIG=0
REBUILD=0
THEME_OVERRIDE=""
NO_ROUTER=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)     AUTO_MODE=1; shift ;;
        --model)    BASE_MODEL="$2"; MODEL_OVERRIDE=1; shift 2 ;;
        --fast)     FAST_MODE=1; shift ;;
        --no-router) NO_ROUTER=1; shift ;;
        -y|--yes)   YES_FLAG=1; shift ;;
        --theme)    THEME_OVERRIDE="$2"; shift 2 ;;
        --classic)  CLASSIC=1; shift ;;
        --vibe-coder|--engine) VIBE_CODER=1; shift ;;
        --serve)    SERVE=1; shift ;;
        --attach)   ATTACH_URL="$2"; shift 2 ;;
        -p|--prompt) PROMPT="$2"; shift 2 ;;
        --doctor)   doctor; exit 0 ;;
        --print-config) PRINT_CONFIG=1; shift ;;
        --rebuild)  REBUILD=1; shift ;;
        --version)  echo "vibe-local v${VIBE_VERSION}"; exit 0 ;;
        -h|--help)  grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)          EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# --theme の別名解決 (null / vaporwave の短縮名を許可)
case "$THEME_OVERRIDE" in
    null|mono|dark)  THEME_OVERRIDE="vibe-null" ;;
    vaporwave|vapor) THEME_OVERRIDE="vibe-vaporwave" ;;
esac

apply_model_matrix

# --- 自動判定モード: オンラインならクラウドのClaude Codeへ ---
if [ "$AUTO_MODE" -eq 1 ] && check_network; then
    if command -v claude &>/dev/null; then
        echo "🌐 ネットワーク接続あり → 通常の Claude Code を起動"
        exec claude ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    else
        echo "🌐 オンラインですが claude CLI が無いためローカルモードで続行します"
    fi
elif [ "$AUTO_MODE" -eq 1 ]; then
    echo "📡 ネットワーク接続なし → ローカルモード (${BASE_MODEL})"
fi

# --- 教室モード: attach (生徒側。モデル不要、サーバーに繋ぐだけ) ---
if [ -n "$ATTACH_URL" ]; then
    command -v opencode &>/dev/null || { echo "❌ opencode が未インストールです (brew install opencode)"; exit 1; }
    ensure_patched_tui || true
    export OPENCODE_DISABLE_TERMINAL_TITLE=1
    echo "🏫 教室モード: ${ATTACH_URL} に接続します"
    exec "$OPENCODE_BIN" attach "$ATTACH_URL" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
fi

# --- ここからローカル起動系: ollama 必須 ---
command -v ollama &>/dev/null || { echo "❌ ollama が未インストールです。install.sh を実行してください。"; exit 1; }
ensure_ollama || exit 1
check_ollama_version lenient

# --- モデル準備 ---
MAIN_ALIAS="vibe-coder"
SMALL_ALIAS="vibe-fast"

ensure_base_model "$BASE_MODEL" || exit 1
[ "$REBUILD" -eq 1 ] && rm -f "${STATE_DIR}/${MAIN_ALIAS}.sig" "${STATE_DIR}/${SMALL_ALIAS}.sig" \
                              "${STATE_DIR}/vibe-tui.sig"
ensure_alias "$MAIN_ALIAS" "$BASE_MODEL" "$NUM_CTX" || exit 1

if [ -n "$SMALL_BASE" ]; then
    if model_exists "$SMALL_BASE" || ensure_base_model "$SMALL_BASE"; then
        ensure_alias "$SMALL_ALIAS" "$SMALL_BASE" "$SMALL_CTX" || SMALL_ALIAS="$MAIN_ALIAS"
    else
        SMALL_ALIAS="$MAIN_ALIAS"
    fi
else
    SMALL_ALIAS="$MAIN_ALIAS"
fi

# --fast: 小型モデルを主役に (応答サクサク優先。品質は main に劣る)
if [ "$FAST_MODE" -eq 1 ] && [ "$SMALL_ALIAS" != "$MAIN_ALIAS" ]; then
    MAIN_ALIAS="$SMALL_ALIAS"
    BASE_MODEL="$SMALL_BASE"
    NUM_CTX="$SMALL_CTX"
fi

# --- 設定保存 (--model の一時指定は恒久化しない) ---
if [ "$MODEL_OVERRIDE" -eq 0 ]; then
    cat > "$CONFIG_FILE" <<EOF
# vibe-local v2 設定 (自動生成。編集可)
BASE_MODEL="${BASE_MODEL}"
SMALL_BASE="${SMALL_BASE}"
NUM_CTX="${NUM_CTX}"
OLLAMA_HOST="${OLLAMA_HOST}"
EOF
fi

# --- パーミッション: 既定は毎回確認 (教育向け)、-y で自動許可 ---
PERM="ask"
PERM_LABEL="毎回確認 (安全・推奨)"
if [ "$YES_FLAG" -eq 1 ]; then
    PERM="allow"
    PERM_LABEL="ツール自動許可 (自己責任)"
fi

# --- classic モード: Claude Code CLI + Ollama ネイティブ /v1/messages ---
if [ "$CLASSIC" -eq 1 ]; then
    command -v claude &>/dev/null || { echo "❌ claude CLI が未インストールです (npm install -g @anthropic-ai/claude-code)"; exit 1; }
    check_ollama_version strict || exit 1
    warmup_model "$MAIN_ALIAS"
    show_splash "classic (Claude Code UI)" "${MAIN_ALIAS} (${BASE_MODEL})" "$SMALL_ALIAS" "${NUM_CTX} tok" "$PERM_LABEL"
    echo " Endpoint: ${OLLAMA_HOST} (Ollama native /v1/messages)"
    echo ""
    CLASSIC_ARGS=()
    [ "$YES_FLAG" -eq 1 ] && CLASSIC_ARGS+=(--dangerously-skip-permissions)
    ANTHROPIC_BASE_URL="$OLLAMA_HOST" \
    ANTHROPIC_AUTH_TOKEN="ollama" \
    ANTHROPIC_API_KEY="" \
    ANTHROPIC_SMALL_FAST_MODEL="$SMALL_ALIAS" \
    ANTHROPIC_DEFAULT_HAIKU_MODEL="$SMALL_ALIAS" \
    exec claude --model "$MAIN_ALIAS" ${CLASSIC_ARGS[@]+"${CLASSIC_ARGS[@]}"} ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
fi

# --- vibe-coder エンジンモード: 内蔵Python版 (RAG / tool-call XMLフォールバック / tty許可入力) ---
# コミュニティ製 vibe-coder.py をそのまま起動する。OpenCode を使わず、
# --rag などの vibe-coder.py 固有オプションはすべて EXTRA_ARGS 経由で透過する。
if [ "$VIBE_CODER" -eq 1 ]; then
    command -v python3 &>/dev/null || { echo "❌ python3 が未インストールです"; exit 1; }
    VIBE_CODER_SCRIPT=""
    for _p in "${SCRIPT_DIR}/vibe-coder.py" "${LIB_DIR}/vibe-coder.py"; do
        [ -f "$_p" ] && { VIBE_CODER_SCRIPT="$_p"; break; }
    done
    [ -n "$VIBE_CODER_SCRIPT" ] || { echo "❌ vibe-coder.py が見つかりません (install.sh を実行してください)"; exit 1; }
    warmup_model "$MAIN_ALIAS"
    show_splash "vibe-coder (Python engine)" "${MAIN_ALIAS} (${BASE_MODEL})" "$SMALL_ALIAS" "${NUM_CTX} tok" "$PERM_LABEL"
    echo " Engine: vibe-coder.py (direct, no proxy)"
    echo ""
    VC_ARGS=(-m "$MAIN_ALIAS")
    [ "$YES_FLAG" -eq 1 ] && VC_ARGS+=(-y)
    [ -n "$PROMPT" ] && VC_ARGS+=(-p "$PROMPT")
    OLLAMA_HOST="$OLLAMA_HOST" \
    VIBE_LOCAL_MODEL="$MAIN_ALIAS" \
    VIBE_LOCAL_SIDECAR_MODEL="$SMALL_ALIAS" \
    exec python3 "$VIBE_CODER_SCRIPT" "${VC_ARGS[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
fi

# --- TUI 準備 (OpenCodeエンジン) ---
command -v opencode &>/dev/null || { echo "❌ opencode が未インストールです (brew install opencode)"; exit 1; }
install_theme
write_agents_md

# ルーター起動 (雑談→小型 / コーディング→大型 の自動振り分け)。
# モデルが1本の時 (--fast や16GB機) も小型モデルのthinking無効化のために通す
USE_ROUTER=0
if [ "$NO_ROUTER" -eq 0 ]; then
    ensure_router && USE_ROUTER=1
fi

write_opencode_config "$MAIN_ALIAS" "$SMALL_ALIAS" "$NUM_CTX" "$PERM" "$USE_ROUTER"

if [ "$PRINT_CONFIG" -eq 1 ]; then
    cat "$OPENCODE_CONFIG_FILE"
    exit 0
fi

ensure_patched_tui || true    # 失敗しても素の opencode で続行
warmup_model "$MAIN_ALIAS"    # TUI起動の裏でモデルを先読み
[ "$SMALL_ALIAS" != "$MAIN_ALIAS" ] && warmup_model "$SMALL_ALIAS"

export OPENCODE_CONFIG="$OPENCODE_CONFIG_FILE"
export OPENCODE_DISABLE_TERMINAL_TITLE=1
# vibe-localは独立環境: ユーザーのClaude Code用スキル群やモデルカタログ取得を
# 取り込まない (プロンプト肥大 69KB→30KB = 応答が倍速に。オフラインでも安全)
export OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1
export OPENCODE_DISABLE_EXTERNAL_SKILLS=1
export OPENCODE_DISABLE_MODELS_FETCH=1
[ -t 1 ] && printf '\033]0;vibe-local\007'   # タイトルは vibe-local に

# --- 教室モード: serve (先生側) ---
if [ "$SERVE" -eq 1 ]; then
    local_ip="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "<このMacのIP>")"
    echo ""
    echo "============================================"
    echo " 🏫 vibe-local 教室モード (サーバー)"
    echo " Model: ${MAIN_ALIAS} (${BASE_MODEL})"
    echo " 生徒側: vibe-local --attach http://${local_ip}:4096"
    echo " 終了: Ctrl+C"
    echo "============================================"
    echo ""
    exec "$OPENCODE_BIN" serve --hostname 0.0.0.0 --port 4096
fi

# --- 起動 ---
if [ -n "$PROMPT" ]; then
    # ワンショットは演出なしで即実行
    exec "$OPENCODE_BIN" run "$PROMPT" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
fi

MODE_LABEL="vibe TUI"
[ "$FAST_MODE" -eq 1 ] && MODE_LABEL="vibe TUI (fast)"
show_splash "$MODE_LABEL" "${MAIN_ALIAS} (${BASE_MODEL})" "$SMALL_ALIAS" "${NUM_CTX} tok" "$PERM_LABEL"

exec "$OPENCODE_BIN" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
