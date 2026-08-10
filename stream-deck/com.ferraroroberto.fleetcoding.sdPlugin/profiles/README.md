# `profiles/` — local-only, not tracked in git

This directory is where the plugin's bundled profile,
`fleet-coding-xl.streamDeckProfile`, lives on your machine — but the file
itself is **gitignored** (fleet-config#596), not committed.

**Why:** an exported `.streamDeckProfile` is a real zip whose top-level
manifest embeds the physical Stream Deck device's own `Device.UUID` and
`Device.Model` — hardware-identifying metadata that has no reason to sit in
a public repo, alongside the actual key layout (which action goes where).

**What this means practically:**

- A fresh clone of this repo has no `fleet-coding-xl.streamDeckProfile` here
  at all. `streamdeck validate` / `streamdeck pack` (`npm run package`) will
  fail with a clear `Profiles[0].Name file not found` error until you create
  one — that's expected, not a bug (see `docs/stream-deck-plugin.md`'s
  gotchas).
- To create it: follow `stream-deck/README.md`'s "The one manual step"
  section — build the layout in the live Stream Deck app, export, and save
  the result at exactly this path with exactly this filename
  (`fleet-coding-xl.streamDeckProfile`, matching `manifest.json`'s
  `Profiles[0].Name`).
- `npm run profile-diff` reads whatever file is here to tell you which
  registry targets still need a physical key — it works the same whether
  the file is tracked or not, since it only ever reads from disk.
- Re-export and overwrite this file (still gitignored, still local-only)
  whenever the physical key layout changes — same cadence as before, just
  without a commit at the end.
