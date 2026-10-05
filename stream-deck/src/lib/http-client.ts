import type { ActionAppConfig } from "../types.js";

const REQUEST_TIMEOUT_MS = 5000;

/**
 * Calls an app's `POST /api/actions/{actionId}` alias endpoint (home-automation#641;
 * facilitation-suite shares the contract, fleet-config#1006). `actionId` only ever
 * comes from the trusted, committed registry (resolved via resolveTarget before this
 * is called) — never key-supplied text. `X-Automation-Source: streamdeck` reuses the
 * actor-tagging header that endpoint already understands, so a Stream Deck-triggered
 * call is distinguishable from a webapp-UI one in the app's own activity log. The
 * `Authorization` header is sent only when the app's config has a token
 * (facilitation-suite trusts loopback callers). home-automation's ids are one
 * segment, encoded whole; facilitation-suite's actions may take an argument
 * ("obs_profile/camera_pip"), so each of its segments is encoded on its own.
 *
 * A reasonable timeout + a thrown error on any non-ok response, so a slow or
 * unreachable app can never block the plugin's event loop — the caller shows
 * `showAlert()` on rejection.
 */
export async function callAppAction(
  app: string,
  actionId: string,
  config: ActionAppConfig,
): Promise<void> {
  const path = app === "home-automation"
    ? encodeURIComponent(actionId)
    : actionId.split("/").map(encodeURIComponent).join("/");
  const headers: Record<string, string> = { "X-Automation-Source": "streamdeck" };
  if (config.token) headers.Authorization = `Bearer ${config.token}`;
  const response = await fetch(`${config.baseUrl}/api/actions/${path}`, {
    method: "POST",
    headers,
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`${app} action "${actionId}" failed: HTTP ${response.status}`);
  }
}
