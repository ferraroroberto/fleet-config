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

If `list()` is still `[]` after a restart with the plugin enabled, treat the browser step as unavailable and fall back to non-browser verification — do **not** try to "install" the backend from the session.

## What this repo does and doesn't own

`install.ps1` links this repo's shared assets into `~/.codex` (see the [Codex parity section in the README](../README.md#codex-parity--one-source-both-agents)). It deliberately does **not** install or manage the OpenAI-bundled Browser plugin runtime — that bundle is Codex-managed state outside this repo. Fixing an empty `list()` is always a Codex-client action, never a change to `claude-config`.
