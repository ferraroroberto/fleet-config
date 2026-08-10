import assert from "node:assert/strict";
import { test } from "node:test";

import { validateAndResolve, readPngDimensions } from "../scripts/sync-assets.mjs";

function fakePng(width: number, height: number): Buffer {
  const buf = Buffer.alloc(24);
  buf.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  buf.writeUInt32BE(width, 16);
  buf.writeUInt32BE(height, 20);
  return buf;
}

const PROJECTS = {
  "app-launcher": { cwd_prefix: "E:/automation/app-launcher", tray_cmd: "tray.bat" },
  "photo-ocr": { cwd_prefix: "E:/automation/photo-ocr", tray_cmd: "tray.bat" },
};

function baseRegistry() {
  return {
    targets: [
      {
        id: "app-launcher",
        kind: "tray",
        label: "App Launcher",
        projectsTomlKey: "app-launcher",
        iconSource: "E:/automation/app-launcher/assets/stream-deck/app-launcher-144.png",
      },
    ],
  };
}

test("readPngDimensions parses width/height from a valid header", () => {
  assert.deepEqual(readPngDimensions(fakePng(144, 144)), { width: 144, height: 144 });
});

test("readPngDimensions returns null for a non-PNG buffer", () => {
  assert.equal(readPngDimensions(Buffer.from("not a png, too short")), null);
});

test("validateAndResolve succeeds for a fully valid target", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const { errors, resolvedTargets, piOptions } = validateAndResolve(baseRegistry(), PROJECTS, io);
  assert.deepEqual(errors, []);
  assert.equal(resolvedTargets.length, 1);
  assert.equal(resolvedTargets[0].cwd, "E:/automation/app-launcher");
  assert.equal(resolvedTargets[0].command, "tray.bat");
  assert.match(piOptions[0], /value="app-launcher"/);
});

test("validateAndResolve fails when the projectsTomlKey is missing from projects.toml", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = baseRegistry();
  registry.targets[0].projectsTomlKey = "does-not-exist";
  const { errors, resolvedTargets } = validateAndResolve(registry, PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /no \[does-not-exist\] entry/);
});

test("validateAndResolve fails when the tray launcher file is missing on disk", () => {
  const io = { fileExists: () => false, readFile: () => fakePng(144, 144) };
  const { errors, resolvedTargets } = validateAndResolve(baseRegistry(), PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /launcher not found on disk/);
});

test("validateAndResolve fails when the icon is undersized", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(64, 64) };
  const { errors, resolvedTargets } = validateAndResolve(baseRegistry(), PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /64x64, need >=144x144/);
});

test("validateAndResolve fails when iconSource is not a .png", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = baseRegistry();
  registry.targets[0].iconSource = "E:/automation/app-launcher/assets/stream-deck/app-launcher.svg";
  const { errors, resolvedTargets } = validateAndResolve(registry, PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /must be a \.png/);
});

test("validateAndResolve resolves a valid http-action entry with no icon copy needed", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = {
    targets: [{ id: "light-on", kind: "http-action", label: "Light On", actionId: "plug_on" }],
  };
  const { errors, resolvedTargets, piOptions, httpActionPiOptions } = validateAndResolve(registry, PROJECTS, io);
  assert.deepEqual(errors, []);
  assert.equal(resolvedTargets.length, 1);
  assert.deepEqual(resolvedTargets[0], { kind: "http-action", id: "light-on", label: "Light On", actionId: "plug_on" });
  assert.deepEqual(piOptions, []);
  assert.match(httpActionPiOptions[0], /value="light-on"/);
});

test("validateAndResolve fails when an http-action entry has no actionId", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = {
    targets: [{ id: "light-on", kind: "http-action", label: "Light On", actionId: "" }],
  };
  const { errors, resolvedTargets } = validateAndResolve(registry, PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /needs a non-empty actionId/);
});

test("validateAndResolve fails when an http-action entry has no label", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = {
    targets: [{ id: "light-on", kind: "http-action", label: "  ", actionId: "plug_on" }],
  };
  const { errors, resolvedTargets } = validateAndResolve(registry, PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /needs a non-empty label/);
});

test("validateAndResolve fails loudly on an unknown target kind", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = { targets: [{ id: "mystery", kind: "url", label: "Mystery" }] };
  const { errors, resolvedTargets } = validateAndResolve(registry, PROJECTS, io);
  assert.equal(resolvedTargets.length, 0);
  assert.match(errors[0], /unknown kind "url"/);
});

test("validateAndResolve handles a mix of tray and http-action entries independently", () => {
  const io = { fileExists: () => true, readFile: () => fakePng(144, 144) };
  const registry = baseRegistry();
  registry.targets.push({ id: "light-on", kind: "http-action", label: "Light On", actionId: "plug_on" });
  const { errors, resolvedTargets, piOptions, httpActionPiOptions } = validateAndResolve(registry, PROJECTS, io);
  assert.deepEqual(errors, []);
  assert.equal(resolvedTargets.length, 2);
  assert.equal(piOptions.length, 1);
  assert.equal(httpActionPiOptions.length, 1);
});
