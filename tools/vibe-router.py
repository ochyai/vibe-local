#!/usr/bin/env python3
"""vibe-router — モデル自動振り分けパススルー (stdlibのみ、変換なし)

opencode (OpenAI互換クライアント) と Ollama の間に挟まる極小ルーター。
仮想モデル "vibe-auto" へのリクエストだけ、内容を見て実モデルに振り分ける:

  - 雑談・短い質問            → fast (小型モデル、thinking無効化で即答)
  - コーディング・ツール使用中 → main (大型モデル)

それ以外のパス・モデル名はバイトそのままの素通し。v1(自作プロキシ)のような
API形式変換は一切しない = 壊れる場所がない。

使い方:
  vibe-router.py --port 11435 --upstream http://localhost:11434 \
                 --main vibe-coder --fast vibe-fast --sig <識別文字列>

ヘルスチェック: GET /vibe-router/health → {"sig": ..., "main": ..., "fast": ...}
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTO_MODEL = "vibe-auto"

# コーディング/作業タスクを示す語 (どれかを含めば main 行き)
WORK_WORDS = re.compile(
    r"作っ|書い|実装|修正|直し|なおし|デバッグ|動かし|実行|テスト|ファイル|コード|"
    r"エラー|バグ|関数|リファクタ|インストール|ビルド|デプロイ|解析|分析|調べ|"
    r"読ん|検索|保存|開い|消し|削除|変更|追加|作成|生成して|"
    r"\bcode\b|\bwrite\b|\bcreate\b|\bmake\b|\bbuild\b|\bfix\b|\bdebug\b|\brun\b|"
    r"\btest\b|\binstall\b|\bimplement\b|\brefactor\b|\bfile\b|\berror\b|\bbug\b|"
    r"\bscript\b|\bdeploy\b|\banalyze\b|\bsearch\b|\bread\b|\bgit\b|\bcommit\b|"
    r"\bfunction\b|\bclass\b|\bapi\b",
    re.IGNORECASE,
)


def text_of(content):
    """OpenAIのcontent (str または parts配列) からテキストを取り出す"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def has_non_text(content):
    if isinstance(content, list):
        return any(isinstance(p, dict) and p.get("type") not in (None, "text")
                   for p in content)
    return False


def pick_model(body, main, fast):
    """vibe-auto の振り分け。判断できない時は常に main (安全側)。"""
    msgs = body.get("messages") or []
    # ツールを既に使った会話 = 作業セッション → main 固定 (一貫性・ツール品質)
    for m in msgs:
        if m.get("role") == "tool":
            return main, "tool-in-history"
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return main, "tool-calls-in-history"
    last = None
    for m in reversed(msgs):
        if m.get("role") == "user":
            last = m
            break
    if last is None:
        return main, "no-user-message"
    if has_non_text(last.get("content")):
        return main, "attachment"
    t = text_of(last.get("content")).strip()
    if not t or len(t) > 240:
        return main, f"length={len(t)}"
    if "```" in t or t.count("\n") >= 3:
        return main, "code-block"
    if WORK_WORDS.search(t):
        return main, "work-keyword"
    return fast, "chat"


def make_handler(upstream, main, fast, sig):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _health(self):
            payload = json.dumps(
                {"sig": sig, "main": main, "fast": fast, "upstream": upstream}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _forward(self, body):
            url = upstream + self.path
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length",
                                            "connection", "accept-encoding")}
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method=self.command)
            try:
                resp = urllib.request.urlopen(req, timeout=600)
            except urllib.error.HTTPError as e:
                resp = e   # エラー応答もそのまま返す
            except Exception as e:
                msg = json.dumps({"error": {"message": f"vibe-router: upstream unreachable: {e}"}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            with resp:
                self.send_response(resp.status)
                length = resp.headers.get("Content-Length")
                for k, v in resp.getheaders():
                    if k.lower() in ("transfer-encoding", "connection",
                                     "content-length"):
                        continue
                    self.send_header(k, v)
                if length is not None:
                    self.send_header("Content-Length", length)
                    self.end_headers()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                else:
                    # ストリーミング(SSE等) → chunked で流し込む
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(b"%x\r\n" % len(chunk))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")

        def do_GET(self):
            if self.path == "/vibe-router/health":
                self._health()
                return
            self._forward(None)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            if self.path.endswith("/chat/completions"):
                try:
                    d = json.loads(body)
                    size_note = (f"{len(body)//1024}KB "
                                 f"msgs={len(d.get('messages') or [])} "
                                 f"tools={len(d.get('tools') or [])}")
                    why = "pinned"
                    if d.get("model") == AUTO_MODEL:
                        model, why = pick_model(d, main, fast)
                        d["model"] = model
                    # 小型モデル宛は常にthinkingを切る。qwen系小型は思考ループで
                    # タイトル生成に数十秒溶かし、同一モデルのキューを塞ぐ
                    # (実測: thinkingあり15-29s / なし0.2s)
                    if d.get("model") == fast:
                        d.setdefault("reasoning_effort", "none")
                    body = json.dumps(d).encode()
                    print(f"route: {d.get('model')} ({why}) {size_note}", flush=True)
                except Exception as e:
                    print(f"route: passthrough (parse error: {e})", flush=True)
            self._forward(body)

        def do_DELETE(self):
            self._forward(None)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--upstream", default="http://localhost:11434")
    ap.add_argument("--main", default="vibe-coder")
    ap.add_argument("--fast", default="vibe-fast")
    ap.add_argument("--sig", default="dev")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(
        ("127.0.0.1", a.port),
        make_handler(a.upstream.rstrip("/"), a.main, a.fast, a.sig),
    )
    print(f"vibe-router: 127.0.0.1:{a.port} -> {a.upstream} "
          f"(auto: chat->{a.fast} / work->{a.main})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
