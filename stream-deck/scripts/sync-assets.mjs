#!/usr/bin/env node
// Build-time cross-check + asset sync for the Fleet Coding Stream Deck plugin
// (fleet-config#370, #574). Hard-fails (non-zero exit, all problems reported
// together) on any drift between registry/targets.json and the fleet's real
// hooks/projects.toml, a missing/undersized icon, or a malformed http-action
// entry — "fail clearly when a configured target is missing/broken" per the
// issue.
//
// Also renders templates/launch-target.pi.html and templates/call-action.pi.html
// into the plugin's Property Inspectors, and writes the runtime-resolved
// target list (com.ferraroroberto.fleetcoding.sdPlugin/registry/targets.generated.json)
// that src/lib/registry.ts reads at runtime — the committed registry only
// carries the declarative per-kind shape, never a launch path or endpoint
// URL, so this step is the one place a tray entry's cwd_prefix/tray_cmd get
// resolved (http-action entries need no such resolution — actionId is
// already everything src/lib/http-client.ts needs).

import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parse as parseToml } from "smol-toml";

const HERE = dirname(fileURLToPath(import.meta.url));
const STREAM_DECK_ROOT = resolve(HERE, "..");
const REPO_ROOT = resolve(STREAM_DECK_ROOT, "..");
const SD_PLUGIN_DIR = join(STREAM_DECK_ROOT, "com.ferraroroberto.fleetcoding.sdPlugin");
const PROJECTS_TOML_PATH = join(REPO_ROOT, "hooks", "projects.toml");
const REGISTRY_PATH = join(STREAM_DECK_ROOT, "registry", "targets.json");
const LAUNCH_TARGET_PI_TEMPLATE_PATH = join(STREAM_DECK_ROOT, "templates", "launch-target.pi.html");
const CALL_ACTION_PI_TEMPLATE_PATH = join(STREAM_DECK_ROOT, "templates", "call-action.pi.html");

export function readPngDimensions(fileBuffer) {
  const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (fileBuffer.length < 24 || !fileBuffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
    return null;
  }
  return { width: fileBuffer.readUInt32BE(16), height: fileBuffer.readUInt32BE(20) };
}

/**
 * Pure validation/resolution core, with all filesystem access injected via
 * `io` — this is what tests/sync-assets.test.mts exercises directly, no
 * fixture directory tree required.
 *
 * @param {{targets: Array}} registry - parsed registry/targets.json
 * @param {Record<string, any>} projects - parsed hooks/projects.toml
 * @param {{fileExists: (p: string) => boolean, readFile: (p: string) => Buffer}} io
 */
function validateTrayTarget(target, projects, io, errors) {
  const project = projects[target.projectsTomlKey];
  if (!project) {
    errors.push(`target "${target.id}": no [${target.projectsTomlKey}] entry in hooks/projects.toml`);
    return null;
  }
  if (!project.tray_cmd) {
    errors.push(`target "${target.id}": [${target.projectsTomlKey}] in hooks/projects.toml has no tray_cmd`);
    return null;
  }
  if (!project.cwd_prefix) {
    errors.push(`target "${target.id}": [${target.projectsTomlKey}] in hooks/projects.toml has no cwd_prefix`);
    return null;
  }
  const launcherPath = join(project.cwd_prefix, project.tray_cmd);
  if (!io.fileExists(launcherPath)) {
    errors.push(`target "${target.id}": launcher not found on disk at ${launcherPath}`);
    return null;
  }

  if (!io.fileExists(target.iconSource)) {
    errors.push(`target "${target.id}": iconSource not found at ${target.iconSource}`);
    return null;
  }
  if (!target.iconSource.toLowerCase().endsWith(".png")) {
    errors.push(`target "${target.id}": iconSource must be a .png, got ${target.iconSource}`);
    return null;
  }
  const dims = readPngDimensions(io.readFile(target.iconSource));
  if (!dims) {
    errors.push(`target "${target.id}": iconSource is not a valid PNG (${target.iconSource})`);
    return null;
  }
  if (dims.width < 144 || dims.height < 144) {
    errors.push(
      `target "${target.id}": iconSource is ${dims.width}x${dims.height}, need >=144x144 (${target.iconSource})`,
    );
    return null;
  }

  return {
    kind: "tray",
    id: target.id,
    label: target.label,
    cwd: project.cwd_prefix,
    command: project.tray_cmd,
    icon: `imgs/targets/${target.id}.png`,
    iconSource: target.iconSource,
  };
}

function validateHttpActionTarget(target, errors) {
  if (typeof target.label !== "string" || target.label.trim() === "") {
    errors.push(`target "${target.id}": http-action entry needs a non-empty label`);
    return null;
  }
  if (typeof target.actionId !== "string" || target.actionId.trim() === "") {
    errors.push(`target "${target.id}": http-action entry needs a non-empty actionId`);
    return null;
  }
  return { kind: "http-action", id: target.id, label: target.label, actionId: target.actionId };
}

/**
 * Pure validation/resolution core, with all filesystem access injected via
 * `io` — this is what tests/sync-assets.test.mts exercises directly, no
 * fixture directory tree required. Branches per `target.kind`; an unknown
 * kind fails loud rather than being silently skipped.
 *
 * @param {{targets: Array}} registry - parsed registry/targets.json
 * @param {Record<string, any>} projects - parsed hooks/projects.toml
 * @param {{fileExists: (p: string) => boolean, readFile: (p: string) => Buffer}} io
 */
export function validateAndResolve(registry, projects, io) {
  const errors = [];
  const resolvedTargets = [];
  const piOptions = [];
  const httpActionPiOptions = [];

  for (const target of registry.targets) {
    if (target.kind === "tray") {
      const resolved = validateTrayTarget(target, projects, io, errors);
      if (resolved) {
        resolvedTargets.push(resolved);
        piOptions.push(`      <option value="${target.id}">${target.label}</option>`);
      }
    } else if (target.kind === "http-action") {
      const resolved = validateHttpActionTarget(target, errors);
      if (resolved) {
        resolvedTargets.push(resolved);
        httpActionPiOptions.push(`      <option value="${target.id}">${target.label}</option>`);
      }
    } else {
      errors.push(`target "${target.id}": unknown kind "${target.kind}"`);
    }
  }

  return { errors, resolvedTargets, piOptions, httpActionPiOptions };
}

function main() {
  const registry = JSON.parse(readFileSync(REGISTRY_PATH, "utf-8"));
  const projects = parseToml(readFileSync(PROJECTS_TOML_PATH, "utf-8"));

  const { errors, resolvedTargets, piOptions, httpActionPiOptions } = validateAndResolve(registry, projects, {
    fileExists: existsSync,
    readFile: readFileSync,
  });

  if (errors.length > 0) {
    console.error("sync-assets: FAILED\n");
    for (const message of errors) console.error(`  - ${message}`);
    console.error(`\n${errors.length} problem(s) — see registry/targets.json and hooks/projects.toml.`);
    process.exit(1);
  }

  const targetsDir = join(SD_PLUGIN_DIR, "imgs", "targets");
  const registryOutDir = join(SD_PLUGIN_DIR, "registry");
  const piOutDir = join(SD_PLUGIN_DIR, "pi");
  mkdirSync(targetsDir, { recursive: true });
  mkdirSync(registryOutDir, { recursive: true });
  mkdirSync(piOutDir, { recursive: true });

  // Only tray entries carry an icon to copy in — http-action entries have
  // none (the action's own bundled icon covers every instance).
  for (const target of resolvedTargets) {
    if (target.kind === "tray") {
      copyFileSync(target.iconSource, join(SD_PLUGIN_DIR, target.icon));
    }
  }

  writeFileSync(
    join(registryOutDir, "targets.generated.json"),
    JSON.stringify(
      {
        $schemaVersion: 1,
        targets: resolvedTargets.map(({ iconSource, ...rest }) => rest),
      },
      null,
      2,
    ) + "\n",
  );

  const launchTargetTemplate = readFileSync(LAUNCH_TARGET_PI_TEMPLATE_PATH, "utf-8");
  writeFileSync(
    join(piOutDir, "launch-target.html"),
    launchTargetTemplate.replace("{{TARGET_OPTIONS}}", piOptions.join("\n")),
  );

  const callActionTemplate = readFileSync(CALL_ACTION_PI_TEMPLATE_PATH, "utf-8");
  writeFileSync(
    join(piOutDir, "call-action.html"),
    callActionTemplate.replace("{{TARGET_OPTIONS}}", httpActionPiOptions.join("\n")),
  );

  const trayCount = resolvedTargets.filter((t) => t.kind === "tray").length;
  const httpActionCount = resolvedTargets.filter((t) => t.kind === "http-action").length;
  console.log(
    `sync-assets: OK — ${resolvedTargets.length}/${registry.targets.length} targets validated ` +
      `(${trayCount} tray, ${httpActionCount} http-action)`,
  );
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main();
}
