#!/bin/bash
# vibe-local.sh
# ローカルLLM (Ollama) で vibe-coder を起動するスクリプト
# Python + Ollama だけで動作 — Node.js不要、Claude Code不要、プロキシ不要
#
# NOTE: This project is NOT affiliated with, endorsed by, or associated with Anthropic.
#
# 使い方:
#   vibe-local                    # インタラクティブモード
#   vibe-local -p "質問"          # ワンショット
#   vibe-local --auto             # ネットワーク状況で自動判定
#   vibe-local --model qwen3:8b   # モデル手動指定
#   vibe-local -y                 # パーミッション確認スキップ (自己責任)
#   vibe-local --debug            # デバッグモード

# NOTE: set -e を使わない (途中停止を防ぐ)
set -uo pipefail

# --- ディレクトリ初期化 ---
STATE_DIR="${HOME}/.local/state/vibe-local"
mkdir -p "$STATE_DIR" 2>/dev/null || true
chmod 700 "$STATE_DIR" 2>/dev/null || true

# --- 設定読み込み (安全なパーサー) ---
CONFIG_FILE="${HOME}/.config/vibe-local/config"
LIB_DIR="${HOME}/.local/lib/vibe-local"
VIBE_CODER_SCRIPT="${LIB_DIR}/vibe-coder.py"

# デフォルト値
MODEL=""
SIDECAR_MODEL=""
TOOLUSE_MODEL=""
OLLAMA_HOST="http://localhost:11434"
VIBE_LOCAL_DEBUG=0

# [C1 fix] source ではなく grep + cut で既知キーのみ安全に読む
# cut is safer than sed for values containing special characters
if [ -f "$CONFIG_FILE" ]; then
    _val() { grep -E "^${1}=" "$CONFIG_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' | sed "s/^[\"']//;s/[\"'[:space:]]*$//;s/[[:space:]]*#.*//" || true; }
    _m="$(_val MODEL)"
    _s="$(_val SIDECAR_MODEL)"
    _t="$(_val TOOLUSE_MODEL)"
    _h="$(_val OLLAMA_HOST)"
    _d="$(_val VIBE_LOCAL_DEBUG)"
    [ -n "$_m" ] && MODEL="$_m"
    [ -n "$_s" ] && SIDECAR_MODEL="$_s"
    [ -n "$_t" ] && TOOLUSE_MODEL="$_t"
    [ -n "$_h" ] && OLLAMA_HOST="$_h"
    [ -n "$_d" ] && VIBE_LOCAL_DEBUG="$_d"
    unset -f _val
    unset _m _s _t _h _d
fi

# [SEC] Validate OLLAMA_HOST - only allow localhost (SSRF prevention)
# Strict regex: reject @-credential injection (e.g. http://localhost:11434@attacker.com)
_host_valid=0
if [[ "$OLLAMA_HOST" =~ ^http://(localhost|127\.0\.0\.1|\[::1\]):[0-9]{1,5}(/.*)?$ ]]; then
    [[ "$OLLAMA_HOST" != *@* ]] && _host_valid=1
fi
if [ "$_host_valid" -eq 0 ]; then
    echo "⚠️  OLLAMA_HOST='$OLLAMA_HOST' はlocalhostではありません。セキュリティのためlocalhostにリセットします。"
    OLLAMA_HOST="http://localhost:11434"
fi
unset _host_valid

# --- python3 存在確認 ---
if ! command -v python3 &>/dev/null; then
    echo "❌ エラー: python3 が見つかりません"
    echo ""
    echo "インストール方法:"
    echo "  macOS: brew install python3"
    echo "  Ubuntu/Debian: sudo apt-get install python3"
    echo "  Fedora: sudo dnf install python3"
    exit 1
fi

# --- vibe-coder.py の探索 ---
if [ ! -f "$VIBE_CODER_SCRIPT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
    if [ -n "$SCRIPT_DIR" ] && [ -f "${SCRIPT_DIR}/vibe-coder.py" ]; then
        VIBE_CODER_SCRIPT="${SCRIPT_DIR}/vibe-coder.py"
    else
        echo "エラー: vibe-coder.py が見つかりません"
        echo "  install.sh を実行するか、vibe-coder.py を同じディレクトリに置いてください"
        exit 1
    fi
fi

# --- ollama が起動しているか確認・起動 ---
ensure_ollama() {
    if curl -s --max-time 2 "$OLLAMA_HOST/api/tags" &>/dev/null; then
        return 0
    fi

    if ! command -v ollama &>/dev/null; then
        echo "❌ エラー: ollama コマンドが見つかりません"
        echo ""
        echo "インストール方法:"
        echo "  macOS: brew install ollama  または  https://ollama.com/download"
        echo "  Linux: curl -fsSL https://ollama.com/install.sh | sh"
        return 1
    fi

    echo "🦙 ollama を起動中..."
    if [[ "$(uname)" == "Darwin" ]]; then
        open -a Ollama 2>/dev/null || (ollama serve &>/dev/null &)
    else
        ollama serve &>/dev/null &
    fi

    for i in $(seq 1 15); do
        printf "\r  🦙 ollama 起動待ち... %ds " "$((i * 2))"
        sleep 2
        if curl -s --max-time 2 "$OLLAMA_HOST/api/tags" &>/dev/null; then
            printf "\r%-40s\n" ""
            echo "✅ ollama 起動完了"
            return 0
        fi
    done
    printf "\r%-40s\n" ""

    echo "❌ エラー: ollama が起動できませんでした"
    echo ""
    echo "対処法:"
    echo "  macOS: Ollama アプリを手動で起動してください"
    echo "  Linux: ollama serve を実行してください"
    return 1
}

# --- ネットワーク接続チェック ---
check_network() {
    curl -s --max-time 3 https://api.anthropic.com/ &>/dev/null
}

# --- 引数パース ---
AUTO_MODE=0
YES_FLAG=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)
            AUTO_MODE=1
            shift
            ;;
        --model)
            if [[ $# -lt 2 ]]; then
                echo "Error: --model requires an argument"
                exit 1
            fi
            MODEL="$2"
            shift 2
            ;;
        -y|--yes|--dangerously-skip-permissions)
            YES_FLAG=1
            shift
            ;;
        --debug)
            VIBE_LOCAL_DEBUG=1
            shift
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- 自動判定モード ---
if [ "$AUTO_MODE" -eq 1 ]; then
    if check_network; then
        # Check if claude CLI exists
        if command -v claude &>/dev/null; then
            echo "🌐 ネットワーク接続あり + Claude Code あり → Claude Code を起動"
            _claude_args=()
            [ "$YES_FLAG" -eq 1 ] && _claude_args+=(--dangerously-skip-permissions)
            exec claude ${_claude_args[@]+"${_claude_args[@]}"} ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
        fi
        echo "🌐 ネットワーク接続あり (Claude Code なし) → ローカルモードで起動"
    else
        echo "📡 ネットワーク接続なし → ローカルモード"
    fi
fi

# --- ローカルモードで起動 ---
if ! ensure_ollama; then
    echo ""
    echo "ollama が起動できないため終了します。"
    exit 1
fi

# モデル引数を組み立て
MODEL_ARGS=()
if [ -n "$MODEL" ]; then
    MODEL_ARGS+=(--model "$MODEL")
fi

# モデルがロード済みか確認 (モデルが指定されている場合のみ)
# Two-stage check: Python JSON first, grep -F fallback
if [ -n "$MODEL" ]; then
    _model_found=0
    _api_response="$(curl -s "$OLLAMA_HOST/api/tags" 2>/dev/null)"
    if [ -n "$_api_response" ]; then
        # Try Python JSON parsing for exact match
        # [SEC] Pass MODEL via env var, not interpolation (prevents injection)
        if echo "$_api_response" | TARGET_MODEL="$MODEL" python3 -c "
import sys,json,os
try:
    d=json.load(sys.stdin)
    names=[m['name'].strip() for m in d.get('models',[])]
    want=os.environ['TARGET_MODEL'].strip()
    found = want in names or want+':latest' in names
    found = found or any(n.startswith(want+':') or n.startswith(want+'-') or n==want for n in names)
    want_base = want.split(':')[0] if ':' in want else want
    found = found or any(n.split(':')[0] == want_base for n in names)
    sys.exit(0 if found else 1)
except: sys.exit(1)
" 2>/dev/null; then
            _model_found=1
        # Fallback: simple grep (less precise but handles edge cases)
        elif echo "$_api_response" | grep -qF "$MODEL"; then
            _model_found=1
        fi
    fi
    if [ "$_model_found" -eq 0 ]; then
        echo "❌ AIモデル $MODEL がまだダウンロードされていません"
        echo ""
        echo "ダウンロードするには、以下のコマンドを貼り付けてEnterを押してください:"
        echo "  ollama pull \"$MODEL\""
        echo ""
        echo "(数分～数十分かかります。完了後に再度 vibe-local を実行してください)"
        echo ""
        echo "インストール済みモデル:"
        curl -s "$OLLAMA_HOST/api/tags" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get('models', []):
        print(f\"  - {m['name']}\")
except: pass
" 2>/dev/null || echo "  (一覧取得失敗)"
        exit 1
    fi
fi

# --- パーミッション確認 ---
PERM_ARGS=()

if [ "$YES_FLAG" -eq 1 ]; then
    PERM_ARGS+=(-y)
else
    echo ""
    echo "============================================"
    echo " ⚠️  パーミッション確認 / Permission Check"
    echo "============================================"
    echo ""
    echo " vibe-local はツール自動許可モード (-y) で起動できます。"
    echo ""
    echo " This means the AI can execute commands, read/write"
    echo " files, and modify your system WITHOUT asking."
    echo ""
    echo " ローカルLLMはクラウドAIより精度が低いため、"
    echo " 意図しない操作が実行される可能性があります。"
    echo ""
    echo "--------------------------------------------"
    echo " [y] 自動許可モード (Auto-approve all tools)"
    echo " [N] 通常モード (Ask before each tool use)"
    echo "--------------------------------------------"
    echo ""
    printf " 続行しますか？ / Continue? [y/N]: "
    read -r -t 30 REPLY </dev/tty 2>/dev/null || read -r -t 30 REPLY 2>/dev/null || REPLY="n"
    echo ""

    case "$REPLY" in
        [yY]|[yY][eE][sS]|はい|是)
            PERM_ARGS+=(-y)
            echo " → 自動許可モードで起動します"
            ;;
        *)
            echo " → 通常モード (毎回確認) で起動します"
            ;;
    esac
fi

DEBUG_ARGS=()
if [ "$VIBE_LOCAL_DEBUG" = "1" ] || [ "$VIBE_LOCAL_DEBUG" = "true" ]; then
    DEBUG_ARGS+=(--debug)
fi

# --- 起動 ---
echo ""
echo "============================================"
echo " 🤖 vibe-local (vibe-coder)"
if [ -n "$MODEL" ]; then
    echo " Model: $MODEL"
else
    echo " Model: (auto-detect)"
fi
echo " Ollama: $OLLAMA_HOST"
echo " Engine: vibe-coder.py (direct, no proxy)"
echo "============================================"
echo ""

OLLAMA_HOST="$OLLAMA_HOST" \
VIBE_LOCAL_MODEL="${MODEL:-}" \
VIBE_LOCAL_SIDECAR_MODEL="${SIDECAR_MODEL:-}" \
VIBE_LOCAL_TOOLUSE_MODEL="${TOOLUSE_MODEL:-}" \
VIBE_LOCAL_DEBUG="${VIBE_LOCAL_DEBUG:-0}" \
exec python3 "$VIBE_CODER_SCRIPT" \
    ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    ${PERM_ARGS[@]+"${PERM_ARGS[@]}"} \
    ${DEBUG_ARGS[@]+"${DEBUG_ARGS[@]}"} \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
