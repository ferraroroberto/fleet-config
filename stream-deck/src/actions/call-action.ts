import streamDeck, { action, KeyDownEvent, SingletonAction } from "@elgato/streamdeck";

import { callHomeAutomationAction } from "../lib/http-client.js";
import { resolveTarget } from "../lib/registry.js";
import type { CallActionSettings, HomeAutomationConfig, ResolvedTarget } from "../types.js";

/**
 * Generic action, visible in the actions list so it can be dragged onto the
 * Fleet Coding XL layout once per http-action target during the one-time
 * manual profile-export step (fleet-config#574) — same shape as
 * launch-target.ts. Each instance's `targetId` is set via this action's
 * Property Inspector dropdown; unlike launch-target, there is no per-target
 * icon to refresh — the bundled icon covers every instance, and individual
 * keys are titled/re-iconed in the Stream Deck app itself if wanted.
 */
@action({ UUID: "com.ferraroroberto.fleetcoding.call-action" })
export class CallAction extends SingletonAction<CallActionSettings> {
  constructor(
    private readonly targets: ResolvedTarget[],
    private readonly config: HomeAutomationConfig | undefined,
  ) {
    super();
  }

  override async onKeyDown(ev: KeyDownEvent<CallActionSettings>): Promise<void> {
    if (!ev.action.isKey()) return;

    // Only ever resolves against the trusted, already-loaded registry — never
    // executes key-supplied text.
    const target = resolveTarget(this.targets, ev.payload.settings.targetId);
    if (!target || target.kind !== "http-action") {
      streamDeck.logger.error(
        `call-action: unknown, unset, or non-http-action targetId "${ev.payload.settings.targetId ?? ""}"`,
      );
      await ev.action.showAlert();
      return;
    }
    if (!this.config) {
      streamDeck.logger.error(
        "call-action: home-automation connection is not configured — see .env.sample",
      );
      await ev.action.showAlert();
      return;
    }

    try {
      await callHomeAutomationAction(target.actionId, this.config);
      await ev.action.showOk();
    } catch (err) {
      streamDeck.logger.error(`call-action: failed to call "${target.id}"`, err);
      await ev.action.showAlert();
    }
  }
}
