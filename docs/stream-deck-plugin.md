# Stream Deck plugin (`stream-deck/`, fleet-config#370)

## Why this exists

Coding-relevant Stream Deck keys (six — now seven — fleet tray launchers plus
terminal shortcuts) used to be hand-maintained directly in the Elgato Stream
Deck desktop app's `Roberto` profile: launch paths and icons copy-pasted per
key, drifting independently from the repos that actually own them. This gives
tray targets, icons, and action code one source-controlled home
(`stream-deck/`), installable as a normal plugin alongside the existing app —
see `stream-deck/README.md` for the day-to-day usage/install/maintenance
workflow. This doc is the durable "why does it work this way" record,
including the non-obvious failure modes hit standing it up, for whoever
touches this next.

## Architecture in one paragraph

`registry/targets.json` is the only committed source of truth for every
target, a discriminated union by `kind`: `tray` (`id`/`label`/
`projectsTomlKey`/`iconSource`, deliberately carrying **no** launch path) and,
since fleet-config#574, `http-action` (`id`/`label`/`actionId` — an
authenticated `POST` against home-automation's `POST
/api/actions/{action_id}` alias endpoint, carrying no URL/token of its own;
those live in the gitignored plugin-bundle `.env`, see the README's
"Configuring the home-automation connection"). `scripts/sync-assets.mjs` (a
`prebuild` step) validates each kind differently — a `tray` entry is
cross-checked against `hooks/projects.toml` (must have `tray_cmd` +
`cwd_prefix`, launcher file must exist) and its icon copied in; an
`http-action` entry just needs a non-empty `label`/`actionId`, no icon or
projects.toml lookup — and writes the build-only
`registry/targets.generated.json` the runtime reads, so the registry can
never drift from the fleet's real launch/call contract and a missing/renamed
launcher, icon, or malformed http-action entry fails the build loudly instead
of silently. Four actions ship: `launch-target` and `call-action` (each
generic, resolving only trusted registry IDs of their own kind, never
key-supplied text — `call-action` additionally needs the `.env` connection
config, absent which every press shows `showAlert()`), `open-coding`, and
`back`.

## The one step that can't be automated

Elgato only supports authoring a plugin-bundled profile through the real
desktop app's editor/export — there's no file format or CLI to generate a
`.streamDeckProfile` from code. The plugin therefore ships buildable and
installable, but the `Fleet Coding XL` profile itself has to be built once by
hand in the app and exported into
`com.ferraroroberto.fleetcoding.sdPlugin/profiles/` — see the README's
step-by-step. This is a standing constraint, not a TODO: re-do it only when
the physical key layout changes, never for target/icon/registry code changes.

The exported file itself is **gitignored, not committed** (fleet-config#596)
— an export embeds the physical device's own `Device.UUID`/`Model`, which has
no reason to be in this public repo. It only ever needs to exist on the local
machine that runs `streamdeck link`/`npm run package`; see
`com.ferraroroberto.fleetcoding.sdPlugin/profiles/README.md`.

## Gotchas hit during bring-up (all empirically confirmed on this machine, Stream Deck 7.4.2)

- **A custom `Category` without a matching `CategoryIcon` silently kills the
  whole plugin.** The schema calls `CategoryIcon` optional, but that's only
  true if you don't set a custom `Category` at all — declare one without the
  icon and the live app refuses to load the plugin: no spawned `node.exe`
  process, no per-plugin log file, nothing in the UI. The only trace is a
  single line in the app's own log,
  `%APPDATA%\Elgato\StreamDeck\logs\StreamDeck.log`:
  `Plugin invalid '<uuid>': (category icon not defined)`. `streamdeck
  validate` does **not** catch this — it's an app-level check, not a CLI-level
  one. If a linked plugin never appears anywhere (not even a process), grep
  that log for `Plugin invalid` before suspecting anything else.
- **`VisibleInActionsList: false` blocks the only way to author a bundled
  profile by hand.** The setting is meant for actions "hidden from users,
  whilst still available as part of pre-defined profiles distributed with the
  plugin" — but since *we* are the ones who have to drag the action into the
  layout via the GUI (see above), it has to stay visible for that one-time
  authoring step. There's no plugin-side way to pre-populate a profile
  programmatically to justify hiding it from the list.
- **`cmd.exe /c <relative-filename>` is unreliable under Node's `spawn`, even
  with a correct `cwd`.** `spawn("cmd.exe", ["/c", "tray.bat"], { cwd:
  "E:/automation/app-launcher" })` reproducibly fails with `'tray.bat' is not
  recognized...` despite `cwd` being verifiably honored (`cmd /c cd` from the
  same call returns the right directory). `spawn("cmd.exe", ["/c",
  "E:/automation/app-launcher/tray.bat"], { cwd: ... })` — an **absolute**
  path to both the launcher and (for good measure) `cmd.exe` itself
  (`C:\Windows\System32\cmd.exe`) — works every time. `src/lib/launcher.ts`
  builds the absolute path via `join(target.cwd, target.command)` rather than
  relying on relative resolution. Don't regress this back to a bare filename.
- **`manifest.json` edits need a full app restart; `bin/plugin.js` edits
  don't.** `streamdeck restart <uuid>` (or the app's own reload) reliably
  picks up a rebuilt `bin/plugin.js`, but changes to `Category`,
  `CategoryIcon`, or `Profiles` in `manifest.json` needed a full quit-from-
  tray-and-relaunch of the Stream Deck app before they took effect in
  practice. If a manifest change doesn't seem to apply, restart the whole app
  before assuming the change is wrong.
- **`switchToProfile(deviceId)` with no name returns to whatever profile
  *this plugin* last explicitly switched away from — not "whatever the user
  had open."** It's server-tracked per plugin, confirmed straight from the
  SDK source (`elgatosf/streamdeck`, `packages/plugin/src/plugin/profiles.ts`
  JSDoc). Practically: `back.ts` will do nothing until `open-coding.ts` has
  completed at least one successful switch — that's expected sequencing, not
  a bug in `back.ts`.
- **A `Profiles[]` entry pointing at a missing `.streamDeckProfile` blocks
  `streamdeck validate`/`pack`** with a clear `Profiles[0].Name file not
  found` error — this one's the documented, expected behavior (see the
  README's manual-step section), not a surprise. Whether the *live app's*
  loader (as opposed to the CLI) also refuses to load a plugin over this
  specific condition was never isolated as its own test during bring-up (the
  `CategoryIcon` fix and re-adding `Profiles` happened in separate,
  sequenced steps) — treat as unconfirmed rather than assuming either way.
- **Pressing a `launch-target` key can itself trigger a Stream Deck profile
  switch, with zero code in this plugin causing it.** `launch-target.ts` only
  spawns the target and shows ok/alert — it never calls `switchToProfile`.
  Observed on this machine: launching an app auto-navigated the device back to
  the default profile, most likely Stream Deck's own built-in **Auto-Switch
  Profiles** (profile-by-foreground-app) feature reacting to the newly
  launched window taking focus. If this behavior is unwanted, it's configured
  in the Stream Deck app's own Profiles settings, not in this plugin.
- **A freshly-dragged `Call Action` key that flashes a warning on first press
  is almost always an unset Property Inspector dropdown, not a code bug.**
  Confirmed live (fleet-config#591/#593): placing the key alone doesn't set
  `targetId` — its Action dropdown has to be explicitly clicked to a specific
  option, and that selection has to actually persist. When it hasn't,
  `com.ferraroroberto.fleetcoding.sdPlugin/logs/com.ferraroroberto.fleetcoding.<N>.log`
  (the newest-numbered file) shows `ERROR call-action: unknown, unset, or
  non-http-action targetId ""` for that press — check this log before
  suspecting `call-action.ts` or the home-automation endpoint. Fix is live,
  no rebuild/export: reopen that key's Property Inspector and re-pick the
  target from its dropdown. A `TimeoutError` in the same log instead (the 5s
  `AbortSignal.timeout` in `src/lib/http-client.ts`) means the dropdown was
  fine and the real HTTP call to home-automation was just slow that one time
  — a single occurrence isn't evidence of a systemic problem, but if it
  recurs the timeout may need raising.
