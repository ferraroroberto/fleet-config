# MCP connector audit — context cost vs. actual usage

**Audit date:** 2026-06-15 · **Issue:** [#128](https://github.com/ferraroroberto/claude-config/issues/128) · **First migration:** [life-os#29](https://github.com/ferraroroberto/life-os/issues/29)

The question this answers: *how many MCP servers feed my sessions, how much context do they cost, how often is each actually used across the whole `E:\automation` fleet, and what should move off MCP onto plain scripts/CLI?*

## How MCP reaches these sessions

There is **no local MCP configuration** anywhere in the fleet — no `.mcp.json`, no `mcpServers` block in `~/.claude.json` (top-level or per-project), and no MCP keys in `~/.claude/settings.json`. Every connector below is therefore an **account-level claude.ai managed connector**, attached to the Claude account and auto-injected into every Claude Code session on this machine. The one custom server in the fleet (`mcp-personal-onedrive`) exists for claude.ai web/mobile and is **not** wired into Claude Code locally.

Consequence: enabling/disabling a connector is a **claude.ai account setting** (Settings → Connectors), not a repo change. Whatever is enabled there is paid for in *every* session, fleet-wide.

## Inventory + real usage

Tool counts are the schemas each connector injects. Usage is the **actual invocation count** measured by grepping every session transcript under `~/.claude/projects/` (945 transcripts as of the audit date) for `"name":"mcp__…"` tool-use entries.

| Connector | Tools | Real invocations | Where used | Verdict |
|---|---:|---:|---|---|
| **Notion** | 14 | **115** | 100% in `life-os` (the daily journal) | **Migrate to script**, then disable |
| **Slack** | 19 | 33 | scattered (`search_channels`, `send_message`) | **Disable** — automation already uses the Python helper |
| **Gmail** | 12 | 16 | scattered, interactive (`search_threads`, `create_draft`) | **Disable** (re-enable on demand / scriptify if it recurs) |
| **Google Calendar** | 8 | 3 | `list_events`, interactive | **Disable** |
| **Google Drive** | 8 | **0** | never invoked | **Disable** |
| **Spotify** | 7 | **0** | never invoked | **Disable** |
| **Supabase** | 29 | **0** | never invoked | **Disable** (largest single schema cost) |
| **Uber Eats** | 2 | **0** | never invoked | **Disable** |
| **Zoom** | 7 | **0** | never invoked | **Disable** |
| **OneDrive** (custom, local) | 5 | **0** | never invoked *in Claude Code* | Keep server for web/mobile; not loaded in Code |
| **Total** | **111** | **167** | — | — |

Per-tool breakdown of the live ones: `notion-fetch` 45, `notion-search` 33, `notion-update-page` 20, `notion-create-pages` 17; `slack_search_channels` 15, `slack_send_message` 14; `gmail search_threads` 9, `create_draft` 4; `calendar list_events` 3.

### What the numbers say

- **One workflow dominates.** Notion is 115 of 167 invocations (69%), and every Notion call comes from the `life-os` daily journal. The entire "real automation" value of the connector set is one skill.
- **Five connectors are pure context tax.** Google Drive, Spotify, Supabase, Uber Eats, Zoom have **never once** been invoked across 945 transcripts. They contribute **53 tool schemas** (Supabase alone is 29) and provide zero value.
- **Slack is already off MCP for automation.** Completion pings and file posts go through `hooks/slack_notify.py` / `hooks/notify_complete.py` (stdlib Python, bot identity). The 33 Slack MCP calls are ad-hoc/interactive; nothing unattended depends on the connector.
- **Gmail/Calendar are low, interactive, and undepended-on.**

## Context cost

Precise tokens require running `/context` in a live session (and toggling connectors to diff). Estimating from the schema sizes: MCP tool definitions run ~150–300 tokens each (name + description + input JSON schema), so **111 tools ≈ 20–30K tokens** if eagerly injected — on the order of 10–15% of a 200K window, spent on every session before any project code loads.

**Caveat — deferral.** Recent Claude Code can defer MCP tool schemas behind a tool-search step (this very session shows the connector tools as *deferred*, loaded on demand via `ToolSearch`), which drops the **resting** cost toward zero until a tool is actually searched. Deferral mitigates but does not eliminate the cost (descriptions are still indexed; instruction blocks like Supabase's and Uber Eats' are still present), and it does nothing about the *conceptual* clutter of 111 tools that are 97% unused. To confirm the real figure: run `/context` with the current set, then again after disabling the zero-use connectors, and record the delta.

## Recommendations

Ordered by value-for-effort:

1. **Disable the five zero-use connectors now** (Google Drive, Spotify, Supabase, Uber Eats, Zoom) in claude.ai → Connectors. Removes 53 tool schemas for zero functional loss. No code, no migration — pure win.
2. **Disable Slack, Gmail, Calendar as default connectors.** Automation doesn't depend on them; re-enable interactively when genuinely needed. Slack's automation path (the Python helper) is unaffected.
3. **Migrate the journal off the Notion MCP** ([life-os#29](https://github.com/ferraroroberto/life-os/issues/29)) onto a Notion REST script, reusing the proven `inspiration-system/src/notion_client.py` pattern (`NOTION_API_TOKEN` integration). Once it's proven, disable the Notion connector too. This converts the only real connector dependency into a script and reclaims the last 14 schemas.
4. **Leave `mcp-personal-onedrive` as-is.** It serves the claude.ai web/mobile gap (consumer OneDrive) and is not loaded into Claude Code, so it costs nothing here.

End state: **zero account connectors enabled by default**, the journal running on a script, and any interactive need met by toggling a connector on for that session. Estimated reclaim: the full ~20–30K-token connector tax (confirm via `/context`).

## Generalizable convention (→ project-scaffolding)

> For unattended/automation workflows, prefer a thin Python script (standard SDK or REST) over a session-injected MCP connector. Reserve connectors for genuinely interactive, exploratory use, and keep the **default** session connector set minimal — every enabled connector is a fleet-wide, every-session context cost regardless of whether a given session uses it.

This mirrors the existing fleet rules ("don't duplicate hub functionality" / Slack-via-Python-helper) and is routed up to `project-scaffolding` so the whole fleet inherits it: [project-scaffolding#65](https://github.com/ferraroroberto/project-scaffolding/issues/65).

## Method (reproducible)

```bash
# Inventory: connectors are account-level — no local config to read.
# Confirm absence of local MCP config:
find /e/automation -maxdepth 3 -name .mcp.json          # → none

# Usage: count real invocations across every transcript
grep -roh '"name":"mcp__claude_ai_[A-Za-z_]*__[a-z_-]*' \
  /c/Users/rober/.claude/projects/ \
  | sed 's/"name":"mcp__claude_ai_//' | sort | uniq -c | sort -rn

# Context: run /context in a live session, toggle connectors, diff.
```
