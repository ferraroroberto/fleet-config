import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { join } from "node:path";
import { PYTHON, ROOT } from "./policy_hooks.ts";

// fleet-config#349 — reports Pi's lifecycle events into the same
// sessions-state.json row session_state.py already maintains for Claude
// Code, so a Pi terminal shows working/needs-you on the Fleet Board instead
// of unknown. Shells out to session_state_pi.py rather than duplicating the
// atomic-write/prune logic here — that module documents itself as the sole
// writer. The repo root and venv interpreter come from policy_hooks.ts, which
// derives them from this file's own realpath (no hardcoded user paths).
const SCRIPT = join(ROOT, "hooks", "session_state_pi.py");

function report(event: string, sessionId: string | undefined, cwd: string | undefined) {
	if (!sessionId) return;
	const payload = JSON.stringify({
		event,
		session_id: sessionId,
		cwd: cwd ?? null,
		transcript_path: null,
	});
	try {
		// Advisory-only, fire-and-forget: a reporting failure must never
		// disturb the session, so stdout/stderr are ignored and any spawn
		// error is swallowed rather than surfaced.
		const child = spawn(PYTHON, [SCRIPT], { stdio: ["pipe", "ignore", "ignore"], windowsHide: true });
		child.on("error", () => {});
		child.stdin.end(payload);
	} catch {
		// same advisory-only contract
	}
}

export default function (pi: ExtensionAPI) {
	pi.on("input", (_event, ctx) => {
		report("input", ctx.sessionManager.getSessionId(), ctx.cwd);
	});

	pi.on("agent_settled", (_event, ctx) => {
		report("agent_settled", ctx.sessionManager.getSessionId(), ctx.cwd);
	});

	pi.on("session_shutdown", (_event, ctx) => {
		report("session_shutdown", ctx.sessionManager.getSessionId(), ctx.cwd);
	});
}
