import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

import { DEFAULT_FACILITATION_SUITE_URL, loadActionAppConfigs, loadHomeAutomationConfig } from "../src/lib/config.ts";

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

test("loadActionAppConfigs gives facilitation-suite its loopback default without a token (fleet-config#1006)", () => {
  const dir = makeSdPluginDir(
    "HOME_AUTOMATION_BASE_URL=https://ha.example.ts.net:8447\nHOME_AUTOMATION_TOKEN=secret-token\n",
  );
  const configs = loadActionAppConfigs(dir);
  assert.deepEqual(configs["facilitation-suite"], { baseUrl: DEFAULT_FACILITATION_SUITE_URL });
  assert.equal(configs["home-automation"]?.token, "secret-token");
});

test("loadActionAppConfigs reads the suite's own URL and token when set", () => {
  const dir = makeSdPluginDir(
    "FACILITATION_SUITE_BASE_URL=https://pc.example.ts.net:8449/\nFACILITATION_SUITE_TOKEN=t0k\n",
  );
  const errors: unknown[] = [];
  const configs = loadActionAppConfigs(dir, (err) => errors.push(err));
  assert.deepEqual(configs["facilitation-suite"], { baseUrl: "https://pc.example.ts.net:8449", token: "t0k" });
  assert.equal(configs["home-automation"], undefined); // its keys are missing: left out, and said
  assert.equal(errors.length, 1);
});

test("loadActionAppConfigs keeps the suite working with no .env at all", () => {
  const dir = makeSdPluginDir(undefined);
  const errors: unknown[] = [];
  const configs = loadActionAppConfigs(dir, (err) => errors.push(err));
  assert.deepEqual(configs["facilitation-suite"], { baseUrl: DEFAULT_FACILITATION_SUITE_URL });
  assert.equal(configs["home-automation"], undefined);
  assert.ok(errors.length >= 1);
});
