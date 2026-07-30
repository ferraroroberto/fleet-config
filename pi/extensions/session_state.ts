import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";

// fleet-config#349 — reports Pi's lifecycle events into the same
// sessions-state.json row session_state.py already maintains for Claude
// Code, so a Pi terminal shows working/needs-you on the Fleet Board instead
// of unknown. Shells out to session_state_pi.py (via ~/.claude/hooks, the
// stable junction target every absolute-path hook invocation in this repo
// uses) rather than duplicating the atomic-write/prune logic here — that
// module documents itself as the sole writer.
const PYTHON = "E:/automation/fleet-config/.venv/Scripts/python.exe";
const SCRIPT = "C:/Users/rober/.claude/hooks/session_state_pi.py";

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
