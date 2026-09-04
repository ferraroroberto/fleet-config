# Telegram ↔ Claude workflow

How I get Claude and my unattended agents to reach me on Telegram. This is the durable reference for the capability built in `fleet-config` (issue #26, migrated off Slack in #540).

## Why a chat app at all, working solo

Solo doesn't mean synchronous. The fleet runs **unattended** agents — `schedule-autoheal`, `audit-fleet` on a weekly app-launcher job, the planning schedulers. Their whole value is working while I'm AFK. The bottleneck isn't collaboration, it's **reachability**: when a headless job gets stuck at 2pm and I'm away from the machine, a desktop toast is useless. A Telegram notification hits my phone.

## Why Telegram, and not Slack any more

The `ferraroroberto` Slack workspace was downgraded to the free plan on 2026-08-02, and Slack was barely used for anything else. The deciding factor was **consolidation, not cost or breakage**: `whatsapp-radar`, `home-automation`, `app-launcher` and `app-launcher-lite` already delivered over Telegram, so the fleet was running two notification systems. Slack's free plan also drops message history after 90 days, which quietly undermined the `log` channel's stated job as an activity record.

The inbound half went away with it. Slack's native "Claude in Slack" (`@Claude` from the phone to spin up a cloud session) is **not** replaced by a Telegram command bot — there is deliberately no inbound Telegram path. Phone-side remote control is the **app-launcher Board over Tailscale**, which already covers that need and is what the `board_url` deep link below points at.

## The two mechanisms (transport vs. trigger)

| Mechanism | Who triggers | Direction | Use it for |
|---|---|---|---|
| **Bot helper** — `notify_send.py` (`sendMessage` / `sendDocument`, Bot API token) | a skill/job calls it explicitly | machine→me | unattended jobs that decide to alert me ("I'm stuck / I'm done") |
| **Session hook** — `notify_on_idle.py` (`Notification` event) | fires automatically when a live session needs input | machine→me | "stop babysitting" my interactive sessions |

The **bot helper is the pipe**. The **session hook is an automatic trigger** that rides the same pipe.

## The fleet's three chats

Every machine→human message in the fleet lands in exactly one of three chats. The axis is **who the message is for and how fast**, not which repo emitted it.

| Chat | Id | Carries | Fed by |
|---|---|---|---|
| **`family radar news`** | `-5052659143` | household matters and physical-world alarms: the family obligations digest, alarm arm/disarm, panel connection loss | `whatsapp-radar` (its own `.env`), `home-automation` (its own `config/notify_config.json`) |
| **`coding alerts`** | `-1004408175579` | **jobs and automation errors**, plus anything blocking a human right now | `app-launcher` job failures (its own `webapp_config.json`), `content-management` pipeline failures, and this repo's `attention` category |
| **`coding log`** | `-1004387099086` | everything else — the activity record | this repo's `log` category |

`family radar news` predates the split and is deliberately **not** routed by this repo. Its two senders each hold their own credentials and chat id, and neither imports anything from here; converging them is explicitly out of scope (see #540). Keeping them independent is what stops a fleet-config change from ever silencing the house alarm.

Mute `coding log`. Leave `coding alerts` and `family radar news` unmuted. That is the entire point of the split, and it is the one piece of setup no code can do for you.

## Chat routing by intent — attention vs log (issue #139)

This repo emits into the two coding chats only. One chat mixing "come look, I'm blocked" pings with "shipped, FYI" pings becomes unscannable: ten `🔔 awaits your input` interleaved with a `🚀 Shipped` hides what actually needs action. So every ping carries an **intent category**. The split axis is *"do I need to act now?"* — **not** which hook emitted it.

| Category | Chat | Pings |
|---|---|---|
| **`attention`** — act now | `coding alerts` (`telegram_chat_attention`) | `notify_on_idle` 🔔 awaits-your-input; `notify_complete` `start` (🚦 ready to validate), `batch` (🏁), `security` (🔒 audit self-healed a security gap — review the fix), `cleanup` *when issues await review* |
| **`log`** — activity record | `coding log` (`telegram_chat_log`) | `notify_complete` `add` 🆕 / `finish` ✅ / `yolo` 🚀 / `audit` 📊 / `recap` 🔄 / `learning` 📓 / `finish-batch` 🏁; the `/system-map` image; the `/insights-weekly` digest |

Note `start` and `batch` are calls-to-action that come out of the *completion* helper, so routing-by-hook would misfile them — they go to the attention chat.

**The routing is single-sourced** in `_lib.resolve_notify_target(cwd, category=…)`. It tries the per-category chat (project override → `[global]`) and **falls back to `telegram_chat` when that category chat is unset**. That fallback is what keeps a single-chat setup working unchanged and lets the split roll out one chat at a time. Config (in `hooks/projects.toml`):

```toml
[global]
telegram_chat           = "-1004408175579"   # fallback (used when a category chat is unset)
telegram_chat_attention = "-1004408175579"   # "coding alerts" — come-look pings
telegram_chat_log       = "-1004387099086"   # "coding log" — activity record
```

Both category keys are also valid as a per-project override. The bot must be a member of each chat — a bot cannot start a conversation, so you add it, it never adds itself.

**Use a supergroup or a channel (`-100…`), never a basic group.** Telegram silently rewrites a basic group's id when it upgrades it to a supergroup, and every send to the old id fails from that moment on. `notify_send._log_rejection` surfaces the replacement id when it happens, but the cheap fix is to never be in that position: convert the group deliberately at creation time (group settings → *Chat history for new members* → **Visible**, or enable Topics) and read the id afterwards.

Chat ids are committed here on purpose. A chat id is inert without the bot token — the same trust model as the Slack channel ids it replaces. **The token is never committed**: `fleet-config` is a public repo.

## 1. Bot helper — `notify_send.py`

Lives at `hooks/notify_send.py`, reachable fleet-wide as `~/.claude/hooks/notify_send.py` via the `hooks/` junction — zero install. Uses stdlib `urllib` (hooks run on system Python, no venv, no `requests`).

CLI:

```bash
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category attention --text "stuck on twitter, come look"
echo "a longer body" | E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category log
```

Import:

```python
import notify_send
notify_send.notify("done", chat="-1004408175579")   # supergroup / channel id
notify_send.notify("done", chat="123456789")        # a positive id is a private chat
notify_send.notify("done", chat="@somechannel")     # a public @name also works
```

`--chat` / `chat=` accepts a numeric chat id or an `@publicname` (a `https://t.me/<name>` link is reduced to `@<name>`). The call **never raises**: a missing token, bad chat id, network error, or Bot API error logs to stderr and returns `False` / a non-zero exit, so an unattended job keeps running.

Instead of a hardcoded `--chat`, a caller should pass `--category {attention,log}` to route by intent (see *Chat routing by intent* above) — the helper resolves the chat from `projects.toml`. The `/system-map` and `/insights-weekly` skills post their image/digest with `--category log`:

```bash
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category log --file architecture/system-map.png --text "🛠️ Fleet map"
```

`--chat` still wins when both are given; with neither, the CLI errors.

**Manual / conversational pings go here too.** When I ask a session to "ping me" or "notify me like you do when you finish a job" — *outside* a skill — the answer is still this CLI.

### The three Bot API constraints the transport absorbs

These are handled once, in the transport, so no call site has to know about them.

| Constraint | What it would break | How it is handled |
|---|---|---|
| `sendMessage` caps at **4096 characters** — and *rejects*, not truncates | any digest that grows past it, silently | `_chunks()` splits on line boundaries into numbered `[i/n]` messages; a single over-long line is hard-split with no content loss |
| A `sendPhoto`/`sendDocument` caption caps at **1024 characters** | `/insights-weekly` and `/fleet-health` pipe a multi-line digest as a `--file` caption | over 1024, the document is sent uncaptioned and the body follows as its own message(s) |
| `sendPhoto` re-encodes to JPEG and caps dimensions | `config-map.png` and `system-map.png` are dense diagrams; JPEG artefacts make the small labels unreadable | **always `sendDocument`** (50 MB, lossless — verified byte-identical on the 1,138,704-byte config map) |

**No `parse_mode`.** Messages go as plain text deliberately. Telegram rejects a *whole* message whose HTML/Markdown fails to parse, and these bodies are digests full of code fences, tables, `<`, `&` and unbalanced `*` — precisely the input that would make a formatted send fail as a unit. A ping that arrives unstyled beats one that silently doesn't arrive. Slack's `<url|label>` markup is flattened to `label: url` in the transport rather than translated into a second dialect that can also fail.

### Completion pings — `notify_complete.py`

The `issue-*` skills don't hand-assemble their "done" message — that invites paraphrase, a wrong/missing PR link, or a dropped ping. They call `notify_complete.py` with structured args, and the **canonical format + the real GitHub URL are built in Python** (via `gh pr view` / `gh issue view`), so every completion ping is byte-identical and correctly linked. The model only passes the numbers it already has.

```bash
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind finish --issue 30 --pr 31     # ✅ Done #30 <title> — PR merged · <pr-url>
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind add    --issue 30             # 🆕 Filed #30 <title> · <issue-url>
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind start  --issue 30 --summary "review the diff, then /issue-finish"   # 🚦 #30 <title> — ready to validate. … · <issue-url>
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind yolo   --issue 30 --pr 31     # 🚀 Shipped #30 <title> — PR · <pr-url>
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind security --issue 42 --pr 43 --summary "auto-merged, review the diff"   # 🔒 Security #42 <title> — auto-merged, review the diff · <pr-url>
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind batch  --passed 2 --total 3   # 🏁 Batch done: 2/3 passed — …
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind recap  --summary "3 skills swept | alt-text +2"   # 🔄 Weekly recap — 3 skills swept · alt-text +2
E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind design  --summary "8 swept | 3 drifted | 11 findings filed"   # 🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed
```

**Keep every `--summary` / `--text` pure ASCII** (emoji aside). A Windows command line is not a UTF-8-safe channel end to end: the harness → shell → `CreateProcess` leg re-encodes, and a literal `·` in a design-sweep summary arrived as `??` (fleet-config#507 — reproduced: a BOM-less UTF-8 command handed to Windows PowerShell 5.1 is decoded with the ANSI codepage, so `·`'s two UTF-8 bytes arrive as `Â·`, and a further narrowing to an OEM codepage turns that pair into `??`). Spell a multi-part summary's separator with the ASCII token `|` — `notify_complete.normalize_summary()` renders it as `·` from a **Python source literal**, the same reason the leading emoji and the em-dash always survived. Recoverable mojibake is repaired on the way in (`_lib.repair_mojibake`, also applied to `notify_send --text`), but a boundary that already replaced the character with `?` has destroyed it — hence the token, not just the repair. `--text` gets no token expansion: its body carries markdown, where `|` is a table cell. The acceptance matrix fails on any `SKILL.md` that authors non-ASCII punctuation into one of these arguments.

Every kind **leads with a status mark** (`✅ 🆕 🚦 🏁 🚀 📊 🔄`) as a glanceable cue. It maps each `--kind` to an intent category (`category_for()` — `start`/`batch`/`cleanup`-with-review → `attention`, the rest → `log`) and resolves the chat via the shared `_lib.resolve_notify_target(cwd, category=…)` (project override → `[global]` category chat → `telegram_chat` fallback), is a silent no-op when no chat is configured, and always exits 0 — a notification failure can never block a skill. The one thing it can't force is the model remembering to *call* it; making the firing itself deterministic would need a merge-detecting hook, which is more brittle than it's worth.

## 2. Session hook — `notify_on_idle.py`

Wired to Claude Code's `Notification` event (Claude needs input / a permission / has gone idle). It rides `notify_send`, so an AFK human gets a phone notification instead of a toast.

**Opt-in, default off.** It does nothing unless the current project sets `telegram_chat` in `hooks/projects.toml`, or a `[global] telegram_chat` fallback is set:

```toml
[content-management]
cwd_prefix    = "E:/automation/content-management"
telegram_chat = "-1004408175579"   # numeric chat id

[global]
telegram_chat = "-1004408175579"   # fleet-wide fallback
```

The hook posts with `category="attention"`, so when `telegram_chat_attention` is set (see *Chat routing by intent* above) its pings land in `coding alerts`; otherwise they fall back to `telegram_chat`. With no chat set at all, the hook is a silent no-op — that keeps notification noise off by default. It hooks `Notification` (not `Stop`) deliberately, so it doesn't ping on every turn-end.

**No `@mention` machinery.** Slack needed an `<@U…>` tag to guarantee a mobile push, so the transport carried a single-sourced mention decision plus a `slack_notify_user` id and a `slack_notify_mention` toggle. Telegram pushes every message to a chat you are a member of, which made all three dead weight — they were deleted in #540, not ported. Per-chat mute is the control now, and it lives in the Telegram client, not in config.

**Verify it's actually wired.** `settings.template.json` carries the `Notification` block, but `install.ps1` merges it into your live `~/.claude/settings.json` *once* — there's no re-sync, so the live file can silently drift and lose the block (then idle/permission pings just never fire). Confirm with `E:/automation/fleet-config/.venv/Scripts/python.exe -c "import json;print(list(json.load(open(r'C:/Users/rober/.claude/settings.json'))['hooks']))"` — `Notification` must be in the list. After re-adding it, restart Claude Code (or open `/hooks` once) so the harness reloads settings.

**Only the `permission_prompt` push fires; everything else no-ops.** The `Notification` event carries several sub-types. `permission_prompt` (a permission gate *or* an `AskUserQuestion` — the mid-task "come look, I'm blocked" push) is the only one that pings. `idle_prompt` (the 💤 "gone idle" nag) no-ops — a session you left idle is rarely something you need to run back to, so that nag was noise. `agent_needs_input` / `agent_completed` (fleet-config#274 — added when a Claude Code update introduced background sub-agent lifecycle notifications, one per Task/Agent-tool spawn) also no-op — a fan-out skill like `/issue-batch` or `/cleanup-fleet` spawns many of these, and only the parent session's own prompt is worth a phone push; without the no-op, every sub-agent needing input or finishing fired its own unformatted ping. A `permission_prompt` is reworded to `Claude Code awaits your input` (it's as often an `AskUserQuestion` as a real permission gate, so "needs your permission" overclaims). The agent name is spelled out in full — this hook is **Claude Code only** (it's the only agent with a `Notification` event; extending the ping to Codex/Pi is tracked in [#213](https://github.com/ferraroroberto/fleet-config/issues/213)), so the label is a deterministic constant, not inferred. It can't say *what* Claude Code is waiting on: in a remote-control / bridge session the tool being gated lives in the cloud transcript, and the local `transcript_path` holds only bridge metadata (no `tool_use`), so a question (`AskUserQuestion`) and a real permission gate are indistinguishable locally. Don't add transcript-tool-sniffing here expecting it to work from the phone — it won't.

**Deep-link back into the session.** A bridge session's transcript opens with a `bridge-session` entry whose `bridgeSessionId` (`cse_01H…`) maps to `https://claude.ai/code/session_01H…` (drop the `cse_` prefix). The hook appends that URL to the ping (`… · https://claude.ai/code/session_…`) so you can tap the notification and resume on the web. Local terminal sessions have no bridge entry, so no link is appended.

**Board deep link (fleet-config#242) — this is the remote control.** When `board_url` is also configured, the ping appends a second line: `📋 Open on the Board: https://<host>:8445/?board=<session_id>`. `<session_id>` is Claude's transcript UUID — the same id `session_state.py` already persists as the board row's key, and the same id app-launcher#307 (shipped) resolves to a card's claimed `state_sid`. `board_url` must be a Tailscale-reachable address (e.g. `https://<pc>.<tailnet>.ts.net:8445`), not loopback, since the ping is tapped on the phone. With Slack's native integration retired, **this link is how work is driven from the phone.**

**Set it via `FLEET_BOARD_URL`, not `[global] board_url` (fleet-config#271).** `_lib.resolve_board_url` checks, in order: a project's own `board_url` override in `projects.toml`, then the `FLEET_BOARD_URL` environment variable, then the committed `[global] board_url` fallback. The real hostname belongs in `FLEET_BOARD_URL` — set it in `~/.claude/settings.json`'s `env` block, same placement as `TELEGRAM_BOT_TOKEN` — because fleet-config is a **public** repo and committing a real Tailscale hostname into `projects.toml` would permanently expose a device name + tailnet id in public git history. `[global] board_url` stays empty here; it exists only as a documented extension point (e.g. for a private fork). With nothing configured, the ping stays link-free.

**Bake a bearer token in for a frictionless tap (fleet-config#273).** If app-launcher's `auth_token`/`auth_password` are configured, a device that hasn't already stashed the token gets a login overlay instead of landing straight on the card. `FLEET_BOARD_URL` can carry its own query string — e.g. `https://<host>:8445?token=<token>`, the exact value the tray's own **Copy Tailscale URL** menu item already produces — and `board_link()` merges `board=<session_id>` into it via `urllib.parse` rather than concatenating, so the existing `?token=` survives alongside `?board=`. Paste the tray's Copy-URL value straight into `FLEET_BOARD_URL` for a link that authenticates on tap, same trust model as that existing feature.

## One-time bot setup

The fleet reuses the bot `whatsapp-radar` already had (`@whatsappRadarBot`), so in practice only steps 3–5 are new. For a clean install:

1. **Create the bot** — message [@BotFather](https://t.me/BotFather) → `/newbot` → name it → copy the token (`<bot_id>:<35-char secret>`).
2. **Create the chats** — one for `attention`, one for `log`, adding the bot as a member of each. Make each a **supergroup or channel** (see the warning above): create the group, then group settings → *Chat history for new members* → **Visible**, which converts it immediately.
3. **Read the chat ids** — a bot cannot see ordinary group messages under default privacy settings, but *being added to a chat* always emits a `my_chat_member` update. So add the bot first, then read:

   ```bash
   curl "https://api.telegram.org/bot<token>/getUpdates"
   ```

   Take the `chat.id` for each. A basic group that has since been upgraded reports `migrate_to_chat_id` — use **that** value. Telegram keeps unconfirmed updates for only ~24h, so read them the same day. (If nothing appears, send `/start@<botname>` in the chat: a command addressed to the bot always gets through.)
4. **Put the ids in `hooks/projects.toml`** under `[global]`, as `telegram_chat` / `telegram_chat_attention` / `telegram_chat_log`.
5. **Put the token in `~/.claude/settings.json`'s `env` block** — **never** committed, never in `projects.toml` or `settings.template.json`:

   ```json
   { "env": { "TELEGRAM_BOT_TOKEN": "<bot_id>:<secret>" } }
   ```

   That one place reaches every hook, skill, and venv subprocess (they inherit Claude Code's environment). `notify_send.py` also reads this file directly as a fallback when `TELEGRAM_BOT_TOKEN` is absent from the environment — so it works the same under launchers that don't inject the `env` block (Pi, Codex, GitHub Copilot, a bare terminal, a scheduled `.bat`), not just Claude Code.

   `hooks/secret_scan_guard.py` blocks a commit containing a live-shaped bot token (`_lib.SECRET_PATTERNS`), while leaving placeholder forms like the one above committable.
6. **Verify with a live ping** — a green test suite proves the code runs, not that the message landed:

   ```bash
   E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_send.py --category attention --text "notify_send is live"
   ```

## Consumers

- **`app-launcher`** — per-job failure alerts (`Job.alert_on_failure`, 19 of 21 jobs) → `coding alerts`, via its own vendored `src/notify/telegram.py` and `config/webapp_config.json`. Deliberately a **separate** sender sharing this bot and chat; converging the two implementations is out of scope (see #540).
- **`whatsapp-radar`** — the family obligations digest → `family radar news`, on the same bot but its own chat and its own `.env`.
- **`home-automation`** — alarm arm/disarm and panel-connection errors → `family radar news`, via its own `config/notify_config.json` and `build_alarm_notifier`. Physical-world events belong with the household, not with the code fleet.
- **`content-management`** — the reporting-pipeline failure alert (`reporting_pipeline.send_failure_alert`) and the `schedule-autoheal` escalation (`content-management#68`), both through this helper with `--category attention`. It holds **no chat id of its own** — the only sister repo that routes by category rather than by a destination it owns (`content-management#264`).
- **`grocery-shopping-automation`** — has the vendored notifier and a chat id in `config/notify_config.json`, but **no call site**: `build_notify_notifier` is never invoked, so nothing is sent. Dormant, not broken.
- Any future unattended job: call the CLI or import `notify`. No per-project install.
