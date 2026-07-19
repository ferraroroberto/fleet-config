import streamDeck, { action, KeyDownEvent, SingletonAction } from "@elgato/streamdeck";

/**
 * Visible action — one key in the bundled Fleet Coding XL profile.
 */
@action({ UUID: "com.ferraroroberto.fleetcoding.back" })
export class Back extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    try {
      // No profile name => Stream Deck returns to whichever profile was
      // active before the last explicit switch. Confirmed by the Elgato
      // team, not (yet) in the formal API reference:
      // https://github.com/orgs/elgatosf/discussions/117
      await streamDeck.profiles.switchToProfile(ev.action.device.id);
    } catch (err) {
      streamDeck.logger.error("back: switchToProfile failed", err);
      await ev.action.showAlert();
    }
  }
}
