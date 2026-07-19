# legacy/ — v1 の構成ファイル（アーカイブ）

vibe-local v1 (2026-02) は Claude Code CLI を自作プロキシ経由で Ollama に接続していた。

- `anthropic-ollama-proxy.py` — Anthropic Messages API → Ollama OpenAI互換API の変換プロキシ（903行）
- `localllm.py` — `--direct` モード用の MLX 直接推論サーバー（930行）
- `bench.py` — Ollama vs MLX のベンチマーク
- `tests/` — v1 のテスト（localllm/pipeline 中心、121関数）

## v2 で廃止した理由

2026年1月に Ollama v0.14 が Anthropic Messages API (`/v1/messages`) をネイティブ実装し、
llama.cpp / LM Studio も追随したため、変換プロキシ自体が不要になった。
また v2 は TUI を OpenCode（マルチプロバイダネイティブ対応）に移行したため、
Anthropic API 互換レイヤーへの依存そのものが消えた。

v1 のプロキシには以下の既知バグがあり、修正せずアーカイブする:

- Claude Code が内部で使う haiku 系モデル名を無変換で Ollama に渡し 500 エラー
- system prompt を 4000 字で切り詰め（Claude Code の指示が大量欠落）
- max_tokens 上限 4096（長い Write/Edit が途切れる）
- OpenAI 互換経路のため num_ctx を指定できずコンテキストが黙って切り詰められる
