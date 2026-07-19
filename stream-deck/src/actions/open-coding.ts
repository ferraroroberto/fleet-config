import streamDeck, { action, KeyDownEvent, SingletonAction } from "@elgato/streamdeck";

import { FLEET_CODING_PROFILE } from "../lib/constants.js";

/**
 * Visible action — placed once, by hand, in the existing general-purpose
 * profile (per the issue, never inserted automatically). Pressing it enters
 * the bundled, read-only Fleet Coding XL profile.
 */
@action({ UUID: "com.ferraroroberto.fleetcoding.open-coding" })
export class OpenCoding extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    try {
      await streamDeck.profiles.switchToProfile(ev.action.device.id, FLEET_CODING_PROFILE);
    } catch (err) {
      streamDeck.logger.error("open-coding: switchToProfile failed", err);
      await ev.action.showAlert();
    }
  }
}
