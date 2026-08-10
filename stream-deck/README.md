# Fleet Coding — Stream Deck plugin

Source-controlled Elgato Stream Deck plugin ([fleet-config#370](https://github.com/ferraroroberto/fleet-config/issues/370)) that installs a **Fleet Coding** control surface: the fleet's tray launchers, home-automation actions ([fleet-config#574](https://github.com/ferraroroberto/fleet-config/issues/574)), an **Open Coding** action, and a **Back** action. It installs alongside the existing Elgato Stream Deck application and does not touch, replace, or modify any other installed plugin or profile — including the current hand-maintained `Roberto` profile.

## What it does not do

- Does not replace or automate the Elgato Stream Deck desktop app.
- Does not mutate any user-created profile, or auto-insert **Open Coding** into one.
- Does not write into Elgato's live `%APPDATA%\Elgato\StreamDeck\ProfilesV2`/`ProfilesV3` data directories — those are undocumented private formats. Profile installation goes exclusively through the plugin manifest's `Profiles` mechanism.
- v1 shipped only the fleet tray launchers (`app-launcher`, `photo-ocr`, `voice-transcriber`, `home-automation`, `local-llm-hub`, `whatsapp-radar`, `grocery-shopping-automation`); v2 (#574) added `http-action` targets — an authenticated call into home-automation's `POST /api/actions/{action_id}` — starting with `light-on`/`light-off`/`ac-on`/`ac-off`. A generic arbitrary-URL/method target (terminal/remote-screen/script) is still out of scope — see "Follow-up" below.

## Prerequisites

- Node.js 24+ (already on this machine's PATH).
- The Elgato Stream Deck desktop app installed and running (7.1+; tested against the locally installed 7.4.2).
- From `stream-deck/`: `npm install`.
- For the home-automation actions (`Call Action`): a configured `.env` — see "Configuring the home-automation connection" below.

## Configuring the home-automation connection

`Call Action` keys (light/AC on-off, etc.) need home-automation's real HTTPS URL and a bearer token to call `POST /api/actions/{action_id}`:

```powershell
Copy-Item com.ferraroroberto.fleetcoding.sdPlugin\.env.sample com.ferraroroberto.fleetcoding.sdPlugin\.env
# then edit .env: HOME_AUTOMATION_BASE_URL (the https://<host>.<tailnet>.ts.net:8447 URL)
# and HOME_AUTOMATION_TOKEN (from home-automation/scripts/gen_token.py)
```

`.env` is gitignored — never committed. It's read from the plugin bundle directory itself (`com.ferraroroberto.fleetcoding.sdPlugin/.env`), which `streamdeck link`'s dev install resolves straight back to this checkout. **A packaged (non-`link`) install is different**: Elgato copies the `.sdPlugin` folder's contents into its own live plugin directory, which does *not* include a gitignored file that only exists in the source checkout — copy `.env` into the installed plugin's own directory too after a `npm run package` + double-click install, the same way the profile-export step below needs redoing after a packaged rebuild.

A missing or incomplete `.env` doesn't crash the plugin — every other action still works; only `Call Action` keys show `showAlert()` on press until it's fixed (check the plugin log for the exact missing key).

## Local install

```powershell
npm run verify
streamdeck link com.ferraroroberto.fleetcoding.sdPlugin
```

`npm run verify` chains typecheck → unit tests → asset sync/validation → build — everything that's provable without the physical device or the Stream Deck app's GUI. `streamdeck link` then installs the unpacked plugin directly into the live app for local development (auto-reloads on `npm run dev`), which is how you get the `Launch target`/`Call Action`/`Open Coding`/`Back` actions available to drag in the step below.

`streamdeck validate` and `streamdeck pack` (bundled as `npm run package`) additionally require the bundled profile file to exist — see the manual step immediately below — so they aren't part of `npm run verify` and will fail with a clear "Profiles[0].Name file not found" error until that step is done once. Once it is, `npm run package` produces `dist/*.streamDeckPlugin`, installable the same way an end user would (double-click it) instead of `streamdeck link`.

Either way, installing only adds this plugin and its bundled, read-only **Fleet Coding XL** profile — every other installed plugin and profile is left exactly as it was.

## The one manual step: exporting the Fleet Coding XL profile

Elgato only supports creating a plugin-bundled profile through the real desktop app's editor — there is no CLI or file format for generating a `.streamDeckProfile` from code. **This step cannot be automated** and only needs to be redone when the physical key layout changes (not for target/icon/registry code changes):

1. Run `npm run verify` then `streamdeck link com.ferraroroberto.fleetcoding.sdPlugin` at least once first, so the `Launch target`, `Call Action`, `Open Coding`, and `Back` actions are available to drag into a layout.
2. Open the Stream Deck app. On the Stream Deck XL device, create a new profile named **exactly** `Fleet Coding XL`.
3. Drag seven **Launch target** actions onto the desired 8×4 layout. For each one, open its Property Inspector and pick the target from the dropdown (App Launcher, Photo OCR, Voice Transcriber, Home Automation, Local LLM Hub, WhatsApp Radar, Grocery Automation).
3b. Drag four **Call Action** actions onto the layout the same way — one each for Light On, Light Off, AC On, AC Off (pick from that action's own Property Inspector dropdown; needs the `.env` from "Configuring the home-automation connection" above to actually fire, but can still be placed/exported without it).
4. Drag one **Back** action onto the remaining key of your choice.
5. Export the profile (Stream Deck app's profile export/share gesture) and save it as:
   `com.ferraroroberto.fleetcoding.sdPlugin/profiles/fleet-coding-xl.streamDeckProfile`
6. Run `npm run package` (`streamdeck validate` + `streamdeck pack`) so the packed `.streamDeckPlugin` includes the exported profile and passes validation end-to-end.
7. In your existing general-purpose profile, manually drag one **Open Coding** action onto a key (per the issue, this is never auto-inserted). Pressing it switches to Fleet Coding XL; **Back** returns to whatever profile was active before.

## Icon maintenance

Tray-target icons are **not** owned by this plugin — they come from each sister repo's `assets/stream-deck/<project>-144.png`, generated via `project-scaffolding/scripts/brand_gen.py`. `npm run sync-assets` (which `build`/`verify` run automatically) copies the current icons in and fails loudly if one is missing, not a `.png`, or under 144×144. To refresh an icon: regenerate it in the owning sister repo, then re-run `npm run build` here — no manual copy step.

The plugin's own top-level icon (`imgs/plugin/icon.png`) is a flat-color placeholder generated by `npm run icon` (`scripts/generate-plugin-icon.mjs`) — swap it for a real design whenever one exists; it's cosmetic only (shown in the Stream Deck app's plugin list) and isn't part of the `verify` gate.

## Adding or changing a tray target

1. Edit `registry/targets.json` (add/update the entry — `id`, `label`, `projectsTomlKey` pointing at the matching `[project]` block in `../hooks/projects.toml`, and `iconSource`).
2. Run `npm run verify` — it cross-checks the entry against `hooks/projects.toml` (must have `tray_cmd` + `cwd_prefix`, and the launcher file must exist on disk) and against the icon file.
3. If the **set** of targets changed (not just an icon or label), also redo the manual profile-export step above to add/remove the physical key — code changes alone don't move keys around on the device.

## Adding an HTTP-action target

An `http-action` entry is a `Call Action` key that calls one of home-automation's registered `action_id`s (see `home-automation/README.md`'s "Generalized action alias" section for the full current list).

1. Edit `registry/targets.json`, add an entry: `id` (this plugin's own key, kebab-case), `"kind": "http-action"`, `label` (shown in the Property Inspector dropdown), and `actionId` (home-automation's `action_id` — sent verbatim, not cross-validated against home-automation's live list, so a typo fails at press-time with a clear 404 rather than at build time).
2. Run `npm run verify` — it fails loudly on an empty `label`/`actionId` or an unknown `kind`.
3. No icon is required — `Call Action` ships one bundled icon for every instance; set a per-key title/icon in the Stream Deck app itself if you want one to stand out.
4. If the **set** of `http-action` targets changed, redo the manual profile-export step above to add/remove the physical key, same as a tray-target set change.

## Validate

```powershell
npm run verify    # typecheck -> unit tests -> sync-assets -> build (no device/GUI needed)
npm run package   # streamdeck validate -> streamdeck pack (needs the exported profile, see above)
```

## Uninstall / rollback

```powershell
streamdeck unlink com.ferraroroberto.fleetcoding
```

(`streamdeck list` shows the plugin's bare UUID to pass here — it's `com.ferraroroberto.fleetcoding`, not the `.sdPlugin` folder name used by `link`/`validate`/`pack`.)

Or remove it from the Stream Deck app's plugin manager. Either way, this only removes the Fleet Coding plugin and its bundled profile — the profile that was active before **Open Coding**, and every other installed plugin/profile, are untouched. Delete the one manually-placed **Open Coding** key from your general profile yourself if you want it fully gone.

## Follow-up (out of scope for this PR)

Terminal/remote-screen/script controls (beyond the existing tray launchers and the `http-action` kind added in fleet-config#574) remain out of scope — a `"terminal"`/`"url"` target kind, if ever needed, joins `TargetDefinition`'s union the same additive way `http-action` did.
