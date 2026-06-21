# The fleet design system — rationale & references

**Written:** 2026-06-21 · **Issue:** [#178](https://github.com/ferraroroberto/fleet-config/issues/178) · **Shipped in:** [#181](https://github.com/ferraroroberto/fleet-config/pull/181) (the system), [#184](https://github.com/ferraroroberto/fleet-config/pull/184) (shadcn rule), and the PR that adds this doc.

This is the *educational* companion to `design.md` / `design.dark.md`. Those two files are **instructions** — terse, machine-readable, telling an agent exactly what to build. This file is the **why**: the problem the design system solves, the prior art we studied, the synthesis we landed on, and the ideas we looked at but did not (yet) adopt. If you come back to this in 30 days and need to remember *what we were doing and which references to re-open*, start here, then read `design.md`.

## TL;DR (30-second recap)

We adopted Google's **design.md** format (design-as-Markdown: machine-readable YAML tokens + human-readable prose rationale, one file) as the fleet's single source of visual + interaction truth for FastAPI + static-PWA web apps. We used **Vercel Geist** for the two-file light/dark convention, the **GitHub mobile app** for the palette/look, and **shadcn/ui** as the reference for component structure + accessibility. The spec is junctioned into `~/.claude/` like the global CLAUDE.md, every session is told to consult it for web-app UI work, and a `/design-sync` skill measures per-app drift from it. This doc records the references behind each of those choices.

## Why this exists

The problem was **drift**. Each web app (home-automation, app-launcher, photo-ocr, voice-transcriber, …) had grown its own ad-hoc palette, its own light/dark values, and — worst — its own *copy-pasted* navigation. home-automation's `tabs.js` literally said *"Mirrors app-launcher's nav.tabs pattern"*: the floating bottom-tab pill was re-implemented per repo, slightly differently each time. Every new app re-invented both the look and the interaction. There was no shared contract for either.

The fix is a **single fleet-wide design system** so every app *looks* and *interacts* the same. The non-obvious decision was the *format*: rather than a JSON token file or a Figma library, we adopted **design.md** — design expressed as Markdown. The value of that choice (per Google Labs, who created the format):

- **LLM-native.** Markdown is the format coding agents understand with the highest fidelity. The spec is *for* the agent that writes the CSS.
- **Captures intent, not just values.** JSON tokens say *what* a color is; design.md's prose says *what it is for* ("use the accent only for interactive emphasis", "status colors signal state, never decoration"). Google's framing: *"Instead of guessing intent, AI agents can know exactly what a color is for, and can validate their choices against WCAG accessibility rules."*
- **No tooling, framework-agnostic.** Plain text, versioned in Git, works immediately, independent of React/Vue/vanilla.
- **A semantic layer.** It encodes rules ("one accent does all emphasis", the navigation contract) that a flat token dump cannot.

That last point is why the **Navigation & interaction** section is the load-bearing part of our spec — it makes two apps *feel* identical, not just match colors.

## What we built

Pointers, not a re-description (the files are the truth):

- **`design.md`** (light) + **`design.dark.md`** (dark) at the repo root — same token names, different values, cross-linked in each `description` (the Vercel convention).
- **Junction** — `install.ps1` symlinks both into `~/.claude/design.md` / `design.dark.md`, so they are live in every session exactly like `global-CLAUDE.md`.
- **Global pointer** — `global-CLAUDE.md` tells every session to consult them for any web-app UI work (Streamlit POC spikes exempt).
- **`/design-sync` skill** (`skills/design-sync/`) — reads *both* spec files, maps their tokens onto a target app's CSS custom properties (light **and** dark), reports drift, and files one deduped `design-drift` issue per repo so `/cleanup-fleet design-drift` can fix a whole bucket at once.
- **shadcn rule** — components are modelled on their shadcn equivalents for structure + ARIA, then skinned with fleet tokens (added in #184).
- **Vendored components** — the actual nav HTML/CSS/JS lives in `project-scaffolding` and is copied verbatim per app (same model as `single_instance.py` / `tray_lifecycle.ps1`), so the spec describes the contract and the scaffold ships the implementation.

## The synthesis — what we borrowed from whom

We did not invent a format; we synthesized three prior arts plus a look:

| Ingredient | Source | What we took |
|---|---|---|
| **Format + schema** | Google Stitch `design.md` | YAML frontmatter, token sections in canonical order (colors → typography → rounded → spacing → components), `{path.to.token}` cross-references, and the 8 fixed prose `##` sections (Overview, Colors, Typography, Layout, Elevation & Depth, Shapes, Components, Do's & Don'ts). |
| **Light/dark convention** | Vercel Geist | Two sibling files, identical token names, different values, each cross-linking the other in its `description`; `*-p3` `oklch()` wide-gamut twins per accent. |
| **Palette + look** | GitHub mobile app | True-black-capable canvas, elevated cards, one blue accent, large radii, colored rounded-square icon tiles, the floating bottom-tab pill. |
| **Component structure + a11y** | shadcn/ui | The markup shape, ARIA roles/states, and keyboard behavior of each control — copied by hand (these apps are vanilla, not React) and skinned with our tokens. |

## The journey (two rounds)

**Round 1 — build the system ([#178](https://github.com/ferraroroberto/fleet-config/issues/178) → [#181](https://github.com/ferraroroberto/fleet-config/pull/181)).** Authored `design.md` + `design.dark.md` on the Google-schema/Vercel-convention/GitHub-palette synthesis above; wired the `install.ps1` junction; added the `global-CLAUDE.md` pointer; built the `/design-sync` drift detector with `design-drift` as a first-class audit bucket. [#184](https://github.com/ferraroroberto/fleet-config/pull/184) followed, adding the "model components on shadcn" rule.

**Round 2 — audit + this doc (this PR).** A review of the shipped spec found three things in the shadcn section: (1) a **broken reference URL** — `ui.shadcn.com/docs/components/base` 404s; there is no `/components/base` page, Base UI is a *primitive backend* selected at project creation, and components live at `/docs/components/<name>` (fixed → `/docs/components`); (2) imprecise terminology — "shadcn base-UI variant" reads as a component variant when Base UI is a backend (tightened to "shadcn Switch", "model on shadcn"); (3) the substantive gap — shadcn components are **React + TSX + Tailwind**, but the fleet apps are **vanilla HTML/CSS/JS**, so you cannot "derive from" shadcn as code; you borrow its *markup + ARIA + interaction semantics* and re-author them by hand (now stated explicitly in the spec). The audit also confirmed `/design-sync` **does** fully consume `design.dark.md` (it reads, maps, drift-checks, and reports the dark theme symmetrically with light) — so a `/design-sync` run does check dark. This doc is the third output of that round.

## References, by type

Grouped by *what kind of resource it is*, so when you re-open one you know what to expect — a spec to follow, an example to study, a library to pull from, or inspiration to browse.

### Format & spec — *the instructions: what design.md is and how to write one*

| Resource | What it is | Why it mattered |
|---|---|---|
| [Stitch · design.md overview](https://stitch.withgoogle.com/docs/design-md/overview) | Google's official docs for the design.md format. | The canonical schema + canonical section order we follow. |
| [Google blog · introducing design.md](https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/) | The announcement + the "why". | The value framing — intent over values, WCAG validation, cross-tool portability. |
| [github.com/google-labs-code/design.md](https://github.com/google-labs-code/design.md) | The open-sourced format + the `@google/design.md` CLI (`lint`, `diff`, `export`, `spec`). | The reference implementation; the schema is here, and the lint/diff tooling we have *not* yet adopted (see horizon below). |
| [designmd.app · what is design.md](https://designmd.app/what-is-design-md) | Third-party explainer + ecosystem hub; 400+ ready-made design.md files; agent compatibility (Claude Code, Cursor, Kiro, Windsurf). | The clearest plain-English explanation of the concept and tooling around it. |

### Example design.md files — *real ones in the wild, to study*

| Resource | What it is | Why it mattered |
|---|---|---|
| [Vercel · design.md](https://vercel.com/design.md) + [design.dark.md](https://vercel.com/design.dark.md) | Geist as two cross-linked light/dark files with `*-p3` twins. | The exact two-file convention we copied. |
| [Resend · design.md](https://resend.com/design.md) | design.md used as a thin **navigation hub** that points to modular Agent Skill repos (brand / design system / marketing) rather than embedding tokens. | The opposite end of the spectrum from a monolith — informs our hybrid (self-contained spec + separate `/design-sync` skill + vendored components in `project-scaffolding`). |

### Component libraries — *the building blocks to model controls on*

| Resource | What it is | Why it mattered |
|---|---|---|
| [shadcn/ui · components](https://ui.shadcn.com/docs/components) | Copy-in React/Tailwind components over Radix **or** Base UI primitives. | The reference for component structure + ARIA; the per-component page is what you mirror by hand for each vanilla control (Switch, Select, Input, Button, Dialog, Tabs, …). |

### Design-inspiration galleries — *what good looks like, browse before you build*

| Resource | What it is | Why it mattered |
|---|---|---|
| [Mobbin](https://mobbin.com/) | Huge searchable library of real, shipped mobile + web app screenshots and full user flows. | Inspiration source for the GitHub-mobile-app look and real-world nav patterns (login-walled). |
| [Neuform · featured](https://neuform.ai/community/featured) | Gallery of AI-generated HTML/UI design templates, mobile-app-shaped. | Browse for layout/component ideas an agent could start from. |
| [Refero · styles](https://styles.refero.design/) | 2,000+ AI-readable design systems extracted from real products — each with colors, typography, spacing, components, **and a downloadable DESIGN.md**; plus Refero MCP for agents to search real screens. | Both an example library *and* a study-before-you-build tool; closest external analogue to what we built. |

## Ideas not yet incorporated (open horizon)

Things the references take seriously that our spec / workflow does **not** (yet) — recorded so a future pass can decide whether to adopt them, not as TODOs:

- **WCAG contrast as a first-class concern.** Google's whole pitch is that an agent can *"validate their choices against WCAG accessibility rules,"* and the official CLI's `lint` checks WCAG contrast. Our spec lists colors but never declares a target contrast or asserts that fg/canvas pairings meet AA. A short contrast note (or a lint pass) would close this.
- **The official `@google/design.md` CLI** (`lint`, `diff`, `export`, `spec`). We hand-author the spec and built our own `/design-sync` drift detector for *apps*. The official `lint` (structure + broken **token** references — note: it validates `{path.to.token}` refs, *not* external doc URLs, so it would **not** have caught our 404) and `diff` (regressions between versions) are adjacent tooling we could run on the spec itself.
- **`export` to Tailwind / W3C Design Tokens.** An interop path to other token formats. Low relevance while our apps use vanilla CSS custom properties, but recorded as an option if a React app ever joins the fleet.
- **Motion / animation tokens.** Neither Stitch's 8 canonical sections nor our spec cover transitions, durations, or easing — yet our nav already animates. A `motion` token group + a Motion prose section is the most natural next addition.
- **"Study real screens before building"** (Mobbin / Refero MCP). The idea that an agent searches real product screens before it builds. We rely on the static spec and do inspiration manually/ad-hoc; folding a screen-study step into the design workflow is an option.
- **Versioning discipline.** We stamp `version: alpha` but don't bump or `diff` across changes. If the identity starts evolving, the CLI `diff` exists for exactly that.
