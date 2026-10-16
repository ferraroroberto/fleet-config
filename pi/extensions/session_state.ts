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

// fleet-config#1455 — Pi's settle event carries no reply text, so the
// chief-inbox excerpt comes from the session's own branch: the newest
// assistant message's text blocks. null when there is none; the Python side
// then records why the excerpt is unavailable.
function lastAssistantText(ctx: any): string | null {
	try {
		const branch = ctx.sessionManager.getBranch() ?? [];
		for (let i = branch.length - 1; i >= 0; i--) {
			const message = branch[i]?.type === "message" ? branch[i].message : undefined;
			if (message?.role !== "assistant" || !Array.isArray(message.content)) continue;
			const text = message.content
				.filter((block: any) => block?.type === "text" && typeof block.text === "string")
				.map((block: any) => block.text)
				.join("\n")
				.trim();
			if (text) return text;
		}
	} catch {
		// advisory-only: an unreadable branch is an unavailable excerpt
	}
	return null;
}

function report(event: string, sessionId: string | undefined, cwd: string | undefined, lastAssistantMessage: string | null = null) {
	if (!sessionId) return;
	const payload = JSON.stringify({
		event,
		session_id: sessionId,
		cwd: cwd ?? null,
		transcript_path: null,
		last_assistant_message: lastAssistantMessage,
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
		report("agent_settled", ctx.sessionManager.getSessionId(), ctx.cwd, lastAssistantText(ctx));
	});

	pi.on("session_shutdown", (_event, ctx) => {
		report("session_shutdown", ctx.sessionManager.getSessionId(), ctx.cwd, lastAssistantText(ctx));
	});
}
