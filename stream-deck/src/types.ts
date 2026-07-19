/** Committed source-of-truth shape (registry/targets.json). */
export interface TrayTargetDefinition {
  id: string;
  kind: "tray";
  label: string;
  projectsTomlKey: string;
  iconSource: string;
}

// Future kinds (terminal/url/script — see fleet-config#370's "out of scope"
// follow-up) join this union additively; nothing below assumes "tray" is the
// only kind.
export type TargetDefinition = TrayTargetDefinition;

export interface TargetRegistryFile {
  $schemaVersion: number;
  targets: TargetDefinition[];
}

/** Build-time resolved artifact (com.ferraroroberto.fleetcoding.sdPlugin/registry/targets.generated.json). */
export interface ResolvedTarget {
  id: string;
  label: string;
  cwd: string;
  command: string;
  icon: string;
}

export interface ResolvedTargetsFile {
  $schemaVersion: number;
  targets: ResolvedTarget[];
}

export interface LaunchTargetSettings {
  targetId?: string;
  [key: string]: string | undefined;
}
