import { spawn } from "node:child_process";
import { join } from "node:path";
import type { ResolvedTarget } from "../types.js";

const CMD_EXE = "C:\\Windows\\System32\\cmd.exe";

/**
 * Launches a resolved, trusted target's command. `target.cwd`/`target.command`
 * always come from the committed registry (via loadResolvedTargets/
 * resolveTarget) — this never accepts or interpolates key-supplied text.
 *
 * Windows can't CreateProcess a .bat/.cmd file directly (spawn with
 * shell:false ENOENTs on tray.bat) — cmd.exe /c is required. Both cmd.exe
 * itself and the launcher are passed as absolute paths rather than relying
 * on PATH/cwd resolution: empirically, `cmd.exe /c tray.bat` with a `cwd`
 * option reproducibly fails ("'tray.bat' is not recognized") under
 * Node's spawn even though the cwd is correctly set — cmd.exe's relative
 * lookup isn't reliable in a non-interactive spawn context, and the
 * Stream Deck app's own child-process environment is more restricted still.
 * `cmd.exe /c <absolute path>\tray.bat` is confirmed reliable. The command
 * still travels as discrete argv elements, not a concatenated shell string,
 * so this stays free of shell-injection even though cmd.exe is involved.
 */
export function launch(target: ResolvedTarget): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(CMD_EXE, ["/c", join(target.cwd, target.command)], {
      cwd: target.cwd,
      windowsHide: true,
      detached: true,
      stdio: "ignore",
    });
    child.once("error", reject);
    child.once("spawn", () => {
      child.unref();
      resolve();
    });
  });
}
