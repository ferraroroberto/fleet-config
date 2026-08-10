import streamDeck, {
  action,
  DidReceiveSettingsEvent,
  KeyDownEvent,
  SingletonAction,
  WillAppearEvent,
} from "@elgato/streamdeck";
import { join } from "node:path";

import { launch } from "../lib/launcher.js";
import { resolveTarget } from "../lib/registry.js";
import type { LaunchTargetSettings, ResolvedTarget } from "../types.js";

/**
 * Generic action, visible in the actions list so it can be dragged onto the
 * Fleet Coding XL layout (six times, once per tray target) during the
 * one-time manual profile-export step — Stream Deck gives no other way to
 * author a bundled profile. Each instance's `targetId` is set via the
 * launch-target Property Inspector dropdown.
 */
@action({ UUID: "com.ferraroroberto.fleetcoding.launch-target" })
export class LaunchTarget extends SingletonAction<LaunchTargetSettings> {
  constructor(
    private readonly sdPluginDir: string,
    private readonly targets: ResolvedTarget[],
  ) {
    super();
  }

  override async onWillAppear(ev: WillAppearEvent<LaunchTargetSettings>): Promise<void> {
    if (!ev.action.isKey()) return;
    await this.refreshIcon(ev.action, ev.payload.settings);
  }

  // The Property Inspector dropdown updates settings on an already-appeared
  // key — onWillAppear alone only fires once per appearance, so without this
  // handler a target picked after dragging the key in never gets its icon
  // refreshed (the launch itself still works either way, since onKeyDown
  // re-resolves settings fresh on every press).
  override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<LaunchTargetSettings>): Promise<void> {
    if (!ev.action.isKey()) return;
    await this.refreshIcon(ev.action, ev.payload.settings);
  }

  private async refreshIcon(
    action: WillAppearEvent<LaunchTargetSettings>["action"],
    settings: LaunchTargetSettings,
  ): Promise<void> {
    const target = resolveTarget(this.targets, settings.targetId);
    if (target && target.kind === "tray") {
      await action.setImage(join(this.sdPluginDir, target.icon));
    }
  }

  override async onKeyDown(ev: KeyDownEvent<LaunchTargetSettings>): Promise<void> {
    if (!ev.action.isKey()) return;

    // Only ever resolves against the trusted, already-loaded registry — never
    // executes key-supplied text.
    const target = resolveTarget(this.targets, ev.payload.settings.targetId);
    if (!target || target.kind !== "tray") {
      streamDeck.logger.error(
        `launch-target: unknown, unset, or non-tray targetId "${ev.payload.settings.targetId ?? ""}"`,
      );
      await ev.action.showAlert();
      return;
    }

    try {
      await launch(target);
      await ev.action.showOk();
    } catch (err) {
      streamDeck.logger.error(`launch-target: failed to launch "${target.id}"`, err);
      await ev.action.showAlert();
    }
  }
}
