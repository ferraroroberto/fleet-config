#!/usr/bin/env node
// Diffs registry/targets.json against the target ids actually placed as keys
// in the exported, bundled .streamDeckProfile — the export is a real zip
// (confirmed empirically: `file` reports "Zip archive data"), and each
// nested page's manifest.json carries one Settings.targetId per placed
// launch-target/call-action key. This is what tells a human exactly which
// registry entries still need a physical key dragged in, instead of
// re-deriving it by eye every time the registry grows (fleet-config#591).

import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const STREAM_DECK_ROOT = resolve(HERE, "..");
const REGISTRY_PATH = join(STREAM_DECK_ROOT, "registry", "targets.json");
const PROFILE_PATH = join(
  STREAM_DECK_ROOT,
  "com.ferraroroberto.fleetcoding.sdPlugin",
  "profiles",
  "fleet-coding-xl.streamDeckProfile",
);

// The two action UUIDs that place a registry target on a physical key —
// keep in sync with manifest.json's Actions[].UUID entries.
const PLACEABLE_UUIDS = new Set([
  "com.ferraroroberto.fleetcoding.launch-target",
  "com.ferraroroberto.fleetcoding.call-action",
]);

function findManifestFiles(dir) {
  const results = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...findManifestFiles(full));
    } else if (entry.name === "manifest.json") {
      results.push(full);
    }
  }
  return results;
}

/**
 * Pure: given every manifest.json found inside the exported profile zip
 * (parsed), collect the targetId placed on each key of ours. Non-page
 * manifests (the top-level .sdProfile manifest, which carries Device/Pages
 * metadata, not Controllers) safely contribute nothing — same for any key
 * that isn't one of our two placeable actions (e.g. Elgato's own "Switch
 * Profile" page-nav key).
 */
export function placedTargetIds(pageManifests) {
  const ids = new Set();
  for (const manifest of pageManifests) {
    const actions = manifest?.Controllers?.[0]?.Actions ?? {};
    for (const key of Object.values(actions)) {
      if (PLACEABLE_UUIDS.has(key.UUID) && key.Settings?.targetId) {
        ids.add(key.Settings.targetId);
      }
    }
  }
  return ids;
}

/** Pure: registry entries (in file order) with no key placed yet. */
export function missingTargets(registryTargets, placedIds) {
  return registryTargets.filter((t) => !placedIds.has(t.id));
}

function extractProfileZip(profilePath) {
  const dir = mkdtempSync(join(tmpdir(), "sd-profile-diff-"));
  // The project is Windows-only already (src/lib/launcher.ts hardcodes
  // cmd.exe); Expand-Archive is a built-in PowerShell cmdlet, so this needs
  // no extra dependency (Node has no built-in zip reader, and the zip
  // library actually used to *build* .streamDeckProfile files, @zip.js,
  // is only a transitive dependency of @elgato/cli — not safe to import
  // directly here). Expand-Archive validates the *extension*, not the
  // content, so a .streamDeckProfile (real zip bytes, wrong extension) is
  // copied to a .zip-named temp file first — confirmed empirically.
  const zipCopyPath = join(dir, "profile.zip");
  copyFileSync(profilePath, zipCopyPath);
  execFileSync("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    `Expand-Archive -LiteralPath '${zipCopyPath}' -DestinationPath '${dir}' -Force`,
  ], { windowsHide: true });
  return dir;
}

function main() {
  const registry = JSON.parse(readFileSync(REGISTRY_PATH, "utf-8"));
  const registryTargets = registry.targets.map((t) => ({ id: t.id, kind: t.kind, label: t.label }));

  const dir = extractProfileZip(PROFILE_PATH);
  let placed;
  try {
    const manifests = findManifestFiles(dir).map((p) => JSON.parse(readFileSync(p, "utf-8")));
    placed = placedTargetIds(manifests);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  const missing = missingTargets(registryTargets, placed);

  if (missing.length === 0) {
    console.log("profile-diff: every registry target already has a physical key. Nothing to add.");
    return;
  }

  console.log(`profile-diff: ${missing.length} registry target(s) have no physical key yet:\n`);
  for (const t of missing) {
    const actionName = t.kind === "tray" ? "Launch Target" : "Call Action";
    console.log(`  - ${t.id} (${t.label}) — drag a "${actionName}" key, pick "${t.label}" from its dropdown`);
  }
  console.log(
    "\nAfter placing them: export the profile over " +
      "com.ferraroroberto.fleetcoding.sdPlugin/profiles/fleet-coding-xl.streamDeckProfile, then `npm run package`.",
  );
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main();
}
