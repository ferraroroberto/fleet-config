# Where coding agents run, and how work reaches them

Any coding-agent setup answers three questions: where the agent runs, how work reaches it, and how the agent gets the human's attention when a turn ends. This page lays out the options for each, compares a VPS + tmux reference setup with this fleet's tower + launcher model, and lists the ideas worth borrowing.

## Where the agent runs

| Option | Good for | Watch out for |
| --- | --- | --- |
| A. Rented server (VPS) | Always on, isolated from personal data, reachable from anywhere over a private network. | No GPU or desktop, small CPU, memory limits; backups and security updates are yours. |
| B. Your own computer | Fast hardware, GPU, desktop apps, local files, no hosting bill. | Sleep, reboots and OS updates stop work; agents sit next to personal data. |
| C. Hybrid | Headless work on a server, GUI and GPU work on the PC, each doing what it is good at. | Two machines to keep in sync: logins, repos, config. |
| D. Vendor cloud agent | Zero setup: Claude Code on the web, Codex cloud, a chat product's hosted computer. | No access to local tools or the local network; a monthly cost; less control over the environment. |

This fleet is option B: one Windows tower with a fast CPU, a local GPU (Whisper, local models), a real browser and desktop apps. Option C is the natural next step if headless work ever needs to survive tower reboots.

## How work reaches the agent

| Pattern | What it is | When to use it |
| --- | --- | --- |
| Standing room | A long-lived interactive session (a tmux pane, a launcher terminal) that a human or a bot types into. | Back-and-forth work where context should carry over. |
| Fresh lane | A new session per task, started with a written brief and closed when the task ships. | Issue work: clean context, easy to run in parallel, easy to audit. |
| Headless call | A one-shot `claude -p` run with no human, usually on a schedule. | Weekly audits, reports, maps. It must finish inside its own turn, because nothing resumes it. |
| Sub-agent or fork | A helper started from inside a session that reports back to it. | Parallel research or review inside one task. Its brief must be explicit, since it inherits nothing it is not given. |
| Chat front door | A bot (a chat product, Telegram, the launcher Board chat) that turns plain words into one of the patterns above. | Steering from a phone. The bot should dispatch work, not do the coding. |

## How the agent wakes the human

- **Polling:** simple, but slow or wasteful.
- **Push on turn end:** a Stop hook or a doorbell rings the moment the turn ends. It is fast, but it needs a really-idle check, because a turn can end while tests, a package install or a shell are still running under the agent.
- **Both:** push as the main signal and a slow poll as the safety net. This is what the fleet runs: the doorbell rings into the chief's inbox, and a 15-minute poll catches a missed ring.

## A VPS + tmux reference setup against the tower + launcher

The reference setup runs Claude Code on a rented Linux server, one tmux room per project, and steers each room from a per-project chat bot. The fleet runs Claude Code on one Windows tower and steers it from the launcher Board chat through a chief session.

One request, start to finish:

| Step | VPS + tmux rooms | Tower + launcher |
| --- | --- | --- |
| 1 | The owner writes to the project's bot in plain words. | The owner writes to the chief in the Board chat, or to an area bot on Telegram. |
| 2 | The bot joins the private network if needed and connects to the server over SSH. | The chief picks the next item from the plan and dispatches a lane. |
| 3 | It reads the project's tmux pane; if the agent is idle it types the prompt, then Enter. | The launcher starts a fresh session in its own git worktree, with a brief. |
| 4 | The bot ends its turn and the agent works. | The lane builds, runs the repo's gate and ratchets, and merges on green. |
| 5 | A Stop hook checks the agent is not mid-job, then pings the bot's webhook. | Turn end rings the doorbell into the chief's inbox. |
| 6 | The bot reads the pane and summarises it. | The chief reads the result, deploys, and dispatches the next item. |
| 7 | A save command writes a rolling project state file and posts a log to the chat. | State lives in GitHub issues, the plan file and the handover log. |

Side by side:

| Topic | VPS + tmux rooms | Tower + launcher |
| --- | --- | --- |
| Uptime | 24/7; unaffected by laptop sleep, OS updates or a home power cut. | Only while the tower is on; a reboot or power cut stops everything. |
| Parallel work | One room per repo; two agents on one repo collide. | Worktrees: several lanes, even on one repo, each on its own branch. |
| Quality gate | House rules in prompts plus a deny list; trust sits with the model. | Deterministic gates: tests, ratchets and hooks that refuse; merge only on green. |
| Hardware | Small server CPU, no GPU, no desktop. | Fast CPU, local GPU, a real browser and desktop apps. |
| GUI tasks | None; headless only. | Browser scheduling, photos, mail and screenshots all work. |
| Blast radius | The server holds only agent work and no personal files. | Agents run beside personal files and browser sessions; hooks limit this but not fully. |
| Human and bot share a session | The same tmux pane; the human attaches from a terminal app and takes over. | Through the launcher terminal on the phone, but lanes are short-lived rather than standing rooms. |
| Front door | One bot per project: clean context, split across chats. | One chief plus area bots: one plan, one queue, one place to ask. |
| Wake-up | Stop hook to webhook, with a mid-job check and a debounce. | Doorbell into the chief inbox, plus a 15-minute fallback poll. |
| Cost | A small monthly server bill plus the chat product's subscription. | No hosting cost; electricity for an always-on tower. |
| Setup effort | A few shell scripts; easy to copy. | Large: launcher, hub, hooks, skills and many repos. Powerful but heavy. |
| Who can run it | Built for a non-programmer: plain words in, plain summaries out. | Built for a technical owner who reads PRs, gates and plans. |

## Ideas worth borrowing

- **Don't ring while the agent is still busy.** The reference wake hook checks whether tests, a package manager or a shell are still running under the agent, then waits out a short debounce before pinging. The doorbell trusts the session's status label, which has shown idle for sessions still mid-turn. A child-process check is a small, deterministic fix.
- **A live memory and disk alarm.** The reference setup posts to a bot when the server runs low. The fleet measures hardware once a week, and a lane plus Whisper plus local models can fill the tower between checks; an alarm to Telegram catches it the same day.
- **A small Linux night runner for headless repos (benchmark first).** Most cleanup lanes need no GPU or GUI. Running them on a cheap server keeps work going through tower reboots, OS updates and a locked desktop, which stops scheduled desktop jobs. GUI and GPU work stays on the tower.
- **A standing room per busy repo (consider).** For repos the owner steers by hand, one long-lived session that the owner and the chief both type into could replace starting a fresh lane for small back-and-forth fixes. Lanes stay the default for issue work.
- **Already covered.** Startup context and saved state map to `CLAUDE.md`, the SessionStart hooks and the handover log. The rule to read the pane, type, then press Enter separately maps to the verified-delivery verdicts of the chief's send command. A deny list is weaker than the refusing hooks.

## What fits this fleet

Keep the tower as the main machine: the GPU, the GUI tasks and the gates are the core of the fleet, and a VPS cannot replace them. The reference setup is better on uptime and isolation and simpler to copy. The cheapest wins are the doorbell's busy check and a memory alarm. A Linux night runner for headless lanes deserves a measured trial rather than an opinion.

The trial is one week with three numbers: lanes lost to reboots, locks or sleep; minutes from a lane finishing to the owner being told; and approvals the owner had to give per lane. If the first number is near zero, the night runner is not worth it.
