import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { truncateToWidth } from "@earendil-works/pi-tui";

function modelFamily(modelId: string | undefined, modelName: string | undefined): string {
	const raw = `${modelId ?? ""} ${modelName ?? ""}`.toLowerCase();
	if (raw.includes("opus")) return "opus";
	if (raw.includes("sonnet")) return "sonnet";
	if (raw.includes("haiku")) return "haiku";
	return modelName || modelId || "";
}

function projectName(cwd: string): string {
	const trimmed = cwd.replace(/[\\/]+$/, "");
	return trimmed.split(/[\\/]/).filter(Boolean).pop() || trimmed;
}

export default function (pi: ExtensionAPI) {
	pi.on("session_start", (_event, ctx) => {
		ctx.ui.setFooter((tui, theme, footerData) => {
			const unsub = footerData.onBranchChange(() => tui.requestRender());

			return {
				dispose: unsub,
				invalidate() {},
				render(width: number): string[] {
					const segments: string[] = [];
					const usage = ctx.getContextUsage();

					if (usage?.percent !== null && usage?.percent !== undefined) {
						const pct = Math.round(usage.percent);
						const label = `${pct}%`;
						if (pct >= 35) {
							segments.push(theme.fg("error", label));
						} else if (pct >= 30) {
							segments.push(theme.fg("warning", label));
						} else {
							segments.push(theme.fg("success", label));
						}
					}

					const model = modelFamily(ctx.model?.id, ctx.model?.name);
					if (model) segments.push(model);

					const branch = footerData.getGitBranch();
					const dir = projectName(ctx.cwd);
					segments.push(branch ? `${dir} (${branch})` : dir);

					return [truncateToWidth(segments.join(" | "), width, theme.fg("dim", "..."))];
				},
			};
		});
	});

	pi.on("session_shutdown", (_event, ctx) => {
		ctx.ui.setFooter(undefined);
	});
}
