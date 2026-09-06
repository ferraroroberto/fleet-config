// Test transport only: no network, credentials, tool replacements or hook mocks.
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";

export default function (pi: ExtensionAPI) {
    pi.registerProvider("fleet-synthetic", {
        baseUrl: "http://unused.invalid", apiKey: "synthetic-unused", api: "openai-completions",
        models: [{ id: "deterministic", name: "Synthetic conformance (no network)", reasoning: false,
            input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 32000, maxTokens: 4096 }],
        streamSimple(model, context) {
            const stream = createAssistantMessageEventStream();
            queueMicrotask(() => {
                const last = context.messages.at(-1);
                const output: any = { role: "assistant", content: [], api: model.api,
                    provider: model.provider, model: model.id, timestamp: Date.now(),
                    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
                stream.push({ type: "start", partial: output });
                if (last?.role === "toolResult") {
                    // Evidence that Pi delivered the final tool result into model context.
                    appendFileSync(process.env.PI_POLICY_RECEIVED!, JSON.stringify(last) + "\n");
                    output.content = [{ type: "text", text: JSON.stringify(last.content) }];
                    output.stopReason = "stop";
                    stream.push({ type: "text_end", contentIndex: 0, content: output.content[0].text, partial: output });
                } else {
                    const call = JSON.parse(readFileSync(process.env.PI_POLICY_CALL!, "utf8"));
                    output.content = [{ type: "toolCall", id: "synthetic-call", name: call.name, arguments: call.arguments }];
                    output.stopReason = "toolUse";
                    stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
                    stream.push({ type: "toolcall_end", contentIndex: 0, toolCall: output.content[0], partial: output });
                }
                stream.push({ type: "done", reason: output.stopReason, message: output });
                stream.end();
            });
            return stream;
        },
    });
    // Observe the real lifecycle writer after its earlier extension handler.
    for (const event of ["input", "agent_settled", "session_shutdown"] as const) {
        pi.on(event, async (_event: any, ctx: any) => {
            const expected = event === "input" ? "working" : event === "agent_settled" ? "needs-you" : null;
            let observed: string | null | undefined;
            for (let n = 0; n < 40; n++) {
                await new Promise(resolve => setTimeout(resolve, 25));
                try {
                    const state = JSON.parse(readFileSync(join(process.env.CLAUDE_HOOKS_STATE_DIR!, "sessions-state.json"), "utf8"));
                    observed = state[ctx.sessionManager.getSessionId()]?.status ?? null;
                    if (observed === expected) break;
                } catch {}
            }
            appendFileSync(process.env.PI_POLICY_LIFECYCLE!, JSON.stringify({ event, expected, observed }) + "\n");
            if (event === "agent_settled") ctx.shutdown();
        });
    }
}
