import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveTarget } from "../src/lib/registry.ts";
import type { ResolvedTarget } from "../src/types.ts";

const TARGETS: ResolvedTarget[] = [
  { id: "app-launcher", label: "App Launcher", cwd: "E:/automation/app-launcher", command: "tray.bat", icon: "imgs/targets/app-launcher.png" },
  { id: "photo-ocr", label: "Photo OCR", cwd: "E:/automation/photo-ocr", command: "tray.bat", icon: "imgs/targets/photo-ocr.png" },
];

test("resolveTarget finds a known target by id", () => {
  const found = resolveTarget(TARGETS, "photo-ocr");
  assert.equal(found?.label, "Photo OCR");
});

test("resolveTarget returns undefined for an unknown id", () => {
  assert.equal(resolveTarget(TARGETS, "not-a-real-target"), undefined);
});

test("resolveTarget returns undefined when targetId is unset", () => {
  assert.equal(resolveTarget(TARGETS, undefined), undefined);
});
