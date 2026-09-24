/** Committed source-of-truth shape (registry/targets.json). */
export interface TrayTargetDefinition {
  id: string;
  kind: "tray";
  label: string;
  projectsTomlKey: string;
  iconSource: string;
}

/**
 * The apps an http-action key can call — each exposes the same
 * `POST /api/actions/{action_id}` contract (home-automation#641).
 */
export type ActionApp = "home-automation" | "facilitation-suite";
export const ACTION_APPS: readonly ActionApp[] = ["home-automation", "facilitation-suite"];

/**
 * A POST against one app's action (fleet-config#574; the `app` field #1006).
 * `actionId` is that app's own `action_id` (e.g. "plug_on", or
 * "obs_profile/camera_pip" for facilitation-suite). `app` defaults to
 * "home-automation"; the base URL and token are per-app connection config
 * (src/lib/config.ts), not per-entry fields.
 */
export interface HttpActionTargetDefinition {
  id: string;
  kind: "http-action";
  label: string;
  actionId: string;
  app?: ActionApp;
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
  app: ActionApp;
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

/** One app's connection: a token only when the app asks for one. */
export interface ActionAppConfig {
  baseUrl: string;
  token?: string;
}

export type ActionAppConfigs = Partial<Record<ActionApp, ActionAppConfig>>;
