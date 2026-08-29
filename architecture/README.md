# architecture/ — fleet system map

Self-portrait of the whole `E:/automation` fleet. Built in #94; made **self-describing** in #148 (each repo declares its own card).

**Source of truth is distributed:** each repo declares its own map card in a root **`.fleet.toml`**, and **`.claude/skills/system-map/build_data.py`** aggregates those into the generated `fleet.data.js`. The layered narrative [`ARCHITECTURE.md`](ARCHITECTURE.md) (compute → connectivity → enabling tools → working apps → governance) is the human-readable companion the data must agree with. So a new repo appears on the map automatically, correct, with zero central editing — and the picture can't go stale.

## The visual: `system-map.html` → `system-map.png`

A **light-theme, horizontal, Janis-style** infographic — grouped zone panels, every project a card with a one-line description. Built as **hand-authored HTML/CSS**, chosen over Mermaid so each block carries real text and the layout is fully controlled.

**Data flow:** `fleet.data.js` (`window.FLEET = { …strict JSON… }`: governance / access / edge / compute / enabling / web / pipe / external + principles) is the file `system-map.html` renders — but it is **generated**, never hand-edited. `build_data.py` assembles it from two inputs:

- each repo's root **`.fleet.toml`** — that repo's self-declared card, authoritative when present;
- **`fleet.residual.json`** — hand-maintained: the non-repo structure (access/edge/compute/external/principles), a fallback card per repo in curated order, and an `_adopted` registry listing repos that MUST self-describe.

`tests/run_acceptance.py` asserts the fleet, the generated data, and `ARCHITECTURE.md` never drift apart, plus — hard — that fleet-config's own card matches its own `.fleet.toml`. The fleet-wide `.fleet.toml` assertions (`fleet.data.js` is exactly what `build_data.py` regenerates; no `_adopted` repo has lost its declaration; every declaration parses) still run every time and still name the drift, but report as **`SKIP` (advisory), not a failure** — their inputs are *sibling repos'* live checkouts, so a `.fleet.toml` commit in any other repo would otherwise make fleet-config's gate red and unshippable for a reason no commit here can fix (fleet-config#562). `/system-map` owns clearing them, weekly.

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

**Keep it current:** update `.fleet.toml` in the same PR as any material change (layer, port, role, one-line description, exposed services). A repo listed in the residual's `_adopted` registry whose `.fleet.toml` goes missing is reported by the drift test (advisory here, gating in `/system-map`). After editing any `.fleet.toml`, run `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py` to regenerate `fleet.data.js`.

### Optional per-repo `[vendored]` table (fleet-config#338)

An adopter of a `project-scaffolding`-sourced component — the `_vendored/` UI
library (`app/webapp/static/_vendored/<component>/`) or a machine-local
primitive like the tray lifecycle (`app/tray/tray_lifecycle.ps1` and kin) —
records that fact in its own `.fleet.toml`, one entry per component:

```toml
[vendored]
nav = { src = "app/webapp/static/_vendored/nav", sha = "<scaffold commit>", dest = "app/webapp/static/_vendored/nav" }
```

| Field | Meaning |
|---|---|
| `src`  | path inside `project-scaffolding` the component lives at (a directory for a UI component, a single file for a tray primitive) |
| `sha`  | the scaffold commit this adopter's copy was last vendored from |
| `dest` | path inside the adopter repo the component was copied to — usually identical to `src` |

**Written by the skill, not by hand.** `/propagate-vendored` (`skills/propagate-vendored/`)
writes the entry on first adoption and bumps `sha` on every successful
re-vendor; a human never hand-edits it. This is the same anti-staleness
contract as the rest of `.fleet.toml` ("keep it current in the same PR as the
change") — here "the change" *is* the propagation PR, so the invariant holds
by construction rather than by discipline. `skills/_lib/vendored_drift.py`
reads every adopter's `[vendored]` table across the fleet to answer "who's
behind" as a query instead of a periodic manual audit.

**Why `.fleet.toml`, not a separate `VENDORED.lock`:** the map-build machinery
(`.claude/skills/system-map/build_data.py`'s `card_from_toml`) already parses
`.fleet.toml` with `tomllib.loads` and only ever reads its own named keys via
`dict.get(...)` — confirmed empirically (`card_from_toml` on a `.fleet.toml`
carrying an extra `[vendored]` table returns the same card as one without it,
no `KeyError`, no drift-test failure). An unrecognized top-level table is
silently ignored, so adding `[vendored]` costs nothing on the map-build path.
One file per repo to read instead of two, and it's already the place every
repo declares its map card — a second propagation-status file would just be
one more thing to keep in sync with reality. Full decision writeup, including
what would have flipped the answer to `VENDORED.lock`, is in
`skills/propagate-vendored/README.md`.

**Its mirror image lives in the scaffold: the `[components]` catalog
(project-scaffolding#230).** `[vendored]` says what an adopter *copied*;
`project-scaffolding`'s own `.fleet.toml` carries a `[components]` table saying
what the scaffold *publishes* — `<manifest key> = { src = "<path in the scaffold>" }`,
one entry per vendor-verbatim component, and **only** that repo carries it.

```toml
[components]
nav       = { src = "app/webapp/static/_vendored/nav" }
no_window = { src = "src/no_window.py" }
```

Without it, `/propagate-vendored`'s adopter list — built from `[vendored]`
entries — cannot see a repo that carries a component and declared nothing, so a
wave re-vendors the declarers, reports success, and leaves the rest stale with
nobody told (`#228` reached one repo out of seven that way). With it,
`skills/_lib/vendored_drift.py` hashes every fleet repo's copy of a *known*
component path and reports `undeclared_carriers` by name, plus a `coverage`
block the skill must state out loud. A scaffold checkout with no `[components]`
table makes carrier detection report `catalog_known: false` — *could not look*,
never *nothing to find*.

Both sides of every hash comparison read **committed blobs**, not the working
tree: these checkouts store LF and check out CRLF, so a filesystem-side read
would differ from its own blob in every line of every text file and report
universal false drift.

### Optional per-repo `[cert]` table (fleet-config#418)

A repo that has triaged and disproved a `cert-drift` finding — `skills/_lib/cert_drift.py`
flagging it as tailnet-reachable and still self-signed-only, when it structurally
isn't (e.g. `tailscale cert` can't serve its loopback SANs) — declares that verdict
once, durably, instead of relying on prose the detector re-parses every sweep:

```toml
[cert]
not_applicable = true
reason         = "tailscale cert cannot serve this app's loopback SANs"
disproof       = "https://github.com/<owner>/<repo>/issues/151"
```

| Field | Meaning |
|---|---|
| `not_applicable` | must be `true` to opt out; any other value is ignored |
| `reason` | one line, folded into the `REASON=` output of `cert_drift.py detect` |
| `disproof` | link to the issue/comment where the finding was triaged |

`classify()` treats this as the highest-precedence signal — always `clean`,
before the three tailnet/self-signed/ts-cert signals are even consulted. This
is the only guard against `audit_issue.py` refiling a closed-as-not-planned
`cert-drift` issue: its dedup only queries *open* issues, so a closed verdict
is otherwise invisible to the next sweep. Same silent-if-unrecognized parsing
as `[vendored]` above — adding `[cert]` costs nothing on the map-build path.

### Optional per-repo `[worktree]` table (fleet-config#620)

`skills/_lib/worktree_claim.py` junctions a repo's `.venv` into every fresh
`/issue-start` worktree so a 24-repo fleet never recreates heavy venvs per
worktree. A repo whose own gate also needs a *different* gitignored,
untracked path — a vendored install, a model cache — declares it here so
that path gets junctioned too, instead of the worktree silently lacking it
and failing its own gate for a reason that looks like the issue being built,
not the isolation primitive:

```toml
[worktree]
extra_junctions   = ["vendor/comfyui"]
blank_config_keys = ["mirror.dir", "mirror.backup_dir"]
```

| Field | Meaning |
|---|---|
| `extra_junctions` | list of paths, relative to the repo root, to junction into a worktree alongside `.venv` |
| `blank_config_keys` | dotted keys in a copied `config/*.json` that point at real, machine-bound state (a synced mirror/backup folder, another repo's database) — blanked to `""`/`[]` in the worktree's copy instead of carrying the primary's real path across (fleet-config#713) |

`.venv` is always junctioned first and remains the *only* target when this
table (or `.fleet.toml` itself) is absent — an undeclared repo behaves
exactly as before. A declared path that doesn't exist in the primary is
skipped, never a setup failure. Same silent-if-unrecognized parsing as
`[vendored]`/`[cert]` above — adding `[worktree]` costs nothing on the
map-build path.

A repo that declares no `blank_config_keys` (no `[worktree]` table, or the
table without that key) gets a conservative built-in default instead of no
protection at all: any string value in the copied config that looks
machine-bound — an `{onedrive}`-style template placeholder, or an absolute
Windows path — is blanked, list entries filtered the same way. An explicit
empty list (`blank_config_keys = []`) opts a repo out of that default
heuristic entirely.

### Local specs — kept out of git 🔒

The committed `DATA.compute` (and the committed `system-map.png`) show **placeholder** hardware specs. Real GPU/CPU/RAM are personal detail, so they live in **`system-map.local.js`** (gitignored via `*.local.*`). `system-map.html` loads it with a plain `<script>` tag — works under `file://`, no CORS — and merges `window.LOCAL` over the placeholders. Missing on a fresh checkout → harmless 404, placeholders stay.

```powershell
cp system-map.local.example.js system-map.local.js   # then edit in your specs
```

So a local render shows your real specs; anything pushed (PNG, HTML, the issue, Slack) shows placeholders.

### Render

Regenerate the data first if any `.fleet.toml` or the residual changed: `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py`. Data is then inline, so **no web server is needed** (unlike a `fetch()`-based page) — render straight from `file://`:

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

The thin hand-maintained input is [`config.residual.json`](config.residual.json) — only what can't be derived: the agent columns, the matrix row structure (non-derivable cells carry an `annot`), the universal-skill scope set, the project-wired hooks, and the conventions prose. `tests/run_acceptance.py` checks `config.data.js` is exactly what `build_data.py` regenerates — advisory (a `SKIP` line, not a failure), because the sweep reads sibling repos; `/config-map` owns clearing it, weekly. **By construction the dataset holds only wiring/structure — never a secret** (`build_data.py` reads the committed `settings.template.json`, never the live `~/.claude/settings.json`).

Regenerate + render the same way as the system map:

```powershell
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py     # introspect → config.data.js
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/render.py          # config-map.html → config-map.png (2×)
```
