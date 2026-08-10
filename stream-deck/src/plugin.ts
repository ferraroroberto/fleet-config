import streamDeck from "@elgato/streamdeck";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Back } from "./actions/back.js";
import { CallAction } from "./actions/call-action.js";
import { LaunchTarget } from "./actions/launch-target.js";
import { OpenCoding } from "./actions/open-coding.js";
import { loadHomeAutomationConfig } from "./lib/config.js";
import { loadResolvedTargets } from "./lib/registry.js";
import type { HomeAutomationConfig, ResolvedTarget } from "./types.js";

streamDeck.logger.setLevel("info");

// After Rollup bundles every module into this one output file at
// <sdPlugin>/bin/plugin.js, import.meta.url uniformly refers to that output
// file regardless of which source module the code originated from.
const SD_PLUGIN_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");

let targets: ResolvedTarget[] = [];
try {
  targets = loadResolvedTargets(SD_PLUGIN_DIR);
} catch (err) {
  streamDeck.logger.error(
    "Failed to load registry/targets.generated.json — run `npm run sync-assets` before packaging.",
    err,
  );
}

// A missing/unfilled .env leaves this undefined rather than crashing the
// whole plugin — call-action.ts shows showAlert() per press while
// unconfigured (mirrors the missing-registry handling above).
let homeAutomationConfig: HomeAutomationConfig | undefined;
try {
  homeAutomationConfig = loadHomeAutomationConfig(SD_PLUGIN_DIR);
} catch (err) {
  streamDeck.logger.error(
    "Failed to load home-automation connection config — see .env.sample.",
    err,
  );
}

streamDeck.actions.registerAction(new LaunchTarget(SD_PLUGIN_DIR, targets));
streamDeck.actions.registerAction(new CallAction(targets, homeAutomationConfig));
streamDeck.actions.registerAction(new OpenCoding());
streamDeck.actions.registerAction(new Back());

streamDeck.connect();
