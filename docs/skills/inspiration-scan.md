# `/inspiration-scan`

- **`/inspiration-scan`** — the **ideas lens**: a daily pass over the newsletters, YouTube channels and X accounts the owner follows, keeping only items likely to give an idea worth trying in the fleet. `.claude/skills/inspiration-scan/scan.py run` does the deterministic work and prints a manifest; the skill delivers the digest and reports. From the digest the owner picks an idea and files it with the card's `/issue-add` line.

## How each source is read

- **Newsletters:** the publication's RSS or Atom feed, a plain read-only GET (`feeds.py`). No mail account is read. A newsletter without a feed is listed as `not-read`.
- **YouTube:** each channel's public Atom feed (`https://www.youtube.com/feeds/videos.xml?channel_id=…`): title, description and link.
- **X:** `x_reader.py` opens each listed account's profile page in a dedicated, persistent Chrome profile and reads the posts that render. It never posts, likes, follows or clicks. It runs under app-launcher's `.venv`, which has Playwright, because fleet-config's runtime is stdlib-only; `scan.py` spawns it and reads one JSON line back. Its one launch helper, `x_session.py`, holds the fleet's stealth profile from [recurring-gotchas.md](../recurring-gotchas.md) and waits for a held profile with backoff (60, 120, 240, 480 s) instead of killing the holder. A login wall or a challenge page ends the pass, and every account not yet read carries that reason. The X API is paid and is not used.

Each source ends the run as `ok`, `not-read` with a reason, `login-needed` (X only: the Chrome profile is not logged in) or `unknown` (the reader crashed, timed out or has no Playwright Python). None of them is ever folded into "nothing new". Items older than `lookback_days` are ignored, and the run reads each source once.

## Relevance scoring

New items go to the local hub's `POST /v1/messages` in batches of 20 (`score.py`, model `claude-haiku-5-5` by default). The prompt carries a fleet profile, one line per repo built from every `.fleet.toml` `description`, plus the owner's "what I like" note. Each item comes back with a 0–10 score, why it fits, an experiment and a one-line issue idea. Items scoring 6 or more are kept, top `top_n` first. A batch the hub could not score is listed as unscored, named in `SCORING_ERROR=`, and not marked seen, so the next run scores it again. Scored items go into `seen.json`, which drops them from later digests; ids older than 60 days are pruned.

## The digest

One self-contained HTML file: inline CSS with light and dark tokens, no scripts. A header chip per source type shows how many sources were read and why the rest were not; X skipped for no login shows as "skipped: not logged in" with a note that it is not a quiet day. With no usable source list the digest is a single card, "Source list not filled in yet", naming the file to fill in. Then come ranked cards: source, linked title, score, why it fits, what to try and a monospace `/issue-add …` line. A footer counts the items left out as already seen. It is sent with `send_file.py` in a launcher session, and otherwise uploaded to the owner's private bot chat with `hooks/notify_send.py --category log --file`.

## Private data

The source list, `seen.json`, the Chrome profile and every digest live in the machine-local state root, `~/.claude/inspiration-scan/` (override with `INSPIRATION_SCAN_ROOT`). The repo holds only `sources.example.toml`, with placeholder names. Nothing from a digest goes to GitHub, and the manifest names source types and counts only.

## Owner setup

1. `scan.py run` creates `~/.claude/inspiration-scan/sources.toml` from the example when it is missing (`scan.py init` does the same on demand) and delivers a digest naming the file. Fill in the feeds, channel ids, X handles and the `likes` note; a list still identical to the example is treated as unfilled.
2. `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/inspiration-scan/scan.py login-x` (the owner's own step; no agent enters credentials) opens the dedicated profile headed on the X login page. Log in once, then close the window.
3. Add the app-launcher Job to the live `config/jobs.json`:

```json
{"id": "inspiration-scan", "name": "Daily Inspiration Scan", "script_path": "E:\\automation\\fleet-config\\.claude\\skills\\inspiration-scan\\run-weekly.bat", "args": "", "schedule": {"type": "daily", "at": "03:00"}, "visible": true, "alert_on_failure": true, "cooldown_seconds": 3600}
```

03:00 keeps the run, and the Chrome window the X reader opens, away from the owner's day.

Tests: `tests/inspiration_scan/test_inspiration_scan.py`.
