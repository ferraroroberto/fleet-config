import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { HomeAutomationConfig } from "../types.js";

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
  const envPath = join(sdPluginDir, ".env");
  let contents: string;
  try {
    contents = readFileSync(envPath, "utf-8");
  } catch (err) {
    throw new Error(`Failed to read ${envPath} (copy .env.sample and fill in real values)`, {
      cause: err,
    });
  }

  const values = parseEnvFile(contents);
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
