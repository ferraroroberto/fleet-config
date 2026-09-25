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
  accent:        "#2f81f7"   # links, focus ring, the base the accent derivatives mix from
  accent-fg:     "#ffffff"   # text/icon on an accent fill
  accent-fill:   "#1f6feb"   # solid primary fill (button-primary) — one step below accent so white text holds AA (4.63:1; accent gives 3.75)
  accent-text:   "#58a6ff"   # accent-coloured TEXT on an accent-soft tint (button-tint, nav-tab-active) — 5.63:1
  # accent derivatives — theme-independent color-mix over the per-theme accent (same strings as design.md)
  accent-soft:          "color-mix(in srgb, var(--accent) 16%, transparent)"   # tinted fill (button-tint)
  accent-border-soft:   "color-mix(in srgb, var(--accent) 24%, transparent)"   # hairline on a tinted fill
  accent-border-strong: "color-mix(in srgb, var(--accent) 28%, transparent)"   # hairline on a solid accent fill
  success:       "#3fb950"
  danger:        "#f85149"
  attention:     "#d29922"
  # status text on its own 16% tint — same role as design.md, brighter for dark
  success-text:   "#56d364"   # 6.90:1 on the success tint
  danger-text:    "#ff7b72"   # 5.70:1 on the danger tint
  attention-text: "#e3b341"   # 6.84:1 on the attention tint
  # neutral control surfaces
  control-border: "#6e7681"   # input/select boundary + switch off-track — 3.77:1 vs card (WCAG 1.4.11); card hairlines keep border
  neutral-soft:   "color-mix(in srgb, var(--fg-muted) 16%, transparent)"   # chip / filter-pill fill — replaces canvas-subtle (true black) inside a card
  # rounded-square icon-tile fills (Home-screen tiles) — the emphasis step, so a white glyph holds 4.6:1
  tile-green:    "#238636"
  tile-blue:     "#1f6feb"
  tile-purple:   "#8957e5"
  tile-orange:   "#bd561d"
  tile-yellow:   "#9e6a03"
  # wide-gamut (P3) twins — identical token family as design.md (Vercel convention).
  # A P3 display picks these up via `@media (color-gamut: p3)`; sRGB displays use the hex above.
  accent-p3:     "oklch(0.64 0.18 256)"
  accent-fill-p3: "oklch(0.57 0.21 260)"
  success-p3:    "oklch(0.72 0.18 150)"
  danger-p3:     "oklch(0.68 0.20 25)"
  attention-p3:  "oklch(0.77 0.13 85)"
  tile-green-p3:  "oklch(0.55 0.17 146)"
  tile-blue-p3:   "oklch(0.57 0.21 260)"
  tile-purple-p3: "oklch(0.58 0.23 296)"
  tile-orange-p3: "oklch(0.57 0.17 45)"
  tile-yellow-p3: "oklch(0.57 0.13 75)"
typography:
  heading-xl: { fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", fontSize: 2rem,    fontWeight: 700, lineHeight: 1.15, letterSpacing: "-0.02em" }
  heading-lg: { fontFamily: "system-ui, sans-serif", fontSize: 1.5rem,  fontWeight: 700, lineHeight: 1.2 }
  heading-md: { fontFamily: "system-ui, sans-serif", fontSize: 1.25rem, fontWeight: 700, lineHeight: 1.25 }   # 20px — section / group title between body and heading-lg
  body:       { fontFamily: "system-ui, sans-serif", fontSize: 1rem,    fontWeight: 400, lineHeight: 1.5 }
  body-sm:    { fontFamily: "system-ui, sans-serif", fontSize: 0.875rem, fontWeight: 400, lineHeight: 1.45 }   # 14px — every secondary line: paths, status, "last run", row meta, helper copy
  label:      { fontFamily: "system-ui, sans-serif", fontSize: 0.875rem, fontWeight: 600, lineHeight: 1.1 }   # 14px — field and control labels, button text
  caption:    { fontFamily: "system-ui, sans-serif", fontSize: 0.75rem, fontWeight: 600, lineHeight: 1.1 }   # 12px — chips, badges and timestamps only
  overline:   { fontFamily: "system-ui, sans-serif", fontSize: 0.75rem, fontWeight: 600, lineHeight: 1.1, letterSpacing: "0.06em", textTransform: uppercase }   # 12px — list group headers only (date groups); the one legal caps use
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
rows:
  sm: 44px        # compact row (inline control lockstep) — structural, identical to Light
  md: 52px        # standard list-row / disclosure closed height
  lg: 60px        # spacious row — meta line + action rail
components:
  card:           { backgroundColor: "{colors.card}", textColor: "{colors.fg}", rounded: "{rounded.lg}", padding: "{spacing.md}" }
  button-primary: { backgroundColor: "{colors.accent-fill}", textColor: "{colors.accent-fg}", borderColor: "{colors.accent-border-strong}", rounded: "{rounded.md}", typography: "{typography.label}", height: 48px }
  button-tint:     { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent-text}", borderColor: "{colors.accent-border-soft}", rounded: "{rounded.md}", fontWeight: 700, height: 48px }   # secondary emphasis — structural, identical to Light (see design.md)
  button-ghost:    { backgroundColor: transparent, borderColor: "{colors.border}", textColor: "{colors.fg-muted}", rounded: "{rounded.md}" }   # ghost = TRANSPARENT fill on a hairline border — structural, identical to Light
  button-surface:  { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}", rounded: "{rounded.md}", height: "{components.control.height}" }   # utility/toolbar/icon button at the control height
  button-disabled: { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}" }   # ONE disabled recipe for every tier, both themes (home-automation#362)
  control:        { height: 36px, rounded: "{rounded.md}", backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.control-border}", textColor: "{colors.fg}" }   # shared height for inline select / input so a row of controls lines up
  switch:         { width: 44px, height: 26px, rounded: "{rounded.pill}", thumbSize: 20px, trackOff: "{colors.control-border}", trackOn: "{colors.success}", thumbColor: "{colors.accent-fg}" }   # shadcn Switch — no text label; on = green (success), the universal on-state
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 61px, margin: 21px, maxTabs: 5 }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 53px }
  nav-tab-active: { backgroundColor: "{colors.accent-soft}", borderColor: "{colors.accent-border-soft}", textColor: "{colors.accent-text}" }   # accent-soft tint, not canvas-subtle — the inset surface (true black here) reads as a black hole (project-scaffolding#159)
  chip:           { backgroundColor: "{colors.neutral-soft}", textColor: "{colors.fg}", rounded: "{rounded.pill}" }   # neutral chip / filter pill — structural, identical to Light (see design.md)
  disclosure:     { align: left, chevron: right, closedHeight: 52px, summaryPadding: "0 14px", bodyPadding: "12px 14px 14px" }   # collapsible details/summary header — structural, identical to Light (see design.md)
  modal:          { rounded: "{rounded.lg}", closeSize: 34px, rowPadding: "12px 0", primaryButton: "{components.button-primary}" }   # editor <dialog> — structural, identical to Light (see design.md)
  list-row:       { rowPadding: "{components.modal.rowPadding}", divider: "{colors.border-muted}" }   # repeating entries inside a card — structural, identical to Light (see design.md)
  action-row:     { minHeight: "{rows.md}", title: "{typography.body}", titleWeight: 600, meta: "{typography.body-sm}", metaColor: "{colors.fg-muted}", leadingToggles: 1, trailingAccessories: 1, extraVisibleActions: 1, accessorySize: "{components.hit-target.min}", destructiveColor: "{colors.danger-text}", filterAboveRows: 12, filterHeight: 44px, filterBorder: "{colors.control-border}" }   # tap the row = primary action — structural, identical to Light (see design.md)
  empty-state:    { iconSize: "{icons.size.feature}", gap: "{spacing.sm}", padding: "{spacing.xl} {spacing.md}", actionMinWidth: 96px, textColor: "{colors.fg-muted}" }   # icon + one-line reason + optional action, centered
  icon-tile:      { rounded: "{rounded.md}", iconSize: "{icons.size.feature}", iconColor: "{colors.accent-fg}" }   # Home-screen rounded-square — one tile-* fill, centered Lucide glyph
  page-header:    { minHeight: "{rows.md}", padding: "0 14px", title: "{typography.body}", titleWeight: 700, context: "{typography.body-sm}", contextColor: "{colors.fg-muted}", trailingActions: 2, actionSize: "{components.hit-target.min}" }   # every pane's first element — structural, identical to Light (see design.md)
  hit-target:     { min: 44px }   # minimum effective pointer-target square — structural, identical to Light (see design.md Touch targets)
focus:            { outline: "2px solid {colors.accent}", offset: 2px }   # one tokenized :focus-visible ring app-wide — identical behavior to Light, brighter accent value
layout:                           # desktop placement (Layout) — theme-independent
  measure:     772px              # centered content column below the wide breakpoint
  wide:        1100px             # (min-width: 1100px) and (pointer: fine): left rail + master-detail
  rail:        80px               # wide-layout nav rail — icon over a short label, never icon-only
  list-pane:   1fr                # master-detail split: list : detail
  detail-pane: 1.5fr
text-size:                        # the user's zoom-lock escape (Layout, "Text size") — theme-independent
  key-suffix: ".textsize"         # localStorage key `<app>.textsize`, stamped pre-paint as html[data-textsize]
  small:   93.75%                 # root font-size per step; rem-based type scales, px geometry stays fixed
  default: 100%
  large:   112.5%
icons:
  size:                           # theme-independent; the full icon spec (set/grid/stroke/license) is in design.md
    inline:  16px                 # inline with body text / row affordances
    title:   18px                 # section-title & disclosure leading glyph
    feature: 24px                 # empty-state, icon tiles, large standalone (== grid)
    nav-tab: 20px                 # bottom-nav tab glyph (--bottom-tabs-icon — phone-validated geometry, home-automation#118)
---

## Overview

The Dark theme of the Fleet identity — the same structure, radii, typography, and
navigation contract as the [Light theme](design.md), rendered on a near-black
canvas with elevated cards. The accent steps one notch brighter (`#2f81f7`) so it
holds contrast against the dark surface, while the solid primary fill
(`accent-fill`, `#1f6feb`) steps one notch darker so its white label holds AA. Everything in `design.md`'s prose applies
here unchanged except the surface/elevation notes below.

## Colors

Identical token names to the Light theme, different values. `canvas` is GitHub's
near-black (`#0d1117`); `canvas-subtle` drops to true black (`#010409`) for insets;
`card` is the one elevated surface (`#161b22`). One brighter blue accent does all
interactive emphasis; status colors signal state only; the five `tile-*` fills are
the only saturated surfaces. Every saturated color ships a `*-p3` `oklch()` twin
under the same token name — consume it behind `@media (color-gamut: p3)`.

The fill-versus-text split in `design.md` (Colors) applies unchanged:
`*-text` for text on a tint, `accent-fill` for the solid primary,
`control-border` for input boundaries, `neutral-soft` + `fg` for chips. Dark
needs it most. A chip or search field on `canvas-subtle` (`#010409`) inside a
`#161b22` card is the black hole this file already rejects for the active tab.
The dark `tile-*` fills are GitHub's emphasis step, darker than the base hues,
so a white glyph holds 4.6:1. The brighter base hues gave 2.5–3.4:1.

The user-selectable theme-switching contract (pre-paint boot script, persisted
sun/moon toggle, dual `theme-color` metas) is defined in the
[Light theme](design.md) and applies unchanged; this theme's `canvas`
(`#0d1117`) is the dark `theme-color` meta value. So does the text-size
contract (Layout, "Text size"): the same `<app>.textsize` key and
`html[data-textsize]` stamp, stamped by the same boot script, whichever theme
is showing.

## Typography

Unchanged from the Light theme: the same system font stack, the same eight
roles on the same whole-pixel scale, and the same caps rule (`overline` group
headers only).

## Layout

Unchanged from the Light theme. Reserve bottom padding equal to the nav height +
safe-area inset so the fixed bar never covers content. The wide layout
(`layout.wide`, 1100px and fine pointer: left rail + master-detail, board
exception) applies unchanged. The rail is a `card` surface, so in dark it
sits one step above the canvas like any other card.

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
one active tab at a time (accent-soft tint + `accent-text`, `aria-selected` tracked),
unselected tabs with no boundary (the 1.4.11 exemption: icon + label identify
them, pill and rail alike), `localStorage`-persisted selection, hidden under an open modal
(`body:has(dialog[open])`), tap targets ≥ 44px with icon + label, at most five
tabs with Settings as a page-header action, every pane opening with the one
`page-header`, the same
tokenized `:focus-visible` ring on every interactive element, and the same
behavior rendered inline at the top on fine pointers, or as the left rail
at `layout.wide` (1100px) and up. The dark theme changes the
*colors* of these elements (the focus ring uses the brighter dark `accent`),
never their *behavior*.

## Components

Structurally unchanged from the Light theme — the four button tiers
(`button-primary` / `button-tint` / `button-ghost` / `button-surface`, one
shared `button-disabled` recipe; the accent derivatives are the same
`color-mix` strings over the brighter dark accent), `card`,
`control`, `switch`, `nav-bar`, `nav-tab`, `disclosure`, plus the `modal`,
`page-header`, `list-row`, `action-row`, `empty-state`, and `icon-tile` **Component contracts** and the `icons.size` steps
defined in `design.md` — all with the vendored snippets from
`project-scaffolding` reused verbatim. Only values change for dark: the `switch`
on-track is still **green (`success`)**, at the brighter dark `success` value;
the `modal` disabled recipe holds AA on the dark surface (~5.5:1); the
`icon-tile` fills use the brighter dark `tile-*` values. The **Base UI — model
components on shadcn** rule in `design.md` applies here unchanged: every
interactive component is modelled on its shadcn component (structure + ARIA), then
skinned with the (dark) tokens. The **Async data & feedback**, **Touch
targets**, and **Charts** contracts and the **dense collection** composition
in `design.md` are behavioral and **theme-invariant** — they apply here
unchanged (`hit-target.min` is the same 44px in both themes); only the colours
they reference resolve to the dark values.

## Do's and Don'ts

- **Do** keep behavior byte-for-byte identical to the Light theme — only values change.
- **Do** lean on surface lightness, not shadow, for elevation on the dark canvas.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Do** hold AA contrast on the dark surface for disabled controls and muted text — use the authored disabled recipe, never the browser default (which drops sub-AA).
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** use status colors decoratively — they signal state only.
- **Don't** apply this spec to Streamlit POC spikes.
