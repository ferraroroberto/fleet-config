import assert from "node:assert/strict";
import { test } from "node:test";

import { missingTargets, placedTargetIds } from "../scripts/profile-diff.mjs";

function pageManifest(actions: Record<string, { UUID: string; targetId?: string }>) {
  return {
    Controllers: [
      {
        Actions: Object.fromEntries(
          Object.entries(actions).map(([pos, { UUID, targetId }]) => [
            pos,
            { UUID, Settings: targetId ? { targetId } : {} },
          ]),
        ),
      },
    ],
  };
}

test("placedTargetIds collects targetId from our own launch-target/call-action keys only", () => {
  const manifests = [
    pageManifest({
      "0,0": { UUID: "com.ferraroroberto.fleetcoding.launch-target", targetId: "app-launcher" },
      "1,0": { UUID: "com.ferraroroberto.fleetcoding.call-action", targetId: "light-on" },
      // A third-party/unrelated key (e.g. Elgato's own Switch Profile) must not leak in.
      "7,0": { UUID: "com.elgato.streamdeck.profile.rotate" },
    }),
  ];
  assert.deepEqual(placedTargetIds(manifests), new Set(["app-launcher", "light-on"]));
});

test("placedTargetIds ignores non-page manifests with no Controllers key", () => {
  const topLevelProfileManifest = { Device: {}, Name: "Fleet Coding XL", Pages: {}, Version: "3.0" };
  assert.deepEqual(placedTargetIds([topLevelProfileManifest]), new Set());
});

test("placedTargetIds merges across multiple pages", () => {
  const manifests = [
    pageManifest({ "0,0": { UUID: "com.ferraroroberto.fleetcoding.launch-target", targetId: "app-launcher" } }),
    pageManifest({ "0,0": { UUID: "com.ferraroroberto.fleetcoding.call-action", targetId: "ac-on" } }),
  ];
  assert.deepEqual(placedTargetIds(manifests), new Set(["app-launcher", "ac-on"]));
});

test("missingTargets returns registry entries with no placed key, in file order", () => {
  const registryTargets = [
    { id: "app-launcher", kind: "tray", label: "App Launcher" },
    { id: "light-on", kind: "http-action", label: "Light On" },
    { id: "light-off", kind: "http-action", label: "Light Off" },
  ];
  const placed = new Set(["app-launcher"]);
  assert.deepEqual(missingTargets(registryTargets, placed), [
    { id: "light-on", kind: "http-action", label: "Light On" },
    { id: "light-off", kind: "http-action", label: "Light Off" },
  ]);
});

test("missingTargets returns an empty list when everything is placed", () => {
  const registryTargets = [{ id: "app-launcher", kind: "tray", label: "App Launcher" }];
  assert.deepEqual(missingTargets(registryTargets, new Set(["app-launcher"])), []);
});
