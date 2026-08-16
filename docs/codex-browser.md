# Codex Browser plugin — diagnosing a missing `iab` backend

Codex ships an OpenAI-bundled **Browser** plugin. Its instructions tell the agent to drive a real browser after significant frontend changes to a local app (via `agent.browsers`). The catch: the plugin's *instructions* load even when the plugin's *runtime backend* isn't actually live, so the agent can confidently reach for a browser that isn't there. This note is the durable recovery path for that failure mode.

## The failure mode

Observed while working in `illustration-color-edit`: the Browser plugin was present, `~/.codex/config.toml` had `browser@openai-bundled` enabled plus `BROWSER_USE_AVAILABLE_BACKENDS = "iab"`, yet from the session:

- `agent.browsers.list()` returned `[]`
- `agent.browsers.get("iab")` failed with `Browser is not available: iab`

So config said "use `iab`", the plugin files were on disk, but no backend was registered at runtime — the agent had no usable browser.

## Installed plugin files ≠ a live backend

These are two different things, and the distinction is the whole point:

- **Installed plugin files** are Codex-managed state on disk — e.g. `C:\Users\rober\.codex\plugins\cache\openai-bundled\browser\26.601.21317`. Their presence only means the bundle was downloaded and its instructions will load.
- **A live backend** is a runtime registration inside the running Codex client. `agent.browsers.list()` reports *this*, not the files. An empty list means nothing is wired up *right now*, regardless of what's cached on disk or enabled in `config.toml`.

You can have the files and the config and still get `[]`. Don't infer availability from `config.toml` or the cache directory — only the live `list()` call is authoritative.

## Diagnose it from a Codex session

Run this from the session before relying on the browser:

```text
agent.browsers.list()
```

Three outcomes:

| `list()` result | What it means | What to do |
|---|---|---|
| `[]` **and** no `browser@openai-bundled` in `config.toml` / no cache dir | Plugin **missing** | Enable the Browser plugin in the Codex host UI (it isn't installed by this repo). |
| `[]` **but** plugin enabled in `config.toml` and cache dir present | Plugin **installed, backend not registered** | Restart Codex; confirm the Browser pane/plugin is enabled in the host UI. See recovery below. |
| `["iab", …]` (and `agent.browsers.get("iab")` succeeds) | Backend **available** | Proceed — use the browser as the global instructions direct. |

## Recovery when `list()` returns `[]`

This is **client/runtime state, not a repo dependency.** There is nothing to `pip install`, `npm install`, or add to this repo — the Browser backend lives entirely inside the Codex desktop client. Concretely:

1. **Restart Codex.** The backend registers at client startup; a stale or half-initialized session is the most common cause of an empty list.
2. **Confirm the Browser plugin / browser pane is enabled in the Codex host UI.** Config enabling it (`browser@openai-bundled`, `BROWSER_USE_AVAILABLE_BACKENDS = "iab"`) is necessary but not sufficient — the host UI must actually surface the pane.
3. **Re-run `agent.browsers.list()`** to confirm `iab` is now present before retrying the browser step.

If `list()` is still `[]` after a restart with the plugin enabled, the in-app backend is genuinely gone for this session — but that no longer means abandoning browser verification. **Use the Playwright fallback below** to complete the visual gate with a real local browser; do **not** try to "install" the `iab` backend from the session.

## Playwright fallback when `iab` stays `[]`

A missing `iab` backend used to force the agent onto non-browser verification, which **cannot** satisfy `/issue-finish`'s (and `/issue-yolo`'s) visual leg — the design-conformance gate needs an actual render. The fleet ships a working Playwright + real-Chrome stack, so `iab` absence is a *fallback*, not a dead end (fleet-config#351).

The decision is deterministic and single-sourced in [`skills/_lib/browser_verify.py`](../skills/_lib/browser_verify.py). Preflight `iab`; when it's unavailable, drive Playwright with real Chrome against the live feature-branch app:

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/browser_verify.py plan <repo> --base-url <app-root> --iab-available no
```

It prints (KEY=VALUE, JSON where structured):

- **`BACKEND`** — `iab` when the in-app backend is live (use it unchanged), else `playwright`.
- **`KEY_VIEWS`** — the views to render, from the repo's `## UX surface` block (same source `ux_surface.py` reads, so the two never disagree). `SPEC_APPLIES=no` → nothing to render.
- **`VENV_PYTHON` / `PLAYWRIGHT_PROBE`** — the discovered project `.venv` interpreter and the `import playwright` probe command.
- **`LAUNCH_KWARGS` / `INIT_SCRIPT`** — `launch_persistent_context` kwargs implementing the fleet browser-safety contract (real Chrome `channel="chrome"`, `--enable-automation` stripped, `AutomationControlled` disabled, persistent profile, 1280×900) plus the `navigator.webdriver` init script. The fallback **composes** this contract, it never re-inlines launch args — see the "Browser automation must not look like a bot" and shared-Chrome-profile rules in `global-CLAUDE.md`.
- **`CAPTURES`** — one screenshot spec per `KEY_VIEWS` entry × {light, dark}, each written to a **local, gitignored scratch dir** (`SCRATCH_DIR`, under system temp). Inspect in-session only; **never** attach a UI screenshot to a PR, issue, or comment.
- **`FAILURES`** — the distinct, actionable message for each capability failure. These do **not** collapse into one generic error:
  - `PLAYWRIGHT_MISSING` — Playwright not importable from the `.venv` (install it into the existing `.venv`, never a bare `venv`).
  - `CHROME_MISSING` — real Chrome absent (install Chrome; do **not** fall back to bundled Chromium — it fails bot detection).
  - `APP_UNREACHABLE` — the live app didn't answer at `--base-url` (launch it via the project's run/verify skill; the browser stack is fine, the target is down).
  - `PROFILE_LOCK_EXHAUSTED` — the persistent profile stayed locked past the 60/120/240/480s backoff (a sibling job holds it — **wait**, never kill the holder, or use a distinct profile dir).
  - `RENDER_FAILED` — Playwright launched and the app is reachable but a capture errored (inspect the page error for that view).

The pure decision logic (backend choice, launch kwargs, capture plan, failure classification) is covered by `tests/test_browser_verify.py`, run from `tests/run_acceptance.py`.

## What this repo does and doesn't own

`install.ps1` links this repo's shared assets into `~/.codex` (see [`cross-agent-parity.md`](cross-agent-parity.md)). It deliberately does **not** install or manage the OpenAI-bundled Browser plugin runtime — that bundle is Codex-managed state outside this repo. Fixing an empty `list()` is always a Codex-client action, never a change to `fleet-config`.

What this repo **does** own is the skill-level preflight-and-fallback: the deterministic backend choice and Playwright launch/capture contract in `skills/_lib/browser_verify.py`, so the visual gate stays operational even when the proprietary `iab` backend is absent.
