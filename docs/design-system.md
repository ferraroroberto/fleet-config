# The fleet design system — rationale & references

**Written:** 2026-06-21 · **Issue:** [#178](https://github.com/ferraroroberto/fleet-config/issues/178) · **Shipped in:** [#181](https://github.com/ferraroroberto/fleet-config/pull/181) (the system), [#184](https://github.com/ferraroroberto/fleet-config/pull/184) (shadcn rule), and the PR that adds this doc. · **v2:** [#278](https://github.com/ferraroroberto/fleet-config/issues/278) — component-level contracts + the polish-round decisions (see Round 3 below). · **Round 4:** [#342](https://github.com/ferraroroberto/fleet-config/issues/342) — mobile feedback & interaction contracts (see Round 4 below).

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
| **Format + schema** | Google Stitch `design.md` | YAML frontmatter, token sections in canonical order (colors → typography → rounded → spacing → components), `{path.to.token}` cross-references, and the 8 fixed prose `##` sections (Overview, Colors, Typography, Layout, Elevation & Depth, Shapes, Components, Do's & Don'ts) — v2 extends this with a 9th **Motion** section for the reduce-motion contract; Round 4 adds three more (**Async data & feedback**, **Touch targets**, **Charts**) for the mobile interaction contracts. |
| **Light/dark convention** | Vercel Geist | Two sibling files, identical token names, different values, each cross-linking the other in its `description`; `*-p3` `oklch()` wide-gamut twins per accent. |
| **Palette + look** | GitHub mobile app | True-black-capable canvas, elevated cards, one blue accent, large radii, colored rounded-square icon tiles, the floating bottom-tab pill. |
| **Component structure + a11y** | shadcn/ui | The markup shape, ARIA roles/states, and keyboard behavior of each control — copied by hand (these apps are vanilla, not React) and skinned with our tokens. |

## The journey (four rounds)

**Round 1 — build the system ([#178](https://github.com/ferraroroberto/fleet-config/issues/178) → [#181](https://github.com/ferraroroberto/fleet-config/pull/181)).** Authored `design.md` + `design.dark.md` on the Google-schema/Vercel-convention/GitHub-palette synthesis above; wired the `install.ps1` junction; added the `global-CLAUDE.md` pointer; built the `/design-sync` drift detector with `design-drift` as a first-class audit bucket. [#184](https://github.com/ferraroroberto/fleet-config/pull/184) followed, adding the "model components on shadcn" rule.

**Round 2 — audit + this doc ([#181](https://github.com/ferraroroberto/fleet-config/pull/181) follow-up).** A review of the shipped spec found three things in the shadcn section: (1) a **broken reference URL** — `ui.shadcn.com/docs/components/base` 404s; there is no `/components/base` page, Base UI is a *primitive backend* selected at project creation, and components live at `/docs/components/<name>` (fixed → `/docs/components`); (2) imprecise terminology — "shadcn base-UI variant" reads as a component variant when Base UI is a backend (tightened to "shadcn Switch", "model on shadcn"); (3) the substantive gap — shadcn components are **React + TSX + Tailwind**, but the fleet apps are **vanilla HTML/CSS/JS**, so you cannot "derive from" shadcn as code; you borrow its *markup + ARIA + interaction semantics* and re-author them by hand (now stated explicitly in the spec). The audit also confirmed `/design-sync` **does** fully consume `design.dark.md` (it reads, maps, drift-checks, and reports the dark theme symmetrically with light) — so a `/design-sync` run does check dark. This doc is the third output of that round.

**Round 3 — v2: component contracts + the polish-round decisions ([#278](https://github.com/ferraroroberto/fleet-config/issues/278)).** v1 was **token-level** (colors, type, radii, spacing) plus the navigation contract, but had no written contract *below* the token layer — nothing said how a card header, a disclosure row, an editor modal, an empty-state, or an icon tile is composed. The home-automation polish round (home-automation#358, closed) drove that app to canon on every token dimension and, in doing so, produced concrete, shipped, on-device-validated component patterns. v2 promotes them from tribal knowledge (they lived only in issue comments) into the spec: a **Component contracts** subsection (`card` header, `modal`, `empty-state`, `icon-tile` — each modelled on the existing Navigation contract's prose), the four decisions the round settled recorded normatively (**switch on = green `success`** not the blue accent; **desktop content centered at `max-width: 772px`**; **one tokenized `:focus-visible` ring** app-wide; **`prefers-reduced-motion` honored** via a new Motion section), and an **`icons.size` token group** (`inline 16 / title 18 / feature 24 / nav-tab 24` — the round-3 original; `nav-tab` was later revised to **20px** on phone-validated geometry, home-automation#118, and 16 / 18 / 20 / 24 is the live step set) that closes the one census dimension never unified (icon sizing was still 16 distinct values). This is the foundational Phase-4 change: the component-vendoring work (project-scaffolding) implements these contracts, `/design-sync` v2 ([#277](https://github.com/ferraroroberto/fleet-config/issues/277)) checks conformance to them, and the fleet rollout migrates apps to them.

**Round 4 — mobile feedback & interaction contracts ([home-automation#409](https://github.com/ferraroroberto/home-automation/issues/409) → [home-automation PR #427](https://github.com/ferraroroberto/home-automation/pull/427), promoted in [#342](https://github.com/ferraroroberto/fleet-config/issues/342)).** The home-automation mobile-UX refinement was validated incrementally on the installed iPhone, then locked by 386 backend tests and 184 Chromium/WebKit e2e cases — and its lessons were broader than visual token alignment, so Round 4 promotes five behavioral contracts to canon. (1) An explicit **async lifecycle** — `loading / ready / empty / stale / error` — where a failed background refresh *preserves* last-known content, labels it `Last updated … · live data unavailable`, and disables freshness-sensitive actions: per [Apple HIG · Alerts](https://developer.apple.com/design/human-interface-guidelines/alerts), cached data is labelled nonintrusively, never escalated to an alert. (2) **Feedback altitude** — passive/background status renders inline beside the affected surface; global toasts are reserved for user-initiated commands ([Apple HIG · Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback): give status *near the item it describes*). (3) **Effective ≥44×44px touch targets** app-wide — [Apple HIG · Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)'s 44pt hit region, with [WCAG 2.2 · Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) legitimizing invisible active-area expansion for isolated compact controls; the on-device lesson the guidelines don't spell out is that **adjacent clusters need real geometry**, because two invisibly-expanded rectangles that overlap route taps to the wrong control. (4) **Responsive, colour-independent charts** — a viewport-aware tick budget recomputed on resize ([Chart.js cartesian tick options](https://www.chartjs.org/docs/latest/api/): `maxTicksLimit` / `autoSkip` / `maxRotation: 0`), no canvas-driven page overflow (holding [WCAG 2.1 · Reflow](https://www.w3.org/WAI/WCAG21/Understanding/reflow)'s 320px single-scroll-direction baseline), and a non-colour second channel — dash + point style + fill — per colour-distinguished series ([WCAG 2.2 · Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color)). (5) The **dense collection** composition — saved automation items as flat summary rows opening staged native-`<dialog>` editors, Save as the only persistence boundary, Delete a labelled in-body danger action — a presentation-only refactor that changed no API payloads. Rejected along the way: a bar-chart history experiment (read as clutter on-device, reverted) and treating desktop Playwright/WebKit projections as authoritative for the installed-PWA shell (they are not; on-device confirmation stays mandatory). The round also settled the **verification split** now written into the spec, the lint, and `/design-sync`: static lint proves *authored* facts (tokens, markup shape, component CSS — including recognizing form-less JS-managed editor dialogs, plus the new `hit-target`, `chart-tick-budget`, `chart-noncolor-cue`, and `async-lifecycle` checks), while effective hit rectangles, overlap, chart-label collision, and page overflow are *rendered* facts that only a browser harness can prove — a clean static scan is not UX conformance, and the canonical rendered-geometry helper is tracked in [project-scaffolding#157](https://github.com/ferraroroberto/project-scaffolding/issues/157).

## The two borrowed vocabularies — why shadcn, why Lucide

`design.md`'s **Base UI** and **Icons** sections state the rule; the reasoning behind both lives here (they are one decision, not two: the icon set was chosen *because* it is the component reference's native set).

**Why components are modelled on shadcn/ui.** [shadcn/ui](https://ui.shadcn.com/docs/components) is the reference for component **structure, markup, accessibility, and interaction** because its primitives encode the WAI-ARIA patterns correctly — on whichever headless backend a shadcn project picks, [Radix UI](https://www.radix-ui.com/primitives) or [Base UI](https://base-ui.com/). But these apps are **vanilla HTML/CSS/JS, not React**, so shadcn is never installed: you copy its *markup shape and interaction semantics* (element structure, ARIA roles/states, keyboard behavior) by hand and skin them with the fleet tokens. That is the Round-2 correction — you cannot "derive from" shadcn as code, only borrow its semantics — and it is why the rule is phrased as *model on*, never *use*. The raw headless libraries are often the more direct read when hand-rolling a control (see the Component libraries table below); the same caveat applies to them.

**Why Lucide is the one icon set.** [Lucide](https://lucide.dev) is shadcn/ui's default icon set, and the spec already endorses shadcn as the structural reference for every interactive component — so the glyphs and the component shapes come from the same vocabulary rather than a new, unrelated dependency. It ships ~1,600 icons under the permissive **ISC** license on a **24×24** grid with a **2px** outline stroke — the calm, GitHub-mobile line-icon style this identity is modelled on — as plain **SVG**, so it drops into vanilla HTML/CSS/JS PWAs with no React and no build step. Unlike an icon font it carries no web-font payload, consistent with the system-font, instant-first-paint typography choice. **Rejected:** [Radix Icons](https://www.radix-ui.com/icons) (the set behind the Radix UI backend above) — ~300 glyphs at 15×15 is too small a library to dress a multi-app fleet.

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
| Headless primitives — [Radix UI](https://www.radix-ui.com/primitives), [Base UI](https://base-ui.com/), [Headless UI](https://headlessui.com/) | Unstyled, behaviour-only component libraries: the ARIA roles/states + keyboard interaction with no visual skin (Radix & Base UI are shadcn's two backends; Headless UI is Tailwind Labs' separate one). | A *more direct* reference than shadcn's pages when hand-rolling vanilla controls — you want the raw accessibility/keyboard contract, not the React/Tailwind wrapper around it. Same caveat as shadcn: take the *semantics*, not the code (all are React/Vue). |

### Platform & accessibility guidelines — *the normative sources behind the mobile interaction contracts (Round 4)*

| Resource | What it is | Why it mattered |
|---|---|---|
| [Apple HIG · Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) | Apple's accessibility guidance, including the 44×44pt minimum hit region. | The floor behind the fleet-wide `components.hit-target.min` (44px) effective-target contract — not just the nav tabs. |
| [Apple HIG · Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback) | How and *where* an app should communicate status. | The feedback-altitude rule: passive status belongs inline near the item it describes; only user-initiated commands earn a global toast. |
| [Apple HIG · Alerts](https://developer.apple.com/design/human-interface-guidelines/alerts) | When (not) to interrupt with an alert. | Why stale data is *labelled* (`Last updated … · live data unavailable`), never escalated to a disruptive alert — cached/placeholder content gets a nonintrusive label. |
| [WCAG 2.2 · Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) | Success criterion for pointer-target size. | Confirms invisible active-area expansion (the `.hit-target` `::before` pattern) is a valid way to meet the floor — and why overlap between expanded areas breaks it. |
| [WCAG 2.2 · Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color) | Colour must never be the only visual means of conveying information. | The chart contract's non-colour second channel: border dash + point style + fill per series, legend rendering point styles. |
| [WCAG 2.1 · Reflow](https://www.w3.org/WAI/WCAG21/Understanding/reflow) | Content must reflow to 320 CSS px with single-direction scrolling. | The 320px baseline of the rendered viewport matrix (320/390/430/772) and the "canvas never drives page overflow" chart rule. |
| [Chart.js · cartesian tick options](https://www.chartjs.org/docs/latest/api/) | The `maxTicksLimit` / `autoSkip` / `autoSkipPadding` / `maxRotation` axis-tick API. | The mechanics of the viewport-aware tick budget (~4 phone / ~8 desktop, recomputed in `onResize`, zero label rotation). |

### Design-inspiration galleries — *what good looks like, browse before you build*

| Resource | What it is | Why it mattered |
|---|---|---|
| [Mobbin](https://mobbin.com/) | Huge searchable library of real, shipped mobile + web app screenshots and full user flows. | Inspiration source for the GitHub-mobile-app look and real-world nav patterns (login-walled). |
| [Neuform · featured](https://neuform.ai/community/featured) | Gallery of AI-generated HTML/UI design templates, mobile-app-shaped. | Browse for layout/component ideas an agent could start from. |
| [Refero · styles](https://styles.refero.design/) | 2,000+ AI-readable design systems extracted from real products — each with colors, typography, spacing, components, **and a downloadable DESIGN.md**; plus Refero MCP for agents to search real screens. | Both an example library *and* a study-before-you-build tool; closest external analogue to what we built. |

### Tools — *extract / generate a design.md from a real product*

Paste a URL (or point a browser extension at a live page) and get a DESIGN.md back — colors, typography, spacing, CSS variables, tokens — ready for an AI agent to match that look. This is the concrete realisation of the "study real screens before building" horizon idea below: rather than eyeballing a reference site, you extract its design.md and diff against ours. Note: [designmd.app](https://designmd.app/what-is-design-md) (in *Format & spec* above) is the ecosystem hub but does **not** itself do URL extraction — these third-party tools do.

| Resource | What it is | Why it mattered |
|---|---|---|
| [Design Extractor](https://www.design-extractor.com/) | Paste a URL → DESIGN.md + Tailwind v4 + design tokens for your AI agent. | The cleanest single-purpose URL→design.md extractor. |
| [Context.dev · design.md generator](https://www.context.dev/free-tools/design-md-generator) | Enter a domain → design.md with colors, fonts, components, and visual rules. | Domain-level extraction with a components pass. |
| [DESIGN.md Generator (Chrome extension)](https://chromewebstore.google.com/detail/designmd-generator/jbgahjopiacfecejenojopjpljocdigb) | Click the extension on any live page → DESIGN.md or SKILL.md for Claude Code / Cursor / Codex. | Extract from a page you're actually browsing, including login-walled ones a URL fetcher can't reach. |
| [MYDESIGN.MD](https://www.mydesignmd.com/) | Public URL → DESIGN.md + CSS variables + JSON tokens + Tailwind config. | Broadest output set (tokens + CSS vars + Tailwind in one pass). |

## Ideas not yet incorporated (open horizon)

Things the references take seriously that our spec / workflow does **not** (yet) — recorded so a future pass can decide whether to adopt them, not as TODOs:

- **WCAG contrast as a first-class concern.** Google's whole pitch is that an agent can *"validate their choices against WCAG accessibility rules,"* and the official CLI's `lint` checks WCAG contrast. Our spec lists colors but never declares a target contrast or asserts that fg/canvas pairings meet AA. A short contrast note (or a lint pass) would close this.
- **The official `@google/design.md` CLI** (`lint`, `diff`, `export`, `spec`). We hand-author the spec and built our own `/design-sync` drift detector for *apps*. The official `lint` (structure + broken **token** references — note: it validates `{path.to.token}` refs, *not* external doc URLs, so it would **not** have caught our 404) and `diff` (regressions between versions) are adjacent tooling we could run on the spec itself.
- **`export` to Tailwind / W3C Design Tokens.** An interop path to other token formats. Low relevance while our apps use vanilla CSS custom properties, but recorded as an option if a React app ever joins the fleet.
- **Motion / animation tokens.** ~~Neither Stitch's 8 canonical sections nor our spec cover transitions, durations, or easing.~~ **Partly addressed in v2 ([#278](https://github.com/ferraroroberto/fleet-config/issues/278)):** a **Motion** prose section now covers the `prefers-reduced-motion` contract and the functional-not-decorative rule. Still open: named `motion` *tokens* (durations, easing curves) — v2 added the section and the reduce-motion behavior, not a token group, since the fleet's transitions are still simple enough not to warrant one.
- **"Study real screens before building"** (Mobbin / Refero MCP). The idea that an agent searches real product screens before it builds. We rely on the static spec and do inspiration manually/ad-hoc; folding a screen-study step into the design workflow is an option. The tooling for this now exists off-the-shelf — the *Tools — extract/generate a design.md* references above turn any reference URL into a design.md you can diff against ours, so the open part is the *workflow* (when/where to invoke one), not the capability.
- **Versioning discipline.** We stamp `version: alpha` but don't bump or `diff` across changes. If the identity starts evolving, the CLI `diff` exists for exactly that.
