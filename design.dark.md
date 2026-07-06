---
name: Fleet
version: alpha
description: >
  The shared visual identity, navigation, and interaction language for the
  automation fleet's web apps (FastAPI + static PWA). This is the Dark theme —
  the same token names as the Light theme with different values. The Light theme
  lives at `~/.claude/design.md`. True-black-ish canvas, elevated dark cards,
  one brighter blue accent, large radii, and the same floating bottom-tab pill.
  Streamlit POC spikes are exempt.
colors:
  canvas:        "#0d1117"   # page background (GitHub dark canvas)
  canvas-subtle: "#010409"   # inset / off-card surfaces (true black)
  card:          "#161b22"   # elevated card surface
  border:        "#30363d"   # default hairline
  border-muted:  "#21262d"   # quieter hairline (dividers inside a card)
  fg:            "#e6edf3"   # primary text
  fg-muted:      "#7d8590"   # secondary text
  accent:        "#2f81f7"   # links, primary CTA
  accent-fg:     "#ffffff"   # text/icon on an accent fill
  success:       "#3fb950"
  danger:        "#f85149"
  attention:     "#d29922"
  # rounded-square icon-tile fills (Home-screen tiles) — brighter for dark
  tile-green:    "#2ea043"
  tile-blue:     "#2f81f7"
  tile-purple:   "#a371f7"
  tile-orange:   "#db6d28"
  tile-yellow:   "#d29922"
  # wide-gamut (P3) twins — identical token family as design.md (Vercel convention).
  # A P3 display picks these up via `@media (color-gamut: p3)`; sRGB displays use the hex above.
  accent-p3:     "oklch(0.64 0.18 256)"
  success-p3:    "oklch(0.72 0.18 150)"
  danger-p3:     "oklch(0.68 0.20 25)"
  attention-p3:  "oklch(0.77 0.13 85)"
  tile-green-p3:  "oklch(0.66 0.16 150)"
  tile-blue-p3:   "oklch(0.64 0.18 256)"
  tile-purple-p3: "oklch(0.67 0.18 295)"
  tile-orange-p3: "oklch(0.67 0.15 50)"
  tile-yellow-p3: "oklch(0.77 0.13 85)"
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
  switch:         { width: 44px, height: 26px, rounded: "{rounded.pill}", thumbSize: 20px, trackOff: "{colors.border}", trackOn: "{colors.success}", thumbColor: "{colors.accent-fg}" }   # shadcn Switch — no text label; on = green (success), the universal on-state
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 61px, margin: 21px }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 53px }
  nav-tab-active: { backgroundColor: "{colors.canvas-subtle}", textColor: "{colors.accent}" }
  disclosure:     { align: left, chevron: right, closedHeight: 52px, summaryPadding: "0 14px", bodyPadding: "12px 14px 14px" }   # collapsible details/summary header — structural, identical to Light (see design.md)
  modal:          { rounded: "{rounded.lg}", closeSize: 34px, rowPadding: "12px 0", primaryButton: "{components.button-primary}" }   # editor <dialog> — structural, identical to Light (see design.md)
  empty-state:    { iconSize: "{icons.size.feature}", gap: "{spacing.sm}", padding: "{spacing.xl} {spacing.md}", actionMinWidth: 96px, textColor: "{colors.fg-muted}" }   # icon + one-line reason + optional action, centered
  icon-tile:      { rounded: "{rounded.md}", iconSize: "{icons.size.feature}", iconColor: "{colors.accent-fg}" }   # Home-screen rounded-square — one tile-* fill, centered Lucide glyph
focus:            { outline: "2px solid {colors.accent}", offset: 2px }   # one tokenized :focus-visible ring app-wide — identical behavior to Light, brighter accent value
icons:
  size:                           # theme-independent; the full icon spec (set/grid/stroke/license) is in design.md
    inline:  16px                 # inline with body text / row affordances
    title:   18px                 # section-title & disclosure leading glyph
    feature: 24px                 # empty-state, icon tiles, large standalone (== grid)
    nav-tab: 24px                 # bottom-nav tab glyph
---

## Overview

The Dark theme of the Fleet identity — the same structure, radii, typography, and
navigation contract as the [Light theme](design.md), rendered on a near-black
canvas with elevated cards. The accent steps one notch brighter (`#2f81f7`) so it
holds contrast against the dark surface. Everything in `design.md`'s prose applies
here unchanged except the surface/elevation notes below.

## Colors

Identical token names to the Light theme, different values. `canvas` is GitHub's
near-black (`#0d1117`); `canvas-subtle` drops to true black (`#010409`) for insets;
`card` is the one elevated surface (`#161b22`). One brighter blue accent does all
interactive emphasis; status colors signal state only; the five `tile-*` fills are
the only saturated surfaces. Every saturated color ships a `*-p3` `oklch()` twin
under the same token name — consume it behind `@media (color-gamut: p3)`.

## Typography

Unchanged from the Light theme — same system font stack and the same five roles.

## Layout

Unchanged from the Light theme. Reserve bottom padding equal to the nav height +
safe-area inset so the fixed bar never covers content.

## Elevation & Depth

This is where dark differs most: depth comes from **surface lightness**, not
shadow. `card` reads as elevated because it is lighter than `canvas`, reinforced
by the `border` hairline. Shadows are nearly invisible on a black canvas, so the
floating bottom-nav bar leans on its backdrop blur + a faint border rather than a
drop shadow to separate from content.

## Shapes

Unchanged from the Light theme: `rounded.lg` cards, `rounded.md` buttons/inputs,
`rounded.pill` chips and active tab, `rounded.nav` nav bar, squircle icon tiles.

## Motion

Unchanged from the Light theme: motion is functional, not decorative, and
`@media (prefers-reduced-motion: reduce)` collapses every authored transition and
animation to near-instant (`0.01ms`) while leaving functional timing delays
alone. See `design.md` for the full note.

## Navigation & interaction (fleet contract — the part that must feel identical)

Identical to the [Light theme contract](design.md): fixed floating bottom-tab pill
on coarse pointers, viewport-anchored via `100dvh` + `env(safe-area-inset-bottom)`,
one active tab at a time (subtle surface + accent text, `aria-selected` tracked),
`localStorage`-persisted selection, hidden under an open modal
(`body:has(dialog[open])`), tap targets ≥ 44px with icon + label, the same
tokenized `:focus-visible` ring on every interactive element, and the same
behavior rendered inline at the top on fine pointers. The dark theme changes the
*colors* of these elements (the focus ring uses the brighter dark `accent`),
never their *behavior*.

## Components

Structurally unchanged from the Light theme — `button-primary`, `card`,
`control`, `switch`, `nav-bar`, `nav-tab`, `disclosure`, plus the `modal`,
`empty-state`, and `icon-tile` **Component contracts** and the `icons.size` steps
defined in `design.md` — all with the vendored snippets from
`project-scaffolding` reused verbatim. Only values change for dark: the `switch`
on-track is still **green (`success`)**, at the brighter dark `success` value;
the `modal` disabled recipe holds AA on the dark surface (~5.5:1); the
`icon-tile` fills use the brighter dark `tile-*` values. The **Base UI — model
components on shadcn** rule in `design.md` applies here unchanged: every
interactive component is modelled on its shadcn component (structure + ARIA), then
skinned with the (dark) tokens.

## Do's and Don'ts

- **Do** keep behavior byte-for-byte identical to the Light theme — only values change.
- **Do** lean on surface lightness, not shadow, for elevation on the dark canvas.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Do** hold AA contrast on the dark surface for disabled controls and muted text — use the authored disabled recipe, never the browser default (which drops sub-AA).
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** use status colors decoratively — they signal state only.
- **Don't** apply this spec to Streamlit POC spikes.
