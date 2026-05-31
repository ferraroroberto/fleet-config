# Slack ↔ Claude workflow

How I get Claude and my unattended agents to reach me on Slack, and how I drive work from Slack. This is the durable reference for the capability built in `claude-config` (issue #26) plus the one Anthropic-hosted piece that isn't code.

## Why Slack at all, working solo

Solo doesn't mean synchronous. The fleet runs **unattended** agents — `schedule-autoheal`, `audit-fleet` on a weekly app-launcher job, the planning schedulers. Their whole value is working while I'm AFK. The bottleneck isn't collaboration, it's **reachability**: when a headless job gets stuck at 2pm and I'm away from the machine, a desktop toast is useless. A Slack notification hits my phone. Slack is the universal async inbox for my own fleet of agents — and, with the native integration, the remote control to kick off or steer work from my phone.

## The three mechanisms (transport vs. trigger)

| Mechanism | Who triggers | Direction | Use it for |
|---|---|---|---|
| **Bot helper** — `slack_notify.py` (`chat.postMessage`, `xoxb-` token) | a skill/job calls it explicitly | machine→me | unattended jobs that decide to alert me ("I'm stuck / I'm done") |
| **Session hook** — `notify_on_idle.py` (`Notification` event) | fires automatically when a live session needs input / goes idle | machine→me | "stop babysitting" my interactive sessions |
| **Native "Claude in Slack"** (`@Claude` in a channel) | I @mention from Slack | me→machine | driving / delegating work from my phone (spins up a cloud Claude Code session) |

The **bot helper is the pipe**. The **session hook is an automatic trigger** that rides the same pipe. **Native integration is its own inbound pipe** — a separate Anthropic product, no code, set up once in the account/Slack admin.

Not to be confused with the **claude.ai Slack MCP connector** (`mcp__claude_ai_Slack__*`): that posts *as me*, and Slack never notifies me about my own messages, so it can't deliver a notification. That's exactly why the bot helper exists.

## 1. Bot helper — `slack_notify.py`

Lives at `hooks/slack_notify.py`, reachable fleet-wide as `~/.claude/hooks/slack_notify.py` via the `hooks/` junction — zero install. Uses stdlib `urllib` (hooks run on system Python, no venv, no `requests`).

CLI:

```bash
py ~/.claude/hooks/slack_notify.py --channel C0123ABCD --text "stuck on twitter, come look"
echo "a longer body" | py ~/.claude/hooks/slack_notify.py --channel C0123ABCD
```

Import:

```python
import slack_notify
slack_notify.notify("done", channel="C0123ABCD")          # bare channel id
slack_notify.notify("done", channel="U0123ABCD")          # user id → DM
slack_notify.notify("done", channel="https://x.slack.com/archives/C0123ABCD")  # archive URL also accepted
```

`--channel` / `channel=` accepts a bare channel id, a user id (for a DM), or a pasted archive URL (the id is parsed out). The call **never raises**: a missing token, bad channel, network error, or Slack API error logs to stderr and returns `False` / a non-zero exit, so an unattended job keeps running.

**Manual / conversational pings go here too.** When I ask a session to "ping me on Slack" or "notify me like you do when you finish a job" — *outside* a skill — the answer is still this CLI, **not** the Slack MCP connector. The `@mention` (`<@U…>`) is mandatory; without it Slack delivers no mobile push.

```bash
py ~/.claude/hooks/slack_notify.py --channel C0123ABCD --text "<@U0123ABCD> ✅ [project] <message>"
```

A model that reaches for `mcp__claude_ai_Slack__send_message` instead has made the classic mistake below: it posts as me and pings nobody.

### Completion pings — `notify_complete.py`

The `issue-*` skills don't hand-assemble their "done" message — that invites paraphrase, a wrong/missing PR link, or a dropped ping. They call `notify_complete.py` with structured args, and the **canonical format + the real GitHub URL are built in Python** (via `gh pr view` / `gh issue view`), so every completion ping is byte-identical and correctly linked. The model only passes the numbers it already has.

```bash
py ~/.claude/hooks/notify_complete.py --kind finish --issue 30 --pr 31     # ✅ Done #30 <title> — PR merged · <pr-url>
py ~/.claude/hooks/notify_complete.py --kind add    --issue 30             # 🆕 Filed #30 <title> · <issue-url>
py ~/.claude/hooks/notify_complete.py --kind start  --issue 30 --summary "review the diff, then /issue-finish"   # 🚦 #30 <title> — ready to validate. …
py ~/.claude/hooks/notify_complete.py --kind yolo   --issue 30 --pr 31     # 🚀 Shipped #30 <title> — PR · <pr-url>
py ~/.claude/hooks/notify_complete.py --kind batch  --passed 2 --total 3   # 🏁 Batch done: 2/3 passed — …
```

Every kind **leads with a terminal mark** (`✅ 🆕 🚦 🏁 🚀`) — the signal the idle hook reads to suppress the redundant follow-up idle ping. It resolves channel/user via the shared `_lib.resolve_slack_target()` (project override → `[global]` fallback), is a silent no-op when no channel is configured, and always exits 0 — a notification failure can never block a skill. The one thing it can't force is the model remembering to *call* it; making the firing itself deterministic would need a merge-detecting hook, which is more brittle than it's worth.

## 2. Session hook — `notify_on_idle.py`

Wired to Claude Code's `Notification` event (Claude needs input / a permission / has gone idle). It rides `slack_notify`, so an AFK human gets a phone notification instead of a toast.

**Opt-in, default off.** It does nothing unless the current project sets `slack_notify_channel` in `hooks/projects.toml`, or a `[global] slack_notify_channel` fallback is set:

```toml
[content-management]
cwd_prefix           = "E:/automation/content-management"
slack_notify_channel = "C0123ABCD"   # bare channel id (or a user id for a DM)
slack_notify_user    = "U0123ABCD"   # optional: @mention this user for mobile push

[global]
slack_notify_channel = "C0123ABCD"   # fleet-wide fallback
slack_notify_user    = "U0123ABCD"   # fleet-wide mention fallback
```

With neither channel set, the hook is a silent no-op — that keeps notification noise off by default. It hooks `Notification` (not `Stop`) deliberately, so it doesn't ping on every turn-end.

**Verify it's actually wired.** `settings.template.json` carries the `Notification` block, but `install.ps1` merges it into your live `~/.claude/settings.json` *once* — there's no re-sync, so the live file can silently drift and lose the block (then idle/permission pings just never fire). Confirm with `py -c "import json;print(list(json.load(open(r'C:/Users/rober/.claude/settings.json'))['hooks']))"` — `Notification` must be in the list. After re-adding it, restart Claude Code (or open `/hooks` once) so the harness reloads settings.

**`slack_notify_user` is required for reliable mobile push.** Slack only delivers phone notifications for @mentions and DMs — a bare channel message is silently delivered without a push. Set `slack_notify_user` to your Slack user id (find it in your Slack profile → *Copy member ID*) and every fleet notification will @mention you, guaranteeing the push.

**What the ping can and can't say.** The payload carries only `notification_type` (`permission_prompt` / `idle_prompt`) and a generic `message`; the hook icons by type — 🔔 for a permission prompt, 💤 for an idle wait. A `permission_prompt` is reworded to `Claude awaits your input` (it's as often an `AskUserQuestion` as a real permission gate, so "needs your permission" overclaims); idle and other types pass the message through. It can't say *what* Claude is waiting on: in a remote-control / bridge session the tool being gated lives in the cloud transcript, and the local `transcript_path` holds only bridge metadata (no `tool_use`), so a question (`AskUserQuestion`) and a real permission gate are indistinguishable locally. Don't add transcript-tool-sniffing here expecting it to work from the phone — it won't.

**Deep-link back into the session.** A bridge session's transcript opens with a `bridge-session` entry whose `bridgeSessionId` (`cse_01H…`) maps to `https://claude.ai/code/session_01H…` (drop the `cse_` prefix). The hook appends that URL to the ping (`… · https://claude.ai/code/session_…`) so you can tap the notification and resume on the web. Local terminal sessions have no bridge entry, so no link is appended.

**No idle ping right after a completion.** When a `notify_complete` ping (which leads with a terminal mark `✅ 🆕 🚦 🏁 🚀`) just landed, the follow-up 60s-idle "waiting for your input" is pure noise. Before sending an `idle_prompt`, the hook reads the channel (`conversations.history`, needs the bot's `channels:history`/`groups:history` scope) and skips if the latest ping it sent you leads with a terminal mark within the last 10 minutes. This works across local and cloud/bridge sessions because Slack is the shared medium — the completion ping may originate in the cloud while the idle hook runs locally. It fails open: any missing scope or network error lets the idle ping through.

## 3. Native "Claude in Slack" (remote control)

This is the answer to "drive work from my phone". `@Claude` in a Slack channel → Claude detects coding intent, spins up a Claude Code session **on the web**, posts status back to the thread as it works, and finishes by @mentioning you with a summary + a PR link + action buttons. It does **not** use the bot helper or `SLACK_BOT_TOKEN` — it's a separate Anthropic-hosted product. Setup is account/Slack-admin, not code.

### One-time admin setup (repeatable)

1. **Install the Claude app to the workspace** — open the Slack Marketplace listing (<https://slack.com/marketplace/A08SF47R6P4-claude>) → **Add to Slack**. If your workspace requires admin approval for apps, a Workspace Owner/Admin must approve it (Slack admin → *Manage apps*). On a workspace you own, you approve it yourself.
2. **Connect it to your Claude account** — the first time you @mention `@Claude`, it replies with a link to authenticate; sign in with the Claude account whose Claude Code subscription should run the sessions. This binds the Slack app to your Claude account.
3. **Add Claude to the channels you want it in** — in each channel, `/invite @Claude` (it only responds in channels it's been added to). For solo use, a private channel or a DM with Claude works.
4. **Pick the response mode** (in the app's settings):
   - **Code Only** — every `@Claude` mention routes to a Claude Code session.
   - **Code + Chat** — Claude decides per-message whether to answer in chat or spin up a coding session.
5. **Use it** — `@Claude <task>` in a thread. It gathers thread context, works on the web, streams status to the thread, and hands back a summary + PR link. Continue the session or open the PR from the web at claude.ai/code.

Docs: <https://code.claude.com/docs/en/slack> · Help: <https://support.claude.com/en/articles/11506255-get-started-with-claude-in-slack>.

### Native vs. the bot helper — don't confuse them

The bot helper (mechanism 1) and native integration are independent and solve opposite directions. Native is **inbound** (you → machine, drive work remotely); the bot helper is **outbound** (machine → you, alerts from unattended jobs). They don't share a token, a channel requirement, or any setup. Run both: native to launch/steer work from your phone, the bot helper so headless jobs can reach you.

## One-time bot setup (for mechanisms 1 & 2)

1. Create a Slack app at <https://api.slack.com/apps> → **Add a Bot User** → give it the `chat:write` scope → **Install to Workspace**. Copy the `xoxb-…` **Bot User OAuth Token**.
2. `/invite` the bot to each target channel (e.g. `#claude`).
3. Put the token in `~/.claude/settings.json`'s `env` block — **never** committed, never in `projects.toml` or `settings.template.json`:

   ```json
   { "env": { "SLACK_BOT_TOKEN": "xoxb-…" } }
   ```

   That one place reaches every hook, skill, and venv subprocess (they inherit Claude Code's environment).
4. Verify with a live ping:

   ```bash
   py ~/.claude/hooks/slack_notify.py --channel C0123ABCD --text "✅ slack_notify is live"
   ```

## Consumers

- **`content-management` `schedule-autoheal`** — its "not confident" escalation posts via this helper (`content-management#68`), so the stuck-scheduler ping actually notifies me.
- Any future unattended job: call the CLI or import `notify`. No per-project install.
