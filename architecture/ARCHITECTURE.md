# Roberto's System — Architecture

> The system explained layer by layer: how I reach it, what runs it, the shared tools that enable everything, the apps I actually use, and the governance on top. This document is the **source of truth** — the visual map (horizontal, Janis-style) is generated *from* the structure below, so the knowledge lives here in words first.

## How to read this doc (fixed structure)

The system is a **stack of layers**; each layer builds on the ones beneath it.

```
            ┌─────────────────────────────────────────────┐
   on top   │  L4  GOVERNANCE   scaffolding · claude-config │
            ├─────────────────────────────────────────────┤
            │  L3  WORKING APPS & EXPERIMENTS               │  ← the things I do
            ├─────────────────────────────────────────────┤
            │  L2  ENABLING META-TOOLS (shared)             │  ← the tools that let me
            ├─────────────────────────────────────────────┤
            │  L1  CONNECTIVITY & ACCESS                    │  ← how I reach it
            ├─────────────────────────────────────────────┤
   base     │  L0  COMPUTE FOUNDATION (the PC)              │  ← where it all runs
            └─────────────────────────────────────────────┘
                          ↘ EXTERNAL INTEGRATIONS ↙
              (from individual apps AND the orchestration layer)
```

**The schema is fixed; only the per-project data changes.** Every project is one row with the same fields, so this doc — and the visual built from it — can be regenerated as the fleet evolves:

| Field | Meaning |
|---|---|
| **Project** | repo name under `E:/automation` |
| **What it is** | one-line description (longer for *shared* pieces, brief for self-explanatory leaf apps) |
| **Port / type** | the service port, or `pipeline` / `library` / `static` |
| **Builds on** | the lower-layer pieces or APIs it consumes |
| **Consumed by / External** | who uses its API, and which outside services it talks to |

**Two classes of project:**
- **Shared (used more than once)** → always carries a real description; these are the load-bearing pieces (the hub, the launcher, whisper, scaffolding). *These are the ones to get right.*
- **Leaf (self-explanatory)** → name + a short line is enough (e.g. `vibe-coding-workshop`, `grocery-shopping-automation`, `pvgis`).

---

## L0 — Compute foundation

One **Windows 11 PC is the entire server.** Unlike a rented VPS, all compute and memory are local: a **powerful discrete GPU** does the heavy calculation (the local LLMs and speech models), and **ample RAM** lives here. Nothing in the mapped stack runs in someone else's cloud.

| Resource | Spec | Role |
|---|---|---|
| **GPU** | discrete NVIDIA GPU `<exact model in local config>` | runs the local LLM models (llama-servers) and the whisper speech model |
| **CPU** | multi-core desktop CPU `<exact model in local config>` | drives the apps, pipelines, and CPU-offload model layers |
| **RAM** | large `<exact size in local config>` | holds the loaded models + every running tray app |
| **Host** | Windows 11 | the always-on machine for all layers above |

> 🔒 **Exact hardware specs are personal detail kept out of git.** The real values live in `architecture/system-map.local.js` (gitignored); `architecture/system-map.local.example.js` shows the format. The visual reads them at render time — placeholders here and in the committed PNG, real values only on the local machine.

> 🧭 **Local by choice (design decision).** This whole stack *could* run on a remote VPS — but everything (all my data, every app) lives and interconnects here, and **Tailscale** makes the single box completely private. For now I deliberately keep it local, with backups, rather than rent a server. Not a constraint — a preference.

---

## L1 — Connectivity & access (how I reach the system)

I reach the PC from several places, through two private/public layers. This is the part that makes a single home PC usable as a personal cloud.

| Access point | What it is |
|---|---|
| 📱 **iPhone** | **phone-first** — one tap launches coding agents, apps, jobs, productivity skills |
| 🖥️ **The home PC** | the server itself — also where I work locally |
| 💻 **Laptop (remote)** | the other machine I work from, reaching the home PC over Tailscale |

…routed through two layers:

| Layer | What it is |
|---|---|
| 🔒 **Tailscale** | private mesh VPN — the **only** inbound path for sensitive surfaces (live terminal, passkey-gated browsing). Used by every remote-access app. |
| ☁️ **Cloudflare named tunnels** | **public**, bearer-token-gated entry (`launcher.…`, `grocery.…`). For surfaces meant to be reachable without the tailnet. Used by several apps. |
| 📶 **Local Wi-Fi** | direct `https://<host>:84xx` when on the same network |

---

## L2 — Enabling meta-tools (shared infrastructure)

These are the **pieces that let me do everything else** — each is *used by more than one* working app, so each gets a real description. This is the heart of the system.

| Project | What it is | Port | Builds on | Consumed by / External |
|---|---|---|---|---|
| 🚀 **app-launcher** | The **orchestration layer — where I live.** Phone-first hub with four tabs: Coding (launch Claude Code / Codex / Antigravity / Copilot in any repo), Apps (launch any tray webapp), Jobs (one-shot + scheduled scripts + automation, same executor as Stream Deck & Task Scheduler), Life OS (run a `life-os` skill). The single front door to the whole fleet — and the project that started it all: born from one goal, *do everything on the PC from the phone.* | `:8445` (+ session-host `:8446`) | L1 access, Task Scheduler | **Slack** (job-failure / status pings), **Pushover** |
| 🧠 **local-llm-hub** | The **shared LLM gateway**: one HTTP hub exposing Anthropic-shape and OpenAI-shape APIs, routed by model name to the local models or to the `claude -p` CLI (subscription). Every app that needs an LLM call routes through here — apps never re-implement their own `claude -p` wrapper. | `:8000` | L0 GPU/RAM | downstream apps (e.g. grocery audit), coding agents |
| 🎙️ **voice-transcriber** | The **shared speech layer**: always-on local voice-to-text (whisper.cpp), global `F8` hotkey → auto-paste at the caret. Owns the `whisper-server` on `:8090`, which other apps reuse. | `:8443` (whisper `:8090`) | L0 GPU/RAM | grocery voice-audit (whisper, mutex-shared) |
| 📷 **photo-ocr** | Mobile-first **OCR service**: snap N photos of a document/screen/email → clean text. A reusable capture-to-text surface (tray + PWA + Cloudflare tunnel), sibling to the launcher and voice apps. | `:8444` | L1 access, L2 hub | (capture surface for downstream use) |

**Local models behind the hub** (run on the L0 GPU):

| Model id | Backend |
|---|---|
| `claude-haiku/sonnet/opus-*` | forwarded to local `claude -p` CLI (subscription) |
| `qwen3.5-9b` | llama-server `:8081` |
| `glm-4.5-air` | llama-server `:8082` (MoE, CPU offload) |
| `gemma4-e4b-it` | llama-server `:8086` (edge tier) |
| `gemma4-26b-a4b-it` | llama-server `:8087` (quality tier) |
| whisper `large-v3-turbo` | whisper-server `:8090` |

---

## L3 — Working apps & experiments (the things I actually do)

The **work layer**, enabled by L2. Most mature ones are **web apps exposing an API that other apps can consume** — the fleet composes. Leaf apps get one line; anything shared is noted.

### Web apps & tools (interactive)

| Project | What it is | Port / type | Builds on |
|---|---|---|---|
| 🛒 **grocery-shopping-automation** | Household grocery inventory + shopping-list PWA, with a **voice-narrated audit mode**. | `:8502` FastAPI+PWA | hub + whisper (L2) |
| 💬 **whatsapp-radar** | Local-first spike to cut attention load from high-volume WhatsApp chats (monitor → process only new → deliver). | local app | hub (L2) |
| 🎲 **facilitation-shuffle** | Randomises workshop participants into 1-2-4-all breakout groups for Zoom. | Streamlit | — (Zoom) |
| 💶 **family-accounting** | Local shared household-expense tracker (import bank exports → classify). | Streamlit | — |
| 🧮 **mathgamesforkids** | Educational games + math tools + HTML experiments. | static | — |
| 🌐 **website** | Multi-workshop landing site (one template, per-workshop config). | static | — |
| 🎓 **vibe-coding-workshop** | Hands-on Python workshop — slideshow + structured exercises. | static/notebooks | — |

### Pipelines & batch (non-interactive)

| Project | What it is | Type | Builds on / External |
|---|---|---|---|
| 💳 **accounting-quarterly** | Classifies Stripe payments by activity + region → quarterly reports. | pipeline | **Stripe** |
| 📊 **social-media-analytics** | Analyse + predict social-content performance (2.5y of data). | pipeline | — |
| 💡 **inspiration-system** | Paste an idea → 10 ranked illustration suggestions from the Notion archive. | pipeline | **Notion**, hub |
| 📝 **content-management** | Four content pipelines in one repo. | pipeline | **Notion** |
| 📧 **email-archiver** | Index + archive Outlook emails into a structured OneDrive tree. | pipeline | **Outlook → OneDrive** |
| 📄 **pdf-to-markdown** | PDF → clean, token-efficient Markdown for LLM consumption. | pipeline/lib | — |
| 🧾 **mass-html-to-markdown** | Ingest HTML comparison pages → SQLite → Markdown. | pipeline | — |
| 🎨 **illustration-color-edit** | Industrial SVG conversion pipelines for book illustrations. | pipeline | — |
| ☀️ **pvgis** | Estimate a house's solar-PV output via the EU PVGIS service. | tool | (PVGIS API) |
| 🗂️ **copilot-studio-transcripts** | Streamlit workbench for Copilot Studio transcripts. | Streamlit | — |
| 📚 **closed-company-accounting** | Accounting tooling for a closed company. | pipeline | — |
| 🤖 **automation** | Grab-bag of Python automation tools (audio, image, video, email, Notion, system). | scripts | Notion, etc. |
| 🌱 **life-os** | Personal productivity suite as Claude Code skills (diary, meeting prep, sparring, visual muse, …). Launched from the app-launcher's Life OS tab. | skills | app-launcher (L2) |

---

## L4 — Governance (on top of everything)

The meta-layer that keeps the whole fleet consistent — it sits *above* the apps because it governs them. **Two boxes, one boundary line:** `project-scaffolding` owns *what goes **inside** a project*; `claude-config` owns *what sits **above** all projects*. Every "where does this live?" question answers itself from that line.

> 🧭 **Why two repos, not one (boundary rule — durable decision home).** They have **opposite lifecycles**: `project-scaffolding` is a *clone-per-project seed* (the thing you copy to start a project), while `claude-config` is a *singleton* installed once via junctions into `~/.claude` / `~/.codex` / `~/.agents` (your machine's live hooks and global rules). Merging them was considered and **decided against** — it would force one repo to be both, dragging the whole governance apparatus into every leaf app. So the rule is the boundary, not the merge: *inside-a-project* content lives in scaffolding, *above-all-projects* content lives in claude-config, and neither re-states the other's.

| Project | Scope | What it is |
|---|---|---|
| 📐 **project-scaffolding** | what ships **inside** each project | The **canonical master**: the scaffold + `CLAUDE.md` every sister project derives from. Conventions flow *down* from here; divergence is the thing it prevents. |
| ⚙️ **claude-config** | what governs the **machine**, above all projects | **Fleet-wide Claude Code config**: user-scope hooks, skills, and the issue workflow, installed once via junctions into `~/.claude`. The Slack idle-pings and commit guards live here. |

Plus cross-cutting shared helpers (single source of truth per concern, reused by every app that needs them): the **Chrome stealth + persistent-profile-lock** launch helpers (anti-bot browser automation), and the **tray + PWA + Cloudflare-tunnel** app pattern shared by the launcher / voice / photo / grocery webapps.

---

## External integrations

External connections come from **two sources**: the **orchestration layer** (app-launcher) and **individual apps**.

| Service | Reached from | For |
|---|---|---|
| 💬 **Slack** | **orchestration** (app-launcher + claude-config hooks) | idle/needs-you pings, job status |
| 🔔 **Pushover** | orchestration (app-launcher jobs) | job-failure push notifications |
| 📔 **Notion** | apps: inspiration-system, content-management, automation | content archive, automation |
| 💳 **Stripe** | app: accounting-quarterly | payment classification |
| 📁 **OneDrive** | apps: email-archiver, mcp-personal-onedrive | email archive, file browsing |
| 🐙 **GitHub** | all repos | PRs, CI, issue workflow |
| 🎥 **Zoom** | app: facilitation-shuffle | session facilitation |

---

## Cross-cutting principles

1. **Each layer builds on the one below.** A working app (L3) leans on the enabling tools (L2), which lean on the compute foundation (L0), reached through connectivity (L1), all kept consistent by governance (L4).
2. **Apps are web apps with APIs that compose.** Mature pieces expose HTTP surfaces consumable by other pieces — the hub, whisper, the launcher, OCR are all called by others rather than re-implemented.
3. **Don't duplicate the hub.** Any LLM/voice need routes through `local-llm-hub` / `whisper-server`; downstream apps never re-implement model access.
4. **Don't diverge from scaffolding.** Reusable conventions flow up into `project-scaffolding`; per-project specifics stay local.
5. **Phone-first.** The default interaction is one tap on the iPhone through the launcher, not a desktop session.

---

## Maintaining this doc

- **Structure is fixed** (the layers and the per-project schema above). When the fleet changes, only the **rows** change — add/edit a project line in the right layer with the same fields.
- The **visual map is generated from this file**: it reads the layers + per-project rows and lays them out as horizontal blocks (light theme, Janis-style). Keeping descriptions here means the picture never goes stale.
- Source of fleet membership: `hooks/projects.toml`. Source of per-project descriptions: each repo's `README.md` / `CLAUDE.md`.
- **Excluded repos** (vendored, legacy, or out-of-scope) are listed once in `hooks/projects.toml` under `[global] architecture_ignore` — the doc and the generated visual both skip them, so the exclusion stays maintainable in one place. Currently: `suna`, `externalrisk`, `arboldelossuenos`, `notion-automation-files`.

> Status: **draft for review.** Verify the L2/L3 split matches how you think about "enabling vs. working."
