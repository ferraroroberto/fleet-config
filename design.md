---
name: Fleet
version: alpha
description: >
  The shared visual identity, navigation, and interaction language for the
  automation fleet's web apps (FastAPI + static PWA — installed on the phone,
  also opened on the PC). This is the Light theme. The Dark theme uses the same
  token names with different values and lives at `~/.claude/design.dark.md`.
  Modelled on the GitHub mobile app: a quiet canvas, elevated cards, one blue
  accent, large radii, and a single floating bottom-tab pill. Streamlit POC
  spikes are exempt.
colors:
  canvas:        "#ffffff"   # page background
  canvas-subtle: "#f6f8fa"   # inset / off-card surfaces
  card:          "#ffffff"   # elevated card surface
  border:        "#d1d9e0"   # default hairline
  border-muted:  "#d8dee4"   # quieter hairline (dividers inside a card)
  fg:            "#1f2328"   # primary text
  fg-muted:      "#656d76"   # secondary text
  accent:        "#0969da"   # links, primary CTA
  accent-fg:     "#ffffff"   # text/icon on an accent fill
  success:       "#1a7f37"
  danger:        "#cf222e"
  attention:     "#9a6700"
  # rounded-square icon-tile fills (Home-screen tiles) — the only saturated surfaces
  tile-green:    "#1f883d"
  tile-blue:     "#0969da"
  tile-purple:   "#8250df"
  tile-orange:   "#bc4c00"
  tile-yellow:   "#bf8700"
  # wide-gamut (P3) twins — identical token family in design.dark.md (Vercel convention).
  # A P3 display picks these up via `@media (color-gamut: p3)`; sRGB displays use the hex above.
  accent-p3:     "oklch(0.52 0.18 256)"
  success-p3:    "oklch(0.55 0.14 150)"
  danger-p3:     "oklch(0.55 0.20 25)"
  attention-p3:  "oklch(0.55 0.11 80)"
  tile-green-p3:  "oklch(0.57 0.15 150)"
  tile-blue-p3:   "oklch(0.52 0.18 256)"
  tile-purple-p3: "oklch(0.55 0.22 295)"
  tile-orange-p3: "oklch(0.56 0.16 50)"
  tile-yellow-p3: "oklch(0.66 0.13 85)"
typography:
  heading-xl: { fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", fontSize: 2rem,    fontWeight: 700, lineHeight: 1.15, letterSpacing: "-0.02em" }
  heading-lg: { fontFamily: "system-ui, sans-serif", fontSize: 1.5rem,  fontWeight: 700, lineHeight: 1.2 }
  body:       { fontFamily: "system-ui, sans-serif", fontSize: 1rem,    fontWeight: 400, lineHeight: 1.5 }
  label:      { fontFamily: "system-ui, sans-serif", fontSize: 0.92rem, fontWeight: 600, lineHeight: 1.1 }
  caption:    { fontFamily: "system-ui, sans-serif", fontSize: 0.78rem, fontWeight: 600, lineHeight: 1.1 }
rounded:
  sm:   8px
  md:   12px
  lg:   16px
  pill: 9999px
  nav:  30px      # the floating bottom-tab bar
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 12px    # uniform gap between cards/tiles and from the page edges
components:
  card:           { backgroundColor: "{colors.card}", textColor: "{colors.fg}", rounded: "{rounded.lg}", padding: "{spacing.md}" }
  button-primary: { backgroundColor: "{colors.accent}", textColor: "{colors.accent-fg}", rounded: "{rounded.md}", typography: "{typography.label}", height: 48px }
  control:        { height: 36px, rounded: "{rounded.md}", backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg}" }   # shared height for inline select / input so a row of controls lines up
  switch:         { width: 44px, height: 26px, rounded: "{rounded.pill}", thumbSize: 20px, trackOff: "{colors.border}", trackOn: "{colors.accent}", thumbColor: "{colors.accent-fg}" }   # shadcn Switch — no text label
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 61px, margin: 21px }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 53px }
  nav-tab-active: { backgroundColor: "{colors.canvas-subtle}", textColor: "{colors.accent}" }
  disclosure:     { align: left, chevron: right }   # collapsible details/summary header
icons:
  set:     "Lucide"               # canonical fleet icon set — https://lucide.dev
  url:     "https://lucide.dev"
  grid:    24px                   # 24×24 viewBox
  stroke:  2px                    # outline weight
  format:  SVG                    # inline SVG — no icon-font / web-font payload
  license: ISC
---

## Overview

A calm, high-contrast, true-black-capable identity modelled on the GitHub mobile
app: generous radii, elevated cards on a quiet canvas, one blue accent, and a
single floating bottom-tab pill for navigation. Mobile-first, installable PWA,
identical on phone and desktop web. The point of this spec is that two apps built
from it both *look* the same **and** *interact* the same — the navigation contract
below is as load-bearing as the palette.

## Colors

One accent (blue) does all interactive emphasis. The status colors
(`success` / `danger` / `attention`) are reserved for state, never decoration.
The five `tile-*` fills are the only saturated surfaces, used solely for the
Home-screen rounded-square icon tiles. Every saturated color ships a `*-p3`
`oklch()` twin with the identical token name in `design.dark.md`; consume it
behind `@media (color-gamut: p3)` so wide-gamut displays render the richer color
and sRGB displays fall back to the hex value. Neutrals (canvas, card, border,
text) are sRGB-only by design — they gain nothing from wide gamut.

## Typography

System font stack everywhere (no web-font payload, instant first paint). Bold,
tight headings; relaxed body; ALL-CAPS avoided — use weight, not case, for
hierarchy. The five roles cover every text need: don't introduce ad-hoc sizes.

## Layout

Card grid on a quiet canvas. Content column max ~480px on phones, centered. A
single **`spacing.gutter` (12px)** sets every gap — between cards/tiles *and* from
the page edges — so the spacing reads uniform in every direction.
**Reserve bottom padding equal to the nav height + safe-area inset** so the fixed
bar never covers content (`padding-bottom: calc(61px + env(safe-area-inset-bottom))`).
Installable PWAs lock to a fixed scale: viewport
`maximum-scale=1, user-scalable=no` + `touch-action: manipulation` on the body —
no pinch, no double-tap zoom.

## Elevation & Depth

Cards sit one step above the canvas via surface color + a hairline border, not
heavy shadows. The bottom-nav bar is the *only* element with a real shadow +
backdrop blur, because it is the only thing that floats over scrolling content.

## Shapes

`rounded.lg` (16px) for cards, `rounded.md` (12px) for buttons and inputs,
`rounded.pill` for chips and the active nav tab, `rounded.nav` (30px) for the nav
bar itself. Icon tiles are squircles at `rounded.md`.

## Navigation & interaction (fleet contract — the part that must feel identical)

This section is the reason the spec exists. Two apps built from it must navigate
identically; treat every bullet as a hard requirement, not a suggestion.

- **Primary nav is a fixed floating bottom-tab pill** on touch / coarse pointers
  (`@media (pointer: coarse)`): `position: fixed`, anchored to the *viewport*
  bottom via `100dvh` + `env(safe-area-inset-bottom)` (never the content bottom —
  that is the iOS-PWA footgun), `rounded.nav` corners, backdrop blur, an
  equal-width grid of tabs. The bar stands **61px** tall and sits with **equal
  21px margins on left, right, and bottom** so it reads
  centered and breathes evenly.
- **One active tab at a time.** The active tab takes the subtle surface + accent
  text and sits at `tabindex 0`; the others are `tabindex -1`, with
  `aria-selected` tracked so it is announced correctly.
- **Selection persists** across reloads and PWA relaunch via `localStorage`, so
  the app reopens on the tab you left.
- **The nav hides whenever a modal/overlay is open** (`body:has(dialog[open])`) so
  it never floats above a dialog.
- **Tap targets ≥ 44px.** Tabs show an icon **and** a short label, never
  icon-only. The icon is a **Lucide** glyph (see Icons).
- **Desktop / fine pointers** may render the same tabs inline at the top; the
  behavior (single active tab, persistence) is unchanged — only the placement
  differs.

## Components

`button-primary` for the one main action per view; `card` for every content group;
`nav-bar` + `nav-tab` per the contract above. Inline form controls (`select`,
`input`) share the `control` height (36px) so they line up on a row. The on/off
`switch` is the shadcn Switch — a compact track + sliding thumb, **no text
label** (state is read from thumb position + track color; `role="switch"` +
`aria-checked` carry it for assistive tech), one canonical size everywhere. Its
track is the accent when on; a **state** toggle (power on/off, alarm bypass) may
substitute the relevant status color instead. Collapsible `details/summary`
headers (`disclosure`) left-align the icon + title with the chevron pinned right.
Reuse the **vendored** nav/UI snippets from `project-scaffolding` verbatim — do
not re-author them per app (the same model as `single_instance.py` /
`tray_lifecycle.ps1`).

## Base UI — model components on shadcn

shadcn/ui (<https://ui.shadcn.com/docs/components>) is the reference for component
**structure, markup, accessibility, and interaction** — its primitives encode the
WAI-ARIA patterns correctly (on whichever backend you pick: [Radix UI](https://www.radix-ui.com/primitives) or [Base UI](https://base-ui.com/)). But
**these apps are vanilla HTML/CSS/JS, not React**, so you do not install shadcn —
you copy its *markup shape and interaction semantics* (the element structure, ARIA
roles/states, keyboard behavior) by hand, then skin them with the fleet tokens
above (colors, radii, spacing, and the `control` / `switch` dimensions). Before
hand-rolling any control, read its shadcn component page and mirror that structure;
don't reinvent interaction semantics shadcn already gets right.

Mapping for the controls this fleet uses:

- **Switch** → shadcn `switch` (the on/off toggle above — track + thumb, no label).
- **Select** / **Input** → shadcn `select` / `input`, sized to `control` (36px).
- **Button** → shadcn `button` (`button-primary` is its `default` variant).
- **Dialog** → shadcn `dialog` (the detail / rename modals).
- **Tabs** → shadcn `tabs` (rendered as the floating bottom-nav pill on coarse
  pointers per the Navigation contract).
- **Tooltip / Checkbox / Radio / Accordion …** → the matching shadcn component.

When a new component is needed, model it on the matching shadcn component and apply
the tokens — that is how every app stays visually *and* behaviorally identical.

## Icons

One icon set, fleet-wide: **Lucide** (<https://lucide.dev>). This is the
consistent choice rather than a new dependency — Lucide is shadcn/ui's default
icon set, and the spec already endorses shadcn as the structural reference for
every interactive component, so the glyphs and the component shapes come from the
same vocabulary. Lucide ships ~1,600 icons under the permissive **ISC** license
on a **24×24** grid with a **2px** outline stroke — the calm, GitHub-mobile
line-icon style this identity is modelled on — and it ships as plain **SVG**, so
it drops into these vanilla HTML/CSS/JS PWAs with **no React and no build step**.
Unlike an icon font it carries no web-font payload, consistent with the
system-font, instant-first-paint typography choice above. Radix Icons (the set
behind the Radix UI reference above) was considered and rejected: ~300 glyphs at
15×15 is too small a library to dress a multi-app fleet.

Use Lucide everywhere the fleet shows an icon: the bottom-nav **tabs** (icon +
label, per the Navigation contract), the **disclosure** header glyph (with the
chevron pinned right), and the Home-screen **icon tiles**. Reach for the matching
Lucide icon the same way you reach for the matching shadcn component — never mix a
second icon set or hand-draw a one-off glyph.

**Adoption:** vendor only the handful of SVGs an app actually uses into
`project-scaffolding`'s `app/webapp/static/_vendored/icons/` (an inline Lucide
sprite + `icons.js` helper) and import them from there — exactly the model used
for the nav/UI snippets ("Reuse the **vendored** nav/UI snippets from
`project-scaffolding`" above). Apps do not each pull the whole Lucide library.

## Do's and Don'ts

- **Do** use the one blue accent for all interactive emphasis.
- **Do** model every interactive component on its shadcn component (structure + ARIA), then skin it with the fleet tokens.
- **Do** draw every icon from **Lucide** — the shadcn-native set — vendored through `project-scaffolding`.
- **Do** keep the bottom nav identical across apps — same radius, blur, and
  persistence behavior.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Don't** hand-roll a primitive (switch, select, dialog, tabs…) that shadcn already defines.
- **Don't** mix a second icon set or hand-draw a one-off glyph — use the matching Lucide icon.
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** use status colors decoratively — they signal state only.
- **Don't** apply this spec to Streamlit POC spikes.
