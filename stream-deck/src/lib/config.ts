import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ActionAppConfigs, HomeAutomationConfig } from "../types.js";

/**
 * Minimal `KEY=VALUE` .env parser — just the two keys this plugin needs, no
 * interpolation/multiline/export support. Kept hand-rolled rather than
 * pulling in the `dotenv` package: dotenv's CJS entry does a
 * `require("../package.json")` for its own version string, which Rollup's
 * commonjs plugin can't bundle without also adding `@rollup/plugin-json` —
 * not worth a second build-tool dependency for two env vars.
 */
function parseEnvFile(contents: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

/**
 * Loads the home-automation connection config from the plugin bundle's own
 * `.env` (gitignored, `.env.sample` committed) — never committed, per the
 * fleet's standing secrets convention. Throws a clear error on a missing
 * file or missing values; the caller (plugin.ts) logs and leaves the config
 * undefined rather than crashing the whole plugin, matching how a missing
 * registry is already handled.
 */
export function loadHomeAutomationConfig(sdPluginDir: string): HomeAutomationConfig {
  return homeAutomationFrom(parseEnvFile(readEnv(sdPluginDir)), join(sdPluginDir, ".env"));
}

function readEnv(sdPluginDir: string): string {
  const envPath = join(sdPluginDir, ".env");
  try {
    return readFileSync(envPath, "utf-8");
  } catch (err) {
    throw new Error(`Failed to read ${envPath} (copy .env.sample and fill in real values)`, {
      cause: err,
    });
  }
}

function homeAutomationFrom(values: Record<string, string>, envPath: string): HomeAutomationConfig {
  const baseUrl = values.HOME_AUTOMATION_BASE_URL?.trim();
  const token = values.HOME_AUTOMATION_TOKEN?.trim();
  if (!baseUrl) {
    throw new Error(`HOME_AUTOMATION_BASE_URL is not set in ${envPath}`);
  }
  if (!token) {
    throw new Error(`HOME_AUTOMATION_TOKEN is not set in ${envPath}`);
  }
  return { baseUrl: baseUrl.replace(/\/+$/, ""), token };
}

/** facilitation-suite runs on this same PC; from loopback it needs no token. */
export const DEFAULT_FACILITATION_SUITE_URL = "http://127.0.0.1:8449";

/**
 * Every app's connection (fleet-config#1006). home-automation is present only
 * when its two keys are set (a missing/partial .env leaves it out, and its
 * keys show `showAlert()` per press — as before); facilitation-suite always
 * has its loopback default unless `FACILITATION_SUITE_BASE_URL` overrides it,
 * with an optional `FACILITATION_SUITE_TOKEN`.
 */
export function loadActionAppConfigs(
  sdPluginDir: string,
  onError: (err: unknown) => void = () => {},
): ActionAppConfigs {
  let values: Record<string, string> = {};
  const envPath = join(sdPluginDir, ".env");
  try {
    values = parseEnvFile(readEnv(sdPluginDir));
  } catch (err) {
    onError(err);
  }
  const configs: ActionAppConfigs = {};
  try {
    configs["home-automation"] = homeAutomationFrom(values, envPath);
  } catch (err) {
    onError(err);
  }
  const suiteUrl = values.FACILITATION_SUITE_BASE_URL?.trim() || DEFAULT_FACILITATION_SUITE_URL;
  const suiteToken = values.FACILITATION_SUITE_TOKEN?.trim();
  configs["facilitation-suite"] = {
    baseUrl: suiteUrl.replace(/\/+$/, ""),
    ...(suiteToken ? { token: suiteToken } : {}),
  };
  return configs;
}
