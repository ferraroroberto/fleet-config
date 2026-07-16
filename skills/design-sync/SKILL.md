---
name: design-sync
description: Check a web app against the fleet design system with the deterministic design_lint helper — token drift (light + dark), component contracts, vendored-component bytes, sibling consistency — then file a deduped design-drift issue for /cleanup-fleet. Skips Streamlit POC spikes. E.g. "/design-sync", "/design-sync app-launcher", "check design drift", "sync this app to design.md", or "/design-sync apply" to write aligned tokens.
---

# design-sync

**Goal:** Keep every fleet web app (FastAPI + static PWA) true to the shared
visual identity in `~/.claude/design.md` (light) + `~/.claude/design.dark.md`
(dark). Run the **deterministic lint** (`skills/_lib/design_lint.py`) — token
mapping + drift (light **and** dark), per-family adoption ratios, component
contracts (including the PWA app-icon family), vendored-component
byte-verification, sibling duplicates — apply
LLM judgment only where measurement can't reach, and file exactly one deduped
`design-drift` issue per repo (the same audit→bucket→cleanup machinery as
`/codebase-audit`, cleared later by `/cleanup-fleet design-drift`). With
`apply`, also write the aligned token values into the working tree for review.

**Measure with the helper, never by eye.** Everything mechanically checkable
comes from `design_lint.py` (pure, unit-tested); never re-derive a ratio,
token comparison, or byte-diff by reading CSS. LLM judgment is confined to:
`unmapped` variable leftovers, the materiality bar, sibling arbitration, and
writing the issue.

**One adjacent infra check rides the same population: tailnet-cert
conformance** (step 1b). A Tailscale-reachable app still provisioning HTTPS
via a self-signed CA + `/install-ca` trust dance instead of the `tailscale
cert` standard (`ferraroroberto/project-scaffolding#89`) is convention drift —
filed as a **separate** deduped `cert-drift` issue, never mixed into
`design-drift`.

**Default mode files issues; it does not edit code.** Only `apply` (step 6)
writes — and only CSS; the cert migration is never auto-applied. Never commit,
push, or restart anything.

## Arguments

- No argument → the **current repo** (cwd).
- One argument that is a path or repo name → that **target repo** (resolve
  relative to `E:/automation/<name>` or as a path; must be a git repo).
- The word `apply` anywhere in the args (`/design-sync apply`,
  `/design-sync app-launcher apply`) → after reporting, **apply** the spec values
  to the app's CSS in the working tree (step 6). Without it, the run is read-only
  on code and only files the issue.

More than one path argument → say only one target is accepted and stop.

## Steps

Run in order. Stop on any hard failure with a one-line error.

### 1. Pre-flight

In parallel, from the target repo root:
- `git rev-parse --is-inside-work-tree` — must print `true`, else stop:
  "Not inside a git repository."
- `git rev-parse --show-toplevel` — capture the repo root.
- `gh repo view --json nameWithOwner -q .nameWithOwner` — capture `OWNER/REPO`.
  If it fails, stop: "No GitHub remote — this skill files issues, can't run
  without one."

### 1b. Tailnet-cert conformance (independent of the CSS check)

Run this **before** step 2's web-app gate, so a repo that short-circuits on
"no token CSS" still gets cert-checked. Self-gating: a non-web, LAN-only, or
already-migrated repo reports clean and files nothing.

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/cert_drift.py detect <repo-root>
```

It prints `CERT_DRIFT=yes|no`, `REASON=...`, and the `TAILNET` / `SELF_SIGNED` /
`TS_CERT` evidence (`file:line`, or `-`). The verdict is a fixed truth table over
three signals — a `*.ts.net`/Tailscale mention in `README.md`/`CLAUDE.md`/`docs/**`,
a `gen_ssl_cert.py`-style provisioner or `/install-ca` route, and the absence of a
`gen_tailscale_cert.py`-style provisioner — so it is deterministic, not a judgment
call. `CERT_DRIFT=no` → note "cert: ok" for the final report and move on.

On `CERT_DRIFT=yes`, file a **separate** deduped `cert-drift` issue (never folded
into `design-drift`):

1. **Ensure the label** (idempotent):

   ```
   gh label create cert-drift --color 'b60205' --description 'Tailnet PWA still on self-signed CA instead of the tailscale-cert standard' || true
   ```

2. **Fetch the existing issue:**

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind cert-drift
   ```

3. **Build the body.** Fresh → use the template below (fill the evidence from the
   `detect` output). Existing → preserve every ticked `- [x]`, append a dated bullet
   to `## Run log`, never tick/close anything, never add `Closes #`.

4. **Upsert** (creates / edits / collapses strays, stamps the marker):

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind cert-drift --label cert-drift \
     --title "audit: cert-drift findings" --body-file <tmpfile>
   ```

   Use a repo-scoped, unique temp file: `E:/tmp/cert-drift-<owner>-<repo>-<short-sha>.md`
   (`<owner>-<repo>` = `OWNER/REPO` with the slash → hyphen). Never a fixed shared name.

**Body shape** for a fresh issue (no hard-wrapped paragraphs — the global CLAUDE.md
rendered-markdown rule applies; the helper prepends the marker):

```markdown
Surfaced by `/design-sync` (tailnet-cert conformance), kept up to date across runs.

## Finding

- [ ] This app is reached over Tailscale and still provisions HTTPS only via a self-signed CA + `/install-ca` mobileconfig trust dance. Migrate to `tailscale cert` (real Let's Encrypt) per the fleet standard. Fix: adopt a `scripts/gen_tailscale_cert.py` (`--check` auto-renew) + webapp wire-up, dropping the self-signed dance.

## Evidence

- tailnet signal: `<file:line>`
- self-signed provisioner: `<file:line | file>`
- tailscale-cert provisioner: absent

## Standard

Canonical decision record: `ferraroroberto/project-scaffolding#89`. Reference impl: `ferraroroberto/grocery-shopping-automation` — `scripts/gen_tailscale_cert.py` (`--check` auto-renew) + `webapp.bat` wire-up.

## Run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Title is **stable** — `audit: cert-drift findings`, no count suffix.

### 2. Detect a web app — else skip

This skill only applies to **FastAPI + static-PWA** apps that style themselves
with CSS custom properties. Decide:

- `git ls-files "*.css"` → the candidate stylesheets.
- Keep only stylesheets that define **CSS custom properties** in a `:root` (or
  equivalent) block (`grep -l -- '--[a-z].*:' <files>`). These carry the tokens
  to compare.
- **Exclude Streamlit POC spikes** — any CSS owned by a Streamlit app (e.g. files
  under a `spike/` dir, alongside a `streamlit_app.py`, or injected via
  `st.markdown(..., unsafe_allow_html=True)`). Streamlit spikes are throwaway and
  explicitly out of scope.

If no token-bearing, non-Streamlit stylesheet remains, stop with:
`<repo> is not a token-styled web app (or is Streamlit-only) — nothing to sync.`
File nothing.

### 3. Run the deterministic lint

One command computes every mechanically checkable dimension (JSON to stdout):

```
E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/design_lint.py all <repo-root>
```

(Reads the spec from `~/.claude/design.md` + `design.dark.md` — the junctioned
canonical copies; `--scaffold` overrides the project-scaffolding root for the
vendored compare, default `E:/automation/project-scaffolding`.)

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
  across weekly sweeps (#180).
- **`contracts`** — PASS/WARN/FAIL/NA per design.md-v2 component contract:
  tokenized `:focus-visible` ring, `prefers-reduced-motion`, the centered
  772px desktop measure, **switch on-track = success (green — FAIL if it is
  the accent)**, no native checkboxes, the disclosure closed-box trio
  (52px / `0 14px` / open divider), native `<dialog>` vs hand-rolled overlay,
  the nav-contract signals (`body:has(dialog[open])` hide, `100dvh`,
  safe-area, **and the standalone fixed-inset `.app` scroller** — the
  home-automation#303 architecture that removes the iOS pill-drift cause;
  a nav missing it caps at WARN even when every grep signal passes and even
  when `_vendored/nav/` is present, because the shell lives app-side; folded
  into the same check is **nav-nesting** — `<nav class="tabs">` found as a
  DOM descendant of `<main class="app">` instead of a `<body>` sibling always
  FAILs on its own, home-automation#232/app-launcher#369), icon px sizes vs
  the spec's `icons.size` steps
  (spec-driven — the allowed set is parsed from the spec, not hardcoded),
  the **viewport zoom lock** (`user-scalable=no` + `maximum-scale=1` — plus
  `viewport-fit=cover` — on every `index.html`; PWAs are never pinch-zoomable,
  fleet-config#296), the **button-tier vocabulary** (hardcoded button
  fills and a filled "ghost" FAIL; a solid accent outside the primary class
  and a tint without accent text WARN — the tiers live in design.md
  `components`, #296), the **user-selectable theme** (pre-paint
  `data-theme` boot script in `<head>` + a persisted `.theme` localStorage
  toggle — either missing FAILs; a missing or spec-drifted scheme-gated
  `theme-color` meta pair WARNs, compared against the two specs' `canvas` —
  spec-driven, #290), the **icon-set** (emoji glyphs in rendered markup text
  or JS UI-copy strings — FAIL when no vendored Lucide sprite is adopted,
  WARN when emoji sit alongside an adopted sprite — one icon set, never
  hand-drawn/mixed, #284), the **app-icon-family** (an installable PWA must
  adopt `project-scaffolding`'s `brand_gen.render_set`, commit the spec-named
  Apple 180 / regular 192+512 / separate maskable 512 / favicon assets, link
  Apple touch + favicon from `index.html`, and keep `any` and `maskable` as
  distinct manifest purposes — #369), **chevron-placement** (a disclosure `<summary>`
  whose chevron glyph/icon sits before its title text — the fleet contract
  pins it right, #284/app-launcher#362), **row-height-scale** (fixed
  `height`/`min-height` literals on row/action-rail selectors outside the
  spec's `rows` 3-step scale — 44px/52px/60px by default, spec-driven —
  WARN, #284/app-launcher#365), the **editor-modal contract** (design.md
  `modal` component, #307) applied to every `<dialog>` that contains a real
  editable field (`input`/`select`/`textarea`) — a `<form>` wrapper is
  **not** required (#342: home-automation#409's JS-managed editors carry
  bare fields and a plain `type="button"` Save; a field-less alert/results
  dialog stays NA):
  `modal-unstyled-rows` (a row class, e.g. `label.stacked`, used inside a
  dialog but only ever styled under some other, unrelated ancestor scope —
  the app-launcher#70 root cause, where `.stacked` was styled only under
  `.settings-card`), `modal-raw-fieldset` (a `<fieldset>`/`<legend>` with
  zero authored CSS — a raw browser legend box instead of a titled plain
  section), `modal-header` (a titled dialog with no square × close button,
  or a footer "Cancel" button standing in its place), `modal-footer` (more
  than one always-visible footer action, or a sole primary that isn't the
  full-width solid-accent recipe), and `modal-top-anchor` (no `max-height` +
  internal scroll, so a tall form jumps vertically as conditional rows
  toggle), plus the four **mobile interaction contracts** promoted from
  home-automation#409 (#342 — all conservative static views of design.md's
  Async data & feedback / Touch targets / Charts sections):
  **hit-target** (spec-driven from `components.hit-target.min`, 44px — a
  fixed-size compact interactive rule below the floor with no `::before`
  hit-area expansion on its own class and no co-applied expansion utility
  in the markup WARNs; NA when the spec lacks the token or the app authors
  no compact fixed-size controls), **chart-tick-budget** (Chart.js present
  with no authored `maxTicksLimit`/`autoSkip` WARNs — phone x-axes collide;
  NA with no Chart.js), **chart-noncolor-cue** (≥2 colour-assigned datasets
  with no `borderDash`/`pointStyle`/`fill` second channel WARN — colour
  must never be the only series cue; NA for single-series apps), and
  **async-lifecycle** (literal `data-state` values checked against the
  canonical `loading/ready/empty/stale/error` vocabulary — shadcn-style
  interaction states like `open`/`closed` are a different channel and
  exempt; non-canonical lifecycle synonyms WARN, and lifecycle states with
  no `role="status"` live region WARN; NA when the app never uses
  `data-state`).
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

**Static PASS is not rendered conformance.** The lint proves *authored*
facts — tokens, markup shape, component CSS, vendored bytes. Effective hit
rectangles and their non-overlap, chart tick/label collision, canvas-driven
page overflow, and behavior across the 320/390/430/772px × light/dark
matrix are **rendered-DOM facts** that only a browser harness can prove
(home-automation#409 verified them deterministically in Playwright;
the canonical shared geometry helper is project-scaffolding#157). When the
target repo ships that harness, run it and report its results alongside the
static sections; when it doesn't, **report the rendered leg as `unmeasured`**
in the findings and the final report — never let a clean static scan read as
whole-UX conformance. home-automation#409/PR#427 is the reference pattern
for what the rendered leg covers; consult it as a pattern, never hardcode
its selectors or APIs into checks.

### 4. LLM judgment layer (only where measurement can't reach)

- **(a) Resolve the `unmapped` leftovers.** For each app variable the alias
  table didn't claim, decide from its name, comment, and usage whether it maps
  to a spec role (then compare values yourself and add a finding on mismatch)
  or is a legitimate derived/app-specific token (`--input-bg: var(--card-off)`,
  the nav geometry vars — fine, note nothing). If an alias is genuinely
  fleet-common, extend `ALIASES` in `design_lint.py` in a proper branch — never
  fudge the mapping ad hoc.
- **(b) Materiality bar** over everything the lint surfaced: a 1-unit
  radius/spacing nitpick or a shadow's `#000` is not a finding; a wrong canvas
  color, a missing dark theme, a `FORKED` vendored file, an accent-colored
  switch, or a FAILed contract is. Spacing adoption is expected to score low
  fleet-wide (never unified — report the ratio, don't inflate findings from
  it); font-size/radius should be near 1.0 on a canon app.
- **(c) Sibling arbitration.** For each `siblings` duplicate (and any repeated
  CSS component pattern you notice while reading), identify the app's
  **dominant/correct variant** and flag the deviants — this is the technique
  that found the missing busy-flag guard (home-automation#368: `vm.js` had the
  pattern `security-alarm.js`/`plugs.js` lacked). A duplicate that is
  byte-identical everywhere is a dedup candidate; one that *diverges* is a
  consistency bug candidate — say which.
- **(d) What the greps can't see.** The nav contract beyond its grep signals
  (single active tab, `localStorage` persistence, ≥44px targets) and the
  disclosure body-padding nuance (`12px 14px 14px`, top dropped when the first
  child is a list) still need a read of the actual code when the lint flags
  the area. A re-implemented or divergent nav is a finding whose fix is to
  **adopt the vendored component from `project-scaffolding`'s `_vendored/`**
  (nav, card, disclosure, modal, empty-state, switch, icon-tile — all shipped)
  — never re-author it. **A nav-contract WARN/FAIL is always a finding, never
  demoted by judgment** — in particular the missing standalone fixed-inset
  scroller (fleet-config#282): the scroll-up/down pill drift persists on any
  app without it, and the settled conclusion (decision on fleet-config#279,
  2026-07-06) is to adopt `_vendored/nav/` verbatim **plus** the app-side
  fixed-inset `.app` shell as one piece. Do not re-litigate this per run.
  **The list-row nested-card anti-pattern** (fleet-config#293): a repeating
  list (history, activity, request log) built as per-entry `canvas-subtle`
  cards instead of flat `list-row` hairline rows. Cross-selector reasoning a
  grep can't do reliably, so it's a judgment call when reading the CSS/markup
  for a list-shaped view — flag it as a finding, fix is to adopt the
  `list-row` contract from `design.md`. **The stack-track overflow class of
  bug** (fleet-config#294): a single-column `display: grid` stack container
  (a pane/pane-body/list wrapper) without an explicit `minmax(0, ...)` track
  that has a no-wrap or `overflow-x` descendant — the same reasoning gap, so
  it's a judgment call, not a lint check. The deterministic
  `scrollWidth <= innerWidth` measurement itself needs a live app + real
  browsers and belongs to the app's own e2e suite / the shared Playwright
  canon in `project-scaffolding`, not this static lint.

### 5. Dedupe and upsert the `design-drift` issue

Exactly one managed `design-drift` issue per repo, reused across runs — identical
mechanics to `/codebase-audit`'s bucket issues. Never `gh issue create` by hand.

1. **Ensure the label** (idempotent):

   ```
   gh label create design-drift --color '006b75' --description 'Web-app CSS tokens drift from the fleet design.md spec' || true
   ```

2. **Fetch the existing issue:**

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind design-drift
   ```

   It prints `{"number": N|null, "body": "...", "duplicates": [...]}`.

3. **Build the merged body.** Fresh → use the template below. Existing → merge
   this run's findings into the returned body: preserve every ticked `- [x]`
   verbatim, match findings by `file` + token role (update the moved line, keep
   the checkbox), keep items not re-surfaced (flag them in the run log), never
   tick or close anything yourself, never add `Closes #`. Append a dated bullet to
   `## Drift run log`.

4. **Upsert** (creates / edits / collapses strays, stamps the marker):

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind design-drift --label design-drift \
     --title "audit: design-drift findings" --body-file <tmpfile>
   ```

   Use a repo-scoped, unique temp file so concurrent runs never clobber each
   other: `E:/tmp/design-sync-<owner>-<repo>-<short-sha>.md`
   (`<owner>-<repo>` = `OWNER/REPO` with the slash → hyphen,
   `<short-sha>` = `git rev-parse --short HEAD`). Never a fixed shared name.

**Body shape** for a fresh issue (no hard-wrapped paragraphs — the global
CLAUDE.md rendered-markdown rule applies; the helper prepends the marker):

```markdown
Surfaced by `/design-sync`, kept up to date across runs. Spec: `~/.claude/design.md` (+ `design.dark.md`). Measured by `skills/_lib/design_lint.py` (deterministic); judgment items marked.

## Findings

- [ ] **<file>:<line>** — `--<var>` (role `<spec token>`, <light|dark>): app `<value>` ≠ spec `<value>`. Fix: set to `<spec value>`.
- [ ] **<file>** — missing dark theme block; spec defines `design.dark.md`. Fix: add `[data-theme="dark"]` with the spec's dark values.
- [ ] **<contract-id> FAIL** — <detail from the lint>. Fix: <align to the design.md v2 contract / adopt the vendored component>.
- [ ] **_vendored/<component>/<file> FORKED** — the vendored copy diverges from project-scaffolding's canonical bytes. Fix: re-vendor verbatim (or upstream the change to the scaffold first).
- [ ] **<file>** — bottom nav re-implemented, diverges from the spec contract (or lacks the standalone fixed-inset `.app` scroller, home-automation#303). Fix: adopt the vendored nav from `project-scaffolding` plus the app-side fixed-inset shell — one piece, never re-authored.

## Token adoption

| family | tokenized/total | ratio | top escapees |
|---|---|---|---|
| color | <n>/<n> | <0.xx> | <file:line value, …> |
| font-size | <n>/<n> | <0.xx> | … |
| radius | <n>/<n> | <0.xx> | … |
| spacing | <n>/<n> | <0.xx> | … (informational — never unified fleet-wide) |

## Contracts

<one line per lint contract check: `PASS|WARN|FAIL id — detail @ evidence`>

## Vendored components

<one line per component: `IDENTICAL | FORKED (files…) | NOT_ADOPTED` — NOT_ADOPTED is informational, not a finding>

## Sibling consistency

- <name> defined in <n> files (<sites>) — <dedup candidate | deviates: which variant is canonical and why>

## Token map (spec role → app var)

<one line per mapped role the LLM resolved beyond the built-in alias table — the table's own mappings need no restating>

## Context

<One short paragraph: the overall shape of the drift (e.g. "navy palette, not the GitHub true-black look"), and anything the next fixer should know.>

## Drift run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Titles are **stable** — `audit: design-drift findings`, no count suffix.

### 6. Optionally apply (only when `apply` was passed)

When `apply` is in the args, write the spec values into the app's CSS in the
working tree so the user can review the diff:
- For each *drift* finding, set the mapped variable to the spec value, in the
  correct theme block (light → `:root`, dark → the dark block).
- For a *missing dark theme*, add the dark block with the spec's dark values.
- **Do not** re-author navigation/components — for nav drift, copy the vendored
  snippet from `project-scaffolding` verbatim if it exists; otherwise leave the
  finding for the follow-up and say so.
- Leave the working tree dirty for the user to review; **never commit, push, or
  restart.** Re-state which files changed.

### 7. Final report

Print one summary and stop:

```
/design-sync summary — <repo>

  theme   roles checked   drift   missing
  ------  -------------   -----   -------
  light        <n>         <n>      <n>
  dark         <n>         <n>      <n>

  adoption: color <0.xx> · font-size <0.xx> · radius <0.xx> · spacing <0.xx>
  contracts: <n> PASS · <n> WARN · <n> FAIL   (<failing ids>)
  rendered leg: <ran (harness results) | unmeasured — no rendered-geometry harness (project-scaffolding#157)>
  vendored: <n> identical · <n> forked · <n> not adopted
  siblings: <n> duplicate names (<top names>)
  nav contract: <ok | drifted: ...>
  cert: <ok | drift, filed #N>   (tailnet-cert conformance, step 1b)
  filed: https://github.com/<owner>/<repo>/issues/<N>   (design-drift)
  applied: <n files changed | not applied (report-only)>
```

The `cert:` line always appears (the step-1b check runs on every target). If the
lint reports zero drift, zero contract FAILs, and no forked vendored files, say
`In sync with design.md — no drift.` and still no-op the design-drift issue
(don't file an empty one; if a prior issue exists with all boxes now
satisfiable, leave it for the user to close) — the cert verdict and the
adoption ratios are reported regardless (ratios are the trend signal for #180
even on a clean app).

## Hard rules

- **Measure with `design_lint.py`, never by eye.** Ratios, token comparisons,
  contract greps, vendored byte-diffs, and sibling detection come from the
  helper (step 3) — the LLM never re-derives them. LLM judgment is confined to
  step 4 (alias leftovers, materiality, sibling arbitration, prose-only
  contract nuances).
- **Default mode never edits code.** Only `apply` writes files, and even then it
  never commits, pushes, or restarts.
- **One managed issue per repo per kind — the helper owns identity.** Always go
  through `skills/_lib/audit_issue.py` (`get` then `upsert`) — `--kind design-drift`
  for CSS, `--kind cert-drift` for the step-1b cert finding. Never hand-roll a
  `gh issue create` — that is what spawns duplicates.
- **Keep cert-drift out of design-drift.** The tailnet-cert finding is its own
  bucket/issue (so `/cleanup-fleet cert-drift` targets it cleanly and it points at
  `project-scaffolding#89`); never fold a cert finding into the CSS `design-drift`
  issue, and never auto-apply the cert migration.
- **Skip Streamlit POC spikes** — never report or apply against them.
- **Never re-author navigation/components.** Reuse the vendored snippets from
  `project-scaffolding` verbatim (the same model as `single_instance.py` /
  `tray_lifecycle.ps1`). A divergent nav is a finding, not a thing to rewrite.
- **Materiality bar.** A senior dev must agree the drift is worth fixing. One-unit
  radius/spacing nitpicks are not findings; wrong palette, missing dark theme, or
  a hand-rolled nav are.
- **Citations or it didn't happen.** Every finding points at a real `file:line`
  (or `file` for a whole-block finding) and names the spec token it diverges from.
- **Never auto-tick or auto-close** the issue — it's a living backlog; closing is
  the user's call via `/issue-finish`.
- **No AI attribution; no hard-wrapped issue-body paragraphs** (per global
  CLAUDE.md).

## Notes

- This is the per-repo detector; the fleet-wide weekly sweep + `/audit-fleet`
  digest integration are a follow-up (`fleet-config#180`) — until then run it
  per repo. The step-1b cert check rides that sweep when it lands.
- `design-drift` and `cert-drift` are both first-class audit buckets
  (`audit_issue.py` `KINDS`) — `/cleanup-fleet` fans out fixers for each, and
  `/issue-triage` treats both like any other issue.
- The cert convention lives in `project-scaffolding#89`; the heuristic in the
  pure, unit-tested `skills/_lib/cert_drift.py`. The lint lenses live in
  `skills/_lib/design_lint.py` (unit-tested, wired into `run_acceptance.py`);
  icon steps, contract targets, and the switch on-color are spec-driven, so a
  spec change propagates without touching the helper. v2 provenance: #277 +
  #278 + project-scaffolding#120.
- The spec, not this skill, owns *what* the look should be — refine
  `design.md` / `design.dark.md` to change the identity; this skill only
  measures and (optionally) applies conformance.
- All structural findings (contracts, vendored forks, siblings) fold into the
  same `design-drift` issue as token drift — separate finding types, not
  separate buckets/labels (fleet-config#231).
