import type { HomeAutomationConfig } from "../types.js";

const REQUEST_TIMEOUT_MS = 5000;

/**
 * Calls home-automation's `POST /api/actions/{actionId}` alias endpoint
 * (home-automation#641). `actionId` only ever comes from the trusted,
 * committed registry (resolved via resolveTarget before this is called) —
 * never key-supplied text. `X-Automation-Source: streamdeck` reuses the
 * actor-tagging header that endpoint already understands, so a Stream
 * Deck-triggered call is distinguishable from a webapp-UI one in
 * home-automation's own activity log.
 *
 * A reasonable timeout + a thrown error on any non-ok response, so a slow or
 * unreachable home-automation instance can never block the plugin's event
 * loop — the caller shows `showAlert()` on rejection.
 */
export async function callHomeAutomationAction(
  actionId: string,
  config: HomeAutomationConfig,
): Promise<void> {
  const response = await fetch(`${config.baseUrl}/api/actions/${encodeURIComponent(actionId)}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.token}`,
      "X-Automation-Source": "streamdeck",
    },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`home-automation action "${actionId}" failed: HTTP ${response.status}`);
  }
}
