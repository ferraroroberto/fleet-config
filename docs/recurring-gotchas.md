# Recurring gotchas

The full entries behind `global-CLAUDE.md`'s "Recurring gotchas" list, which keeps one rule per entry under the same title and points here. Section markers such as *(Claude Code only — skip on other agents)* keep their meaning.

## Git Bash strips backslashes in `settings.json` commands *(Claude Code only — skip on other agents)*

Claude Code executes `settings.json` commands (statusLine, hooks) through **Git Bash**, which treats `\` as an escape — Windows paths in command strings must use **forward slashes** (`C:/Windows/...`) or they silently mangle (`C:\Windows` → `C:Windows`). Codex invokes the Python hook modules directly (no Git Bash, no `run-hook.ps1` shim). Working command form:

```
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/Users/rober/.claude/<script>.ps1
```

## A trailing backslash before a closing double-quote escapes it, not closes it

In Git Bash, `\"` inside a double-quoted string is an escaped literal `"`, not a quote-closer — a Windows path argument that ends in a bare backslash right before its closing `"` (e.g. `"E:\automation\foo\"`) never actually closes that string. Quote parity then shifts for the rest of the command, and a later quoted argument lands unquoted with its own backslashes silently stripped (`fleet-config#800`) — the symptom shows on the *second* path, the defect is the trailing backslash on the *first*. Never end a double-quoted Windows path argument with a bare trailing backslash — drop it or use a forward slash.

## Windows PowerShell in spawned commands (any agent)

- **Avoid `pwsh`** — the PATH `pwsh` is a 0-byte WindowsApps reparse stub that fails non-interactively. Use the absolute path `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`.
- **Never call native `cmd.exe /c` from Git Bash.** MSYS rewrites the single-slash switch to `C:/`, so cmd opens interactively and never runs the command (fleet-config#385). Use the PowerShell tool/absolute `powershell.exe` path; if Bash must call cmd, the MSYS-safe spelling is `cmd.exe //c`.
- **PowerShell scripts reading the agent's stdin JSON** read the whole stream explicitly — `$input` is unreliable across the shell → powershell.exe pipe — and **as UTF-8 on both legs**: `[Console]::In` decodes with the OEM code page (ibm850) and `$payload | python` encodes with `$OutputEncoding` (us-ascii in 5.1), so every non-ASCII codepoint arrives as `?` (fleet-config#912). Use `(New-Object IO.StreamReader([Console]::OpenStandardInput(), (New-Object Text.UTF8Encoding $false))).ReadToEnd()` and set `$OutputEncoding` to the same BOM-less encoding before piping on; avoid changing the console's code page through `[Console]::InputEncoding`, which needs a console a windowless hook may not have and alters the shared console. One exception, same page only: 5.1 writes a piped native child's stdin through `Console.InputEncoding`, which on a code-page-65001 console (what a pwsh 7 parent hands down) prepends a UTF-8 BOM that `json.loads` rejects, so a hook spawned under such a console read `{}` and failed open (fleet-config#920; Claude Code itself spawns hooks on an OEM-page console, so its live sessions were unaffected) — `run-hook.ps1` swaps in the BOM-less UTF-8 instance only when the page is already 65001. The Python side decodes `sys.stdin.buffer` as `utf-8-sig` too, since piped text stdin defaults to cp1252 (see `hooks/run-hook.ps1`, `_lib.read_stdin_json`). When the command only forwards stdin to Python, skip the pipe: `& python.exe hook.py` with no pipeline input inherits the raw stdin bytes, in 5.1 and PowerShell 7 alike (`copilot-hooks/fleet-context-filter.json`; Copilot CLI runs its `powershell` hook key under PowerShell 7 — verified live on 1.0.83 — where the old pipe gave cp850 mojibake instead of `?`). Output has the mirror problem: `Write-Host` and native-command capture use the console code page, so a script printing non-ASCII writes UTF-8 bytes to `[Console]::OpenStandardOutput()` (see `statusline-command.ps1`, fleet-config#913). Never set `[Console]::OutputEncoding` from a child of an agent's terminal — it changes the shared console's code page.
- `[math]::Round(x) + '%'` parses as arithmetic and throws — cast first: `[string][math]::Round(x) + '%'`.

## PYTHONPATH for out-of-tree Python scripts

`& .\.venv\Scripts\python.exe <script-outside-project>` importing project packages fails with `ModuleNotFoundError` — Python sets `sys.path[0]` to the *script's* dir, not CWD, so `cd`-ing in doesn't help. Prepend `$env:PYTHONPATH = (Get-Location).Path;` (Windows) / `PYTHONPATH=$(pwd)` (POSIX). Better when the script can live in-tree (gitignored scratch is fine): `& .\.venv\Scripts\python.exe -m <module.path>` from the repo root — `-m` adds CWD to `sys.path`, no env var.

## Windows Python: UTF-8 stdout under capture

Piped/redirected stdout makes Python fall back to cp1252, so emoji/box-drawing `print()` throws `UnicodeEncodeError` and exits 1 — even though it works in a real terminal. Set `$env:PYTHONUTF8 = "1"` under capture; durable code fix: `sys.stdout.reconfigure(encoding="utf-8")` (and stderr) at entry points.

**The silent variant, when the reader decodes UTF-8:** characters cp1252 *can* encode don't throw. They leave as cp1252 bytes (an em dash becomes `0x97`), and a UTF-8 reader shows U+FFFD. stderr's `backslashreplace` handler turns everything else into `\uXXXX` text. That is how hook refusals reached Claude Code garbled whenever the session lacked `PYTHONIOENCODING` (fleet-config#924). `_lib.block()` and `_lib.warn()` now write Claude-bound plain text as UTF-8, reconfiguring the stream just before exit.

**The inverse, in any process that sets `PYTHONUTF8`:** `subprocess.run(..., text=True)` decodes the *child's* output as UTF-8, but native Windows console tools (`schtasks`, `netsh`, `sc`, `tasklist`, `wmic`, `reg`, `ipconfig`, …) emit the OEM code page (cp850 here), which is not valid UTF-8. It doesn't raise — `proc.stdout` comes back empty/`None`, so an `if not proc.stdout: return None` guard reads it as "the query failed" and the feature degrades silently. Pin decoding at every such call site — `encoding="oem", errors="replace"` (`replace` so one odd byte costs a character, not the whole feature) — never inherit `text=True`'s ambient locale. Reproduces only *inside* the app: from any terminal there is no `PYTHONUTF8`, so identical code looks healthy (`app-launcher#743`).

**Corollary:** a helper that returns `None` on failure must **log** the failure — a dead query must never be indistinguishable from a quiet system.

## Browser automation must not look like a bot

Automation against a third-party site must present as a real human Chrome session (past captchas on detection; social platforms risk account lockouts):

- Strip the automation infobar: `ignore_default_args=["--enable-automation", "--enable-blink-features=IdleDetection"]`.
- `navigator.webdriver` must read `undefined` — `add_init_script` with `Object.defineProperty(navigator, 'webdriver', {get: () => undefined});` (not just a CLI flag).
- Real Chrome (`channel="chrome"`), not bundled Chromium.
- Persistent profile, viewport 1280×900, `--disable-blink-features=AutomationControlled`.
- Also `--disable-features=Translate`, `--no-default-browser-check`, `--no-first-run`.
- `chromium_sandbox=True` on `launch` / `launch_persistent_context` — Playwright's default (`False`) injects `--no-sandbox`, which pops Chrome's *"the `--no-sandbox` flag you are using is not supported"* infobar, itself a bot tell.

**Single source per project:** launch kwargs + init-script live in one helper (e.g. `config/chrome_launch.py`, `automation/browser.py`); every module imports it — never re-inline launch args. If the user reports a captcha or "unusual activity", suspect a stealth regression first.

**Narrow first-party screenshot exception:** when the target is one of our own local apps and the only output is a screenshot, the launch is exempt from the stealth profile and may use Chrome Headless Shell. The shared map renderer prefers it and falls back to full Chrome; a headed visual gate or capture engine promising user-Chrome pixel fidelity may deliberately retain full Chrome. Does not apply to third-party sites or logged-in browser automation. For an agent-controlled loopback HTTPS context, use `ignore_https_errors=True` (Playwright) or `--ignore-certificate-errors --test-type` (Chrome CLI), scoped only to `localhost` / `127.0.0.1` / `::1`. Read `browser_scheme` + `webapp_port` from `hooks/projects.toml`; do not guess the protocol.

**Browser URL:** tooling that cannot set a per-context certificate bypass — including the user's normal Chrome — opens `https://$FLEET_BROWSER_HOST:<webapp_port>` so the Tailscale certificate hostname matches. `FLEET_BROWSER_HOST` is the hostname only and stays in the agent's environment, never committed; plain-HTTP apps use loopback. Do not install a Tailscale leaf in the Windows trust store: trust does not repair its intentional loopback hostname mismatch.

## Shared Chrome profiles: serialize access, never kill a live holder

A persistent Chrome profile allows one live instance; a second launch gets Playwright's *"Opening in existing browser session"* and dies. Never "self-heal" by killing the holder — it's usually a legitimately-running sibling job. **Wait** with exponential backoff (60→120→240→480 s), re-attempting each cycle; raise a precise error only after the schedule (a >15-min holder is hung). On Windows the lock is a live-process kernel object, **not** the POSIX `SingletonLock`/`Cookie`/`Socket` files — deleting those does nothing. Put detect-holder + wait-with-backoff in one helper every session imports; never re-inline a launch-with-retry.

## GitHub's `Closes #N` keyword matches on substrings, not standalone clauses

GitHub's issue-closing parser (`close(s|d)?` / `fix(es|ed)?` / `resolve(s|d)?` + `#N`) matches anywhere in the text, including mid-sentence — "Closes #355 findings for …" auto-closed a tracking issue mid-migration (`app-launcher#355`). When a PR advances one finding of a multi-PR issue without finishing it, avoid the keyword entirely — "Part of #N", "Addresses one of #N's findings", "Progresses #N" — and reserve the literal `Closes #N` / `Fixes #N` for the one PR that actually finishes the issue.

## Three clocks — normalise to UTC before correlating GitHub state with local logs

`gh` JSON timestamps (`closedAt`, `createdAt`, `mergedAt`) are **UTC**, suffixed `Z`. This host is **`+0200` in summer, `+0100` in winter** (Europe/Brussels — read the offset, never hardcode it). An app-launcher job log's `[h:mm:ss]` prefix is **elapsed since run start**, not a wall clock at all (`fleet-config`'s `skills/_lib/claude_progress.py:282`, off `time.monotonic`); only the `<run_id>` directory name is a local wall-clock stamp, so a line's real time is `run_id + elapsed`. Normalise everything to UTC *before* comparing, and state the conversion in the working notes — getting it wrong fails **silently**, yielding a confidently-wrong story rather than an error (`fleet-config#633`).

**The same shape without clocks:** when a claim is "tool X reported the wrong thing at time T", reconstruct what X could *observe* at T. Re-running X now answers a different question and will cheerfully agree with you.

## Subprocess spawns must suppress the console window (Windows)

Any `subprocess.Popen`/`.run`/`.call`/`.check_output`/`.check_call` launching an external executable (ffmpeg, ssh, docker, tailscale, nvidia-smi, clip, a helper script, …) must pass `creationflags=subprocess.CREATE_NO_WINDOW` on Windows — parents with no console of their own (pythonw, a tray app, a scheduled task, a daemon) otherwise flash a new console window per spawn. Default to suppressing; only omit it when the window is meant to be visible (rare — a deliberately-opened interactive terminal). Prior instances: `local-llm-hub`#317/#282/#174/#169, `voice-transcriber`#147; fleet-wide gap audit `fleet-config`#399.

```python
creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
```
For a long-lived child that later needs `CTRL_BREAK_EVENT` or graceful termination, combine with a process group:
```python
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
```
`DETACHED_PROCESS` and `CREATE_NO_WINDOW` are mutually exclusive — never combine them (`local-llm-hub`#282). Repos with 3+ call sites factor this into one `_no_window_flags()` / `NO_WINDOW` helper (see `local-llm-hub/scripts/_lib.py`, `whatsapp-radar/src/subprocess_flags.py`) rather than repeating the ternary at every call site.

## Windows ephemeral port exhaustion takes down the whole fleet at once

Symptom: **all** local web apps unresponsive at once (any subset of app-launcher/home-automation/whatsapp-radar/voice-transcriber/local-llm-hub), dead 1–4 min, self-heals with no restart, no code change. Simultaneity across independent processes is the tell — a shared kernel resource, not one app's diff. Cause: dynamic port range `49152–65535` (16,384 ports), `TcpTimedWaitDelay` unset → every closed outbound connection parks in `TIME_WAIT` ~120 s; a burst drains the range and **no process on the box can open an outbound socket** until it drains. (`fleet-config`#440.)

**Diagnose in one minute:**
```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Tcpip'} -MaxEvents 20 | Select TimeCreated, Id, Message
Get-NetTCPConnection | Group-Object State | Sort-Object Count -Descending
netsh int ipv4 show dynamicport tcp
```
Event IDs 4231 (TCP)/4266 (UDP) = "ephemeral port space ... all such ports being in use". Windows rate-limits these, so absence doesn't rule it out — corroborate with the `TIME_WAIT` count (a normal afternoon on this host oscillates ~325–800, routinely the top state).

**Fix hierarchy — cheapest and most targeted first:**
1. Fix the leak: stop whatever opens short-lived outbound connections in a burst/loop (a poller with no backoff, retry-without-backoff, a health check with no session reuse).
2. Pool connections: module-level `requests.Session` (or equivalent), never a bare `requests.get`/`urlopen` per call inside a loop; back off a failing endpoint instead of retrying at full rate; never point an e2e suite at a live production app.
3. Last resort, machine-level, needs elevation + a reboot — **Roberto's call, never applied unattended by an agent:** `TcpTimedWaitDelay = 30` at `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters` (valid 30–300), ~4x effective capacity without touching the range. Re-measure its effect on the Windows 11 stack after applying, don't assume it.

**Never narrow the range downward** — `netsh int ipv4 set dynamicport tcp start=10000` (seen circulated, wrong) hands out this machine's 16 fixed listeners as ephemeral ports: cloudflared `20241-3`, tailscaled `40746`, OneDrive `42050`, MouseWithoutBorders `15100/1`, llama-server `18093`, StreamDeck `28196/8`, MSI services `26822/32683/33683`, logioptionsplus `19010`, hwinfo `10000` — turning a visible, self-healing outage into intermittent bind failures far harder to diagnose. Safe floor on this host if the range must widen: `netsh int ipv4 set dynamicport tcp start=44000 num=21535` (clears every observed fixed listener) — still machine-level tuning, still Roberto's call.
