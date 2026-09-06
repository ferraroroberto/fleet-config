import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Transport and wiring only. Policy and native payload translation live in Python.
const ROOT = join(dirname(realpathSync(fileURLToPath(import.meta.url))), "../..");
const PYTHON = join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const TIMEOUT_MS = 15_000;
export const WARNING_PREFIX = "[Fleet policy] ";
const SHELL = ["pre_commit_no_ai_trailer", "secret_scan_guard", "safe_kill_guard", "venv_discipline"];
const BASH = ["gh_body_file_guard", "bash_cmdexe_syntax_guard", "bash_windows_path_guard"];
const EDIT = ["docs_dated_filename_guard", "branch_before_edit_guard"];
const POST_EDIT = ["py_syntax_check", "hub_bypass_warn", "browser_stealth_lint"];
const READ_ONLY = new Set(["read", "grep", "find", "ls"]);
type Decision = { fleet_policy: 1; decision: "allow" | "block" | "warn"; message: string };

async function policyPayload(event: any, cwd: string, sessionId: string): Promise<string> {
	let resolvedPath: string | undefined;
	if (["edit", "write"].includes(event.toolName)) {
		// Pi write/edit call this exact packaged resolver. Its aliases (@, ~,
		// file URLs, Unicode spaces, Windows shell paths) must not be reimplemented.
		// Missing package/API support is unavailable enforcement, never a guessed path.
		const { getPackageDir } = await import("@earendil-works/pi-coding-agent");
		const resolver = await import(pathToFileURL(join(getPackageDir(), "dist/core/tools/path-utils.js")).href);
		resolvedPath = resolver.resolveToCwd(event.input.path, cwd);
		if (typeof resolvedPath !== "string" || !isAbsolute(resolvedPath)) throw new Error("unknown target");
	}
	return JSON.stringify({ ...event, fleet_harness: "pi", fleet_resolved_path: resolvedPath, cwd, session_id: sessionId });
}

export function runGuard(name: string, payload: string): Promise<Decision> {
	return new Promise((resolve) => {
		let settled = false;
		let timer: ReturnType<typeof setTimeout> | undefined;
		const unavailable = (why: string): Decision => ({ fleet_policy: 1, decision: "block", message: `${name}: enforcement unavailable (${why}); not verified` });
		const finish = (decision: Decision) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			resolve(decision);
		};
		try {
			const child = spawn(PYTHON, [join(ROOT, "hooks", `${name}.py`)], {
				stdio: ["pipe", "pipe", "ignore"], windowsHide: true,
				env: { ...process.env, PYTHONUTF8: "1" },
			});
			let output = "";
			timer = setTimeout(() => {
				child.kill();
				finish(unavailable("15s timeout"));
			}, TIMEOUT_MS);
			child.on("error", () => finish(unavailable("spawn failed")));
			child.stdin.on("error", () => finish(unavailable("stdin failed")));
			child.stdout.on("data", (chunk) => {
				output += String(chunk);
				if (output.length > 1_000_000) {
					child.kill();
					finish(unavailable("oversized response"));
				}
			});
			child.on("close", (code) => {
				if (code !== 0) return finish(unavailable("process failed"));
				try {
					const result = JSON.parse(output);
					if (result?.fleet_policy !== 1 || !["allow", "block", "warn"].includes(result.decision)
						|| typeof result.message !== "string" || (result.decision !== "allow" && !result.message.trim())) {
						return finish(unavailable("malformed response"));
					}
					finish(result);
				} catch { finish(unavailable("malformed response")); }
			});
			child.stdin.end(payload);
		} catch { finish(unavailable("spawn failed")); }
	});
}

export default function (pi: ExtensionAPI) {
	const pending = new Map<string, string[]>();
	pi.on("tool_call", async (event, ctx) => {
		pending.delete(event.toolCallId);
		if (READ_ONLY.has(event.toolName)) return;
		const guards = event.toolName === "bash" ? [...SHELL, ...BASH]
			: event.toolName === "powershell" ? SHELL
			: ["edit", "write"].includes(event.toolName) ? EDIT : null;
		if (!guards) return { block: true, reason: "Fleet policy: unsupported tool; enforcement unavailable" };
		let payload: string;
		try { payload = await policyPayload(event, ctx.cwd, ctx.sessionManager.getSessionId()); }
		catch { return { block: true, reason: "Fleet policy: target resolution unavailable; not verified" }; }
		const warnings: string[] = [];
		for (const guard of guards) {
			const result = await runGuard(guard, payload);
			if (result.decision === "block") return { block: true, reason: [...warnings, result.message].join("\n") };
			if (result.decision === "warn") warnings.push(result.message);
		}
		if (warnings.length) pending.set(event.toolCallId, warnings);
	});
	pi.on("tool_result", async (event, ctx) => {
		const warnings = pending.get(event.toolCallId) ?? [];
		pending.delete(event.toolCallId);
		if (["edit", "write"].includes(event.toolName)) {
			try {
				const payload = await policyPayload(event, ctx.cwd, ctx.sessionManager.getSessionId());
				for (const guard of POST_EDIT) {
					const result = await runGuard(guard, payload);
					if (result.decision !== "allow") warnings.push(result.message);
				}
			} catch { warnings.push("target resolution unavailable; not verified"); }
		}
		if (warnings.length) {
			// A partial patch preserves details/isError/usage and every original block.
			return { content: [...event.content, ...warnings.map(text => ({ type: "text" as const, text: WARNING_PREFIX + text }))] };
		}
	});
	pi.on("agent_settled", () => { pending.clear(); });
	pi.on("session_shutdown", () => { pending.clear(); });
}
