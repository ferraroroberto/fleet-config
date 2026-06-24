# architecture/ — fleet system map

Self-portrait of the whole `E:/automation` fleet. Built in #94; made **self-describing** in #148 (each repo declares its own card).

**Source of truth is distributed:** each repo declares its own map card in a root **`.fleet.toml`**, and **`.claude/skills/system-map/build_data.py`** aggregates those into the generated `fleet.data.js`. The layered narrative [`ARCHITECTURE.md`](ARCHITECTURE.md) (compute → connectivity → enabling tools → working apps → governance) is the human-readable companion the data must agree with. So a new repo appears on the map automatically, correct, with zero central editing — and the picture can't go stale.

## The visual: `system-map.html` → `system-map.png`

A **light-theme, horizontal, Janis-style** infographic — grouped zone panels, every project a card with a one-line description. Built as **hand-authored HTML/CSS**, chosen over Mermaid so each block carries real text and the layout is fully controlled.

**Data flow:** `fleet.data.js` (`window.FLEET = { …strict JSON… }`: governance / access / edge / compute / enabling / web / pipe / external + principles) is the file `system-map.html` renders — but it is **generated**, never hand-edited. `build_data.py` assembles it from two inputs:

- each repo's root **`.fleet.toml`** — that repo's self-declared card, authoritative when present;
- **`fleet.residual.json`** — hand-maintained: the non-repo structure (access/edge/compute/external/principles), a fallback card per repo in curated order, and an `_adopted` registry listing repos that MUST self-describe.

`tests/run_acceptance.py` asserts the fleet, the generated data, the per-repo `.fleet.toml`s, and `ARCHITECTURE.md` never drift apart — including that `fleet.data.js` is exactly what `build_data.py` regenerates and that no `_adopted` repo has lost its `.fleet.toml` (so per-repo metadata can't silently go stale).

### Per-repo `.fleet.toml` schema

Each repo carries a `.fleet.toml` at its root declaring its one card on the map. Parsed with stdlib `tomllib` (no dependency). Required: `layer`, `icon`, `description`. Optional, used only where the card needs them:

```toml
layer       = "working-pipe"   # governance | enabling | working-web | working-pipe
icon        = "📄"             # emoji shown on the card
description  = "PDF → clean Markdown for LLMs."   # one line; injected as innerHTML
# --- optional ---
display_name = "grocery"        # when the card label ≠ repo directory name
port         = ":8444"          # fixed loopback port the app serves (enabling tier)
chips        = ["whisper :8090"] # sub-services shown as chips (enabling tier)
tag          = ["→", "Notion"]  # [relation, target] edge annotation (working tiers)
```

| Field | Required | Maps to | Notes |
|---|---|---|---|
| `layer` | ✓ | section (`governance`/`enabling`/`web`/`pipe`) | enum above; `working-web`→`web`, `working-pipe`→`pipe` |
| `icon` | ✓ | `ic` | one emoji |
| `description` | ✓ | `ds` | injected as innerHTML — write `&amp;`/`<b>` exactly as it should render |
| `display_name` | | `nm` (+ `repo`) | only when the label differs from the repo dir name |
| `port` | | `port` | enabling cards render it; `:NNNN` form |
| `chips` | | `chips` | enabling cards |
| `tag` | | `tag` | working cards; `[relation, target]` |

**Keep it current:** update `.fleet.toml` in the same PR as any material change (layer, port, role, one-line description, exposed services). A repo listed in the residual's `_adopted` registry whose `.fleet.toml` goes missing fails the drift test. After editing any `.fleet.toml`, run `py .claude/skills/system-map/build_data.py` to regenerate `fleet.data.js`.

### Local specs — kept out of git 🔒

The committed `DATA.compute` (and the committed `system-map.png`) show **placeholder** hardware specs. Real GPU/CPU/RAM are personal detail, so they live in **`system-map.local.js`** (gitignored via `*.local.*`). `system-map.html` loads it with a plain `<script>` tag — works under `file://`, no CORS — and merges `window.LOCAL` over the placeholders. Missing on a fresh checkout → harmless 404, placeholders stay.

```powershell
cp system-map.local.example.js system-map.local.js   # then edit in your specs
```

So a local render shows your real specs; anything pushed (PNG, HTML, the issue, Slack) shows placeholders.

### Render

Regenerate the data first if any `.fleet.toml` or the residual changed: `py .claude/skills/system-map/build_data.py`. Data is then inline, so **no web server is needed** (unlike a `fetch()`-based page) — render straight from `file://`:

```powershell
cd architecture
# measure first (DIMS logged to stderr), then screenshot at that size:
& "C:/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu `
  --enable-logging=stderr --v=0 --virtual-time-budget=8000 --window-size=400,300 `
  --screenshot=_m.png "file:///$($PWD.Path -replace '\\','/')/system-map.html"   # read "DIMS w h"
& "C:/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu `
  --hide-scrollbars --force-device-scale-factor=2 --window-size=<w>,<h> `
  --virtual-time-budget=8000 --screenshot=system-map.png "file:///$($PWD.Path -replace '\\','/')/system-map.html"
```

### Render gotchas (kept from the Mermaid exploration)

1. Measure the page's `scrollWidth/scrollHeight` (logged to console as `DIMS w h`), then size the screenshot window to it — no empty canvas, nothing clipped.
2. If a future variant `fetch()`es a sibling file, `file://` blocks it via CORS — serve over `http://` then. Inline data (as here) avoids it.
3. Verify legibility by cropping the rendered PNG to full-res regions (e.g. with PIL) and inspecting — the on-screen thumbnail downscales too far to trust.

> History: an earlier dark, vertical **Mermaid** auto-layout lost too much information and was retired in favour of this doc-first HTML/CSS approach.

## The config & convention map: `config-map.html` → `config-map.png`

A second weekly map, the **descriptive** companion to `/context-audit` (which is prescriptive — it flags drift). Where the system map answers *"what runs in the fleet?"*, the config map answers *"what configuration does each coding agent get, and what's universal vs repo-specific?"* — the per-agent capability matrix (Claude Code · Codex · Pi · Copilot · Antigravity), the skill inventory (universal / fleet-orchestration / repo-specific), the hook inventory (blocking vs nudge, the Claude-full vs Codex-subset split), and the convention surface (`global-CLAUDE.md`, the design system, the single-home-by-altitude rule). Built in #207.

**Derived, not declared.** Unlike the system map (per-repo `.fleet.toml` cards aggregated), config is centralized in `fleet-config`, so [`.claude/skills/config-map/build_data.py`](../.claude/skills/config-map/build_data.py) *introspects* it into the generated `config.data.js` (`window.CONFIG = { …strict JSON… }`):

- the per-agent matrix wiring → parsed from `install.ps1`'s `$Items` link table + which 5 hooks Codex wires in `codex-hooks.json`;
- universal skills → `skills/*/SKILL.md`; fleet-orchestration skills → `.claude/skills/*/SKILL.md` (+ `run-weekly.bat` = the scheduled flag);
- hooks → `hooks/*.py` (purpose from the module docstring, blocking from a `block(`/`exit(2)` call) + wiring from `settings.template.json`;
- repo-specific skills → a git sweep of each fleet repo's committed `.claude/skills` (same committed-state read the system map uses for `.fleet.toml`);
- convention coverage → committed `CLAUDE.md` / `.fleet.toml` per repo.

The thin hand-maintained input is [`config.residual.json`](config.residual.json) — only what can't be derived: the agent columns, the matrix row structure (non-derivable cells carry an `annot`), the universal-skill scope set, the project-wired hooks, and the conventions prose. `tests/run_acceptance.py` asserts `config.data.js` is exactly what `build_data.py` regenerates, so it can't go stale. **By construction the dataset holds only wiring/structure — never a secret** (`build_data.py` reads the committed `settings.template.json`, never the live `~/.claude/settings.json`).

Regenerate + render the same way as the system map:

```powershell
py .claude/skills/config-map/build_data.py     # introspect → config.data.js
py .claude/skills/config-map/render.py          # config-map.html → config-map.png (2×)
```
