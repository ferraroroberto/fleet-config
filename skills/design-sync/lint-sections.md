# design-sync lint output: what each section means (step 3)

Loaded on demand from the `/design-sync` skill (`SKILL.md`, step 3), which keeps the `design_lint all` command and the actions that follow from each section. Step numbers refer to that file.

The five sections it returns, and what each means:

- **`tokens`** — spec-role → app-var mapping via the built-in alias table
  (`--bg`→`colors.canvas`, `--ink`→`colors.fg`, `--on`→`colors.success`, …),
  compared per theme. `matched` needs no action; every `drift` entry
  (`value-drift` or `missing-theme-value`) and `missing` role is a candidate
  finding; `unmapped` is the list the LLM resolves (step 4a).
- **`adoption`** — per family (color, font-size, radius, spacing):
  `tokenized / total` declaration ratio + up to 40 escapee `file:line`s + the
  literal-value histogram. This is the "how much, where" lens (#234): a
  correct-*valued* token used nowhere still scores low here. Ratios trend
  across weekly sweeps (#180). Counts app-authored CSS only, the same
  exclusion the contracts apply (#940) — a vendored library will never use
  our tokens and we will never repaint it, and because the escapee list is
  capped one bundled library otherwise crowds the real findings out of it.

**Third-party vs `_vendored/` (#940).** A `vendor/` path segment marks a
genuinely third-party library bundled for offline use (Leaflet, xterm.js):
out of scope for every check that judges what the *app authored* — button
tiers, hit targets, icon sets, native checkboxes, token adoption — because
there is no fix available in the repo that vendored it. `_vendored/` is the
opposite: those are `project-scaffolding`'s own components, and they stay
fully in scope (the nav contract keys on them). The match is an exact path
segment, never a substring, so `vendor` cannot read as a prefix of
`_vendored`. The rule lives in one place, `design_lint/files.py`'s
`is_third_party`, which also states which blob a newly added contract should
read.
- **`contracts`** — PASS/WARN/FAIL/NA (or ACCEPTED, below) per design.md-v2 component contract,
  one per line below:
  - **focus ring** — tokenized `:focus-visible` ring.
  - **reduced motion** — `prefers-reduced-motion` respected.
  - **desktop measure** — the centered 772px desktop measure; an app that declares `[design] wide_views` in its `.fleet.toml` (#1113) gets a WARN, not a FAIL, when no cap exists — the other views are `/design-review`'s LAYOUT-06.
  - **switch on-track** — on-track color = the accent (`accent-fill`, #1200);
    FAIL if it is green (`success`), WARN if it cannot be resolved to either.
  - **toast neutral** — no rule on a `toast`/`snackbar` selector draws a green
    (success token, `--on`, or a green literal) background or border (#1200);
    NA when the app has no toast CSS. Only a real error tints.
  - **icon button unpainted** — the app's `--close-bg` (the vendored header
    toggles' and modal close's fill) is `transparent` (or zero-alpha) in every
    theme that sets it, or is unset, which takes the scaffold's transparent
    default: PASS. Any fill FAILs, because an icon button is a glyph on nothing
    (#1259). NA when the app neither sets nor reads `--close-bg`. This contract
    owns `--close-bg`, so it is never an `unmapped` leftover for step 4a.
  - **checkboxes** — a checkbox control must be skinned off the browser's
    own tick (`appearance: none`), per design.md's Checkbox -> shadcn
    `checkbox` mapping. A real `<input type=checkbox>` is the shadcn
    substrate, not the violation; a selector *string* in JS
    (`input:not([type="checkbox"])`) is not a control at all (#843). WARN
    when a skin exists but cannot be attributed to the checkbox. Whether a
    given boolean should have been the switch instead is judgment — step 4,
    not the grep.
  - **disclosure closed-box** — the closed-box trio (52px / `0 14px` /
    open divider).
  - **dialog** — native `<dialog>` vs hand-rolled overlay.
  - **nav contract** — the nav-contract signals (`body:has(dialog[open])`
    hide, `100dvh`, safe-area, and the standalone fixed-inset `.app`
    scroller — the home-automation#303 architecture that removes the iOS
    pill-drift cause; a nav missing it caps at WARN even when every grep
    signal passes and even when `_vendored/nav/` is present, because the
    shell lives app-side). Folds in **nav-nesting**: `<nav class="tabs">`
    found as a DOM descendant of `<main class="app">` instead of a `<body>`
    sibling always FAILs on its own (home-automation#232/app-launcher#369).
  - **icon sizes** — icon px sizes vs the spec's `icons.size` steps
    (spec-driven — the allowed set is parsed from the spec, not hardcoded).
  - **viewport zoom lock** — `user-scalable=no` + `maximum-scale=1` +
    `viewport-fit=cover` on every `index.html`; PWAs are never
    pinch-zoomable (fleet-config#296).
  - **button-tier vocabulary** — hardcoded button fills and a filled
    "ghost" FAIL; a solid accent outside the primary class and a tint
    without accent text WARN — the tiers live in design.md `components`
    (#296). App-authored CSS only (#940 — Leaflet's popup close button is
    Leaflet's tier choice, not the adopting app's).
  - **user-selectable theme** — pre-paint `data-theme` boot script in
    `<head>` + a persisted `.theme` localStorage toggle; either missing
    FAILs. A missing or spec-drifted scheme-gated `theme-color` meta pair
    WARNs, compared against the two specs' `canvas` (spec-driven, #290).
  - **text-size** — the zoom-lock escape: a pre-paint `html[data-textsize]`
    stamp read from a `.textsize` localStorage key in `<head>`, plus a
    persisted control that writes it (a literal key or a named constant).
    Any part missing WARNs, never FAILs, while apps adopt it one by one
    (#967).
  - **icon-set** — emoji glyphs in rendered markup text or JS UI-copy
    strings: FAIL when no vendored Lucide sprite is adopted, WARN when
    emoji sit alongside an adopted sprite — one icon set, never
    hand-drawn/mixed (#284). Comments (#394), JS regex literals, and
    third-party `vendor/` bundles are not rendered text and are excluded —
    a char class matching glyphs coming *in* off a terminal draws none, and
    a vendored bundle's glyph table isn't the adopting app's icon choice
    (#416).
  - **app-icon-family** — an installable PWA must adopt
    `project-scaffolding`'s `brand_gen.render_set`, commit the spec-named
    Apple 180 / regular 192+512 / separate maskable 512 / favicon assets,
    link Apple touch + favicon from `index.html`, and keep `any` and
    `maskable` as distinct manifest purposes (#369).
  - **chevron-placement** — a disclosure `<summary>` whose chevron
    glyph/icon sits before its title text; the fleet contract pins it
    right (#284/app-launcher#362).
  - **row-height-scale** — fixed `height`/`min-height` literals on
    row/action-rail selectors outside the spec's `rows` 3-step scale
    (44px/52px/60px by default, spec-driven) WARN (#284/app-launcher#365).
    Row **containers** only — the rule is about the repeating box, so the
    rightmost compound has to be the row itself (`.trow`, `.link-row`,
    `.rows`, `.action-rail`). A part *inside* a row (`.trow-status`,
    `.trow-check`) carries its own size and is not a finding (#843). A
    truncated stray list says how many values it left out.
  - **editor-modal contract** — design.md `modal` component (#307),
    applied to every `<dialog>` that contains a real editable field
    (`input`/`select`/`textarea`); a `<form>` wrapper is **not** required
    (#342: home-automation#409's JS-managed editors carry bare fields and
    a plain `type="button"` Save; a field-less alert/results dialog stays
    NA). Sub-checks:
    - `modal-unstyled-rows` — a row class, e.g. `label.stacked`, used
      inside a dialog but only ever styled under some other, unrelated
      ancestor scope (the app-launcher#70 root cause, where `.stacked`
      was styled only under `.settings-card`).
    - `modal-raw-fieldset` — a `<fieldset>`/`<legend>` with zero authored
      CSS (a raw browser legend box instead of a titled plain section).
    - `modal-header` — a titled dialog with no square × close button, or
      a footer "Cancel" button standing in its place.
    - `modal-footer` — more than one always-visible footer action, or a
      sole primary that isn't the full-width solid-accent recipe.
    - `modal-top-anchor` — no `max-height` + internal scroll, so a tall
      form jumps vertically as conditional rows toggle.
  - **mobile interaction contracts** — four checks promoted from
    home-automation#409 (#342 — all conservative static views of
    design.md's Async data & feedback / Touch targets / Charts sections):
    - **hit-target** — spec-driven from `components.hit-target.min`
      (44px): a fixed-size compact interactive rule below the floor with
      no `::before` hit-area expansion on its own class and no
      co-applied expansion utility in the markup WARNs; NA when the spec
      lacks the token or the app authors no compact fixed-size controls.
      App-authored surfaces only (#940 — a bundled library's control
      chrome can't be widened in the app that vendored it).
    - **chart-tick-budget** — Chart.js present with no authored
      `maxTicksLimit`/`autoSkip` WARNs (phone x-axes collide); NA with no
      Chart.js.
    - **chart-noncolor-cue** — ≥2 colour-assigned datasets with no
      `borderDash`/`pointStyle`/`fill` second channel WARN (colour must
      never be the only series cue); NA for single-series apps.
    - **async-lifecycle** — literal `data-state` values checked against
      the canonical `loading/ready/empty/stale/error` vocabulary;
      shadcn-style interaction states like `open`/`closed` are a
      different channel and exempt; non-canonical lifecycle synonyms
      WARN, and lifecycle states with no `role="status"` live region WARN
      (the region counts whether it is declared in markup or set from JS
      — `setAttribute('role', 'status')`, `el.role = 'status'` — since a
      JS-rendered drawer is exactly the surface this contract is for and
      the check already reads `dataset.state` the same way, #416); NA
      when the app never uses `data-state`.
  - **audit-promoted checks** — five static views of the 2026-09-21
    rendered audit (#969). Every one WARNs and never FAILs, because the
    apps they flag fix them in their own lanes:
    - **form-font-inherit** — no global `font: inherit` (or
      `font-family: inherit`) on bare `button`, `input`, `select` and
      `textarea`, so controls render in the UA font (Arial on Windows
      Chrome). Vendored CSS counts, since the scaffold base ships the rule
      (project-scaffolding#266). A scoped `.card button` is not the reset.
    - **uppercase-role** — `text-transform: uppercase` on any selector that
      is not the spec's legal caps role. The allowed role is read from the
      spec (`typography.*.textTransform`, today `overline`, #964).
      App-authored CSS only.
    - **break-all** — `word-break: break-all` on a selector that is not a
      path, hash, URL or code string. Names want `overflow-wrap: anywhere`.
    - **glyph-icons** — arrow (U+2190–21FF) and geometric-shape
      (U+25A0–25FF) characters and the kebab (U+22EE) drawn as icons in rendered markup or JS UI
      strings. Same comment, regex-literal and `vendor/` exclusions as
      `icon-set`; the ranges don't overlap its emoji scan.
    - **spec-contrast** — every `components.<name>` text/background pair in
      the spec, composited over `card`, per theme, computed from the spec
      alone. It flags the spec (fleet-config), not the app, and PASSes since
      #963.
  - **rendered-leg** — PASS when the repo has the shared rendered-geometry
    helper (`tests/e2e/_geometry.py`, project-scaffolding#157), WARN
    `rendered leg unmeasured` when it doesn't. A static `hit-target` PASS
    once sat beside 33–39px rendered heights, so the missing harness now
    shows in the contract counts, not only in this prose (#969).
  - **vendored-tokens** — every CSS component the app declares in its
    `.fleet.toml` `[vendored]` table reads only tokens the app defines
    (#1290). The token list is the component's own `var(--x)` reads, so it
    cannot drift from the code. A definition counts anywhere the app ships:
    a stylesheet (the component's own included), an inline `style`, or a JS
    `setProperty`. FAIL names an undefined token read with no fallback
    (`--icon-inline` collapses the icon-button glyph to zero); WARN names
    one the component falls back on, with the fallback value. A nested
    `var()` inside a fallback counts only when the outer token is
    undefined. A token the README documents as optional is exempt: its
    "Required design tokens" row says "optional" (#1301), or a prose line
    calls it a per-context knob (`--icon-btn-box`). A row that only names a
    fallback is not optional. The README's "Required design tokens"
    table is a cross-check only: a token the CSS reads but the table omits
    is noted in the detail for a fix upstream in project-scaffolding, and
    never changes the status. NA when nothing is declared; an unreadable
    `.fleet.toml` FAILs.
  - **`ACCEPTED`** — a WARN/FAIL a repo already examined and accepted, via a
    `[[design.accepted]]` entry in its own `.fleet.toml` (schema:
    `architecture/README.md`, fleet-config#836). The row keeps the original
    detail plus `accepted.raised_status`, `reason`, `record`, and `verified`
    (what the declared assertion proved this run). It matches only the exact
    check + target + detail it accepted, and its assertion is re-run each
    time: if that fails or can't be established, the row comes back as
    WARN/FAIL with the reason appended. **`accepted-exception` WARN** rows
    report a declaration that is malformed or matched nothing. That is a real
    finding: the repo must fix or remove the declaration.
- **`vendored`** — byte-hash comparison of the app's
  `_vendored/<component>/` copies against project-scaffolding's canonical
  files: `IDENTICAL` / `FORKED` (the vendor-verbatim rule broken — always a
  finding) / `NOT_ADOPTED` (informational; adoption is rollout work, not
  drift). `icons-sprite.html` is compared **per `<symbol id>`**, not
  whole-file — the icons component sanctions per-app trimming, so a subset
  whose kept symbols are byte-identical reports `IDENTICAL (trimmed)`, never
  a false `FORKED` (#284).
- **`siblings`** — top-level JS definitions with the same name in ≥2 files
  (the 7×-duplicated `schedule(ms)` of home-automation#369). Detection is
  mechanical; *which variant is canonical* is step 4c.
