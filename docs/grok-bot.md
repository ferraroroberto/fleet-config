# Grok Bot as a phone front door

Grok Bot is xAI's desktop-and-phone agent app. With its local-execution setting on, the iPhone app drives commands that run on the owner's PC through the desktop app, so the owner can read the fleet and dispatch work from the phone next to the Claude chief. The desktop app being down means the front door is shut, so the fleet keeps it running and visible.

## What runs where

`skills/_lib/grokbot/status.py` reads the app and starts it when it is down. It is a plain helper, not a skill.

```powershell
$py = "E:/automation/fleet-config/.venv/Scripts/python.exe"
& $py skills/_lib/grokbot/status.py status   # one read-only reading
& $py skills/_lib/grokbot/status.py ensure   # start the app minimized when it is down, then record the check
```

`attention.py` answers "does anything need me?" on demand: nothing polls or pings. It prints `ATTENTION=none` or one `ATTENTION ...` line per item (a lane waiting on the owner, a failed Job, an open `needs-owner` or `needs-validation` issue, Grok Bot down), reading the Board and `gh` with GETs and reads only. A source it could not read prints its own `UNKNOWN ...` line and the exit is 3, so unread is never reported as quiet.

```powershell
& $py skills/_lib/grokbot/attention.py
```

`status` and `ensure` both print `GROKBOT=<state>` and exit 0 for `up`, 1 for `down` and `signed-out`, 3 for `unknown`. `ensure` also prints `ENSURE=none|started|start-failed|refused` and the outage counter.

| State | Meaning | What `ensure` does |
|---|---|---|
| `up` | The pid in the app's own status file is alive, its executable is the installed `Grok Bot.exe`, and the file says a user is signed in | nothing |
| `signed-out` | The same process runs but nobody is signed in | nothing (only the owner can sign in) |
| `down` | No process runs the installed executable | starts the app minimized, waits up to 30 s for it to report |
| `unknown` | The evidence did not settle it: no install, a status file that does not describe the running process, an unreadable process table | refuses; a second launch over a half-started app is worse than waiting a cycle |

`unknown` is never counted as `up`. The pid is always confirmed against the executable path, so a pid the system reused for another program cannot read as the app. `ensure` never kills a process and never touches the app's settings or startup entries; those are the owner's.

## The app-launcher switch

App Launcher's Settings > Chief has a Grok Bot switch (`settings.grokbot_enabled` on `GET /api/board/chief/settings`, default on). `skills/_lib/grokbot/switch.py` reads it fresh on every call and returns `on`, `off` or `unknown`; an unreachable launcher, a non-2xx, or a missing or non-boolean field is `unknown`, never `on`.

| Reader | Off | Unknown |
| --- | --- | --- |
| `status.py ensure` | prints `ENSURE=disabled` and `GROKBOT=disabled`, exit 0; starts and pings nothing, outage counter untouched | prints `ENSURE=switch-unknown` and `GROKBOT=unknown`, exit 0 |
| digest `grokbot=` | `grokbot=disabled` | `grokbot=unknown (reason)` |
| `relay.py send` | `REFUSED reason=disabled`, nothing sent | `REFUSED reason=unknown`, nothing sent |
| `attention.py` | quiet (the owner's choice) | an `UNKNOWN grokbot` line, exit 3 |

`relay.py read` and `status.py status` ignore the switch. `tests/grokbot/test_switch.py` covers every case against a fake launcher response.

## The board digest

`chief_ops.py board` ends with a `grokbot=` token: `up`, `down`, `signed-out`, or `unknown (reason)`. It is a read-only reading, so polling the board never changes the outage counter.

## One ping per outage

Each finished `ensure` run is one check. A check that ends `down` or `signed-out` is a failed check. The second failed check in a row sends one attention ping through `hooks/notify_send.py` and marks the episode as pinged, so a long outage pings once. An `up` reading closes the episode and the next outage starts fresh. An `unknown` check changes nothing. A ping that was not delivered is not marked sent and is retried on the next check.

The episode lives in `grokbot-outage.json` in the hooks state directory, outside every repo.

## Running it on a schedule

The check only counts when something runs `ensure` regularly. App Launcher runs it as a 10-minute Job; the Job entry belongs to that repo.

## Talking to sessions

`skills/_lib/grokbot/relay.py` is how the Bot sends text into a launcher session and reads one back. It tags every message with its sender, refuses credential-shaped and destructive text, and logs each call outside the code tree. The commands the Bot may run, and why they matter with approval off, are in [grok-bot-skill-pack.md](grok-bot-skill-pack.md).

## Test seams

`tests/grokbot/test_relay.py` runs the relay against a loopback fake launcher that records every POST, so no real session is messaged.

`tests/grokbot/test_status.py` drives every state from a fake process table, a temp status file, a fake launcher and a fake notifier, and replaces `subprocess` for the whole file so a real app start or a real message fails the test. The `GROKBOT_EXE` and `GROKBOT_STATUS_FILE` environment variables point the helper at other files; the chief test suites set them to nothing so they never read the real app.
