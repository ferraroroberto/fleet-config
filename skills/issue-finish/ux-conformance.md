# UX-conformance legs (`/issue-finish` step 3b, UX surface touched)

Loaded on demand from `/issue-finish` step 3b when `ux_surface.py check .` printed `TOUCHED=yes` or `TOUCHED=unknown`, or an override (`ux`, `design`, `ux-full`) forced the gate. Run both legs, then return to step 3b's overrides and on to step 3c.

- `TOUCHED=yes` → run both legs against the files in `MATCHED`:
  - **Token check (fix-now).** Compare the touched CSS custom properties (light
    **and** dark) and the nav contract to `~/.claude/design.md` +
    `design.dark.md`, and **fix material drift in this branch now**, committing
    it — do *not* file-and-defer a `design-drift` issue (that's `/design-sync`'s
    periodic-sweep job; this gate's job is to not *introduce* drift).
    Materiality bar: a wrong canvas color, missing dark theme, hand-rolled nav,
    or broken layout is a blocker; a 1-unit radius nitpick is not.
  - **Visual proof (merge gate).** Every UI diff needs `.proof/<PR>/index.html`: `ui_proof capture --phase before|after --url <gate instance>` (main, branch), then once the PR exists `ui_proof build . --pr <PR>` with one `--criterion` line per criterion (`C:/Users/rober/.claude/skills/_lib/ui_proof`, this venv). Where `layout_lint` is vendored (`ENFORCED=yes`), also capture each criterion's state with `capture_states` and `ui_proof record`. The merge hook enforces this: `ui_proof_merge_guard` refuses `gh pr merge` until the page exists and, where enforced, the proof is fresh; fix what its `NEED=` names. Run `ui_proof check . --pr <PR>` once for the `VISUAL_PROOF=` line, which goes verbatim into the PR body and step-7 report; `ENFORCED=no` → also the visual check below. After the merge, before branch/worktree cleanup, re-run `build --sha <merge sha>` (it copies the proof into the primary's `.proof/`) and send the page privately; report `SENT=`. Commands and states: [ux-conformance-gate](E:/automation/fleet-config/docs/skills/ux-conformance-gate.md).
  - **Visual check (in-session only — never attach the image).** Launch the
    feature-branch working tree, look at the touched view via the repo's own
    launch skill (app-launcher's project `verify`), else Claude Code's built-in
    `run` skill (with `ux-full`, every `KEY_VIEWS` entry, not just the touched one).
    Inspect the render against the spec — nav pill, layout, palette. Save the
    screenshot to a local scratch path, never commit it, and **never attach it
    to the PR body, an issue, or a comment** — assume every repo is public; a
    UI screenshot is an information breach. Text-only conformance line in the
    PR instead (e.g. `Visual: touched view renders per spec — nav pill, layout,
    palette conform`).
    - **Browser-backend preflight (Codex — no live `iab`).** Pick the backend
      deterministically: prefer the in-app Browser (`iab`) when
      `agent.browsers.list()` includes it; `[]` → fall back to installed
      Playwright with real Chrome — `iab` absence is **not** a reason to skip
      the visual leg (fleet-config#351). Get the plan (backend, venv,
      `channel="chrome"` launch kwargs honoring the browser-safety contract,
      the `KEY_VIEWS` × {light, dark} capture list) from:
      ```
      E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/browser_verify.py plan . --base-url <app-root> --iab-available <yes|no>
      ```
      Missing Playwright, missing Chrome, unreachable app, or exhausted
      profile-lock each report distinctly (never one generic error) — report
      only the one observed. Codes/background/recovery: `docs/codex-browser.md`.
      Claude Code: the launch skill's MCP browser is the `iab`-equivalent path.
