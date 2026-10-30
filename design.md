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
  button-surface:  { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}", rounded: "{rounded.md}", height: "{components.control.height}" }   # LABELLED utility/toolbar button at the control height — a glyph-only control is the icon-button, never this tier
  button-disabled: { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", textColor: "{colors.fg-muted}" }   # ONE disabled recipe for every tier, both themes (home-automation#362) — never opacity on a solid fill
  icon-button:    { backgroundColor: transparent, borderColor: transparent, shadow: none, textColor: "{colors.fg-muted}", hoverColor: "{colors.fg}", pressedColor: "{colors.accent-text}", glyph: "{icons.size.inline}", target: "{components.hit-target.min}" }   # a glyph on nothing (fleet-config#1259) — no fill, border or shadow at rest; hover/press move the glyph colour only; the 44px target is invisible
  control:        { height: 36px, rounded: "{rounded.md}", backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.control-border}", textColor: "{colors.fg}" }   # shared height for inline select / input so a row of controls lines up
  switch:         { width: 44px, height: 26px, rounded: "{rounded.pill}", thumbSize: 20px, trackOff: "{colors.control-border}", trackOn: "{colors.accent-fill}", thumbColor: "{colors.accent-fg}" }   # shadcn Switch — no text label; on = the app's accent (fleet-config#1200), never green
  nav-bar:        { backgroundColor: "{colors.card}", rounded: "{rounded.nav}", height: 61px, margin: 21px, maxTabs: 5 }
  nav-tab:        { textColor: "{colors.fg-muted}", rounded: "{rounded.pill}", height: 53px }
  nav-tab-active: { backgroundColor: "{colors.accent-soft}", borderColor: "{colors.accent-border-soft}", textColor: "{colors.accent-text}" }   # accent-soft tint, not canvas-subtle — the inset surface reads as a black hole in dark mode (project-scaffolding#159)
  chip:           { backgroundColor: "{colors.neutral-soft}", textColor: "{colors.fg}", rounded: "{rounded.pill}", padding: "2px {spacing.sm}", typography: "{typography.caption}" }   # neutral chip / filter pill — never canvas-subtle inside a card (the dark black hole again)
  reference-pill: { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent-text}", borderColor: "{colors.accent-border-soft}", rounded: "{rounded.pill}", padding: "{components.chip.padding}", typography: "{components.chip.typography}" }   # a link to something openable (folder, AI conversation, issue, URL) — the chip geometry, never resized into a control box (fleet-config#1259)
  code-inline:    { backgroundColor: "{colors.neutral-soft}", textColor: "{colors.fg}", rounded: 6px, padding: "1px 6px", fontFamily: mono, fontSize: 0.875em }   # inline code in rendered prose (a reply, a doc): mono at 0.875em of the text around it, never caption-sized; never canvas-subtle inside a card (the dark black hole)
  file-chip:      { backgroundColor: "{colors.accent-soft}", textColor: "{colors.accent-text}", borderColor: "{colors.accent-border-soft}", rounded: "{components.code-inline.rounded}", padding: "0 5px", fontFamily: mono, fontSize: "{components.code-inline.fontSize}", glyph: file }   # code-inline that opens something (a file's diff): the reference-pill colours on the code chip's geometry; the 1px hairline replaces 1px of padding so both chips sit the same height in a line
  code-listing:   { backgroundColor: "{colors.card}", borderColor: "{colors.border}", rounded: "{rounded.md}", fontFamily: mono, fontSize: 13px, lineHeight: 1.5, header: "{typography.caption}", headerColor: "{colors.fg-muted}", headerMinHeight: 36px }   # a fenced block or a diff: the card surface on a hairline, a small header (language, copy); 13px is the one sanctioned off-scale size, for mono listings only
  chat-turn:      { promptFill: "{colors.accent-soft}", promptBorder: "{colors.accent-border-soft}", promptText: "{colors.fg}", promptMaxWidth: "min(85%, 560px)", promptRounded: "{rounded.lg} {rounded.lg} 4px {rounded.lg}", headerMinHeight: "{rows.sm}", stepLineMinHeight: "{rows.sm}", stepLine: "{typography.body-sm}", stepLineColor: "{colors.fg-muted}", partsGap: 12px }   # one exchange with an agent: prompt bubble, then the agent's turn as one block on the canvas (no card)
  disclosure:     { align: left, chevron: right, closedHeight: 52px, summaryPadding: "0 14px", bodyPadding: "12px 14px 14px" }   # collapsible details/summary header — the summary owns height+padding; the card's own padding is zeroed so cards align when closed
  modal:          { rounded: "{rounded.lg}", closeSize: 34px, rowPadding: "12px 0", primaryButton: "{components.button-primary}" }   # editor <dialog> — heading-lg title + × close, label/value rows on a top-border divider, one full-width primary
  list-row:       { rowPadding: "{components.modal.rowPadding}", divider: "{colors.border-muted}" }   # repeating entries inside a card — flat full-bleed rows on a top hairline, never nested canvas-subtle cards (photo-ocr .history-item, post-photo-ocr#73)
  action-row:     { minHeight: "{rows.md}", title: "{typography.body}", titleWeight: 600, meta: "{typography.body-sm}", metaColor: "{colors.fg-muted}", leadingToggles: 1, trailingAccessories: 1, extraVisibleActions: 1, accessorySize: "{components.hit-target.min}", destructiveColor: "{colors.danger-text}", filterAboveRows: 12, filterHeight: 44px, filterBorder: "{colors.control-border}" }   # a list row that does something: tap the row = primary action, one trailing kebab/chevron, destructive only in the menu
  empty-state:    { iconSize: "{icons.size.feature}", gap: "{spacing.sm}", padding: "{spacing.xl} {spacing.md}", actionMinWidth: 96px, textColor: "{colors.fg-muted}" }   # icon + one-line reason + optional action, centered
  icon-tile:      { rounded: "{rounded.md}", iconSize: "{icons.size.feature}", iconColor: "{colors.accent-fg}" }   # Home-screen rounded-square — one tile-* fill, centered Lucide glyph
  page-header:    { minHeight: "{rows.md}", padding: "0 14px", title: "{typography.body}", titleWeight: 700, context: "{typography.body-sm}", contextColor: "{colors.fg-muted}", trailingActions: 2, statusReadout: 1, actionSize: "{components.hit-target.min}" }   # every pane's first element — the vendored home-head: tab title, one context line, trailing theme toggle + Settings gear, always both, on every tab, plus at most one status readout
  toast:          { backgroundColor: "{colors.card}", textColor: "{colors.fg}", borderColor: "{colors.border}", rounded: "{rounded.md}", padding: "12px 18px", typography: "{typography.label}", fontWeight: 700, maxWidth: 560px, offsetAboveNav: 8px }   # neutral frosted toast (fleet-config#1200) — the nav-bar glass, centred above the nav; only a real error tints (danger), success never does
  hit-target:     { min: 44px }   # minimum effective pointer-target square, app-wide — see Touch targets
  avatar:         { size: 36px, rounded: "{rounded.md}", backgroundColor: "{colors.neutral-soft}", glyph: "{icons.size.nav-tab}", badgeSize: 12px, badgeRing: "{colors.card}", badgeAlive: "{colors.success}", badgeDown: "{colors.danger}" }   # a row's leading squircle; the corner badge means alive (up) or down (should be up), nothing else (fleet-config#1332)
  meter:          { height: 6px, rounded: "{rounded.pill}", track: "{colors.neutral-soft}", fillUnderPace: "{colors.accent}", fillAheadOfPace: "{colors.attention}", fillCritical: "{colors.danger}", criticalAt: 90%, paceTick: 2px, allocationAttentionAt: 75% }   # a usage bar coloured by pace, not raw percent (fleet-config#1332); an allocation bar by headroom (fleet-config#1386)
  segmented:      { height: "{components.control.height}", target: 44px, inset: 3px, rounded: "{rounded.md}", track: "{colors.neutral-soft}", selectedFill: "{colors.card}", label: "{colors.fg}" }   # one choice among 2–5 peers; the raised selected segment IS the state, never a colour (fleet-config#1334)
  history-mark:   { size: 6px, success: "{colors.fg-muted}", failure: "{colors.danger}", inFlight: "{colors.accent}", unconfirmed: "{colors.attention}", deferred: hollow, unknown: dash, dashHeight: 2px }   # a row's run/outcome strip: the healthy mark muted, colour only for the exception, unknown a dash (fleet-config#1392)
  row-swipe:      { deadZone: 8px, axisRatio: 1.5, edgeGuard: 24px, paneWidth: 88px, restOpenAt: 44px, commitAt: 55%, flickVelocity: 0.5px/ms, flickDistance: 30%, leading: "{colors.accent}" }   # one swipe action per side, also the row menu's first item (fleet-config#1392)
  row-menu:       { backgroundColor: "{colors.canvas-subtle}", borderColor: "{colors.border}", rounded: "{rounded.md}", itemRounded: "{rounded.sm}", padding: "{spacing.xs}", minWidth: 200px, itemHeight: "{rows.sm}", itemTypography: "{typography.body}", itemWeight: 500, glyph: "{icons.size.inline}", textColor: "{colors.fg}", disabledColor: "{colors.fg-muted}", destructiveColor: "{colors.danger-text}", headerTypography: "{typography.body-sm}", headerColor: "{colors.fg}", viewportInset: "{spacing.sm}", aboveNavGap: "{spacing.sm}", anchorGap: 2px, zIndex: 200 }   # the kebab's floating action list (fleet-config#1393) — one raised surface a step above the card it opens over, 44px rows, danger last; the surface is the one value that differs in dark
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
  on it falls under AA. A **reference pill** (a chip that links somewhere)
  is `accent-text` on `accent-soft`, the `button-tint` pairing.

**The light palette, in full.** Nothing outside this list exists; reuse a value
before proposing a new one (`design.dark.md` lists the dark twin).

- **Greys, one per role** (8 solids + 1 mix): `canvas` `#ffffff` page;
  `canvas-subtle` `#f6f8fa` the inset and the **control fill** (text input,
  select, textarea, composer, disabled button); `card` `#ffffff` every
  elevated surface; `border-muted` `#d8dee4` the divider; `border` `#d1d9e0`
  the card hairline; `control-border` `#818b98` a field's boundary and the
  switch off-track; `fg-muted` `#656d76` secondary text, placeholder and
  disabled text; `fg` `#1f2328` primary text. `neutral-soft` is the one mix
  (`fg-muted` at 16%), the chip and filter-pill fill.
- **Blues, one per role** (3 solids): `accent` `#0969da` links, focus ring and
  icons; `accent-fill` `#0969da` the solid primary fill (also `tile-blue`);
  `accent-text` `#0550ae` text on an accent tint. `accent-soft`,
  `accent-border-soft` and `accent-border-strong` are mixes of `accent`.

Measured on the control fill `#f6f8fa`: `fg` 14.84:1, `fg-muted` 4.93:1 (both
clear 4.5), `control-border` 3.24:1 (clears WCAG 1.4.11's 3:1).

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
  `fg-muted` at regular weight, never italic, and at most one line under its
  group, shown only while it explains something on screen; a permanent note
  shown on every visit becomes noise (fleet-config#1332). A **footnote** (how
  a value is computed, what a section means, a standing caveat) goes behind
  an ⓘ `icon-button` beside the group's title that opens it in a sheet; only
  a **live exception** (this value is stale, this host is unreachable) stays
  visible as a line (fleet-config#1386).
- **`caption` (12px) is for chips, badges and timestamps only**, never for a
  line people read.
- **Code is mono at two sizes.** Inline code is the `code-inline` chip at
  0.875em of the text around it (14px in body), never `caption`: a 12px chip
  reads as a timestamp, not as code. A listing (a fenced block, a diff) is
  13px / 1.5 on the `code-listing` frame. 13px is the one sanctioned size off
  the 12 / 14 / 16 scale, and only for mono listings: mono glyphs are wider
  than the system face, so 14px fits too few columns on a 390px phone and
  12px is too small to read a diff on it. Use the stack `ui-monospace,
  SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace`. Both sizes
  and the dark fill are measured as `COMP-17` (fleet-config#1342).
- **`label`** carries field and control labels and button text;
  **`heading-md`** titles a section or group inside a view. A heading is never
  rendered smaller than the body text it introduces (measured as `TYPE-07`,
  fleet-config#1341).
- **Caps are legal in one place.** `text-transform: uppercase` goes through
  the `overline` role only, and only for list group headers (e.g. date
  groups). Chips and badges are sentence case. Numbers and units are never
  transformed: "3M 33S" reads as mega, not minutes. Everywhere else, use
  weight, not case, for hierarchy. The rule is about how the label reads,
  not how it is made: a button, tab or heading whose characters are already
  typed in capitals breaks it the same as `text-transform` does, and only an
  acronym (an API, a PDF, a language chip such as ES) is exempt (measured as
  `TYPE-05`, fleet-config#1365). And each kind of label (buttons, tabs, menu
  items, headings, stat labels) is cased one way across the whole app, sentence
  case throughout or title case throughout, never one tab's counters
  capitalised and another's lowercase (measured as `TYPE-06`).

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
- **Width follows the shape of the view, per view, never per app.** A view
  whose content is one-dimensional (prose, a form, settings, a single list, a
  card list, a detail pane) keeps the centred measure. A view whose content is
  two-dimensional, where the columns themselves carry the meaning, may span
  the full width available to content at any desktop width: board lanes
  (kanban), a data table with many fields, a tree or grid whose columns are
  attributes, calendar days or a timeline. A full-width view's page header and
  toolbar span with it, so one view never mixes two measures, and a
  full-width app's one-dimensional views (settings, a form, a detail) still
  keep the measure. An app declares its wide views in its `.fleet.toml`
  (`[design] wide_views = ["board", "table"]`, the tab ids); an undeclared
  view keeps the measure. A master-detail inside one pane is
  two-dimensional too: a month with its day pane beside it, both inside the
  same tab's pane. LAYOUT-06 sees it without a declaration, as it sees a
  detail pane docked outside the pane: a pane holding two regions side by
  side, each at least a fifth of the window wide and half of it tall, is not
  read as a stretched column (fleet-config#1361). Declaring it in
  `wide_views` stays right, and a declared view still has to fill the window.

Below 1100px, or on a coarse pointer at any width, nothing changes: the
772px measure and the tab placement described in the Navigation contract
apply.

A
single **`spacing.gutter` (12px)** sets every gap — between cards/tiles *and* from
the page edges — so the spacing reads uniform in every direction.
**Reserve bottom padding for the whole bar** (its height, the margin above and
below it, the gutter and the safe-area inset: 115px plus the inset) so the
fixed bar never covers the last row.
On a phone, the `.app` top padding is the safe area plus the 4px top fade, with
no gutter (a Navigation contract rule, below).
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
The one transient exception is the `toast`, which reuses the nav-bar's glass
treatment so the two floating layers read as one family.

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
user-initiated command progress/results, rendered as the one neutral frosted
`toast`.** Announce state changes through a
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
  bottom (never the content bottom — that is the iOS-PWA footgun), `rounded.nav`
  corners, an equal-width grid of tabs. The bar stands **61px** tall and sits
  with **equal 21px margins on left, right, and bottom** so it reads
  centered and breathes evenly. In a browser tab it is a plain fixed element
  with `bottom` set; in the installed app it is placed from the top instead
  (see "Installed PWA", below).
- **The bar is clear glass.** A translucent tint (`--tabbar-bg`: light
  `rgba(255,255,255,0.62)`, dark `rgba(42,45,52,0.55)`) under a heavy backdrop
  blur (`blur(24px) saturate(180%)`, on this small capsule only, never on a
  full-screen layer), a hairline rim all round (`--tabbar-border`: light
  `rgba(0,0,0,0.08)`, dark `rgba(255,255,255,0.14)`), and a soft drop shadow
  (`--tabbar-shadow`: light `0 8px 28px -6px rgba(20,24,40,0.2), 0 1px 4px
  rgba(20,24,40,0.06)`, dark `0 10px 30px -6px rgba(0,0,0,0.6)`). The active
  tab sits on a capsule (`--tabbar-pill`: light `rgba(0,0,0,0.07)`, dark
  `rgba(255,255,255,0.14)`) that springs to the tapped tab; the capsule token
  is required, because with it undefined the active tab has no capsule. The
  active tab's icon and label take `accent-text`.
- **On a phone, the `.app` top padding is the safe area plus the 4px top
  fade.** The vendored nav's phone rule (`@media (pointer: coarse) and
  (max-width: 520px)`) sets `padding-top: calc(env(safe-area-inset-top, 0px) +
  4px)`, with no gutter, so the first card starts directly under the status bar
  in every app. The sides keep their padding and the bottom holds the bar's
  reserve. The vendored nav provides the rule, so a re-vendored app carries no
  override of its own.
- **One active tab at a time.** The active tab takes `accent-text`, over the
  capsule on the phone pill and the accent-soft tint on the desktop control and
  the rail, and sits at `tabindex 0`; the others are `tabindex -1`, with
  `aria-selected` tracked so it is announced correctly. Inactive tabs on the
  phone pill are in full `fg`, not muted, for the iOS system bar's contrast.
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
- **Installed PWA: the page head starts the app at its final viewport.** The
  head sets `<meta name="apple-mobile-web-app-status-bar-style"
  content="default">`, `viewport-fit=cover` in the viewport meta, and the dual
  `theme-color` metas that paint the status bar in the page colour. With
  `default`, iOS gives the installed app its final viewport from the first
  frame, so the bar is in place on a cold launch. `black-translucent` is the
  wrong value: it lets the viewport grow after launch, the bar rides that
  growth, and the status bar needs an app-drawn scrim to hide content behind
  it. Desktop engines don't reproduce the installed app, so the app's e2e
  smoke test asserts the status-bar style and `viewport-fit=cover`.
- **Installed PWA: the window is the scroller and the bar is placed from the
  top.** There is no fixed-inset `.app` scroller, no element-scroller shell and
  no JS pinning. The document is always 1px taller than the viewport (`html`
  gets `min-height: calc(100dvh + 1px)` on the phone pill), because an
  installed app shrinks its viewport whenever the document cannot scroll and
  grows it back over a few seconds; the extra pixel keeps every tab, short ones
  included, at the same offset. In the browser tab the bar is a plain fixed
  element with `bottom` set. In `@media (display-mode: standalone)` it is
  placed with `top: calc(100lvh - margin - height)` and `bottom: auto`, so it
  never rides the viewport growth after launch. The bar takes no JS translate
  and never moves on a flick or an overscroll. An app-side `scroll-lock.js`
  that pins the body under a `<dialog>` is the plain pin, with no standalone
  branch and no `scroll-lock:*` events.
- **Scroll-edge fades.** Rows scroll under the bar and under the status bar,
  so two inert layers (`z-index: 110`, `pointer-events: none`, above `.app`
  and below the bar) dim them in, painted in `canvas` (`--bg`): at the bottom,
  transparent from 16px above the bar's top edge to full `canvas` from the
  bar's bottom edge to the screen edge; at the top, the opaque `default`
  status bar is the solid part and the layer draws only an eased 4px tail
  below it, inside the `.app` top padding, so the first card at scroll-top
  starts where the fade ends and is never dimmed.
  `prefers-reduced-transparency: reduce` drops both. CSS only, no transform
  on the bar; the desktop control and the rail carry no layer. They are the
  nav's own `body::before` and `body::after`, so apps must not style either.
- **Tap targets ≥ 44px.** Tabs show an icon **and** a short label, never
  icon-only. The icon is a **Lucide** glyph (see Icons).
- **At most five primary destinations** (`nav-bar.maxTabs`), per HIG's
  five-tab ceiling and Material's three to five. Five is what keeps every
  label legible at 320px. A sixth tab forces 11px labels and ~53px tabs, and
  pushes the nav toward icon-only. **Settings is never a tab**, at any nav
  size and in any app, however few tabs the app has: it is always the trailing
  gear in the page header (an `icon-button` gear beside the theme toggle),
  on every tab and one tap from each. A gear-only tab, or a "More" tab that
  holds only Settings, is the same violation. The fleet's signature header is
  the title on the left and `[theme toggle] [Settings gear]` on the right
  (fleet-config#1200). Any other rarely used destination may share a fifth
  "More" tab; it still counts toward the five.
- **Desktop / fine pointers** below `layout.wide` (1100px) may render the
  same tabs inline at the top of the 772px column. At 1100px and wider they
  become the **left rail** of the wide layout (Layout): a vertical stack of
  icon-over-label tabs. The behavior (single active tab, `aria-selected`,
  persistence) is unchanged; only the placement differs.
- **A tab may carry a count badge** when something on another tab needs the
  user (a pending queue). It is `rounded.pill`, `caption` size, 16px minimum
  height in px, `card` text on a fill of one of two tones, on the icon's
  top-right corner in the pill, the inline tabs and the rail. `attention`
  (the default) is trouble that needs a look, a to-do; `danger` is an active
  alarm or failure. The caller picks it with `setBadge(tab, count, noun,
  tone)`; both clear 4.5:1 with `card` text (`danger` measures 5.36:1 light
  and 5.16:1 dark, project-scaffolding#342). 1–9
  show the digit, 10 or more show "9+"; 0 removes it. It is never animated,
  and the tab's accessible name carries the count ("Board, 2 waiting"). Use
  the vendored `setBadge`, never a per-app badge (project-scaffolding#338).
  **The count covers every kind of waiting item the tab owns** (questions
  waiting for an answer plus items waiting for the user's turn, not one
  kind alone), and **its feed runs on every tab**: while the owning tab is
  up, the badge reads that tab's own payload; on every other tab it reads a
  poll that already runs everywhere and carries the counts, so the owning
  tab's endpoint is never polled off it. A part whose count cannot be read
  adds nothing: the badge counts what it could read, never a made-up zero
  (app-launcher#1502, owner decision 1 on #1500).
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
height) for labelled toolbar and utility buttons. A glyph-only control is
none of the four: it is the **icon button** (Component contracts), which
draws no surface at all. Every tier shares the **one
`button-disabled` recipe** (the flat control fill / `border` / `fg-muted`
trio, home-automation#362): `canvas-subtle` in light (`fg-muted` 4.93:1), the
`border-muted` value in dark (4.08:1, see `design.dark.md`; disabled text is
exempt from WCAG 1.4.3). Never opacity on a solid fill.
A destructive action **in a dialog or a menu** takes the **outlined
recipe**, the final Delete of a confirm dialog included (transparent on a
hairline, `danger-text` text and a `danger` border, never a fill or tint;
measured as `COMP-08`, fleet-config#1403). A list row shows no button tier at
all: its verbs are plain icon buttons (action-row, fleet-config#1420). Status
colors never fill a non-status button. `card` for
every content group; `nav-bar` + `nav-tab` per the contract above. Inline form controls (`select`,
`input`) share the `control` height (36px) so they line up on a row, and draw
their boundary in `control-border`. The on/off
`switch` is the shadcn Switch — a compact track + sliding thumb, **no text
label** (state is read from thumb position + track color; `role="switch"` +
`aria-checked` carry it for assistive tech), one canonical size everywhere. Its
track is the **accent (`accent-fill`) when on**, so a switch sits in the same
palette as the buttons and tabs beside it; green reads as off-palette next to
the rest of the app (fleet-config#1200 reversed the earlier green-on decision,
rationale in `docs/design-system.md`). A **state** toggle whose on-state is
itself an exception (a destructive mode) may substitute a status color
(`danger` / `attention`) where that state carries its own meaning; a normal
chosen mode (alarm armed, heating) stays accent, since modes are not alarms
(status chip). `success` is never a switch's on-colour.
Collapsible `details/summary`
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
  icon buttons** at the 44px hit target: the theme toggle and the Settings
  gear, **always both, on every tab at every nav size** (Settings is never a
  tab, see Navigation), and **never a third icon button**: a header button that opens a
  pane of its own hides a tab (fleet-config#1386). The one allowed addition is
  **one optional status readout** (`statusReadout`, fleet-config#1404, owner
  decision on app-launcher#1520), placed before the toggle: a short live value
  (a CPU %, a queue depth) in `body-sm`, `fg-muted` at rest, with colour only
  for a warning or critical state (the status tone's `*-text`, never colour
  alone: the value is the text). It is a button at the 44px hit target that
  **opens a sheet** on tap; it is never a navigation target, never a second
  Settings entry, and never a second readout. It **never hides**, on a phone
  too: when the header is tight, the context line's warning switches to a
  compact form of its parts (shorter words, never an ellipsis) and the
  readout stays. An occasional tool the readout's sheet leads to
  (app-launcher's Screen view) opens **from that sheet**, never as another
  header icon. Tab-specific toolbars sit **under** the
  header, never in place of it. The vendored `home-head` component
  (`project-scaffolding` `_vendored/home-head/`) is this shape; reuse it
  verbatim on every pane, not just the home tab. The title is the tab's
  name on the home tab too, **never the app's name** (the app's name belongs
  to the manifest and the install; parking-manager#114, the judge's J-04; measured as `NAV-04`, fleet-config#1360).
  **The context line is live
  and names only exceptions**: what needs the user, each part in its tone's
  `*-text` ("1 needs you · 1 failing", the status chip tone map), at most
  two parts so no ellipsis cuts the part that matters at 390px (~22
  characters), falling back to the plain count in `fg-muted` ("7 jobs") when
  nothing is wrong. Never a fixed slogan, and never a count another tab owns
  (fleet-config#1332). The measure reads the line in every state the walk
  captures of a tab and fails one fixed sentence that carries no digit and no
  build id and sits unwired in the served markup (`NAV-05`, fleet-config#1395);
  a tab with fewer than two readings, or a constant line a script wrote, is
  reported as not measured. A pane with **no count of its own** (Settings) shows
  the running build when nothing is wrong ("Build a1b2c3d", its short id),
  so a stale installed app shows from the header, and names only what the
  pane itself shows as a problem ("Channels: 1 missing" in attention, an
  unreadable source neutral), on the same two-part budget
  (app-launcher#1506, owner decision 10 on #1500). The line is computed from the **same counts the list
  below draws**, so the header and the list can never disagree
  (task-os#393). A **dashboard tab** (one whose job is "is everything
  OK?") may instead carry one plain live fact as its quiet line when nothing
  is wrong, in `fg-muted` (home-automation's Home: the weather now and
  today's range). It is still live, still at most ~22 characters, and still
  yields to an exception. A **no-op mode** (practice or dry run, paused: the
  whole app does nothing until it is changed) is an `attention` part of the
  **home tab's** header line, and the home tab also carries a tinted banner
  that explains it and, for paused, its one verb (Turn on). The control
  itself stays in Settings, its one home. A mode seen only in Settings leaves
  the user trusting an app that is doing nothing (parking-manager#114,
  owner decisions 2 and 7 on parking-manager#112). **The line's parts come
  in severity order** (offline or failed first, then a transient state such
  as an open passkey enrollment window, then a stale loaded shell, "New build,
  reload"), and a part never claims more than it knows: a failed status poll
  reads "Couldn't check services", never "up", and a count over a paged list
  counts the loaded rows only, so it is empty in search mode (voice-transcriber
  PR 273, photo-ocr PR 201, whatsapp-radar PR 383). The line's slot exists
  from the first paint, so no value arriving later moves the row. A mode
  line must still fit: "Incognito: this take won't be saved" ellipsized on a
  phone and became "Incognito: not saved" (voice-transcriber#275).
- **card** (`card`) — the base content group: `rounded.lg` (16px) surface at
  `spacing.md` padding on a hairline border. Its **header** is one row: a
  leading `icons.size.title` glyph + a bold title (`label`/`body` weight 700),
  optional muted meta (`body-sm`, `fg-muted`), and a right-pinned chevron *or*
  meta value. A card that is collapsible drops its own padding to `0` and
  delegates to the disclosure contract (above).
- **editor modal** (`modal`) — a native `<dialog>` for detail/rename/settings
  editing. **Header:** a `heading-lg` title on the left, a × `icon-button`
  on the right whose invisible box is `modal.closeSize` (34px), an
  `icons.size.title` glyph in `fg-muted` on no fill. **Body:** stacked label/value rows, each `modal.rowPadding` (`12px 0`)
  on a `border-muted` top divider, the value control filling ≥55% of the row.
  **Footer:** exactly one full-width `button-primary`, the most prominent control
  in the dialog: no other footer control is filled or larger than Save, and a
  destructive Delete sits in the body (measured as `COMP-16`; fleet-config#1339). Its **disabled** state
  must clear AA contrast in *both* themes (never the browser default, which
  drops sub-AA). The nav hides while it is open (`body:has(dialog[open])`), and
  on mobile the dialog is top-anchored so it never jumps on open/close.
  A `<form>` wrapper is **optional** — a JS-managed editor whose Save reads
  bare `input`/`select`/`textarea` fields is equally canonical; what makes a
  dialog an *editor* is the presence of editable fields, not the wrapper.
  The same dialog is the **detail sheet**: a glance card's or a row's detail
  (usage, a job's runs, other listeners) opens full-height with the × and
  its one primary, and becomes the detail pane at `layout.wide`. There is
  **one detail sheet per kind of thing**, opened from every row on every tab
  that shows that thing, never a per-tab editor or a global Edit mode
  (grocery-shopping-automation#255). It
  is also the **confirm and rename dialog**: never the browser's own
  `confirm()`, `prompt()` or `alert()`, which ignore the theme, the type
  scale and the button tiers (fleet-config#1332). **A take or record
  sheet** reads the thing whole (the full text in its own scrolling box,
  reserved before the text arrives), puts one full-width Copy first, and
  ends with Delete as plain `danger-text` with no fill, never the one filled
  control (a filled or largest destructive control in a dialog or menu is
  measured as `COMP-08`, fleet-config#1364). Delete is confirmed by a second tap ("Tap again to delete",
  disarmed after 4 s or on close) or by a confirm sheet (voice-transcriber
  #261, photo-ocr#196) whose Delete is outlined too, never tinted. **A confirm stacks over the sheet that asked**:
  every open builds its own `<dialog>`, and a sheet has no pinned footer,
  so nothing overlaps on a phone (photo-ocr#193). **At `layout.wide` the
  same card is the detail pane**, moved between the dialog and the pane on
  a `matchMedia` change so one renderer and one set of handlers serve
  both. A non-modal `<dialog>` cannot host the pane, because the nav hides
  whenever any dialog is open (voice-transcriber#266).
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
  chevron when the row only navigates). The row has **exactly one leading
  slot**, either an `avatar`, the one leading toggle (e.g. favorite, or a
  done check that a second tap undoes, grocery-shopping-automation#254), a
  tabular time value, or, in a **date-keyed row**, a **date tile**, and **at most one other visible trailing item**: a
  value (a `meter`, a sparkline), or the row's dominant verb. **Every
  visible verb is a plain icon button** (`icon-button`, owner decision
  2026-10-14, fleet-config#1420, which supersedes the row clause of
  fleet-config#1386): glyph only, like the kebab, at the real 44px square, with
  no fill, border or shadow at rest. Hover and press change the glyph colour
  and nothing else; the accessible name and the tooltip carry the verb, never a
  painted label. Stop is no exception: the outlined Stop is retired, and a verb
  that halts a process is the same unpainted square as one that starts it. One
  glyph per verb, so a verb looks the same in every app: Live `i-video`, Wake
  `i-power`, Start and Run `i-play`, Stop `i-square`, Find `i-search`, Open the
  arrow-out `i-external-link`, Launch `i-rocket`, Attach `i-paperclip`, About `i-info`, Dictate `i-mic`,
  Move the calendar, Complete the check (the done-check lead: the circle until done, then the circle-check) (`COMP-09` reads this map from
  its `verb_glyphs` constant: a control named for one of these verbs and drawn with
  its glyph is a glyph a reader knows; fleet-config#1428, #1431). **Delete and Remove are never a visible verb on
  a row**, with no exception: a sheet's editing list (a quick-add list) is a list
  of rows too. They sit last in the row menu after a divider (see Destructive
  actions below) or behind an explicit edit mode (owner decision 2026-10-14,
  fleet-config#1420; measured as `COMP-15`). A verb whose glyph a reader would not know
  (`COMP-09`) goes into the row menu, or becomes the row tap, instead of
  growing a painted label. Text-label buttons (`button-primary`, `button-tint`,
  `button-ghost`, `button-surface`) are for page and section actions and
  dialogs, not rows. The verb is the action the owner uses most, read from the
  app's own data (task-os#392: most edits were re-dates). A **verb cluster**
  (Up · Stop · Down on a blind, Live · Wake) is an `icon-button-group` of such
  buttons, with no track and no fill, each at real 44px geometry with the
  group's gap at 0; it counts as that one trailing item and sits outside the
  row's tap target so a press never opens the sheet. A choice with a selected
  state (an AC mode) is a `segmented` control and keeps its track; verbs that
  only fire are never one. A painted or bordered verb inside a list row is
  measured as `COMP-14`, a glyph-only button in general as `COMP-05`. A **count chip** is not a verb: a button whose whole visible label is a plus and a number ("+3", "+3 more") discloses more of the same row and acts on nothing, so `COMP-14` skips it and it may keep its tag-like pill (fleet-config#1429, owner decision 2026-10-14). The exemption is the label, never the paint or the position: a painted "Archive", "+ Add" or any other word on the same row still fails.
  The **date tile** is the weekday over the day number in a 44px square
  (the floor), today tinted `accent-soft`, all text at least 14px; the
  title then says what the day *is* ("Booked", "Holiday") and the one meta
  line gives the reason (parking-manager#113, #114). **Title:** `body` at weight
  600, one line, truncated with an ellipsis, never broken mid-word. **One
  line on every width**, a kanban lane card included: the full title lives
  in the detail view or drawer and in the `title` attribute, and a
  two-line clamp counts as a wrap under COMP-07 (task-os#396; owner
  decision on fleet-config#1350, 2026-10-11).
  **Context:** at most one `body-sm` line in `fg-muted`, which a status chip
  may close, for an exception only. A second block of text under the title is
  measured as `COMP-13` (fleet-config#1338): it counts text blocks, so one
  meta line that wraps is one, and chips, the trailing accessory and a leading
  tile or avatar are not lines. Its order is the date, then exception
  chips, then one context name, then at most two counts; at 390px only the
  context name may ellipsize, and links and people move to the menu and the
  detail sheet (task-os#392). **A source names the sending app, never the
  transport**: where a row says where an entry came from, it is plain text in
  that line and appears only when it names an app. A bare transport label
  (`api`) shows on no row and verbatim in the detail sheet, because on 88% of
  one list it was a badge on every row (voice-transcriber#264, owner
  decision 3 on #260). A chat or message row's second line is the relative
  time (kept whole) plus a preview (cut); counts move to the sheet header
  (whatsapp-radar#375, owner decision 6 on #370). Height comes from the `rows` scale
  (`rows.md` minimum). **No vertical rules** between controls inside a row;
  a list is not a spreadsheet. **Destructive actions** (Kill, Delete, Remove) live in the row
  menu (`row-menu`) as its last item, after a divider, in `danger-text`, behind a
  confirm. A row never shows a visible danger button, and its visible Stop is
  the plain square above, which halts and can be started again. **Long lists:** a list
  that can exceed ~12 rows gets a filter field above it: 44px tall, a
  `control-border` boundary on `card`, `rounded.md`. Its placeholder is the
  list's count ("Filter 39 tasks…"), that count's one home (task-os#393).
  Any filter beyond the text lives in a **filter sheet**, never a card
  above the list: sort first, then the filters, each applying as it
  changes, Clear beside Done, and on a phone a footer that ends the sheet in
  the flow instead of pinned over its last rows. Its trigger is an
  `icon-button` beside the field that shows the active count, with an
  accessible name that spells the filters out (task-os#395). **Inside a
  card** (a glance card's rows) the row drops its own inline padding so its
  text lines up with the card's text edge, and a row that does nothing on
  tap drops its pressed background (grocery-shopping-automation#257–#261
  carry this as an app-side override). **One renderer:** rows
  of different tabs that show the same kind of thing share one renderer; a
  tab that needs a variation adds an **optional field** that leaves every
  other use byte-identical, never a per-tab fork. **Selection is a mode, not
  a column**: a list whose rows are occasionally copied or acted on together
  has no permanent checkbox column. A Select action (on the first group's
  heading) turns row taps into ticks, the header line reads "N selected", a
  bottom bar acts on the picked rows or cancels, and an action leaves the
  mode (voice-transcriber#264, owner decision 4 on #260). **Repetitive
  machine entries fold per day** into one row ("48 traffic checks") that
  opens a sheet, so the entries a person wrote or an exception raised stay
  visible; a default scope over a store dominated by one kind is filtered
  in the query before the page limit, or it comes back nearly empty
  (whatsapp-radar#373). A list's narrowing filter (a source, a status) is a
  chip inside the filter field, never a second segmented row under the
  scope control (whatsapp-radar#375). **A question is a row**: a prompt
  waiting for the user's decision is an action-row (the question as its
  one-line title, its reference as meta) with a trailing chevron to its
  answer sheet, never a prose block with a status stripe. An answered
  question not yet picked up stays as an inert row, with no chevron, whose
  meta says it is waiting. A list of questions shows three, then one "N
  more" row that unfolds in place (only when two or more are hidden), then
  one "Answer N questions" action (app-launcher#1502, owner decision 2 on #1500).
  **A launch pad's
  row carries recency**: where rows are launched many times a day, the meta
  says when the row was last used, in words ("used today", "used
  yesterday", "used 3 days ago", "used last week", "used 2 weeks ago",
  "used last month", "used 2 months ago", or that it was never used), and
  the order follows it: pinned first, then most recent, then A–Z. Pinning
  is **Pin to top** in the row's menu, never a favourite toggle, because a
  toggle every row ends up wearing sorts nothing. Rows unused for 30 days
  fold under one "N more" row (app-launcher#1503, owner decision 5 on #1500).
  **A resume keeps the
  conversation's model**: resuming sends no model of its own, so the
  conversation continues on the one it ran on; where an agent's bare resume
  would fall back to the configured default instead, the app passes the
  conversation's last recorded model; when that cannot be read it falls
  back to the default and the reply says so. The launch toolbar's model picker applies to new conversations,
  and its hint says so (app-launcher#1503).
  **Swipe actions** (`row-swipe`). A row may offer **one swipe action per
  side**: the leading side (swipe right) holds the frequent,
  non-destructive action in `accent` (Resume), the trailing side (swipe
  left) the consequential one in neutral (New). A full swipe commits that
  side's action. The thresholds are tokens: an 8px dead zone
  (`row-swipe.deadZone`); horizontal wins at 1.5× the vertical
  (`axisRatio`); the first 24px from the left edge stay with the system
  back gesture (`edgeGuard`); an 88px pane (`paneWidth`) rests open once
  dragged 44px (`restOpenAt`); commit at 55% of the row width
  (`commitAt`), or on a same-direction flick of at least 0.5 px/ms
  (`flickVelocity`) that covers 30% of it (`flickDistance`). A disabled
  side never commits; its pane still opens and says why. **Every swipe
  action is also the first item of the row's menu**, the single-pointer
  alternative [WCAG 2.2 · Pointer Gestures](https://www.w3.org/WAI/WCAG22/Understanding/pointer-gestures)
  asks for, and the panes are `aria-hidden`. Swipes take touch and pen
  only (a `mouse` `pointerType` is ignored), the row sets
  `touch-action: pan-y`, one row rests open at a time, a scroll or a tap
  elsewhere closes it, and reduced motion snaps instead of sliding. A
  swiping list may run **full-bleed on a phone** (under 520px: edge to
  edge, no side border, radius 0, its text keeping its inset; shipped in
  app-launcher#1503, its on-device check still open). Reference impl:
  app-launcher session rows (app-launcher#1025, tap the row + one kebab;
  every tab on the one anatomy since app-launcher#1432; swipe since
  app-launcher#1503).
- **glance card** — the **first card under the page header** answers the
  tab's main question without a tap ("can I launch?", "what runs next?",
  "what is running?"). At most ~5 rows; its detail opens one tap away in the
  detail sheet (`modal`). It may hold the **tab's one primary** (Fill carts,
  Record, Apply after a match), and an optional footer link row on a
  hairline opens the rarer detail. It **never changes size** as late reads
  land: every row is drawn at once, with a dash for a value still loading
  (grocery-shopping-automation#257–#260; measured per tab as `LAYOUT-10`). The simplest way to keep that
  promise is to render the loading state **from the card's own markup**,
  the same function with no values (blank cells, a "Reading…" row), never
  a separate skeleton; parking-manager's measured 239px loading and 239px
  loaded (#114). **A single-job tab's glance card follows the job's
  state**: the take card that asks "what is in these photos?" becomes the
  result card in the same slot after Extract, and the next verb takes the
  primary (Copy text), with the inputs folded to one "3 photos ›" row
  (photo-ocr#195, owner decision 3 on #192). **A self-running app's Home is
  a health glance**: one row per source with a liveness avatar badge and its
  last run, exceptions as chips, no "OK" badge on any row, and a follow-up
  row only while one is pending (whatsapp-radar#374). A tab with no such question has no glance card
  rather than a decorative one, and a fact whose home is another tab is
  never a glance card here (one home per fact).
- **avatar** (`avatar`) — an action-row's leading squircle: `avatar.size`
  (36px), `rounded.md`, `neutral-soft` fill, one `icons.size.nav-tab` glyph
  (an agent, kind or job glyph). Its corner badge (`avatar.badgeSize`, ringed
  in `card` so it reads cut out) means **alive and nothing else**:
  `success` when the process is up, `danger` when it should be up and is
  down, absent when not running. For a **device** row, alive reads as
  **connected and expected to be**: the badge is `success` when it answers
  and `danger` only when it should and does not. A device that is
  legitimately absent (an unplugged plug) gets no badge, and unreachable
  items of one kind fold into a single **Offline** row at the end of their
  group instead of a red mark on each. It replaces separate health dots, alive
  dots, "running" pills and coloured meta text. A source whose state could
  not be read (a failed poll) shows no badge and the word "Unknown", never
  alive (voice-transcriber#265). An app may swap the dot for
  one role glyph (a crown for a coordinating session) on the same corner.
- **status chip** — the `chip` geometry in **one tone map,
  for exceptions only**; a normal state gets no chip. **Neutral**
  (`neutral-soft`, `fg`): a plain fact (detached, paused, stale). **Accent**
  (`accent-soft`, `accent-text`): work in progress. **Attention** (16%
  `attention` tint, `attention-text`): needs the user soon or ahead of plan
  (needs you, due, not firing, uncommitted, ahead of pace). **Danger** (16%
  `danger` tint, `danger-text`): broken (failed, down, stalled, ≥90%).
  `success` is never a chip — it is the avatar badge only (a history mark's
  healthy outcome is muted, see history marks).
  Sentence case. One class with a `data-tone`, never a chip style per
  surface. A chip that opens something is the `reference-pill` instead.
  **Modes are not alarms.** A normal chosen state — armed, heating,
  boosting, recording on a schedule — is drawn as the selected segment (or,
  outside a segmented control, in `accent`), never `danger`. `danger` is
  kept for the emergency (alarm triggered, battery critical, a host down),
  and `attention` for "needs you soon" (detector trouble, on battery). An
  app that paints its normal night-time state red teaches the user to
  ignore red. The same holds for plans: a high priority is one `neutral`
  chip and an overdue or due-today date is `attention`, never a red edge or
  `danger` on the row (task-os#392). **"Needs a manual action elsewhere" is
  `attention`, never `danger`**: a booking the user must cancel on another
  website is a task, not a break; `danger` stays for the thing that is
  actually broken (booked with no spot), and a mode that makes the whole
  app a no-op is `attention` too (parking-manager#113, #114; owner
  decision 6 on parking-manager#112). A holiday or day off is expected, so
  it is neutral with no chip and no colour. The `neutral` entry's "paused"
  above means one item paused, not the app.
- **day marks** — a month or two-week strip of days (parking-manager#115)
  draws each day as its number over **one small shape mark** for its state
  (booked, will book, extra day, looking, not booked on purpose); **state is
  a mark, never a fill**. Colour only **rings an exception**, in `danger`
  (broken) or `attention` (needs the user), so a problem day is the one
  ringed cell in the grid, and no cell is ever filled with a status colour.
  An expected non-day (a holiday, a day off) gets the neutral "off" mark,
  not amber. The **legend lists marks, not swatches**, and a ring is
  explained by the day's own sheet, which keeps the legend short (eight
  swatches, two of them the same fill, became five marks). Each mark is
  distinct in shape alone, so no state is told by colour only. A day
  opens the **detail sheet** (`modal`), the pane at `layout.wide`.
- **history marks** (`history-mark`) — a row's strip of its recent runs or outcomes is
  **quiet for the healthy outcome and coloured only for the exception**:
  each mark a 6px dot, success in `fg-muted`, failure in `danger`, in
  flight in `accent`, unconfirmed in `attention`, deferred a muted hollow
  ring. An **unknown** outcome (skipped, dry run, any status the app does
  not map) differs from the healthy one **by shape, not only colour**: a
  muted 2px dash, never a dot. Every mark's `title` names its exact
  outcome, so no state is told by colour alone (app-launcher#1505, owner
  decision 7 on #1500).
- **meter** (`meter`) — a usage bar: a `meter.height` (6px) `rounded.pill`
  track in `neutral-soft` with a fill coloured **by pace, not raw
  percent**: `accent` while usage ≤ the period's elapsed share,
  `attention` ahead of it, `danger` at `meter.criticalAt` (90%) or more.
  A `meter.paceTick` (2px) `fg` tick marks the elapsed share. The percent is
  tabular figures in the fill's `*-text` (`fg` under pace). A stale reading
  dims and adds a neutral "stale" status chip; unknown keeps its text. One
  renderer in two sizes (a full card, a compact one-row line), both opening
  the same detail sheet. Pace fits a **quota** that refills each period. An
  **allocation** bar (memory on a host, disk, a share of a fixed capacity)
  has no period, so it draws **no pace tick** and colours by headroom:
  `accent` below `meter.allocationAttentionAt` (75%), `attention` from it,
  `danger` at `meter.criticalAt` (90%) or more. A gauge for the same resource
  uses the same two levels (fleet-config#1386).
- **capture control** — the one big button that starts a recording or a
  capture is **`accent` at rest and `danger` only while it is live**
  (the mic is open): "Record" in sentence case, then a danger "Stop" with
  the timer and level meter, then the one disabled recipe while finishing.
  The pulse animates only while live, and the meter is a `neutral-soft`
  track with an `accent` fill, never a green-to-red gradient. Red at rest
  is status colour as decoration ("modes are not alarms", the status chip).
  Its mode toggles (Append, Incognito) are labelled pills beside or under
  it, never glyph-only (voice-transcriber#262, owner decision 1 on #260).
- **settings group** — the Settings pane is **inset groups** under
  `overline` headers: each row a lead `icons.size.title` glyph, a label, the
  current value in `fg-muted` and a chevron opening a detail sheet. A setting
  changed often stays inline (a `switch`, a segmented control). A setting
  saves as it changes (text and numbers on blur), with a `toast`; an editor
  for a saved item keeps the dense collection's staged Save. Launch-time
  choices stay on the toolbar where the user launches, never only in
  Settings: the per-take model and style are neutral chips on Capture that
  open a picker, and Settings keeps only the default a new take starts from,
  saved to the server, with no device-local copy that could shadow it
  (photo-ocr#194, #197, owner decisions 1 and 7 on #192). A sheet that holds
  several related fields (a week of people, a delivery channel) may keep one
  draft and commit it on **Done** with a `toast`; **Close, the backdrop and
  Esc then discard it**, and the sheet says so ("Done saves. Close discards
  your changes."). A rejection keeps the sheet open with the server's
  field-naming message (whatsapp-radar#376, #377; the discard rule was
  chosen in the step PRs, owner decision 5 on #370 only said "save on
  Done"). Labels carry no file path or config key; a read-only sheet names
  its file on one "Where this lives" line. **A health check reads as a
  plain value** in `fg` ("Found", "Installed", "Ready", "Active"); only an
  exception wears a status chip ("Missing", "Needs setup" in attention, an
  unreadable status neutral with its reason), and a config path appears
  only inside a failing check's fix-it line. A row's summary is a plain
  count ("3 profiles") that turns attention only while it is true ("1
  needs setup"). A sheet's row-text rule never outranks a component's
  muted class: a muted detail line inside a sheet stays muted
  (app-launcher#1506). The same row is the **link row inside a detail sheet** (a unit's
  Vanes, Rule, Schedules, Name): lead glyph, label, muted value, chevron to a
  page of the sheet. Settings sheets are `instant` (Done closes); a saved
  item inside one keeps its staged editor stacked on top. A row's value
  takes its tone's `*-text` when it is an exception (a failing login check,
  mail that needs you), and a row that acts on tap carries no chevron. At
  `layout.wide` the sheet is the detail pane beside the list with the
  selected row tinted, and that row's secondary text moves to `accent-text`
  (`fg-muted` on the tint measured 4.17:1); a sheet holding a
  two-dimensional report (a many-column table) spans the window instead. A
  feature the owner never uses stays a row that shows its state, never a
  removed card (task-os#397, grocery-shopping-automation#261). A **compact
  numeric field** (minutes, watts, a ratio, a coordinate) may keep its own
  width instead of the modal's 55% value column when the 55% column would
  wrap its label at 390px; text and select values keep the 55% rule.
- **segmented control** (`segmented`) — one control for a choice among 2–5
  peers that the user switches between in place: a mode (alarm off /
  partial / full, AC mode, fan), a period (day / week / month), a view
  (Energy / Money), a forecast day, the text size. It is one `rounded.md`
  track on `neutral-soft` with a 3px inset, holding equal borderless
  segments at `control` height, and the **selected segment is the state**:
  raised on the `card` surface with a soft shadow, label in `fg`,
  `aria-pressed="true"`. The state is never a colour, so an armed mode is
  not red. It is never a sentence above controls that look disabled, and
  never a row of faded buttons with the current one undrawn. Labels are one
  word or one glyph plus an accessible name, every label `fg` on the track
  (`fg-muted` there falls under 4.5:1). A segment that cannot apply now is
  `disabled` and keeps its `fg` label (never opacity), saying why in its
  title. Adjacent segments reach 44px with real height when they act on the
  world (verbs) or a vertical-only target band (pickers), never overlapping
  a neighbour. More than five options, or options that are not peers,
  become a `select` in a settings row or move into a sheet instead
  (grocery-shopping-automation#256 cut five Items modes to two peers and
  moved target editing into the item sheet). A list's scope (Mine · Issues ·
  All) is a segmented control too (task-os#391). A mode that is not a peer
  of its siblings is not a segment: Live against Dry run became a switch
  (whatsapp-radar#373). **A sort is a labelled segmented control**, never
  an icon-only toggle: a "Sort" label over its orders ("Next run · A–Z")
  as the first group of the list's filter sheet, and an order that only
  applies in one state (Relevance, while a search is typed) shows only
  then (app-launcher#1501, the judge's J-05 on #1500). Reference impl:
  home-automation `.segmented` (home-automation#879). **`range-tab` is
  retired in favour of `segmented`**: a tinted, accent-outlined selected
  button is the one state shape the fleet no longer draws, and `design_lint`
  flags any `.range-tab(s)` outside `_vendored/` (fleet-config#1407).
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
  empty container. An empty list the user can fill from this tab **names its
  way forward in the block**: its one action is the verb that fills it
  ("Capture a take"), and a search with no hits offers "Clear search"
  (photo-ocr#196, the judge's J-09). The measure checks the block holds a
  button or link (`COMP-10`, fleet-config#1367); that the sentence names a
  control elsewhere on the screen stays with the judge. A populated screen
  shows no block, so a live run reports `COMP-10` as not measured. An
  expanded section (an open `details`, or the panel an expanded trigger names
  in `aria-controls`) that shows no text and holds no control is the silent
  empty container this bullet forbids, measured as `COMP-12`
  (fleet-config#1337); a spinner or skeleton is content in flight, not empty.
- **icon tile** (`icon-tile`) — the Home-screen rounded-square (`rounded.md`
  squircle) filled with **one** of the five `tile-*` colors (the only saturated
  surfaces), a centered `icons.size.feature` Lucide glyph in `accent-fg`. The
  fill signals category, not state; never use a `tile-*` color elsewhere.
- **icon button** (`icon-button`) — every glyph-only control: a pencil, a
  trash, a calendar, a ×, a mic, the header's theme toggle and Settings gear,
  the modal close, the row kebab. It is its own component, never a
  `button-surface` with the label left off. **At rest it draws only the
  glyph**: no fill, border or shadow, in either theme, whatever size its
  target is. The glyph is `icons.size.inline` (16px) in `fg-muted`; hover
  darkens it to `fg`, a pressed toggle (`aria-pressed="true"`) takes
  `accent-text`, and a destructive one stays quiet until hovered, then takes
  the danger tone. Only the glyph's colour moves, never a painted square. The
  box is invisible and reaches `hit-target.min` (44px) by either Touch-targets
  route: a `::before` that grows it, or real 44px geometry (the action-row
  kebab). Two side by side sit far enough apart that their targets touch and
  never overlap. Focus is the app-wide `:focus-visible` ring. A state may draw
  itself: a pressed, current, expanded, selected or checked control is not at
  rest. **A glyph alone must be one a reader already knows**: a close ×,
  search, settings gear, plus, chevron, menu, pencil, trash, copy, refresh,
  play or stop, named for what it does. Any other glyph-only control (an
  Incognito toggle, a gallery, a reset) needs a visible text label beside it,
  which in practice means the labelled pill, not an icon button (measured as
  `COMP-09` on the accessible name; fleet-config#1366). The vendored header toggles and modal close are icon buttons through
  `--close-bg: transparent` (the scaffold default) and keep the
  `icons.size.title` glyph their components ship. Vendor `project-scaffolding`'s
  `_vendored/icon-button/`. Reference impl: task-os `.icon-btn` (task-os#357).
- **reference pill** (`reference-pill`) — a link to something the user can
  open (a folder, an AI conversation, an issue, a URL) is one pill: the `chip`
  geometry (`rounded.pill`, `2px` × `spacing.sm` padding, `caption` type) in
  `accent-text` on `accent-soft` with an `accent-border-soft` hairline. No
  context resizes it: in a toolbar, a drawer field or a form row it keeps the
  chip geometry, never the `control` height or `rounded.md`. A chip that opens
  nothing stays the neutral `chip`; a status pill keeps its status colours (a
  `*-text` on its tint, a muted closed state) on the same shape. Reference
  impl: task-os `.chip, .pill` (task-os#357).
- **code chip** (`code-inline`, and its linked variant `file-chip`) — inline
  code inside rendered prose: mono at 0.875em of the surrounding text, `fg`
  on `neutral-soft`, `1px 6px` padding, a 6px radius. 6px is the chip's own
  radius, below `rounded.sm`: a code span sits inside a line of text, and
  8px on a 20px-tall chip reads as a pill. Never `canvas-subtle`, which is
  true black inside a dark card. A code span that names something the app
  can open (a file this session edited, opening its diff) is the **file
  chip**: the `reference-pill` colours (`accent-text` on `accent-soft`, an
  `accent-border-soft` hairline) on the code chip's geometry, a leading
  `file` glyph, and the short name only (the full path on `title`). It links
  only what the app can resolve without guessing; everything else stays the
  plain code chip. Inline in prose, it is exempt from the 44px floor the way
  a text link is ([WCAG 2.2 · Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)'s
  inline exception), and
  its name ellipsizes rather than push the text sideways at 390px. Copy and
  read-aloud take the source text, so a chip changes neither. A listing (a
  fenced block, a diff) sits in the `code-listing` frame: the `card` surface
  on a hairline, a small `caption` header (language, copy). Reference impl:
  app-launcher `.tr-md code`, `.tr-file-chip` (app-launcher#1474, #1478).
- **chat turn** (`chat-turn`) — how an app renders an exchange with an
  agent. **The prompt is a bubble:** right-aligned, `fg` on `accent-soft`
  with an `accent-border-soft` hairline, at most `min(85%, 560px)` wide, the
  corner nearest the column edge tightened to 4px. **The agent's turn is one
  block on the canvas, not a card:** one header for the whole turn (the
  agent's mark on a `neutral-soft` squircle, its name, the time, copy,
  collapse), then its parts in order at a 12px rhythm. Never one card per
  text fragment, each with its own "who · when" header (measured as `COMP-18`,
  fleet-config#1344). **Reply text follows
  the type scale:** `##` is `heading-md`, `###` is body bold, so a heading
  never renders smaller than the body around it. **Tool work is a quiet step
  line:** one flat `rows.sm` row per run of tool calls, chevron, a
  plain-words label in `body-sm` `fg-muted` ("Ran 2 commands, read 3
  files"), then the parts an ellipsis must never cut (`+N −M`, `N failed`, a
  duration) as non-shrinking trailing items (measured at 390px as `LAYOUT-09`
  in status and meta text, never in prose, fleet-config#1343, #1438); opened, the items hang off a
  2px rail. **A turn that changed things closes with a summary card** once
  the turn is over (the next prompt exists, or the app's activity line says
  it stopped), never growing live: a header with the total, then one
  `rows.md` row per item (status badge, name first with its folder as the
  hint, its own counts), the first 3 then "Show N more" on every width; a
  row opens the item's detail, focused on it. When the turn's start is not
  loaded, the card says so ("Earlier steps not loaded · Load older") instead
  of presenting a partial count as the whole. **No one-tap undo of agent
  work from the summary.** The summary is folded from the conversation, not
  from the source of truth, and later work may have changed the item since;
  the card offers **Ask to undo**, a 44px trailing button that puts a plain
  request in the composer ("Undo your edits to `path` from your last turn")
  and sends nothing until the user taps Send, so the agent does it in the
  open. Reference impl: app-launcher `session-transcript.js`,
  `edited-card.js` (app-launcher#1475–#1479).
- **toast** (`toast`) — the one transient message for a user-initiated command
  (feedback altitude, Async data & feedback). The **neutral frosted** recipe
  from app-launcher (`styles.css`, "Neutral frosted toast", ported from
  home-automation #782): `position: fixed`, centred (`left: 50%` +
  `translateX(-50%)`), sitting `toast.offsetAboveNav` (8px) above the floating
  nav (nav height + 2× nav margin + `env(safe-area-inset-bottom)`) so the nav
  never hides it, and above every overlay. The surface is the **nav-bar's glass**:
  the translucent card fill and its hairline, `backdrop-filter: blur(20px)
  saturate(1.25)` (with the `-webkit-` twin), `rounded.md`, the theme shadow.
  Text is `fg`, `label` at weight 700, line-height 1.5, centred, padded
  `12px 18px`. **`width: max-content`** with `max-width: min(100vw - 24px,
  560px)` makes it one line whenever the message fits (without it the box
  shrinks to the 50vw left of its anchor and wraps at half the screen);
  `text-wrap: balance` splits a longer message evenly. **Only a real error
  tints** — a `danger` 75% mix fill, `danger` border and `accent-fg` text.
  Success, info and warning toasts stay neutral: the message says what
  happened, colour does not. Announce through a `role="status"` live region
  (`assertive` for the error). Vendor it from `project-scaffolding` rather than
  re-authoring it.
- **row menu** (`row-menu`) — the floating list a row's trailing kebab opens,
  the same in every app (fleet-config#1393; the owner asked for one ⋮ menu with
  one font, one glyph size and one placement). **Rows:** `rows.sm` (44px) tall,
  the `icons.size.inline` (16px) glyph then the label with a 10px gap and
  `0 12px` padding, the label in `body` (1rem) at weight 500 in `fg`; no glyph
  where an item names none, and a brand mark, where an item has one, is 18px.
  **Surface:** a raised layer one step above the card it opens over —
  `canvas-subtle` in light, `border-muted` in dark (the values the vendored CSS
  reads from its `--card-2`, which falls back to `card` where an app defines
  none) — on a `border` hairline, `rounded.md`, `4px` padding, `2px` between
  rows, the theme shadow, at least 200px wide. Rows are `rounded.sm`; hover
  tints with `accent-soft` only where a pointer exists, and the keyboard ring is
  the app-wide 2px `accent` focus ring drawn inside the row so the menu's border
  never clips it. **An optional header:** one `body-sm` line above the
  items for a fact worth a glance (a pid, an app kind, "No conversation yet"),
  on a hairline, wrapping rather than widening the menu; never an item, never
  focusable. Its colour is `fg` (`headerColor`), not `fg-muted`, at regular
  weight against the items' 500: it reads quieter by weight and the hairline
  under it, since `fg-muted` on the dark menu surface is 4.08:1, under the 4.5:1
  floor for `body-sm` text (`fg` there is 12.88:1; light `fg` on `canvas-subtle`
  is 14.84:1; project-scaffolding#398). **Items are data the app passes in**, in order; none is hard-coded
  in the component. **Danger last:** a destructive item sits at the end after a
  1px divider in `danger-text`, never a filled or tinted red row (measured as `COMP-08`), and its
  confirm is the app's (the `action-row` rule). **Disabled** stays visible and
  focusable with `aria-disabled`, flat `fg-muted` and no hover tint, never
  opacity, and its `title` says why. **Placement, one rule for every app:** the
  menu is `position: fixed`, mounted on `<body>` (or on the open `<dialog>`
  that holds its anchor, whose top layer would hide a body child), so no
  scrolling column or card clips it. It opens below its anchor with the right
  edges aligned, `2px` off it, inside a **band**: the viewport inset `8px` from
  each edge and ending `8px` above the floating nav pill while the pill shows,
  so it never opens under the pill or off the screen. When it does not fit it
  mirrors across the anchor (below to above); only when neither side fits is it
  clamped to the band with its height capped and an internal scroll. The desktop
  bar and the wide rail are not bottom pills, and a menu inside a `<dialog>`
  keeps the whole viewport. It re-fits on resize, on any scroll and on the
  visual viewport, so it follows an anchor in a sideways-scrolling column. **In
  front of the nav:** `z-index` 200, above the pill (120) and a sticky header,
  below the toast (400). **A list that polls rebuilds its rows:** an open menu
  reopens on the new anchor and stays hidden, re-placing each frame for up to
  about a second, until that anchor is back in the document, then appears
  beside it, and closes if it never returns, so a refresh never parks it in the
  corner (project-scaffolding#373). **Behaviour:** the menu-button pattern, an
  anchor with `aria-haspopup="menu"` / `aria-expanded` / `aria-controls` over a
  `role="menu"` of `role="menuitem"` buttons with a roving `tabindex`; Enter,
  Space or a tap opens it on the first item, the arrow keys move (wrapping),
  Home and End jump; it closes on a press outside, Escape (which does not also
  close a surrounding dialog), a second tap of the anchor, choosing an item and
  Tab, and focus returns to the anchor except after a press outside. Vendor it
  from `project-scaffolding` (`row-menu`), never hand-roll a per-app menu or
  add per-app placement CSS.

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
- **Do** cap primary navigation at five tabs, put Settings in the page header as the gear beside the theme toggle on every tab (never as a tab), and open every tab with the one `page-header` (the vendored `home-head`), its context line naming only the exceptions, then a glance card that answers the tab's main question.
- **Do** keep the bottom nav identical across apps — same radius, blur, and
  persistence behavior.
- **Do** reserve bottom padding for the fixed nav so content is never occluded.
- **Do** give every interactive element the one tokenized `:focus-visible` ring — never leave focus to the browser default.
- **Do** color a switch's on-track in the accent (`accent-fill`) — never green.
- **Do** show every command result in the one neutral frosted `toast`; only a real error tints.
- **Do** draw every glyph-only control as the `icon-button`, a glyph on nothing with its 44px target invisible, and every link to something openable as the one `reference-pill`.
- **Do** size every glyph from the canonical `icons.size` steps (16 / 18 / 20 / 24 — `inline` / `title` / `nav-tab` / `feature`) — don't hand-pick a one-off size.
- **Do** honor `prefers-reduced-motion` — collapse authored animation to near-instant.
- **Do** ship the user-selectable theme: pre-paint `data-theme` boot script + persisted sun/moon toggle on the main view — never dark-only or OS-only.
- **Do** ship the persisted Small / Default / Large text-size setting (`<app>.textsize`, stamped pre-paint as `html[data-textsize]`) — it is what makes the zoom lock acceptable.
- **Do** render a repeating list of entries (history, activity log) as flat full-bleed rows on a hairline divider — never nested cards per entry.
- **Do** make a list row's primary action the row itself, with one leading slot (avatar, toggle or time), one trailing kebab for the rest, one shared renderer per kind of row (a variation is an optional field, never a fork), and a filter field above any list that can exceed ~12 rows.
- **Do** give every fact one home: other tabs show the same component in a smaller form, or nothing.
- **Do** pin every single-column stack grid's track to `minmax(0, 1fr)` — never a bare `1fr`/`auto`/implicit track behind a no-wrap or scrollable child.
- **Do** give every non-navigation pointer target a ≥44×44px *effective* hit area — invisible expansion for isolated compact controls, real geometry for adjacent clusters; expanded rectangles never overlap.
- **Do** preserve and label last-known data when a background refresh fails (`Last updated … · live data unavailable`) and disable freshness-sensitive actions — stale state is never actionable.
- **Do** pair every colour-distinguished chart series with a non-colour cue (dash / point style / fill) and a viewport-aware tick budget.
- **Don't** paint an icon-only button (a fill, border or shadow at rest), paint a verb on a list row, or resize a reference pill into a control box.
- **Don't** hand-roll a primitive (switch, select, dialog, tabs…) that shadcn already defines.
- **Don't** show a destructive action as a visible per-row button, split a row into icon columns with vertical rules, or put controls inside a disclosure `summary`.
- **Don't** mix a second icon set or hand-draw a one-off glyph — use the matching Lucide icon.
- **Don't** declare one manifest icon as both `any` and `maskable`, or redraw the app identity independently for the tray.
- **Don't** set a line people read in `caption`, or uppercase anything but an `overline` group header — secondary lines are `body-sm`, and numbers and units are never transformed.
- **Don't** draw inline code at `caption` size or on `canvas-subtle`, and don't give each fragment of one agent turn its own card and header.
- **Don't** put a solid accent fill on any button except the view's primary action — secondary emphasis is the tint, never a second solid.
- **Don't** introduce a second accent or per-app navigation variants.
- **Don't** stretch a single column full-bleed on desktop — keep the centered 772px measure below 1100px, and use the left rail + master-detail layout above it. Only a view whose content is two-dimensional (board lanes, a many-field table, a tree with attribute columns, calendar days) may span the window, and the app declares it in `wide_views`.
- **Don't** use status colors decoratively — they signal state only: chip only an exception, in the one status chip tone map (never a chip style per surface, never a `success` chip), and let the avatar badge alone say a process is alive.
- **Don't** call the browser's `confirm()`, `prompt()` or `alert()` — confirm, rename and detail use the vendored dialog.
- **Don't** make Settings a tab (or a gear-only tab), tint a success toast, or turn a switch green when on.
- **Don't** add a third header icon button (the one optional status readout is the only extra, and it opens a sheet), or open a pane reached only from a header button: a pane that has no nav tab is a tab in disguise, so it becomes a tab, a sheet or a Settings row.
- **Don't** put raw identifiers (hostnames, URLs, exception text or class names, process ids, bare port numbers) in user-facing status or failure copy — sanitize it; a detail sheet or Settings › About may show them, and logs keep the detail. The measure checks the pattern-shaped ones: an IPv4 address, a MAC address or a snake_case token rendered as text outside a `code`, `pre`, `kbd` or `samp` element (`COMP-11`, fleet-config#1336); the rest stays with the judge (J-07).
- **Don't** apply this spec to Streamlit POC spikes.
