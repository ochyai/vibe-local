# 🤖⚡ Ｖ Ｉ Ｂ Ｅ  Ｌ Ｏ Ｃ Ａ Ｌ ⚡🤖

```
    ██╗   ██╗██╗██████╗ ███████╗
    ██║   ██║██║██╔══██╗██╔════╝
    ██║   ██║██║██████╔╝█████╗
    ╚██╗ ██╔╝██║██╔══██╗██╔══╝
     ╚████╔╝ ██║██████╔╝███████╗
      ╚═══╝  ╚═╝╚═════╝ ╚══════╝
              ██╗      ██████╗  ██████╗ █████╗ ██╗
              ██║     ██╔═══██╗██╔════╝██╔══██╗██║
              ██║     ██║   ██║██║     ███████║██║
              ██║     ██║   ██║██║     ██╔══██║██║
              ███████╗╚██████╔╝╚██████╗██║  ██║███████╗
              ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝
```

> 🌴✨ **Free AI Coding Environment — v2** ✨🌴
>
> No network. No cost. Local LLM agent coding.

**🇯🇵** オフラインのワークショップでAIエージェントを使って学習者をサポートしたり、有料プランに未加入の学生がエージェントコーディングを練習したり、ネットワークのない環境で自然言語を使ってターミナル操作を学んだり――そんな場面を想定した、非営利の研究・教育目的のユーティリティツールです。

**🌱** やさしい にほんご：これは、むりょう（おかね いらない）で、AI（えーあい）に プログラムを かいて もらう ための どうぐ です。インターネットが なくても つかえます。がっこう や ワークショップで つかう ために つくりました。

**🇺🇸** Built for offline workshops where instructors support learners with AI agents, for students without paid plans who want to practice agent coding, and for beginners learning terminal operations through natural language — a non-profit research and education utility.

**🇨🇳** 面向离线工作坊中使用AI代理辅助学习者、未订阅付费计划的学生练习代理编程、以及初学者通过自然语言学习终端操作等场景，这是一个非营利性的研究与教育实用工具。

---

## 🆕 v2.2 (2026-07)

- **vibe-auto 自動振り分け** — 雑談・短い質問は小型モデルで即答（挨拶 実測30秒→約3秒）、コーディングやツール使用は大型モデルへ。極小パススルールーター (`vibe-router`) が内容を見て振り分ける。`--no-router` で無効化可
- **thinking暴走の抑制** — 小型モデルがタイトル生成のたびに数十秒「考え込んで」キューを塞ぐ問題を修正（思考オフ注入で 15〜29秒 → 0.2秒）
- **プロンプト軽量化** — Claude Code用スキル等の外部取り込みを遮断（システムプロンプト 69KB→30KB ≒ 初回応答が約2倍速）
- **vibe専用UI** — TUIのロゴ・タイトル等の表示を vibe-local 仕様に調整した専用バイナリ (`vibe-tui`) を初回起動時に自動生成（エンジンのアップデートに追従して自動再生成）
- **黒灰テーマ `vibe-null` が新既定** — 零位相の黒と灰。vaporwave (`vibe-vaporwave`) も `--theme vaporwave` で選択可
- **サクサク化** — 起動と同時にモデルを裏で先読み（最初の応答までの待ちを大幅短縮）、モデル常駐2時間、起動演出の高速化
- **`--fast`** — 小型モデルを主役にして軽快に動かすモード

## v2 (2026-07)

- **TUI を [OpenCode](https://opencode.ai) エンジンに刷新** — マルチプロバイダをネイティブサポートするMITライセンスのターミナルAIエージェント。Plan/Buildモード、テーマ、LSP連携
- **自作プロキシを廃止** — Ollama が Anthropic Messages API をネイティブ実装 (v0.14+) したため、変換レイヤーが不要に。壊れやすい部品が2000行消滅
- **モデルを2026年世代に更新** — qwen3.6 / gpt-oss / qwen3-coder-next / qwen3.5
- **コンテキスト長の自動管理** — RAM に応じた `num_ctx` を焼き込んだ別名モデル `vibe-coder` / `vibe-fast` を自動生成（低メモリ機でのメモリ食い潰しを防止）
- **教室モード（実験的）** — 先生のMac 1台でモデルを動かし、生徒はattachするだけ
- **`--classic`** — 従来どおり Claude Code CLI をUIに使うモードも残存（プロキシなしで直結）

---

## 🇯🇵 日本語 | [🌱 やさしい日本語](#-やさしい-にほんご) | [🇺🇸 English](#-english) | [🇨🇳 中文](#-中文)

### これは何？

MacにコマンドをコピペするだけでAIがコードを書いてくれる環境。
ネットワーク不要・完全無料。Ollama + ローカルLLM + OpenCode TUI。

### インストール (3ステップ)

**1.** ターミナルを開く（Spotlight `Cmd+Space` → "ターミナル"で検索）

**2.** 以下をコピペしてEnter:

```bash
curl -fsSL https://raw.githubusercontent.com/ochyai/vibe-local/main/install.sh | bash
```

**3.** 新しいターミナルを開いて起動:

```bash
vibe-local
```

### 使い方

```bash
# 対話モード（AIと会話しながらコーディング）
vibe-local

# ワンショット（1回だけ質問）
vibe-local -p "Pythonでじゃんけんゲーム作って"

# ネットワーク自動判定（ネットがあればClaude Code、なければローカル）
vibe-local --auto

# サクサク優先（小型モデルを主役に）
vibe-local --fast

# モデルを手動指定
vibe-local --model devstral

# テーマ切替（null=黒灰・既定 / vaporwave）
vibe-local --theme vaporwave

# Claude Code のUIで使いたい場合（互換モード）
vibe-local --classic

# 内蔵Pythonエンジン vibe-coder.py で起動（RAG・ツールコールXMLフォールバック対応）
vibe-local --vibe-coder
vibe-local --vibe-coder --rag --rag-path .

# 教室モード: 先生のMacでサーバー起動 → 生徒は attach
vibe-local --serve
vibe-local --attach http://192.168.x.x:4096

# 環境診断
vibe-local --doctor
```

TUIの中では `/theme` でテーマ変更、`Tab` で Plan（相談）/ Build（実行）モード切替ができます。

既定のモデル表示は `vibe-auto (おまかせ)` です。雑談は小型モデルが即答し、
コーディングは大型モデルが引き受けます。特定モデルに固定したい時は TUI内の
`/models` か、`vibe-local --no-router` を使ってください。

### 対応環境

| 環境 | メモリ | モデル | 備考 |
|------|--------|--------|------|
| Apple Silicon Mac (M1以降) | 80GB+ | qwen3-coder-next | 🏆 52GB, 256Kコンテキスト |
| Apple Silicon Mac (M1以降) | 32GB+ | qwen3.6:35b-a3b | ⭐ **推奨** 24GB |
| Apple Silicon Mac (M1以降) | 16GB | gpt-oss:20b | ⭐ 14GB, 十分実用的 |
| Apple Silicon Mac (M1以降) | 8GB | qwen3.5:4b | 最低限動作 |
| Intel Mac / Linux | 16GB+ | gpt-oss:20b | 動作するが遅め |

### トラブルシューティング

<details>
<summary>💡 よくある問題と解決法</summary>

**"ollama が起動できませんでした"**
```bash
open -a Ollama        # macOS
ollama serve          # Linux
```

**"モデルが見つかりません"**
```bash
ollama pull qwen3.6:35b-a3b     # 自分のRAM帯のモデル名は vibe-local --doctor で確認
```

**"opencode が未インストールです"**
```bash
brew install opencode
```

**モデルを変更したい**
```bash
vibe-local --model <モデル名>
# または ~/.config/vibe-local/v2.conf の BASE_MODEL を編集して
vibe-local --rebuild
```

**テーマを変えたい** — `vibe-local --theme vaporwave`（黒灰に戻すなら `--theme null`）、または TUI内で `/theme`

</details>

---

## 🌱 やさしい にほんご

### これは なに？

Mac（まっく）で、AI（えーあい）が コードを かいて くれる どうぐ です。
インターネットが なくても つかえます。おかねも かかりません。

### いれかた（3つの ステップ）

**1.** ターミナルを ひらく（`Cmd+Space` → 「ターミナル」で けんさく）

**2.** したの もじを コピーして、はりつけて、Enterを おす：

```bash
curl -fsSL https://raw.githubusercontent.com/ochyai/vibe-local/main/install.sh | bash
```

**3.** あたらしい ターミナルを ひらいて、これを うつ：

```bash
vibe-local
```

### つかいかた

```bash
# AIと はなしながら プログラムを つくる
vibe-local

# 1かいだけ しつもんする
vibe-local -p "Pythonで じゃんけんゲームを つくって"
```

### きをつけること

> **⚠️ だいじ：AIが あぶない コマンドを うつことが あります！**

AIは かんぺきでは ありません。まちがった コマンドを うつことが あります。

ふつうに つかうと、AIは なにかを する まえに 「これを やっていい？」と きいてきます。
**わからない ときは 「いいえ」を えらんで ください。**

**きけんな サイン — こんな コマンドは ゆるさないで！**

| きけんな キーワード | なぜ あぶない？ |
|---|---|
| `sudo` で はじまる | パソコンの だいじな せっていが かわる |
| `chmod` が はいっている | ファイルの まもりが なくなる |
| いみが わからない ながい コマンド | なにが おきるか わからない！ |

**あんぜんに つかう ほうほう：**

- AIが コマンドを うつまえに、「これを やっていい？」と きいてきます
- わからない コマンドは **ぜったいに ゆるさないで ください**
- だいじな ファイルが ある フォルダでは つかわないで ください
- こまったら、`Ctrl+C` で とめられます

---

## 🇺🇸 English

### What is this?

A free AI coding environment you can set up with a single command on your Mac.
No network required. Completely free. Ollama + local LLM + the OpenCode TUI.

### Install (3 steps)

**1.** Open Terminal (Spotlight `Cmd+Space` → search "Terminal")

**2.** Paste and hit Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/ochyai/vibe-local/main/install.sh | bash
```

**3.** Open a new terminal and run:

```bash
vibe-local
```

### Usage

```bash
# Interactive mode (chat with AI while coding)
vibe-local

# One-shot (ask once)
vibe-local -p "Create a snake game in Python"

# Auto-detect network (uses Claude Code if online, local if offline)
vibe-local --auto

# Snappy mode (small model as the main driver)
vibe-local --fast

# Specify model manually
vibe-local --model devstral

# Switch theme (null = black & gray, default / vaporwave)
vibe-local --theme vaporwave

# Use the Claude Code CLI as the UI (compatibility mode)
vibe-local --classic

# Classroom mode: teacher runs the server, students attach
vibe-local --serve
vibe-local --attach http://192.168.x.x:4096

# Diagnostics
vibe-local --doctor
```

Inside the TUI: `/theme` switches themes, `Tab` toggles Plan / Build mode.

### Supported Environments

| Environment | RAM | Model | Notes |
|-------------|-----|-------|-------|
| Apple Silicon Mac (M1+) | 80GB+ | qwen3-coder-next | 🏆 52GB, 256K context |
| Apple Silicon Mac (M1+) | 32GB+ | qwen3.6:35b-a3b | ⭐ **Recommended**, 24GB |
| Apple Silicon Mac (M1+) | 16GB | gpt-oss:20b | ⭐ 14GB, very capable |
| Apple Silicon Mac (M1+) | 8GB | qwen3.5:4b | Minimum viable |
| Intel Mac / Linux | 16GB+ | gpt-oss:20b | Works but slower |

### Troubleshooting

<details>
<summary>💡 Common issues and solutions</summary>

**"ollama failed to start"**
```bash
open -a Ollama        # macOS
ollama serve          # Linux
```

**"model not found"**
```bash
ollama pull qwen3.6:35b-a3b     # check your RAM tier's model with: vibe-local --doctor
```

**"opencode not installed"**
```bash
brew install opencode
```

**Change model**
```bash
vibe-local --model <name>
# or edit BASE_MODEL in ~/.config/vibe-local/v2.conf, then
vibe-local --rebuild
```

</details>

---

## 🇨🇳 中文

### 这是什么？

在Mac上只需复制粘贴一个命令，AI就能帮你写代码。
无需网络，完全免费。Ollama + 本地大语言模型 + OpenCode 终端界面。

### 安装（3步）

**1.** 打开终端（Spotlight `Cmd+Space` → 搜索"终端"或"Terminal"）

**2.** 粘贴以下命令并按回车：

```bash
curl -fsSL https://raw.githubusercontent.com/ochyai/vibe-local/main/install.sh | bash
```

**3.** 打开新终端并运行：

```bash
vibe-local
```

### 使用方法

```bash
# 交互模式（与AI对话编程）
vibe-local

# 单次执行（只问一次）
vibe-local -p "用Python写一个贪吃蛇游戏"

# 自动检测网络（有网用Claude Code，没网用本地）
vibe-local --auto

# 快速模式（用小模型作为主力）
vibe-local --fast

# 手动指定模型
vibe-local --model devstral

# 切换主题（null = 黑灰色，默认 / vaporwave）
vibe-local --theme vaporwave

# 兼容模式（用 Claude Code CLI 作为界面）
vibe-local --classic

# 教室模式：老师启动服务器，学生连接
vibe-local --serve
vibe-local --attach http://192.168.x.x:4096

# 环境诊断
vibe-local --doctor
```

在TUI中：`/theme` 切换主题，`Tab` 切换 Plan / Build 模式。

### 支持的环境

| 环境 | 内存 | 模型 | 备注 |
|------|------|------|------|
| Apple Silicon Mac (M1及以上) | 80GB+ | qwen3-coder-next | 🏆 52GB, 256K上下文 |
| Apple Silicon Mac (M1及以上) | 32GB+ | qwen3.6:35b-a3b | ⭐ **推荐** 24GB |
| Apple Silicon Mac (M1及以上) | 16GB | gpt-oss:20b | ⭐ 14GB, 足够实用 |
| Apple Silicon Mac (M1及以上) | 8GB | qwen3.5:4b | 最低限运行 |
| Intel Mac / Linux | 16GB+ | gpt-oss:20b | 可运行但较慢 |

### 故障排除

<details>
<summary>💡 常见问题及解决方法</summary>

**"ollama 无法启动"**
```bash
open -a Ollama        # macOS
ollama serve          # Linux
```

**"未找到模型"**
```bash
ollama pull qwen3.6:35b-a3b     # 用 vibe-local --doctor 查看你的内存档位对应的模型
```

**"opencode 未安装"**
```bash
brew install opencode
```

**更换模型**
```bash
vibe-local --model <名称>
# 或编辑 ~/.config/vibe-local/v2.conf 的 BASE_MODEL 后执行
vibe-local --rebuild
```

</details>

---

## 🔧 Architecture (v2)

```
User
  ↓
vibe-local (launcher)
  ├─ RAM検出 → モデル選択 → num_ctx焼き込み別名 (vibe-coder / vibe-fast) を ollama create
  ├─ OpenCode設定生成 (OPENCODE_CONFIG, ユーザーの素のOpenCode設定は汚さない)
  │
  ├─ 既定:        OpenCode TUI ──(OpenAI互換 /v1)──→ Ollama → ローカルLLM
  ├─ --classic:    Claude Code CLI ──(ネイティブ /v1/messages)──→ Ollama → ローカルLLM
  ├─ --vibe-coder: 内蔵Pythonエンジン vibe-coder.py ──(/api/chat)──→ Ollama → ローカルLLM
  ├─ --serve:      OpenCode server を LAN公開 (教室モード, 生徒は --attach)
  └─ --auto:       オンライン→Claude Code(クラウド) / オフライン→ローカル
```

v1 の自作変換プロキシ (`anthropic-ollama-proxy.py`) と MLX 直結サーバー (`localllm.py`) は
Ollama のネイティブ Anthropic API 対応 (v0.14+, 2026-01) と MLX バックエンド (v0.19+) により
役目を終え、`legacy/` にアーカイブされています。

### 内蔵Pythonエンジン `vibe-coder.py` と RAG

コミュニティ製の依存ゼロ (stdlib のみ) コーディングエージェント `vibe-coder.py` を
`--vibe-coder`（別名 `--engine`）で起動できます。OpenCode / Node を使わず、
Ollama の `/api/chat` に直結してツール実行ループを回します。主な機能:

- **ローカル RAG**（`--rag`）: `sqlite3` + Ollama 埋め込みだけで、コードベースの関連コンテキストを
  system prompt に注入。索引作成は `vibe-local --vibe-coder --rag-index <path>`、
  参照は `--rag --rag-path .`。索引は `.vibe/rag/` に保存（git 管理外）。
  トップK件数は `--rag-topk`、埋め込みモデルは `--rag-model`（既定 `nomic-embed-text`）で調整。
- **ツールコール XML フォールバック**: ネイティブ tool_calls が来ない Qwen 系モデルでも、
  応答テキスト中の XML 形式のツールコールを抽出して実行（ストリーミング応答でも動作）。
- **堅牢な許可入力**: stdin が TTY でない場合も `/dev/tty` にフォールバック。日本語「はい」/
  英語 `yes` / 中国語「是」の三言語で承認可能。

`vibe-coder.py` は単体でも実行できます（`python3 vibe-coder.py -p "..."`）。
Windows 向けには `install.ps1` / `vibe-local.ps1`（コミュニティ提供）も同梱しています。

## 🚨 Security / セキュリティ / 安全须知

### 🇯🇵 日本語

> **⚠️ このツールは自己責任でご利用ください。AIが実行するコマンドには注意が必要です。**

v2 の既定は **毎回確認モード** です。AIがファイル編集・コマンド実行をする前に必ず許可を求めます。
`vibe-local -y` を付けると **ツール自動許可モード** になり、AIが確認なしで
ファイルの読み書き・コマンド実行・システム操作を行います。

**ローカルLLMはクラウドAIより精度が低いため、意図しない危険な操作を実行するリスクがあります。**

#### こんなコマンドに注意

AIが提案するコマンドの中に以下のキーワードが含まれていたら、**内容を理解できない限り拒否してください：**

| 注意すべきキーワード | リスク |
|---|---|
| `sudo` で始まるコマンド | システム全体に影響する管理者権限での操作 |
| `chmod` / `chown` | ファイルの権限やセキュリティ設定が変わる |
| `dd` / `mkfs` / `/dev/` | ディスクやパーティションを直接操作する |
| `>` で設定ファイルを上書き | 大事な設定が消える |
| `--force` が付いたコマンド | 安全確認をスキップして強制実行する |
| 意味がわからない長いコマンド | 何が起きるかわからない＝許可してはいけない |

#### 安全に使うためのルール

1. **既定の毎回確認モードで使う** — AIの各操作を事前に確認できます
2. **わからないコマンドは許可しない** — 少しでも不安なら拒否
3. **大事なファイルがあるフォルダでは使わない** — 新しい空フォルダで練習
4. **`sudo` を求められたら基本的に拒否** — ローカルLLMの判断でシステム操作させない
5. **困ったら `Ctrl+C` で停止**

```bash
vibe-local        # 毎回確認モード（推奨・既定）
vibe-local -y     # 自動許可モード（上級者向け・自己責任）
```

### 🌱 やさしい にほんご

> **⚠️ だいじな おしらせ：AIは まちがえることが あります！**

AIが うごかそうとする コマンド（めいれい）を よく みてください。
わからない コマンドは、**ぜったいに ゆるさないで ください。**

- ふつうの モードでは、AIが まいかい 「これ やっていい？」と きいてきます
- `rm`（さくじょ）や `sudo`（かんりしゃ）が はいった コマンドは きけん
- こまったら **`Ctrl+C`**（コントロール と C を いっしょに おす）で とまる
- れんしゅうは **あたらしい からの フォルダ** で やる

### 🇺🇸 English

> **⚠️ Use this tool at your own risk. Pay attention to the commands the AI executes.**

v2 defaults to **ask-before-every-action mode**. With `vibe-local -y` the AI can read/write files,
execute commands, and modify your system **without asking**.

**Local LLMs are less accurate than cloud AI — they may attempt dangerous operations unintentionally.**

If a suggested command contains `sudo`, `chmod`/`chown`, `dd`/`mkfs`/`/dev/`, config-overwriting `>`,
a `--force` flag, or anything you can't read — **reject it.**

```bash
vibe-local        # Ask mode (recommended, default)
vibe-local -y     # Auto-approve mode (advanced users only, at your own risk)
```

### 🇨🇳 中文

> **⚠️ 使用本工具风险自负。请注意AI执行的每一个命令。**

v2 默认为**每次操作前确认**。使用 `vibe-local -y` 后，AI可以在**无需确认**的情况下
读写文件、执行命令、修改系统。

**本地LLM的精度低于云端AI，可能意外执行危险操作。**

如果命令中包含 `sudo`、`chmod`/`chown`、`dd`/`mkfs`/`/dev/`、覆盖配置的 `>`、`--force`，
或任何你看不懂的内容——**务必拒绝。**

```bash
vibe-local        # 确认模式（推荐，默认）
vibe-local -y     # 自动批准模式（仅限高级用户，风险自负）
```

---

## ⚙️ Notes

- Local LLM accuracy is lower than Claude API
- First model download takes time (2.7GB to 52GB depending on RAM)
- Use `vibe-local --auto` to auto-switch to Claude Code when online
- `vibe-coder` / `vibe-fast` are auto-generated Ollama model aliases with RAM-appropriate
  `num_ctx` baked in (weights are shared with the base model — no extra disk usage)

---

## 📜 Disclaimer / 免責事項 / 免责声明

### 🌱 やさしい にほんご

> **この どうぐは Anthropic（あんそろぴっく）という かいしゃとは かんけい ありません。**
> じぶんの せきにんで つかってください。
> なにか もんだいが おきても、つくった ひとは せきにんを とれません。
> **つかうまえに、せんせいや くわしいひとに そうだん してください。**

### 🇯🇵

> **本プロジェクトは Anthropic 社、Alibaba 社、OpenAI 社、および OpenCode プロジェクトとは一切関係ありません。**
> 各社が提供・推奨・保証するものではありません。
> 「Claude」は Anthropic, PBC の商標です。本プロジェクトは非公式のコミュニティツールです。
>
> `--classic` モードは Claude Code CLI を非標準の方法で使用します（ローカルLLMに接続）。
> Claude Code CLI の利用規約に抵触する可能性があります。利用者は自身で利用規約を確認してください。
>
> 本ソフトウェアは現状有姿（AS IS）で提供され、明示的・暗示的を問わず、いかなる保証もありません。
> 使用によって生じたいかなる損害についても、著者は一切責任を負いません。
> **すべて自己責任でご利用ください。**

### 🇺🇸

> **This project is NOT affiliated with, endorsed by, or associated with Anthropic, Alibaba, OpenAI, or the OpenCode project.**
> "Claude" is a trademark of Anthropic, PBC. This is an unofficial community tool.
>
> The `--classic` mode uses the Claude Code CLI in a non-standard way (connecting to local LLMs).
> This may not comply with the Claude Code CLI's terms of service. Users should review the terms themselves.
>
> Third-party dependencies (Ollama, OpenCode, Qwen/gpt-oss models, etc.) have their own licenses and terms.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
> The authors are not liable for any damages arising from the use of this software.
> **Use entirely at your own risk.**

### 🇨🇳

> **本项目与 Anthropic、Alibaba、OpenAI 及 OpenCode 项目无任何关联。**
> 非其提供、推荐或担保。"Claude"是 Anthropic, PBC 的商标。本项目是非官方社区工具。
>
> `--classic` 模式以非标准方式使用 Claude Code CLI（连接本地LLM）。
> 这可能不符合 Claude Code CLI 的服务条款。用户应自行确认相关条款。
>
> 第三方依赖（Ollama、OpenCode、Qwen/gpt-oss模型等）有各自的许可证和使用条款。
>
> 本软件按"原样"提供，不提供任何明示或暗示的保证。
> 作者不对因使用本软件而产生的任何损害承担责任。
> **使用本工具风险完全自负。**

## 📄 License

MIT
