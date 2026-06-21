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
components:
  card:           { backgroundColor: "{colors.card}", textColor: "{colors.fg}", rounded: "{rounded.lg}", padding: "{spacing.md}" }
  button-primary: { backgroundColor: "{colors.accent}", textColor: "{colors.accent-fg}", rounded: "{rounded.md}", typography: "{typography.label}", height: 48px }
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 60px }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 48px }
  nav-tab-active: { backgroundColor: "{colors.canvas-subtle}", textColor: "{colors.accent}" }
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

Card grid on a quiet canvas. Content column max ~480px on phones, centered.
**Reserve bottom padding equal to the nav height + safe-area inset** so the fixed
bar never covers content (`padding-bottom: calc(60px + env(safe-area-inset-bottom))`).

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
  equal-width grid of tabs.
- **One active tab at a time.** The active tab takes the subtle surface + accent
  text and sits at `tabindex 0`; the others are `tabindex -1`, with
  `aria-selected` tracked so it is announced correctly.
- **Selection persists** across reloads and PWA relaunch via `localStorage`, so
  the app reopens on the tab you left.
- **The nav hides whenever a modal/overlay is open** (`body:has(dialog[open])`) so
  it never floats above a dialog.
- **Tap targets ≥ 44px.** Tabs show an icon **and** a short label, never
  icon-only.
- **Desktop / fine pointers** may render the same tabs inline at the top; the
  behavior (single active tab, persistence) is unchanged — only the placement
  differs.

## Components

`button-primary` for the one main action per view; `card` for every content group;
`nav-bar` + `nav-tab` per the contract above. Reuse the **vendored** nav/UI
snippets from `project-scaffolding` verbatim — do not re-author them per app (the
same model as `single_instance.py` / `tray_lifecycle.ps1`).

## Do's and Don'ts

- **Do** use the one blue accent for all interactive emphasis.
- **Do** keep the bottom nav identical across apps — same radius, blur, and
  persistence behavior.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** use status colors decoratively — they signal state only.
- **Don't** apply this spec to Streamlit POC spikes.
