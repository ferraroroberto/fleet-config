import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { callHomeAutomationAction } from "../src/lib/http-client.ts";

const CONFIG = { baseUrl: "https://ha.example.ts.net:8447", token: "secret-token" };

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("callHomeAutomationAction posts to the actions endpoint with the bearer token and streamdeck source", async () => {
  let captured: { url: string; init: RequestInit } | undefined;
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    captured = { url, init };
    return new Response(null, { status: 200 });
  }) as typeof fetch;

  await callHomeAutomationAction("plug_on", CONFIG);

  assert.equal(captured?.url, "https://ha.example.ts.net:8447/api/actions/plug_on");
  assert.equal(captured?.init.method, "POST");
  const headers = captured?.init.headers as Record<string, string>;
  assert.equal(headers.Authorization, "Bearer secret-token");
  assert.equal(headers["X-Automation-Source"], "streamdeck");
});

test("callHomeAutomationAction URL-encodes the action id", async () => {
  let capturedUrl = "";
  globalThis.fetch = (async (url: string) => {
    capturedUrl = url;
    return new Response(null, { status: 200 });
  }) as typeof fetch;

  await callHomeAutomationAction("weird id/x", CONFIG);

  assert.equal(capturedUrl, "https://ha.example.ts.net:8447/api/actions/weird%20id%2Fx");
});

test("callHomeAutomationAction throws on a non-ok response", async () => {
  globalThis.fetch = (async () => new Response(null, { status: 404 })) as typeof fetch;

  await assert.rejects(
    () => callHomeAutomationAction("does_not_exist", CONFIG),
    /HTTP 404/,
  );
});
