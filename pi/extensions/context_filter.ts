import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// fleet-config#545 — the Pi port of the fleet context filter (#392/#541).
// Pi's tool_result middleware can modify a tool's result in place, which is
// strictly better than the PreToolUse command-wrapping the Claude/Codex port
// uses: the output already exists, so nothing is re-executed and no wrapper
// timeout or shell dialect is involved. The Python side stays authoritative
// for everything that matters (mode resolution, skip rules, compression,
// secret handling, telemetry) via `context_filter_cli.py compress`; this file
// only ferries the output across and applies the returned patch.
const PYTHON = "E:/automation/fleet-config/.venv/Scripts/python.exe";
const CLI = "C:/Users/rober/.claude/hooks/context_filter_cli.py";
const COMPRESS_TIMEOUT_MS = 10_000;

// Fast-path pre-gate only: skip the Python spawn entirely when the filter is
// off. The Python side re-resolves the mode authoritatively (env → mode.json
// → off); this duplicate read exists purely so an "off" fleet pays zero
// per-tool-call overhead in Pi sessions. Tolerant of a missing/corrupt file.
function modeLooksActive(): boolean {
	const env = (process.env.FLEET_CONTEXT_FILTER_MODE ?? "").trim().toLowerCase();
	if (env) return env === "shadow" || env === "rewrite";
	try {
		const raw = readFileSync(join(homedir(), ".fleet-context-filter", "mode.json"), "utf-8");
		const mode = String(JSON.parse(raw.replace(/^\uFEFF/, "")).mode ?? "").trim().toLowerCase();
		return mode === "shadow" || mode === "rewrite";
	} catch {
		return false;
	}
}

function compress(payload: string): Promise<{ wrap: boolean; text?: string } | null> {
	return new Promise((resolve) => {
		try {
			const child = spawn(PYTHON, [CLI, "compress", "--agent", "pi"], {
				stdio: ["pipe", "pipe", "ignore"],
				windowsHide: true,
			});
			let out = "";
			// Fail-open on anything: spawn error, timeout, bad JSON — the
			// original result must stand untouched.
			const timer = setTimeout(() => {
				try { child.kill(); } catch {}
				resolve(null);
			}, COMPRESS_TIMEOUT_MS);
			child.on("error", () => { clearTimeout(timer); resolve(null); });
			child.stdout.on("data", (chunk) => { out += String(chunk); });
			child.on("close", () => {
				clearTimeout(timer);
				try { resolve(JSON.parse(out)); } catch { resolve(null); }
			});
			child.stdin.end(payload);
		} catch {
			resolve(null);
		}
	});
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_result", async (event, ctx) => {
		if (event.toolName !== "bash") return;
		if (!modeLooksActive()) return;

		const parts = Array.isArray(event.content) ? event.content : [];
		const text = parts
			.map((p: any) => (p && p.type === "text" ? String(p.text ?? "") : ""))
			.join("");
		if (!text) return;

		const command = String((event.input as any)?.command ?? "");
		const details: any = event.details ?? {};
		const payload = JSON.stringify({
			command,
			output: text,
			session_id: ctx.sessionManager.getSessionId() ?? null,
			cwd: ctx.cwd ?? null,
			exit_code: typeof details.exitCode === "number" ? details.exitCode : null,
		});

		const result = await compress(payload);
		if (result && result.wrap && typeof result.text === "string" && result.text) {
			return { content: [{ type: "text", text: result.text }] };
		}
		// shadow / off / skip / failure: leave the original result untouched
	});
}
