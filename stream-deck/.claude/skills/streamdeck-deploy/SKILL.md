---
name: streamdeck-deploy
description: Build, link/restart, package, and diff the Fleet Coding Stream Deck plugin end-to-end, then report exactly which registry targets still need a physical key placed — the one step that can't be automated. Use after any change to stream-deck/ (registry/targets.json, a new action, manifest.json) — "deploy the stream deck plugin", "add the new stream deck buttons", "what's left for the stream deck".
---

# streamdeck-deploy

**Goal:** do everything that's provable/scriptable for a `stream-deck/` change — connection config, build, link/reload, package — and hand back a short, exact, computed list of what only a human can finish (dragging keys in the Elgato desktop app; there is no CLI/file-format path for that, see `docs/stream-deck-plugin.md`).

Run from `E:/automation/fleet-config/stream-deck` (or the equivalent primary checkout — this skill assumes the primary repo, not a worktree, since it links into the live, real Stream Deck app on this machine).

## Steps

Run in order. Stop and report clearly if any step fails — don't skip ahead on a red step.

### 1. Ensure the home-automation connection is configured

Check `com.ferraroroberto.fleetcoding.sdPlugin/.env` exists and has non-empty `HOME_AUTOMATION_BASE_URL` and `HOME_AUTOMATION_TOKEN`. If either is missing, populate it automatically from already-trusted local sources — don't ask, just do it and say so (same machine, same trust boundary, gitignored file, reusing the token already relied on elsewhere, e.g. the HA custom integration):

- `HOME_AUTOMATION_BASE_URL`: `https://<Self.DNSName from \`tailscale status --json\`, minus the trailing dot>:<port from ../../home-automation/config/webapp_config.json, default 8447>`
- `HOME_AUTOMATION_TOKEN`: `auth_token` from `../../home-automation/config/webapp_config.json` (this is home-automation's real production token, not a per-caller secret — don't rotate it via `gen_token.py --force`, that would break every other caller relying on the same file).

If home-automation's config file or `tailscale status` aren't available/resolvable, stop and ask instead of guessing.

Never print the raw token value in your response — reference "the token" or the last 4 characters only.

### 2. Verify

```powershell
npm run verify
```

typecheck → unit tests → sync-assets (validates every registry entry) → build. Stop and report the failure verbatim if this doesn't pass — nothing past here is trustworthy otherwise.

### 3. Link or reload the live plugin

```powershell
npx streamdeck list
```

- Not listed → `npx streamdeck link com.ferraroroberto.fleetcoding.sdPlugin`.
- Already listed → `npx streamdeck restart com.ferraroroberto.fleetcoding` (picks up the rebuilt `bin/plugin.js`).

**Caveat worth surfacing in your final report, not silently assuming away:** per `docs/stream-deck-plugin.md`'s bring-up gotchas, a `manifest.json` change (a new `Actions[]` entry, `Category`, `Profiles`) has needed a full quit-and-relaunch of the Stream Deck desktop app to show up in practice, beyond what `streamdeck restart` reloads. You cannot quit/relaunch that GUI app yourself — if this run's diff touched `manifest.json`, say so explicitly as a possible extra manual step ("if the new action doesn't appear in the actions list, quit and reopen the Stream Deck app once").

### 4. Repackage

```powershell
Remove-Item dist\*.streamDeckPlugin -ErrorAction SilentlyContinue
npm run package
```

(`streamdeck pack` refuses to overwrite an existing output file, hence the delete first.)

### 5. Compute exactly what's left

```powershell
npm run profile-diff
```

This extracts the *exported* `.streamDeckProfile` (a real zip — each page's `manifest.json` lists which registry `targetId` is already placed on which key) and diffs it against `registry/targets.json`. It prints exactly which targets have no physical key yet, and what action type each needs (`Launch Target` vs `Call Action`) — don't hand-derive or eyeball this list yourself, this is the whole point of the tool.

### 6. Final report

One concise summary:
- What you verified/built/linked/packaged (pass/fail, not just "done").
- The `profile-diff` output verbatim, or "nothing left — every target already has a key" if empty.
- If step 5 found anything: the exact manual sequence — open the Stream Deck app → Fleet Coding XL profile → drag each listed action type onto a free key → pick the listed target from its Property Inspector dropdown → export over `com.ferraroroberto.fleetcoding.sdPlugin/profiles/fleet-coding-xl.streamDeckProfile` → re-run this skill (or `npm run package`) once to repack with the new export.
- Never claim the buttons "work" — you cannot press a physical key. The furthest you can verify is that the HTTP call itself succeeds (e.g. a harmless `POST /api/actions/<unknown-id>` returning a clean 404 proves auth + connectivity, not a specific real button).
