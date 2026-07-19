#!/bin/bash
# vibe-local v2 スモークテスト
#   ./tests/run.sh          # ローカル環境チェック含む全テスト
#   ./tests/run.sh --ci     # 環境非依存のテストのみ (ollama/opencode不要)
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PASS=0; FAIL=0
CI_ONLY=0
[ "${1:-}" = "--ci" ] && CI_ONLY=1

ok()   { PASS=$((PASS+1)); echo "  ✅ $1"; }
ng()   { FAIL=$((FAIL+1)); echo "  ❌ $1"; }
check(){ if eval "$2" &>/dev/null; then ok "$1"; else ng "$1"; fi; }

echo "== 構文 =="
check "vibe-local.sh 構文"            "bash -n vibe-local.sh"
check "install.sh 構文"               "bash -n install.sh"

echo "== テーマ =="
check "テーマJSONが妥当 (全テーマ)"   "python3 - <<'EOF'
import glob, json
files = glob.glob('themes/*.json')
assert len(files) >= 2, files
for f in files:
    json.load(open(f))
EOF"
check "テーマに必須キーが揃っている"  "python3 - <<'EOF'
import glob, json
required = {'primary','secondary','accent','error','warning','success','info',
 'text','textMuted','background','backgroundPanel','backgroundElement',
 'border','borderActive','borderSubtle','diffAdded','diffRemoved',
 'markdownHeading','markdownCode','syntaxComment','syntaxKeyword',
 'syntaxFunction','syntaxString','syntaxNumber','syntaxType'}
for f in glob.glob('themes/*.json'):
    d = json.load(open(f))['theme']
    missing = required - set(d)
    assert not missing, (f, missing)
    for v in d.values():
        assert isinstance(v, dict) and 'dark' in v and 'light' in v, (f, v)
EOF"
check "テーマ全色が defs か #hex を参照" "python3 - <<'EOF'
import glob, json
for f in glob.glob('themes/*.json'):
    t = json.load(open(f))
    defs = set(t['defs'])
    for v in t['theme'].values():
        for mode in ('dark','light'):
            c = v[mode]
            assert c.startswith('#') or c in defs, (f, c)
EOF"
check "vibe-null が黒灰基調 (背景の彩度ほぼ0)" "python3 - <<'EOF'
import json
t = json.load(open('themes/vibe-null.json'))
defs = t['defs']
for key in ('background','backgroundPanel','backgroundElement','border','text','textMuted'):
    c = t['theme'][key]['dark']
    h = defs.get(c, c).lstrip('#')
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    assert max(r,g,b) - min(r,g,b) <= 12, (key, h)
EOF"

echo "== ランチャーのフラグ =="
check "--version が動く"              "./vibe-local.sh --version | grep -q 'vibe-local v2'"
check "--help が動く"                 "./vibe-local.sh --help | grep -q '使い方'"
check "--help に --fast と --theme"   "./vibe-local.sh --help | grep -q -- '--fast' && ./vibe-local.sh --help | grep -q -- '--theme'"

echo "== debrand =="
check "debrand.py 構文"               "python3 -m py_compile tools/debrand.py"
check "debrand.py 引数なしでusage"    "(python3 tools/debrand.py || true) | grep -qi 'opencode'"

echo "== vibe-router =="
check "vibe-router.py 構文"           "python3 -m py_compile tools/vibe-router.py"
check "ルーティング判定 (雑談→fast/作業→main)" "python3 - <<'EOF'
import importlib.util
spec = importlib.util.spec_from_file_location('vr', 'tools/vibe-router.py')
vr = importlib.util.module_from_spec(spec); spec.loader.exec_module(vr)
pick = lambda msgs: vr.pick_model({'messages': msgs}, 'MAIN', 'FAST')[0]
u = lambda t: {'role': 'user', 'content': t}
assert pick([u('こんにちは？')]) == 'FAST'
assert pick([u('元気？')]) == 'FAST'
assert pick([u('What can you do?')]) == 'FAST'
assert pick([u('Pythonでフィボナッチのコードを書いて')]) == 'MAIN'
assert pick([u('fix the bug in main.py')]) == 'MAIN'
assert pick([u('このエラーを直して')]) == 'MAIN'
assert pick([u('x' * 500)]) == 'MAIN'                      # 長文
assert pick([u('やあ'), {'role': 'assistant', 'tool_calls': [{}]},
             {'role': 'tool', 'content': 'ok'}, u('ありがとう')]) == 'MAIN'  # 作業中
assert pick([u([{'type': 'image_url', 'image_url': {}}])]) == 'MAIN'         # 添付
assert vr.pick_model({}, 'MAIN', 'FAST')[0] == 'MAIN'      # 判定不能は安全側
EOF"
check "ルーターhealth応答"            "bash -c '
python3 tools/vibe-router.py --port 21435 --sig testsig &>/dev/null &
RPID=\$!
ok=1
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.2
    if curl -s --max-time 1 http://127.0.0.1:21435/vibe-router/health | grep -q testsig; then ok=0; break; fi
done
kill \$RPID 2>/dev/null
exit \$ok'"

echo "== 起動画面 =="
check "スプラッシュ非TTYフォールバック" "bash -c 'VIBE_VERSION=t; eval \"\$(sed -n \"/^show_splash()/,/^}\$/p\" vibe-local.sh)\"; show_splash M m s c p | grep -c \"vibe-local\"'"

echo "== v1 の残骸が無い =="
check "プロキシ参照が残っていない"    "! grep -q 'anthropic-ollama-proxy' vibe-local.sh"
check "旧モデル(qwen3:1.7b)が残っていない" "! grep -q 'qwen3:1.7b' vibe-local.sh install.sh"

if [ "$CI_ONLY" -eq 0 ]; then
    echo "== ローカル環境 (実機) =="
    check "opencode がインストール済み"  "command -v opencode"
    check "ollama がインストール済み"    "command -v ollama"
    if command -v opencode &>/dev/null; then
        OC_BIN="$(readlink -f "$(command -v opencode)" 2>/dev/null || command -v opencode)"
        TMP_TUI="$(mktemp -d)/vibe-tui"
        if python3 tools/debrand.py "$OC_BIN" "$TMP_TUI" | grep -q 'flat logo: 1'; then
            ok "debrand が実バイナリに適用できる"
            check "パッチ済みTUIのヘルプにopencodeロゴが無い" \
                "! \"$TMP_TUI\" --help 2>/dev/null | head -4 | grep -q '█▀▀█ █▀▀█ █▀▀█ █▀▀▄'"
        else
            ng "debrand が実バイナリに適用できる (パターン不一致: opencode更新でロゴ変更の可能性)"
        fi
        rm -rf "$(dirname "$TMP_TUI")"
    fi
    if curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null; then
        check "Ollama /v1/messages ネイティブ応答" "curl -s --max-time 5 http://localhost:11434/api/version | grep -q version"
        check "--doctor が動く"           "./vibe-local.sh --doctor | grep -c '環境診断'"
    else
        echo "  ⚠️  ollama 停止中のため実機テストをスキップ"
    fi
fi

echo ""
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
