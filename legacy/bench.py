#!/usr/bin/env python3
"""
Benchmark: Ollama vs localllm.py
Measures TTFT (time to first token), total time, tokens/sec
"""
import urllib.request
import json
import time
import sys

PROMPTS = [
    # Short
    {"label": "short", "text": "What is 2+2? Answer in one word."},
    # Medium
    {"label": "medium", "text": "Write a Python function that checks if a number is prime. Include a docstring."},
    # Long
    {"label": "long", "text": "Explain the difference between processes and threads in operating systems. Cover scheduling, memory, IPC, and give code examples in Python for both."},
]

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
LOCALLLM_URL = "http://127.0.0.1:8082/v1/chat/completions"

def bench_stream(url, model, prompt, label=""):
    """Send streaming request, measure TTFT and throughput."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
        "temperature": 0.1,
        "stream": True,
    }).encode()

    req = urllib.request.Request(url, data=payload,
                                headers={"Content-Type": "application/json"})

    t_start = time.perf_counter()
    ttft = None
    token_count = 0
    total_text = ""

    try:
        resp = urllib.request.urlopen(req, timeout=120)
        buf = b""
        for chunk in iter(lambda: resp.read(1), b""):
            buf += chunk
            if chunk == b"\n":
                line = buf.decode("utf-8", errors="replace").strip()
                buf = b""
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        if ttft is None:
                            ttft = time.perf_counter() - t_start
                        token_count += 1
                        total_text += text
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        return {"error": str(e)}

    t_total = time.perf_counter() - t_start

    # tok/s after first token (generation speed)
    gen_time = t_total - (ttft or 0)
    tok_per_sec = token_count / gen_time if gen_time > 0 else 0

    return {
        "ttft_ms": round((ttft or 0) * 1000, 1),
        "total_ms": round(t_total * 1000, 1),
        "tokens": token_count,
        "tok_per_sec": round(tok_per_sec, 1),
        "chars": len(total_text),
    }

def bench_sync(url, model, prompt, label=""):
    """Send non-streaming request, measure total latency."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
        "temperature": 0.1,
        "stream": False,
    }).encode()

    req = urllib.request.Request(url, data=payload,
                                headers={"Content-Type": "application/json"})

    t_start = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        t_total = time.perf_counter() - t_start
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # Rough token estimate
        tokens = len(content.split())
        return {
            "total_ms": round(t_total * 1000, 1),
            "tokens": tokens,
            "tok_per_sec": round(tokens / t_total, 1) if t_total > 0 else 0,
            "chars": len(content),
        }
    except Exception as e:
        return {"error": str(e)}


def run_bench(name, url, model, n_warmup=1, n_runs=3):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  URL: {url}")
    print(f"  Model: {model}")
    print(f"{'='*60}")

    # Warmup
    print(f"\n  Warmup ({n_warmup} run)...")
    bench_sync(url, model, "Say hi.", "warmup")

    for p in PROMPTS:
        print(f"\n  --- {p['label']} prompt ---")
        print(f"  \"{p['text'][:60]}...\"")

        # Streaming
        stream_results = []
        for i in range(n_runs):
            r = bench_stream(url, model, p["text"], p["label"])
            if "error" in r:
                print(f"  Stream run {i+1}: ERROR {r['error']}")
                continue
            stream_results.append(r)
            print(f"  Stream run {i+1}: TTFT={r['ttft_ms']}ms, "
                  f"total={r['total_ms']}ms, "
                  f"{r['tokens']}tok @ {r['tok_per_sec']} tok/s")

        if stream_results:
            avg_ttft = sum(r["ttft_ms"] for r in stream_results) / len(stream_results)
            avg_tps = sum(r["tok_per_sec"] for r in stream_results) / len(stream_results)
            avg_total = sum(r["total_ms"] for r in stream_results) / len(stream_results)
            print(f"  AVG: TTFT={avg_ttft:.0f}ms, total={avg_total:.0f}ms, {avg_tps:.1f} tok/s")

        # Sync
        sync_results = []
        for i in range(n_runs):
            r = bench_sync(url, model, p["text"], p["label"])
            if "error" in r:
                print(f"  Sync  run {i+1}: ERROR {r['error']}")
                continue
            sync_results.append(r)
            print(f"  Sync  run {i+1}: total={r['total_ms']}ms, "
                  f"{r['tokens']}tok @ {r['tok_per_sec']} tok/s")

        if sync_results:
            avg_total = sum(r["total_ms"] for r in sync_results) / len(sync_results)
            avg_tps = sum(r["tok_per_sec"] for r in sync_results) / len(sync_results)
            print(f"  AVG: total={avg_total:.0f}ms, {avg_tps:.1f} tok/s")


if __name__ == "__main__":
    print("Benchmark: Ollama vs localllm.py")
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    targets = sys.argv[1:] if len(sys.argv) > 1 else ["ollama", "localllm"]

    if "ollama" in targets:
        # Run Ollama bench (qwen3-coder:30b = MoE 30B-A3B, Q4_K_M)
        run_bench(
            "Ollama (HTTP → llama.cpp → Metal) [qwen3-coder:30b MoE]",
            OLLAMA_URL,
            "qwen3-coder:30b",
            n_warmup=1,
            n_runs=3,
        )

    if "localllm" in targets:
        # Run localllm bench — same MoE architecture for fair comparison
        run_bench(
            "localllm.py (HTTP → MLX → Metal) [Qwen3-Coder-30B-A3B MoE]",
            LOCALLLM_URL,
            "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
            n_warmup=1,
            n_runs=3,
        )

    if "dense" in targets:
        # Dense 32B for reference
        run_bench(
            "localllm.py (HTTP → MLX → Metal) [Qwen2.5-Coder-32B Dense]",
            LOCALLLM_URL,
            "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit",
            n_warmup=1,
            n_runs=3,
        )

    print("\n" + "="*60)
    print("  DONE")
    print("="*60)
