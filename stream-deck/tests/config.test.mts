import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

import { loadHomeAutomationConfig } from "../src/lib/config.ts";

const dirs: string[] = [];
function makeSdPluginDir(envContents: string | undefined): string {
  const dir = mkdtempSync(join(tmpdir(), "call-action-config-"));
  dirs.push(dir);
  if (envContents !== undefined) {
    writeFileSync(join(dir, ".env"), envContents);
  }
  return dir;
}

after(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
});

test("loadHomeAutomationConfig parses base URL and token, trimming a trailing slash", () => {
  const dir = makeSdPluginDir(
    "HOME_AUTOMATION_BASE_URL=https://ha.example.ts.net:8447/\nHOME_AUTOMATION_TOKEN=secret-token\n",
  );
  assert.deepEqual(loadHomeAutomationConfig(dir), {
    baseUrl: "https://ha.example.ts.net:8447",
    token: "secret-token",
  });
});

test("loadHomeAutomationConfig ignores blank lines and # comments", () => {
  const dir = makeSdPluginDir(
    "# home-automation connection\n\nHOME_AUTOMATION_BASE_URL=https://ha.example.ts.net:8447\nHOME_AUTOMATION_TOKEN=secret-token\n",
  );
  assert.equal(loadHomeAutomationConfig(dir).token, "secret-token");
});

test("loadHomeAutomationConfig throws when the .env file is missing", () => {
  const dir = makeSdPluginDir(undefined);
  assert.throws(() => loadHomeAutomationConfig(dir), /Failed to read/);
});

test("loadHomeAutomationConfig throws when HOME_AUTOMATION_TOKEN is unset", () => {
  const dir = makeSdPluginDir("HOME_AUTOMATION_BASE_URL=https://ha.example.ts.net:8447\n");
  assert.throws(() => loadHomeAutomationConfig(dir), /HOME_AUTOMATION_TOKEN is not set/);
});
