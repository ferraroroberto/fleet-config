# Fleet inference capacity — open-weight model fit per compute node

One-time survey for [#400](https://github.com/ferraroroberto/fleet-config/issues/400): what open-weight model classes realistically fit each fleet compute node's **measured** ceiling, with the size math shown. Consult before assigning any local-inference role in any fleet repo.

**Measured 2026-07-20** — CPU/RAM/GPU captured live over SSH on each box (not copied from config role strings; the `tower` entry's "32 GB RAM" was already stale). Node addresses and login details deliberately not recorded here (public repo) — they live in the private geek-out setup notes and `local-llm-hub`'s `config/models.yaml` `hosts:` block.

**Relationship to the frontier ledger:** [local-llm-hub#272](https://github.com/ferraroroberto/local-llm-hub/issues/272) runs a bi-weekly `/frontier-refresh` that owns *which model wins each role on pc-cuda* (latest run 2026-07-12: `agentic_light`/`agentic_heavy` both **keep**). This doc answers a different question — *what class of model each node can carry at all* — and defers every role verdict to that ledger. The `gaming` bring-up is tracked in [local-llm-hub#323](https://github.com/ferraroroberto/local-llm-hub/issues/323). Kept honest by `/sota-watch`'s `fleet-inference-capacity` watchlist area ([#393](https://github.com/ferraroroberto/fleet-config/issues/393)).

## The fit math

- **Weights:** ≈ `params × bits ÷ 8`, plus ~5–10% GGUF overhead. Rules of thumb: **Q4_K_M ≈ 0.60 GB per B params**, Q6_K ≈ 0.82 GB/B, Q8_0 ≈ 1.07 GB/B.
- **KV cache & runtime:** budget **1–2 GB** on top for context + compute buffers at 8–16k ctx; long contexts (128k+) can add several GB more. Reducing ctx is the first lever when a model almost fits.
- **MoE:** memory is set by **total** params, speed by **active** params — a 26B-A4B MoE costs ~13 GB at IQ4_XS but runs like a 4B.
- **Unified memory (Apple):** the OS + apps share the pool; treat ~65–70% of RAM as the practical weights+cache ceiling.

## Summary

| node | CPU | RAM | GPU / VRAM | practical class ceiling |
|---|---|---|---|---|
| **pc-cuda** | Ryzen 7 7800X3D 8c/16t | 128 GB DDR5 | RTX 5060 Ti · 16 GB (Blackwell) | 26B-class MoE IQ4 on GPU; 100B+-MoE via CPU offload |
| **gaming** | Ryzen 9 5900X 12c/24t | 16 GB (single-stick) | GTX 1070 · 8 GB (Pascal) | 7–9B Q4 on GPU; role: STT/TTS offload |
| **mac-mini-m4** | Apple M4 4P+6E | 16 GB unified | integrated (Metal) | ≤9B Q4 comfortably; 14B Q4 tight |
| **openclaw** | i7-10510U 4c/8t (15 W) | 16 GB | MX250 · 2 GB (not usable) | CPU-only 1–4B Q4, background work |

## pc-cuda — the primary inference workstation

**Measured:** RTX 5060 Ti 16 GB VRAM (Blackwell, FP4-capable, 448 GB/s) · Ryzen 7 7800X3D (96 MB L3 — unusually viable CPU inference) · 128 GB DDR5.

The 16 GB picture is already maintained in depth by the frontier run (`local-llm-hub` `docs/frontier/runs/2026-07-12/report.md`) — not re-derived here. The fit envelope:

- **Dense ≤14B** at Q4–Q6 with full context headroom (14B × 0.60 ≈ 8.4 GB + cache ≈ 10 GB).
- **26B-class MoE at the ceiling:** `gemma4-26b-a4b` IQ4_XS ≈ 13.4 GB weights — fits alone, but collides with the GPU-resident audio services (whisper large-v3-turbo ~1.6 GB + orpheus TTS ~4 GB); exactly the pressure that motivates offloading audio to `gaming` (local-llm-hub#323).
- **Big MoE via CPU offload:** 128 GB RAM + the 7800X3D make expert-offload runs (GLM-4.5-air class) workable; 100B+ dense and ~700B-MoE (GLM-5.2) remain NO-GO — no quant fits 144 GB combined (local-llm-hub `docs/glm-5.2-evaluation.md`).

## gaming — 8 GB Pascal satellite (bring-up in progress)

**Measured:** GTX 1070 8 GB (GP104, Pascal) · Ryzen 9 5900X 12c/24t · 16 GB RAM, **single-stick** (single-channel — roughly halves memory bandwidth vs dual, which directly caps CPU-inference speed) · 100 GB root, 85 GB free · Ubuntu Desktop.

- **⚠️ Driver gap (measured):** the box currently runs **nouveau** — `nvidia-smi` is absent, so no CUDA and no GPU inference until the proprietary NVIDIA driver is installed. A remaining bring-up step for local-llm-hub#323.
- **Pascal caveats:** no tensor cores and crippled FP16 on GP104 — llama.cpp falls back to FP32/DP4A paths, so throughput sits well below what "8 GB" suggests on newer cards. Community guidance treats a 1070 as effectively a 6 GB-tier card for planning.
- **Dense fit:** 7–9B Q4 fits (9B × 0.60 ≈ 5.4–6.6 GB + reduced ctx), e.g. Qwen 3.5 9B — workable, not fast.
- **Intended role (the actual plan, per #323):** STT/TTS offload — whisper large-v3-turbo (~1.6 GB) + orpheus 3B TTS (~4 GB) ≈ **6 GB, comfortably inside 8 GB together**, freeing pc-cuda's VRAM for both agentic lanes. Pascal's compute limits matter far less for these small resident models than for chat-class LLMs; this is the right job for the card.
- **CPU fallback:** the 5900X is strong, but single-channel 16 GB caps it — 4B-class Q4 (~2.6 GB) only; a second RAM stick was removed for boot instability (#323 history), so don't plan around 32 GB.

## mac-mini-m4 — Apple-silicon peer

**Measured:** Apple M4, 10 cores (4 performance + 6 efficiency) · 16 GB unified memory.

- **Practical ceiling:** ~10–11 GB for weights + cache (65–70% of unified memory; macOS keeps the rest).
- **Sweet spot: ≤9B Q4** — the currently enrolled `qwen3.5-9b` (~6.6–7 GB) runs at ~22–28 tok/s on an M4; 3–4B models exceed 25 tok/s.
- **14B Q4 is possible but tight** (~8.4 GB + cache ≈ 10 GB — at the ceiling, short ctx only; community numbers show ~10 tok/s for 14B-class). **26B-class MoE and 24B+ dense are out** of the 16 GB machine's comfort zone.
- Also carries `parakeet` ASR. MLX-based runtimes are the performance-mature path on Apple silicon; GGUF/llama.cpp works and is what the hub's launcher pattern uses today.

## openclaw — CPU-only utility laptop

**Measured:** i7-10510U (4c/8t, 15 W Comet Lake) · 16 GB RAM · GeForce MX250 2 GB (GP108) · 92 GB NVMe, 48 GB free.

- **The MX250 is not an inference GPU:** 2 GB fits no useful quant, and offloading a few layers to a Pascal GP108 gains roughly nothing — plan CPU-only.
- **CPU-only class: 1–4B Q4** — Qwen 3.5 4B Q4 (~2.6 GB) is the realistic default; Gemma 4 E4B (~6 GB weights, ~12.5 GB with full 128k ctx) fits only with reduced context. Expect single-digit tok/s on a 15 W 4-core — background/batch quality, not interactive.
- **Realistic roles:** batch text jobs, small-model experiments, CPU STT (whisper small/medium class) — not a serving node for any latency-sensitive lane. Enrolled in the hub as a managed machine, "future inference node" only in that limited sense.

## Sources

Measured captures 2026-07-20 (this doc). Frontier picture: local-llm-hub `docs/frontier/runs/2026-07-12/report.md`. Class guidance cross-checked against: [engineeredai.net VRAM tiers](https://engineeredai.net/best-local-ai-models-for-your-gpu/), [localllm.in 8 GB benchmarks](https://localllm.in/blog/best-local-llms-8gb-vram-2025), [apxml.com Apple-silicon guide](https://apxml.com/posts/best-local-llm-apple-silicon-mac), [popularai.org CPU-only guide](https://www.popularai.org/p/best-cpu-only-local-llm-2026), [promptquorum.com laptop guide](https://www.promptquorum.com/local-llms/local-llm-on-laptop).
