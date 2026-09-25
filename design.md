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
  accent:        "#0969da"   # links, focus ring, the base the accent derivatives mix from
  accent-fg:     "#ffffff"   # text/icon on an accent fill
  accent-fill:   "#0969da"   # solid primary fill (button-primary) — equals accent here; dark steps it down so white text holds AA
  accent-text:   "#0550ae"   # accent-coloured TEXT on an accent-soft tint (button-tint, nav-tab-active) — 6.04:1
  # accent derivatives — theme-independent color-mix over the per-theme accent (same strings in design.dark.md)
  accent-soft:          "color-mix(in srgb, var(--accent) 16%, transparent)"   # tinted fill (button-tint)
  accent-border-soft:   "color-mix(in srgb, var(--accent) 24%, transparent)"   # hairline on a tinted fill
  accent-border-strong: "color-mix(in srgb, var(--accent) 28%, transparent)"   # hairline on a solid accent fill
  success:       "#1a7f37"
  danger:        "#cf222e"
  attention:     "#9a6700"
  # status text on its own 16% tint (badge, banner, destructive tint) — the base stays for fills, icons and borders
  success-text:   "#116329"   # 5.94:1 on the success tint
  danger-text:    "#a40e26"   # 6.08:1 on the danger tint
  attention-text: "#7d4e00"   # 5.75:1 on the attention tint
  # neutral control surfaces
  control-border: "#818b98"   # input/select boundary + switch off-track — 3.45:1 vs card (WCAG 1.4.11); card hairlines keep border
  neutral-soft:   "color-mix(in srgb, var(--fg-muted) 16%, transparent)"   # chip / filter-pill fill; text on it is fg (fg-muted drops to 4.26:1)
  # rounded-square icon-tile fills (Home-screen tiles) — the only saturated surfaces
  tile-green:    "#1f883d"
  tile-blue:     "#0969da"
  tile-purple:   "#8250df"
  tile-orange:   "#bc4c00"
  tile-yellow:   "#bf8700"
  # wide-gamut (P3) twins — identical token family in design.dark.md (Vercel convention).
  # A P3 display picks these up via `@media (color-gamut: p3)`; sRGB displays use the hex above.
  accent-p3:     "oklch(0.52 0.18 256)"
  accent-fill-p3: "oklch(0.52 0.18 256)"
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
  sm: 44px        # compact row (inline control lockstep)
  md: 52px        # standard list-row / disclosure closed height
  lg: 60px        # spacious row — meta line + action rail
components:
  card:           { backgroundColor: "{colors.card}", textColor: "{colors.fg}", rounded: "{rounded.lg}", padding: "{spacing.md}" }
  button-primary: { backgroundColor: "{colors.accent-fill}", textColor: "{colors.accent-fg}", borderColor: "{colors.accent-border-strong}", rounded: "{rounded.md}", typography: "{typography.label}", height: 48px }
  button-tint:     { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent-text}", borderColor: "{colors.accent-border-soft}", rounded: "{rounded.md}", fontWeight: 700, height: 48px }   # secondary emphasis — the accent-tinted action (home-automation .big-btn)
  button-ghost:    { backgroundColor: transparent, borderColor: "{colors.border}", textColor: "{colors.fg-muted}", rounded: "{rounded.md}" }   # quiet tertiary action — ghost = TRANSPARENT fill on a hairline border, never a tinted fill
  button-surface:  { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}", rounded: "{rounded.md}", height: "{components.control.height}" }   # utility/toolbar/icon button at the control height
  button-disabled: { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}" }   # ONE disabled recipe for every tier, both themes (home-automation#362) — never opacity on a solid fill
  control:        { height: 36px, rounded: "{rounded.md}", backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.control-border}", textColor: "{colors.fg}" }   # shared height for inline select / input so a row of controls lines up
  switch:         { width: 44px, height: 26px, rounded: "{rounded.pill}", thumbSize: 20px, trackOff: "{colors.control-border}", trackOn: "{colors.success}", thumbColor: "{colors.accent-fg}" }   # shadcn Switch — no text label; on = green (success), the universal on-state
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 61px, margin: 21px, maxTabs: 5 }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 53px }
  nav-tab-active: { backgroundColor: "{colors.accent-soft}", borderColor: "{colors.accent-border-soft}", textColor: "{colors.accent-text}" }   # accent-soft tint, not canvas-subtle — the inset surface reads as a black hole in dark mode (project-scaffolding#159)
  chip:           { backgroundColor: "{colors.neutral-soft}", textColor: "{colors.fg}", rounded: "{rounded.pill}" }   # neutral chip / filter pill — never canvas-subtle inside a card (the dark black hole again)
  disclosure:     { align: left, chevron: right, closedHeight: 52px, summaryPadding: "0 14px", bodyPadding: "12px 14px 14px" }   # collapsible details/summary header — the summary owns height+padding; the card's own padding is zeroed so cards align when closed
  modal:          { rounded: "{rounded.lg}", closeSize: 34px, rowPadding: "12px 0", primaryButton: "{components.button-primary}" }   # editor <dialog> — heading-lg title + × close, label/value rows on a top-border divider, one full-width primary
  list-row:       { rowPadding: "{components.modal.rowPadding}", divider: "{colors.border-muted}" }   # repeating entries inside a card — flat full-bleed rows on a top hairline, never nested canvas-subtle cards (photo-ocr .history-item, post-photo-ocr#73)
  action-row:     { minHeight: "{rows.md}", title: "{typography.body}", titleWeight: 600, meta: "{typography.body-sm}", metaColor: "{colors.fg-muted}", leadingToggles: 1, trailingAccessories: 1, extraVisibleActions: 1, accessorySize: "{components.hit-target.min}", destructiveColor: "{colors.danger-text}", filterAboveRows: 12, filterHeight: 44px, filterBorder: "{colors.control-border}" }   # a list row that does something: tap the row = primary action, one trailing kebab/chevron, destructive only in the menu
  empty-state:    { iconSize: "{icons.size.feature}", gap: "{spacing.sm}", padding: "{spacing.xl} {spacing.md}", actionMinWidth: 96px, textColor: "{colors.fg-muted}" }   # icon + one-line reason + optional action, centered
  icon-tile:      { rounded: "{rounded.md}", iconSize: "{icons.size.feature}", iconColor: "{colors.accent-fg}" }   # Home-screen rounded-square — one tile-* fill, centered Lucide glyph
  page-header:    { minHeight: "{rows.md}", padding: "0 14px", title: "{typography.body}", titleWeight: 700, context: "{typography.body-sm}", contextColor: "{colors.fg-muted}", trailingActions: 2, actionSize: "{components.hit-target.min}" }   # every pane's first element — the vendored home-head: tab title, one context line, trailing theme toggle + settings
  hit-target:     { min: 44px }   # minimum effective pointer-target square, app-wide — see Touch targets
focus:            { outline: "2px solid {colors.accent}", offset: 2px }   # one tokenized :focus-visible ring app-wide (a control overrides only where it draws a custom ring)
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
  set:     "Lucide"               # canonical fleet icon set — https://lucide.dev
  url:     "https://lucide.dev"
  grid:    24px                   # 24×24 viewBox
  stroke:  2px                    # outline weight
  format:  SVG                    # inline SVG — no icon-font / web-font payload
  license: ISC
  size:                           # the canonical icon-size steps (closes the census gap — was 16 distinct sizes)
    inline:  16px                 # inline with body text / row affordances
    title:   18px                 # section-title & disclosure leading glyph
    feature: 24px                 # empty-state, icon tiles, large standalone (== grid)
    nav-tab: 20px                 # bottom-nav tab glyph (--bottom-tabs-icon — phone-validated geometry, home-automation#118)
app-icon:
  generator: brand_gen            # project-scaffolding/scripts/brand_gen.py — one Lucide master, no bespoke raster drawing
  apple: icon-180.png             # opaque Apple touch icon
  regular-small: icon-192.png     # manifest purpose: any
  regular-large: icon-512.png     # manifest purpose: any
  maskable: icon-512-maskable.png # separate safe-zone asset; never combine any + maskable on one source
  favicon: favicon.ico
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

**A hue used as text and as a fill needs two tokens.** A base hue cannot be
both a fill and legible text on its own 16% tint — the tint lifts the
background and the pair drops under AA in both themes. So:

- **Text on a tint** uses the `*-text` token: `accent-text` for `button-tint`
  and `nav-tab-active`, `success-text` / `danger-text` / `attention-text` for
  a badge, banner or destructive tint. Every pairing clears 4.5:1 in both
  themes. The base `accent` stays for links, the focus ring and icons;
  `success` / `danger` / `attention` stay for fills, dots and borders.
- **The solid primary fill** uses `accent-fill`, not `accent`. It equals
  `accent` in light; in dark it steps down so white text holds AA.
- **Control boundaries** (input, select, the switch's off-track) use
  `control-border`, which clears WCAG 1.4.11's 3:1 against `card`. Card
  hairlines and dividers keep the quieter `border` / `border-muted`. Nav tabs
  take no `control-border`: unselected tabs are exempt (see the Navigation
  contract's unselected-tab rule).
- **Chips** fill with `neutral-soft` and set their text in `fg`; `fg-muted`
  on it falls under AA.

The `*-text`, `control-border` and `neutral-soft` roles are sRGB-only on
purpose: each holds a verified ratio, and a wide-gamut variant would move it.
`accent-fill` does ship a `-p3` twin, at the same lightness, so the fix holds
on P3 displays. Icon-tile glyphs are graphics, so they need 3:1 against the
tile fill (WCAG 1.4.11), not 4.5:1.

### Theme switching (user-selectable)

Every app ships **both** themes, user-selectable — never dark-only or OS-only. A
**pre-paint inline `<head>` boot script** stamps `html[data-theme]` from a
per-app localStorage key ending **`.theme`** (e.g. `app-launcher.theme`),
falling back to `prefers-color-scheme` — before first paint, so no flash of the
wrong theme. A **persisted sun/moon Lucide icon-button toggle reachable from
the main view** flips it and writes the same key. The glyph shows the *action*
(sun = switch to light, moon = switch to dark), realized either as a JS glyph
swap or as both glyphs shipped and CSS-keyed on `[data-theme]` — both
canonical, as are `dataset.theme` and `setAttribute('data-theme', …)` for the
stamp. Ship **dual `theme-color` metas**, media-gated on `prefers-color-scheme`:
light = `canvas` `#ffffff`, dark = the dark theme's `canvas` `#0d1117`.
Reference impls: home-automation `app/webapp/static/index.html` + `main.js`
(JS swap); app-launcher `app/webapp/static/index.html` + `main.js` (CSS swap).

## Typography

System font stack everywhere (no web-font payload, instant first paint). Bold,
tight headings; relaxed body. The scale is whole pixels at a 16px root —
12 / 14 / 16 / 20 / 24 / 32 — and the eight roles cover every text need: don't
introduce ad-hoc sizes.

- **Secondary lines use `body-sm`** (14px, regular): paths, status lines,
  "last run", the meta line under a row title. Helper copy is `body-sm` in
  `fg-muted` at regular weight, never italic.
- **`caption` (12px) is for chips, badges and timestamps only**, never for a
  line people read.
- **`label`** carries field and control labels and button text;
  **`heading-md`** titles a section or group inside a view.
- **Caps are legal in one place.** `text-transform: uppercase` goes through
  the `overline` role only, and only for list group headers (e.g. date
  groups). Chips and badges are sentence case. Numbers and units are never
  transformed: "3M 33S" reads as mega, not minutes. Everywhere else, use
  weight, not case, for hierarchy.

## Layout

Card grid on a quiet canvas. Content column max ~480px on phones, centered. On
wider viewports the same column stays **centered at `max-width: 772px`
(`layout.measure`, `margin: 0 auto`)** rather than stretching full-bleed — the
phone-first proportions hold on desktop, so an app reads the same at any width.

**Wide layout — `(min-width: 1100px) and (pointer: fine)` (`layout.wide`).**
At 1440px a lone 772px column leaves almost half the window empty, and a
detail view opened from a list covers the list it came from. So on a wide,
fine-pointer window the same app changes placement only. Components, tokens
and behavior stay the same:

- **The nav becomes a left rail** (`layout.rail`, 80px, full height, `card`
  surface with a `border` hairline on its right edge). It shows the same tabs
  as a vertical stack, each an icon over a short label, never icon-only. The
  single active tab, `aria-selected`, persistence and the accent-soft +
  `accent-text` active state are unchanged. The floating pill and its bottom
  padding reservation do not apply here.
- **Master-detail.** A tab whose list opens a detail (a session, a job, a
  run) shows the list and the detail side by side (`layout.list-pane` :
  `layout.detail-pane`, 1 : 1.5, each a `minmax(0, …)` track). The detail
  replaces the overlay it uses on a phone. The selected row keeps the
  accent-soft tint. A tab with no detail keeps the centered 772px measure in
  the space right of the rail.
- **The page header** heads the list pane. A pane scrolls on its own; the
  window does not.
- **Board exception.** A multi-column board (kanban) may span the full width
  available to content at any desktop width. It is the one sanctioned
  exception to the 772px measure. Its page header and toolbar span with it,
  so the view never mixes two measures.

Below 1100px, or on a coarse pointer at any width, nothing changes: the
772px measure and the tab placement described in the Navigation contract
apply.

A
single **`spacing.gutter` (12px)** sets every gap — between cards/tiles *and* from
the page edges — so the spacing reads uniform in every direction.
**Reserve bottom padding equal to the nav height + safe-area inset** so the fixed
bar never covers content (`padding-bottom: calc(61px + env(safe-area-inset-bottom))`).
Installable PWAs lock to a fixed scale: viewport
`maximum-scale=1, user-scalable=no` + `touch-action: manipulation` on the body —
no pinch, no double-tap zoom.
**Text size — the escape that makes the zoom lock acceptable.** Locking the
viewport fails WCAG 1.4.4 (Resize Text) unless the app offers its own way to
enlarge text, so every app ships one, the same way it ships the theme toggle:

- **Control:** a persisted **Text size** setting with three steps (Small /
  Default / Large) in the app's Settings, as a segmented control.
- **Persistence:** a per-app `localStorage` key ending **`.textsize`**
  (`text-size.key-suffix`, e.g. `app-launcher.textsize`), values `small`,
  `default` or `large`.
- **Pre-paint stamp:** the **same inline `<head>` boot script** that stamps
  `data-theme` also stamps `html[data-textsize]` from that key, before first
  paint, so the page never reflows.
- **What scales:** the root `font-size` takes the step (`text-size.small`
  93.75% / `default` 100% / `large` 112.5%), so every rem-based type role
  scales with it.
- **What does not scale:** geometry stays in px — the nav pill, `rows`,
  `hit-target`, the `control` height and the `icons.size` steps. Never
  express geometry in rem, or the Large step would reflow the nav and the
  rows along with the text.
**Single-column stack containers pin their track to `minmax(0, 1fr)`** — any
`display: grid` wrapper around vertically-stacked content (a pane, pane-body,
or list container) that can hold a no-wrap or horizontally scrollable child
must declare `grid-template-columns: minmax(0, 1fr)`, never a bare
`1fr`/`auto`/implicit track. Grid items default to `min-width: auto`, so an
implicit or bare track inherits the widest descendant's min-content and the
page grows past the viewport — the child's own `overflow-x: auto` never
engages because its container already widened (grocery `0a68b0a`,
fleet-config#294).

## Elevation & Depth

Cards sit one step above the canvas via surface color + a hairline border, not
heavy shadows. The bottom-nav bar is the *only* element with a real shadow +
backdrop blur, because it is the only thing that floats over scrolling content.

## Shapes

`rounded.lg` (16px) for cards, `rounded.md` (12px) for buttons and inputs,
`rounded.pill` for chips and the active nav tab, `rounded.nav` (30px) for the nav
bar itself. Icon tiles are squircles at `rounded.md`.

## Motion

Motion is functional, not decorative: a short transition on a state change (a
switch flipping, a tab activating, a disclosure opening), never ambient
animation. **Honor the OS "reduce motion" preference** — under
`@media (prefers-reduced-motion: reduce)`, collapse every authored transition
and animation to near-instant (`0.01ms`, not `0` — some engines skip the
`transitionend`/`animationend` event at `0s`, and code that waits on it would
hang). Leave *functional* delays untouched: a wait that lets the viewport settle
before revealing the nav is a timing dependency, not decoration, so it is not
motion to reduce.

## Async data & feedback

Every data-backed surface declares exactly one of **five lifecycle states** —
`loading` / `ready` / `empty` / `stale` / `error` — and that five-word set is
the whole `data-state` vocabulary for async surfaces (home-automation#409):

- **loading** says what it is reading (`Reading security status…`) via the
  canonical `empty-state` block — never a blank pane.
- **empty** is the *true-empty* message ("no schedules yet"), also the
  `empty-state` block — visually distinct from loading and from failure.
- **stale** (a background refresh failed *after* a good read) **preserves the
  last-known content**, labels it inline
  `Last updated <time> · live data unavailable`, and **disables
  freshness-sensitive actions** — stale state is never actionable (an
  arm/disarm button acting on stale data is a hazard, not a convenience).
- **error** (no good data yet) is an `empty-state` block with a Lucide glyph,
  a one-line reason, and **at most one** concise Retry action.

Feedback stays at the right altitude: **passive/background status renders
inline beside the affected surface; a global toast is reserved for
user-initiated command progress/results.** Announce state changes through a
`role="status"` live region (`aria-live="polite"`, `assertive` only for
errors) without moving focus. Failure copy is sanitized — no hostnames, URLs,
exception classes, or timeout internals in user-facing text (logs keep the
detail) — and repeated background failures dedupe to one notice per
healthy→failing transition.

## Touch targets

**Every non-navigation pointer target presents an effective hit area of at
least `components.hit-target.min` (44×44px)** — the nav contract's 44px
floor, generalized app-wide. Two canonical ways to reach it:

- **Isolated compact control** — keep the compact visual (e.g. a 34×34 icon
  button) and expand the hit area invisibly with one shared utility class
  (canonical name `.hit-target`): `position: relative` on the control plus
  `::before { content: ""; position: absolute; inset: -5px }` (34 + 2×5 = 44).
  Ship the utility once and co-apply the class — never re-inline the
  expansion per control.
- **Adjacent cluster** — controls that sit side by side (weekday selectors,
  action rows, d-pads) get **real 44px geometry or grid tracks**, because
  expanded rectangles must **never overlap**: an overlap makes taps land on
  the wrong control, which is worse than a small target.

Inline form controls at the `control` height (36px) reach the floor by the
same two routes. Effective rectangles and non-overlap are rendered-layout
facts: static lint verifies the authored patterns; only the browser-leg e2e
harness proves the geometry.

## Charts

Charts are responsive citizens of the card grid, never a page-overflow source:

- **Viewport-aware tick budget** — cap x-axis labels (`maxTicksLimit` ~4 at
  phone widths / ~8 at desktop) with `autoSkip` and **zero label rotation**
  (`maxRotation: 0`), recomputed in the chart's own resize hook — no reload.
- **The canvas never drives horizontal page overflow** — it sizes to its card.
- **Every colour-distinguished series carries a non-colour second channel**:
  border dash + point style + fill treatment (e.g. solid/circle/area,
  long-dash/diamond/area, dotted/triangle/no-fill), with the legend rendering
  point styles so the cue is learnable at a glance.
- **Tooltips remain the precise-value path**; axes give the overview.
- Series colours draw from the status/accent tokens — never new decorative
  colours.

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
- **One active tab at a time.** The active tab takes the accent-soft tint +
  `accent-text` and sits at `tabindex 0`; the others are `tabindex -1`, with
  `aria-selected` tracked so it is announced correctly.
- **Unselected tabs carry no boundary (1.4.11 exemption).** An unselected
  tab has no outline, border or fill, in the floating pill and in the wide
  layout's left rail alike. Its icon and text label identify it, which is
  WCAG 2.2 SC 1.4.11 Non-text Contrast's exception for text-identified
  controls, so the 3:1 component-boundary floor `control-border` meets for
  inputs does not apply. Only the selected tab is drawn, with the accent-soft
  tint + `accent-text`. Don't add a boundary to unselected tabs, and don't
  let a contrast check flag one for the lack of it (rationale:
  `docs/design-system.md`, Round 5).
- **Selection persists** across reloads and PWA relaunch via `localStorage`, so
  the app reopens on the tab you left.
- **The nav hides whenever a modal/overlay is open** (`body:has(dialog[open])`) so
  it never floats above a dialog.
- **Installed PWA: the app shell is the scroller, never the window.** Inside
  `@media (display-mode: standalone)` the content wrapper (`.app`) becomes a
  fixed-inset element scroller — `position: fixed; top/left/right: 0`, sized
  with the **large viewport unit** (`height: 100vh` fallback, then `100lvh`),
  `overflow-y: auto` — so all real scrolling happens inside `.app` and a
  momentum bounce can never move the visual viewport and drag the bar
  (home-automation#303 — this removes the pill-drift *cause*; #300 proved a
  measured JS transform "correction" is actively harmful in standalone, so the
  bar takes **no JS translate** there — CSS owns its position).
- **Tap targets ≥ 44px.** Tabs show an icon **and** a short label, never
  icon-only. The icon is a **Lucide** glyph (see Icons).
- **At most five primary destinations** (`nav-bar.maxTabs`), per HIG's
  five-tab ceiling and Material's three to five. Five is what keeps every
  label legible at 320px. A sixth tab forces 11px labels and ~53px tabs, and
  pushes the nav toward icon-only. A rarely used destination (Settings above
  all) is **not a tab**: it is a trailing action in the page header (a
  `button-surface` gear beside the theme toggle), one tap from every tab.
  An app with several such destinations may make the fifth tab "More"; it
  still counts toward the five.
- **Desktop / fine pointers** below `layout.wide` (1100px) may render the
  same tabs inline at the top of the 772px column. At 1100px and wider they
  become the **left rail** of the wide layout (Layout): a vertical stack of
  icon-over-label tabs. The behavior (single active tab, `aria-selected`,
  persistence) is unchanged; only the placement differs.
- **Focus is visible and identical everywhere.** One tokenized rule —
  `:focus-visible { outline: 2px solid {colors.accent}; outline-offset: 2px }` —
  covers *every* interactive element (button, input, switch, summary, tab)
  app-wide. Do not leave focus to the browser default: on Windows Chrome that
  default is themed off the OS accent, so an unauthored ring renders in the
  wrong color. A control overrides this only where it draws its own custom ring
  (via `:focus-visible` / `:focus-within` on itself), never by suppressing it.

## Components

**Four button tiers cover every action** — the vocabulary is fixed
(fleet-config#296 settled it after a fleet sweep found the same class names
meaning different buttons per app): `button-primary` (solid `accent-fill`) for the
one main action per view; `button-tint` (accent-soft fill, `accent-text`, soft
accent border) for secondary emphasis; `button-ghost` for quiet tertiary
actions — *ghost means transparent* on a hairline border; a tinted fill is a
*tint*, never a "ghost"; `button-surface` (subtle surface at the `control`
height) for toolbar, utility, and icon buttons. Every tier shares the **one
`button-disabled` recipe** (the flat `canvas-subtle` / `border` / `fg-muted`
trio — AA in both themes, home-automation#362), never opacity on a solid fill.
A destructive action may restate the tint recipe on `danger`
(`danger` border and shade, `danger-text` text); status colors never fill a non-status button. `card` for
every content group; `nav-bar` + `nav-tab` per the contract above. Inline form controls (`select`,
`input`) share the `control` height (36px) so they line up on a row, and draw
their boundary in `control-border`. The on/off
`switch` is the shadcn Switch — a compact track + sliding thumb, **no text
label** (state is read from thumb position + track color; `role="switch"` +
`aria-checked` carry it for assistive tech), one canonical size everywhere. Its
track is **green (`success`) when on** — green is the universal "on / active"
read, so it is the fleet default rather than the blue accent; a **state** toggle
(alarm armed, a destructive mode) may substitute another status color
(`danger` / `attention`) where that state carries its own meaning. Collapsible `details/summary`
headers (`disclosure`) left-align the icon + title with the chevron pinned right,
and follow one fixed structural contract so a vertical stack of collapsible
cards is pixel-identical whether open or closed: the **card's own `padding` is
zeroed** (it must not double up with the summary's padding — the root cause of
every past regression), the `summary` owns the closed-state box at
`disclosure.closedHeight` (52px) with `disclosure.summaryPadding` (`0 14px`), the
open state adds a `border-bottom: 1px solid {colors.border-muted}` on
`[open] > summary` as the only divider, and the open body content uses
`disclosure.bodyPadding` (`12px 14px 14px` for a plain content block; drop the
top value to `0` when the body's first child is a list whose own items already
carry top padding) so text isn't flush against the left edge while the right
edge keeps matching margin. **The `summary` holds the title, optional meta and
the chevron — nothing else interactive.** Pickers, icon buttons and toggles go
in the body as a toolbar row, because a near-miss on a control packed into the
52px header toggles the card instead. Prefer bundling all four rules into **one shared
modifier class** (e.g. `.card--collapsible`) applied to the card, rather than
hand-listing every card's selector across separate padding/height/divider rules
— that per-selector enumeration is exactly how the contract drifts per new card.
Reuse the **vendored** nav/UI snippets from `project-scaffolding` verbatim — do
not re-author them per app (the same model as `single_instance.py` /
`tray_lifecycle.ps1`).

### Component contracts

Beyond the nav and the switch/disclosure above, these are the recurring
composite components — each a fixed contract so the same pattern is
pixel-identical across apps. All dimensions reference the tokens above; none are
hand-picked per app.

- **page header** (`page-header`) — **the first element of every tab's pane**,
  so every tab opens the same way. It is one card row at `rows.md` (52px,
  `0 14px` inset): a leading `icons.size.title` glyph plus a bold title that
  **names the current tab**, one ellipsized context line (`body-sm`,
  `fg-muted`; e.g. "3 running", "Last run 06:00"), and **at most two trailing
  icon actions** at the 44px hit target: the theme toggle, and the Settings
  entry when Settings is not a tab. Tab-specific toolbars sit **under** the
  header, never in place of it. The vendored `home-head` component
  (`project-scaffolding` `_vendored/home-head/`) is this shape; reuse it
  verbatim on every pane, not just the home tab.
- **card** (`card`) — the base content group: `rounded.lg` (16px) surface at
  `spacing.md` padding on a hairline border. Its **header** is one row: a
  leading `icons.size.title` glyph + a bold title (`label`/`body` weight 700),
  optional muted meta (`body-sm`, `fg-muted`), and a right-pinned chevron *or*
  meta value. A card that is collapsible drops its own padding to `0` and
  delegates to the disclosure contract (above).
- **editor modal** (`modal`) — a native `<dialog>` for detail/rename/settings
  editing. **Header:** a `heading-lg` title on the left, a square `modal.closeSize`
  (34px) × button on the right (an `icons.size.title` glyph, `rounded.md`,
  muted). **Body:** stacked label/value rows, each `modal.rowPadding` (`12px 0`)
  on a `border-muted` top divider, the value control filling ≥55% of the row.
  **Footer:** exactly one full-width `button-primary`. Its **disabled** state
  must clear AA contrast in *both* themes (never the browser default, which
  drops sub-AA). The nav hides while it is open (`body:has(dialog[open])`), and
  on mobile the dialog is top-anchored so it never jumps on open/close.
  A `<form>` wrapper is **optional** — a JS-managed editor whose Save reads
  bare `input`/`select`/`textarea` fields is equally canonical; what makes a
  dialog an *editor* is the presence of editable fields, not the wrapper.
- **list-row** (`list-row`) — how a card renders a **repeating list** of entries
  (history, activity, request log): flat full-bleed rows, `list-row.rowPadding`
  (`12px 0`, shared vocabulary with the modal), separated by a 1px `divider`
  (`border-muted`) top hairline on every row after the first (`.row + .row`) —
  **never** nested `canvas-subtle` cards with their own border/radius per entry.
  Row internals (meta line, badges) are unconstrained by this rule; a row that
  carries actions follows `action-row` below. Reference impl: photo-ocr `styles.css` `.history-item` — first built as
  per-entry cards, rejected on-device as too heavy, rebuilt flat (photo-ocr#73).
  **Row heights** (list-row, action-rail, and other repeating-row selectors)
  draw from the `rows` 3-step scale (`rows.sm` 44px / `rows.md` 52px /
  `rows.lg` 60px — `disclosure.closedHeight` is `rows.md`) via `var(--row-*)`
  or a `calc()` derivation, never an ad hoc literal — consolidated from five
  prior ad hoc heights (30/40/44/52/60px) in app-launcher#365/PR#380.
- **action-row** (`action-row`) — a `list-row` whose entries *do* something
  (launch, run, open). It follows the native list pattern (iOS, Material,
  GitHub Mobile): **tapping the row performs its primary action**, and every
  other action sits behind **one trailing accessory**, a 44px kebab (or a
  chevron when the row only navigates). The row may add **at most one leading
  toggle** (e.g. favorite) and **at most one other visible action**, and only
  when that action is the row's dominant verb (Run on a job row), at real 44px
  geometry. **Title:** `body` at weight 600, one line, truncated with an
  ellipsis, never broken mid-word. **Context:** at most one `body-sm` line in
  `fg-muted`. Height comes from the `rows` scale (`rows.md` minimum). **No
  vertical rules** between controls inside a row; a list is not a
  spreadsheet. **Destructive actions** (Kill, Stop, Delete) live in the row
  menu as its last item, after a divider, in `danger-text`, behind a
  confirm. A row never shows a visible danger button. **Long lists:** a list
  that can exceed ~12 rows gets a filter field above it: 44px tall, a
  `control-border` boundary on `card`, `rounded.md`. Reference impl:
  app-launcher session rows (app-launcher#1025, tap the row + one kebab).
- **dense collection** — how a card renders **saved automation/settings
  items** (schedules, pairings, overrides): each item is a flat `list-row`
  **summary row** — a compact human-readable summary line + the entry's
  `switch` + an edit affordance — and Add/Edit opens a **staged editor
  modal** (the `modal` contract above). **Save is the only persistence
  boundary**: Escape, backdrop click, and the × all discard; focus returns to
  the opener on close; a destructive Delete is a labelled `danger` action in
  the dialog **body**, never a competing footer primary. The API/data shape
  stays independent of presentation — this is presentation-only composition.
  Reference impl: home-automation security schedules / scene pairings /
  overrides (home-automation#409), which replaced always-expanded inline
  forms with summary rows + dialogs and changed no backend payloads.
- **empty-state block** (`empty-state`) — for any list/grid that can legitimately
  render zero items: a centered column of a `icons.size.feature` (24px) muted
  glyph + a one-line reason (`body`, `fg-muted`) + an *optional* single action
  (`actionMinWidth` 96px). One canonical block — never a bare "—" or a silent
  empty container.
- **icon tile** (`icon-tile`) — the Home-screen rounded-square (`rounded.md`
  squircle) filled with **one** of the five `tile-*` colors (the only saturated
  surfaces), a centered `icons.size.feature` Lucide glyph in `accent-fg`. The
  fill signals category, not state; never use a `tile-*` color elsewhere.

## Base UI — model components on shadcn

Model every interactive component on its **shadcn/ui** counterpart
(<https://ui.shadcn.com/docs/components>). **These apps are vanilla HTML/CSS/JS,
not React**, so you do not install shadcn — you copy its *markup shape and
interaction semantics* (the element structure, ARIA roles/states, keyboard
behavior) by hand, then skin them with the fleet tokens above (colors, radii,
spacing, and the `control` / `switch` dimensions). Before hand-rolling any
control, read its shadcn component page and mirror that structure; don't
reinvent interaction semantics shadcn already gets right.
*Why shadcn, and its Radix / Base UI backends: `fleet-config/docs/design-system.md`.*

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

One icon set, fleet-wide: **Lucide** (<https://lucide.dev>) — plain **SVG** on a
**24×24** grid with a **2px** outline stroke, no React and no build step, no
web-font payload.
*Why Lucide, and the rejected alternative: `fleet-config/docs/design-system.md`.*

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

**App identity surfaces:** an installable fleet PWA derives its complete icon
family from one Lucide SVG master through
`project-scaffolding/scripts/brand_gen.py`. Commit the generated 180px Apple
touch icon, 192px + 512px regular manifest icons, separate 512px maskable icon,
and multi-size favicon; link the Apple touch icon and favicon from `index.html`.
Regular and maskable manifest entries are distinct — never declare one source as
both `any` and `maskable`, because the maskable safe-zone padding is deliberately
different. The stable monochrome brand tile does not switch with the app's light
or dark theme. A tray icon may add a state colour (recording, unhealthy, and so
on), but it keeps the same master silhouette and optical box rather than becoming
a second app identity. Generated raster assets are per-app outputs, not vendored
byte-for-byte components.

## Do's and Don'ts

- **Do** use the one blue accent for all interactive emphasis.
- **Do** set text that sits on a tint in the matching `*-text` token, fill the primary with `accent-fill`, and outline inputs and the switch off-track with `control-border`.
- **Do** model every interactive component on its shadcn component (structure + ARIA), then skin it with the fleet tokens.
- **Do** draw every icon from **Lucide** — the shadcn-native set — vendored through `project-scaffolding`.
- **Do** generate every installable app's Apple/PWA/favicon family from one Lucide master through `brand_gen`, with distinct regular and maskable assets.
- **Do** cap primary navigation at five tabs, put Settings in the page header, and open every tab with the one `page-header` (the vendored `home-head`).
- **Do** keep the bottom nav identical across apps — same radius, blur, and
  persistence behavior.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Do** give every interactive element the one tokenized `:focus-visible` ring — never leave focus to the browser default.
- **Do** color a switch's on-track green (`success`) — the universal on-state.
- **Do** size every glyph from the canonical `icons.size` steps (16 / 18 / 20 / 24 — `inline` / `title` / `nav-tab` / `feature`) — don't hand-pick a one-off size.
- **Do** honor `prefers-reduced-motion` — collapse authored animation to near-instant.
- **Do** ship the user-selectable theme: pre-paint `data-theme` boot script + persisted sun/moon toggle on the main view — never dark-only or OS-only.
- **Do** ship the persisted Small / Default / Large text-size setting (`<app>.textsize`, stamped pre-paint as `html[data-textsize]`) — it is what makes the zoom lock acceptable.
- **Do** render a repeating list of entries (history, activity log) as flat full-bleed rows on a hairline divider — never nested cards per entry.
- **Do** make a list row's primary action the row itself, with one trailing kebab for the rest and a filter field above any list that can exceed ~12 rows.
- **Do** pin every single-column stack grid's track to `minmax(0, 1fr)` — never a bare `1fr`/`auto`/implicit track behind a no-wrap or scrollable child.
- **Do** give every non-navigation pointer target a ≥44×44px *effective* hit area — invisible expansion for isolated compact controls, real geometry for adjacent clusters; expanded rectangles never overlap.
- **Do** preserve and label last-known data when a background refresh fails (`Last updated … · live data unavailable`) and disable freshness-sensitive actions — stale state is never actionable.
- **Do** pair every colour-distinguished chart series with a non-colour cue (dash / point style / fill) and a viewport-aware tick budget.
- **Don't** hand-roll a primitive (switch, select, dialog, tabs…) that shadcn already defines.
- **Don't** show a destructive action as a visible per-row button, split a row into icon columns with vertical rules, or put controls inside a disclosure `summary`.
- **Don't** mix a second icon set or hand-draw a one-off glyph — use the matching Lucide icon.
- **Don't** declare one manifest icon as both `any` and `maskable`, or redraw the app identity independently for the tray.
- **Don't** set a line people read in `caption`, or uppercase anything but an `overline` group header — secondary lines are `body-sm`, and numbers and units are never transformed.
- **Don't** put a solid accent fill on any button except the view's primary action — secondary emphasis is the tint, never a second solid.
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** stretch a single column full-bleed on desktop — keep the centered 772px measure below 1100px, and use the left rail + master-detail layout above it. A multi-column board is the one exception.
- **Don't** use status colors decoratively — they signal state only.
- **Don't** put raw infrastructure detail (hostnames, URLs, exception text) in user-facing failure copy — sanitize it; logs keep the detail.
- **Don't** apply this spec to Streamlit POC spikes.
