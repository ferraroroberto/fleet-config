/** Committed source-of-truth shape (registry/targets.json). */
export interface TrayTargetDefinition {
  id: string;
  kind: "tray";
  label: string;
  projectsTomlKey: string;
  iconSource: string;
}

/**
 * An authenticated POST against one home-automation action (fleet-config#574).
 * `actionId` is home-automation's own `action_id` (e.g. "plug_on") — the base
 * URL and bearer token are connection config (src/lib/config.ts), not
 * per-entry fields, since there is exactly one HTTP target.
 */
export interface HttpActionTargetDefinition {
  id: string;
  kind: "http-action";
  label: string;
  actionId: string;
}

// Future kinds (terminal/url/script — see fleet-config#370's "out of scope"
// follow-up) join this union additively; nothing below assumes "tray" is the
// only kind.
export type TargetDefinition = TrayTargetDefinition | HttpActionTargetDefinition;

export interface TargetRegistryFile {
  $schemaVersion: number;
  targets: TargetDefinition[];
}

/** Build-time resolved artifact (com.ferraroroberto.fleetcoding.sdPlugin/registry/targets.generated.json). */
export interface ResolvedTrayTarget {
  kind: "tray";
  id: string;
  label: string;
  cwd: string;
  command: string;
  icon: string;
}

export interface ResolvedHttpActionTarget {
  kind: "http-action";
  id: string;
  label: string;
  actionId: string;
}

export type ResolvedTarget = ResolvedTrayTarget | ResolvedHttpActionTarget;

export interface ResolvedTargetsFile {
  $schemaVersion: number;
  targets: ResolvedTarget[];
}

export interface LaunchTargetSettings {
  targetId?: string;
  [key: string]: string | undefined;
}

export interface CallActionSettings {
  targetId?: string;
  [key: string]: string | undefined;
}

export interface HomeAutomationConfig {
  baseUrl: string;
  token: string;
}
