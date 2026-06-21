---
name: design-sync
description: Check a web app's CSS custom properties (light + dark) against the fleet design system (~/.claude/design.md + design.dark.md), report token drift, and file a deduped design-drift issue so /cleanup-fleet design-drift can fix it later — optionally applying the alignment to the working tree. Skips Streamlit POC spikes. Use when aligning a web app to the fleet look, e.g. "/design-sync", "/design-sync app-launcher", "check design drift", "sync this app to design.md", or "/design-sync apply" to write the aligned tokens.
---

# design-sync

**Goal:** Keep every fleet web app (FastAPI + static PWA) true to the one shared
visual identity, navigation, and interaction language in `~/.claude/design.md`
(light) + `~/.claude/design.dark.md` (dark). The skill reads the spec, maps its
tokens onto a target app's CSS custom properties (light **and** dark), surfaces
the values that have **drifted** from the spec, and files exactly one deduped
`design-drift` issue per repo — the same audit→bucket→cleanup machinery as
`/codebase-audit`. So a weekly run produces a bucket of drift issues you can clear
all at once with `/cleanup-fleet design-drift`. With `apply`, it also writes the
aligned token values into the working tree for you to review.

**Default mode files an issue; it does not edit code.** Reporting + upserting the
`design-drift` issue is the only side effect unless you pass `apply` (step 8).
Never commit, push, or restart anything.

## Arguments

- No argument → the **current repo** (cwd).
- One argument that is a path or repo name → that **target repo** (resolve
  relative to `E:/automation/<name>` or as a path; must be a git repo).
- The word `apply` anywhere in the args (`/design-sync apply`,
  `/design-sync app-launcher apply`) → after reporting, **apply** the spec values
  to the app's CSS in the working tree (step 8). Without it, the run is read-only
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

### 3. Load the spec (light + dark)

Read both spec files in full from the user home (they are junctioned there):
- `~/.claude/design.md` — light values.
- `~/.claude/design.dark.md` — dark values.

(On Windows: `$env:USERPROFILE/.claude/design.md` / `design.dark.md`.)

Parse the frontmatter token groups — `colors`, `typography`, `rounded`,
`spacing`, `components` — into a light map and a dark map keyed by token name
(the names are identical across the two files; only values differ).

### 4. Parse the app's CSS custom properties

From the stylesheets kept in step 2, extract the app's custom properties for both
themes:
- **Light** — the base `:root { … }` block.
- **Dark** — the `[data-theme="dark"]`, `:root[data-theme="dark"]`, or
  `@media (prefers-color-scheme: dark)` block.

Record each `--var: value;` with its file:line, per theme.

### 5. Map spec tokens onto the app's variables (by role)

The app's variable names will differ from the spec's token names (e.g. an app's
`--bg` is the spec's `colors.canvas`; `--text` is `colors.fg`; `--link`/`--accent`
is `colors.accent`). Map by **semantic role**, using the variable name, its
inline comment, and how it's used. Cover at least:

- surface roles → `colors.canvas`, `colors.canvas-subtle`, `colors.card`
- line roles → `colors.border`, `colors.border-muted`
- text roles → `colors.fg`, `colors.fg-muted`
- accent / CTA / link → `colors.accent` (+ `colors.accent-fg` for on-accent text)
- state → `colors.success`, `colors.danger`, `colors.attention`
- radii → `rounded.*`; spacing scale → `spacing.*`

A spec token with **no** corresponding app variable is a *missing-token* finding;
an app variable whose value differs from the mapped spec value is a *drift*
finding. Note your mapping in the issue so a fixer can trust it.

### 6. Compute drift (light and dark)

For every mapped role, compare the app's value to the spec value **in both
themes**. A finding is:
- **drift** — mapped variable's value ≠ spec value (e.g. `--bg: #0a0f1a` vs
  `colors.canvas: #0d1117` in dark), or
- **missing** — a spec token with no app variable for that role.

Also check the **navigation/interaction contract** (the part the spec cares most
about): does the app implement the fixed bottom-tab pill per the spec's
*Navigation & interaction* section (viewport-anchored via `100dvh` + safe-area,
`rounded.nav`, backdrop blur, single active tab, `localStorage` persistence,
hide-under-open-`dialog`, ≥44px targets)? A re-implemented or divergent nav is a
finding whose fix is to **adopt the vendored nav snippet from
`project-scaffolding`** (do not re-author it). If that vendored library does not
exist yet, cite the follow-up that tracks it rather than hand-rolling a nav.

Apply a materiality bar: a 1-unit radius nitpick is not a finding; a wrong canvas
color, a missing dark theme, or a hand-rolled nav is.

### 7. Dedupe and upsert the `design-drift` issue

Exactly one managed `design-drift` issue per repo, reused across runs — identical
mechanics to `/codebase-audit`'s bucket issues. Never `gh issue create` by hand.

1. **Ensure the label** (idempotent):

   ```
   gh label create design-drift --color '006b75' --description 'Web-app CSS tokens drift from the fleet design.md spec' || true
   ```

2. **Fetch the existing issue:**

   ```
   py C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind design-drift
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
   py C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
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
Surfaced by `/design-sync`, kept up to date across runs. Spec: `~/.claude/design.md` (+ `design.dark.md`).

## Findings

- [ ] **<file>:<line>** — `--<var>` (role `<spec token>`, <light|dark>): app `<value>` ≠ spec `<value>`. Fix: set to `<spec value>`.
- [ ] **<file>** — missing dark theme block; spec defines `design.dark.md`. Fix: add `[data-theme="dark"]` with the spec's dark values.
- [ ] **<file>** — bottom nav re-implemented, diverges from the spec contract. Fix: adopt the vendored nav snippet from `project-scaffolding`.

## Token map (spec role → app var)

<one line per mapped role, so a fixer can trust the comparison>

## Context

<One short paragraph: the overall shape of the drift (e.g. "navy palette, not the GitHub true-black look"), and anything the next fixer should know.>

## Drift run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Titles are **stable** — `audit: design-drift findings`, no count suffix.

### 8. Optionally apply (only when `apply` was passed)

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

### 9. Final report

Print one summary and stop:

```
/design-sync summary — <repo>

  theme   roles checked   drift   missing
  ------  -------------   -----   -------
  light        <n>         <n>      <n>
  dark         <n>         <n>      <n>

  nav contract: <ok | drifted: ...>
  filed: https://github.com/<owner>/<repo>/issues/<N>   (design-drift)
  applied: <n files changed | not applied (report-only)>
```

If there is zero drift and the nav contract holds, say
`In sync with design.md — no drift.` and still no-op the issue (don't file an
empty one; if a prior issue exists with all boxes now satisfiable, leave it for
the user to close).

## Hard rules

- **Default mode never edits code.** Only `apply` writes files, and even then it
  never commits, pushes, or restarts.
- **One managed issue per repo — the helper owns identity.** Always go through
  `skills/_lib/audit_issue.py` (`get` then `upsert`, `--kind design-drift`).
  Never hand-roll a `gh issue create` — that is what spawns duplicates.
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

- This is the per-repo detector. A fleet-wide weekly sweep (loop every web app,
  one digest) + `/audit-fleet` digest integration for the `design-drift` bucket
  are tracked as a follow-up — until then run `/design-sync` per repo.
- `design-drift` is a first-class audit bucket (`skills/_lib/audit_issue.py`
  `KINDS`), so `/cleanup-fleet design-drift` already fans out fixers across the
  fleet, and `/issue-triage` treats the issue like any other.
- The spec, not this skill, is the source of truth for *what* the look should be.
  Refine `design.md` / `design.dark.md` when the identity itself should change;
  this skill only measures and (optionally) applies conformance to it.
