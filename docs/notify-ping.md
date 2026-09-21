# Completion pings go through `notify_complete.py` only

Every skill that ends by pinging the user (`/issue-start`, `/issue-finish`, `/issue-yolo`, `/issue-finish-batch`, `/cleanup-fleet`, `/cleanup-fleet-all`) sends that ping with `hooks/notify_complete.py` and nothing else. This note is the one home for the rule's reasoning; each skill states the rule in one line and links here (fleet-config#928), so the rationale cannot drift per copy.

**The rule.** Never use an MCP chat tool (Slack, Telegram, or any other connector's search/send) to find a chat or post the ping, not even when `notify_complete.py` sends nothing.

**Why.**

- **It is a security boundary.** The helper resolves the destination deterministically from `hooks/projects.toml`. A chat the agent picks for itself is an agent-inferred external write destination: content leaves the machine to a place no human configured.
- **It is also wrong.** A chat found by searching can be the wrong chat, so the ping reaches the wrong audience.
- **A silent no-op is the correct outcome.** With no channel configured for the project, the helper exits 0 and sends nothing, by design. That is not a failure to route around, and nothing about it justifies reaching for a chat tool.
