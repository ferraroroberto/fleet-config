import streamDeck from "@elgato/streamdeck";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Back } from "./actions/back.js";
import { LaunchTarget } from "./actions/launch-target.js";
import { OpenCoding } from "./actions/open-coding.js";
import { loadResolvedTargets } from "./lib/registry.js";
import type { ResolvedTarget } from "./types.js";

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

streamDeck.actions.registerAction(new LaunchTarget(SD_PLUGIN_DIR, targets));
streamDeck.actions.registerAction(new OpenCoding());
streamDeck.actions.registerAction(new Back());

streamDeck.connect();
