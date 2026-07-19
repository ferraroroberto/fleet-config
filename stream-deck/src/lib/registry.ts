import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ResolvedTarget, ResolvedTargetsFile } from "../types.js";

/**
 * Reads the build-time-generated, resolved target list (never the committed
 * registry/targets.json directly — that file has no launch path, only the
 * declarative id/label/projectsTomlKey/iconSource shape validated by
 * scripts/sync-assets.mjs).
 */
export function loadResolvedTargets(sdPluginDir: string): ResolvedTarget[] {
  const path = join(sdPluginDir, "registry", "targets.generated.json");
  const raw = readFileSync(path, "utf-8");
  const parsed = JSON.parse(raw) as ResolvedTargetsFile;
  return parsed.targets;
}

/**
 * Only ever resolves against the trusted, already-loaded target list — never
 * accepts or executes arbitrary key-supplied text.
 */
export function resolveTarget(
  targets: ResolvedTarget[],
  targetId: string | undefined,
): ResolvedTarget | undefined {
  if (!targetId) return undefined;
  return targets.find((target) => target.id === targetId);
}
