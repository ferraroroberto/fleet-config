# OTel project attribution for Claude Code sessions

`shell/claude-otel-project.ps1` wraps the `claude` CLI so every invocation
auto-tags `OTEL_RESOURCE_ATTRIBUTES` with the current git repo's name. This
lets any OTel metrics consumer distinguish which project a session's token
usage came from, without per-repo setup.

## Why this exists — and why it's here, not in local-llm-hub

The **only current consumer** of this attribute is `local-llm-hub`
([`ferraroroberto/local-llm-hub`](https://github.com/ferraroroberto/local-llm-hub)):
its `src/claude_code_otel.py` receiver parses Claude Code's own OTel metrics
export and surfaces a "Claude Code (host CLI)" panel in its admin SPA, with a
Project column (added in
[local-llm-hub#234](https://github.com/ferraroroberto/local-llm-hub/issues/234)).
That panel was only ever going to show a project for sessions where
`OTEL_RESOURCE_ATTRIBUTES=project.name=<repo>` was set by hand — which never
happened in practice — so this repo (`fleet-config`#310) automates the
tagging at the shell level instead.

**This is not a generic fleet requirement.** Nothing about `project.name`
attribution is meaningful outside the context of local-llm-hub's receiver —
if that repo's OTel receiver is ever removed or replaced, this hook becomes
dead weight and should be removed too (check there first before assuming it's
still needed). It lives in `fleet-config` rather than `local-llm-hub` because
it's host/shell-level infrastructure (a `$PROFILE` customization affecting
every `claude` invocation on this machine), which is exactly the kind of
thing this repo already owns for hooks, skills, and the global `CLAUDE.md` —
just via a different mechanism (`$PROFILE` dot-source instead of a
junction/symlink into an agent home).

## How it's wired

- `shell/claude-otel-project.ps1` defines a `claude` PowerShell function:
  it runs `git rev-parse --show-toplevel`, takes the basename as
  `project.name`, sets `$env:OTEL_RESOURCE_ATTRIBUTES` accordingly (or clears
  it outside any git repo), then calls the real `claude.exe`. Calling
  `claude.exe` (not `claude`) is deliberate — it resolves the actual binary
  directly and cannot recurse back into the function.
- `install.ps1`'s `Install-OtelProjectProfileHook` ensures **both**
  `Documents\PowerShell\Microsoft.PowerShell_profile.ps1` (pwsh 7) and
  `Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1` (Windows
  PowerShell 5.1) dot-source this script, inside a marked block:

  ```powershell
  # fleet-config:claude-otel-project BEGIN (docs/otel-project-attribution.md, fleet-config#310)
  . "E:\automation\fleet-config\shell\claude-otel-project.ps1"
  # fleet-config:claude-otel-project END
  ```

  Both profiles are targeted explicitly (not the ambient `$PROFILE` variable)
  because `install.ps1` can run this step from inside its own elevated
  Windows PowerShell 5.1 relaunch (see the symlink self-elevation logic
  earlier in that script) — in that context, `$PROFILE` would resolve to the
  *elevated process's* profile, not necessarily the one the user's normal
  interactive shell reads. Hardcoding both real paths sidesteps that
  entirely.
- Idempotent: re-running `install.ps1` reports `OK` if the marker is already
  present with a matching dot-source line, `BLOCKED` if the marker is present
  but points somewhere else (manual investigation needed), or `LINKED` on
  first install.
- `uninstall.ps1`'s `Remove-OtelProjectProfileHook` strips exactly the marked
  block from both profiles, leaving any other profile content untouched. It
  runs even if `~/.claude/.fleet-config-installed.json` (the junction
  manifest) is already gone, since this isn't a junction/symlink and was
  never recorded there.

## Verifying it worked

A fresh shell is required — `$PROFILE` only loads once, at shell startup, so
an already-open terminal won't pick up a change from `install.ps1` until
reopened.

```powershell
# In a NEW PowerShell window, inside any git repo:
cd E:\automation\local-llm-hub
Get-Command claude   # CommandType should be "Function", not just "Application"
claude --version     # or any real invocation
echo $env:OTEL_RESOURCE_ATTRIBUTES   # should print: project.name=local-llm-hub
```

End-to-end (requires local-llm-hub's own OTel receiver env vars already set —
see its `docs/telemetry-langfuse.md`):

```powershell
claude -p "say hi"
# then check the panel:
curl http://127.0.0.1:8000/admin/api/telemetry/claude-code/usage?period=today
# the resulting row's "project" field should be "local-llm-hub"
```

## Troubleshooting

- **`Get-Command claude` shows only `Application`, no `Function`** — the
  profile didn't load. Confirm you're in a shell opened *after*
  `install.ps1` ran, and check
  `Select-String 'claude-otel-project' $PROFILE` finds the dot-source line
  in the profile the shell you're actually using reads (`$PROFILE` — a pwsh
  window and a Windows PowerShell 5.1 window read different files; both get
  wired by `install.ps1`, but if you're in some other host process, it might
  not).
- **`project` shows `null` in the panel even though the function is wired** —
  confirm you're inside a git repo (`git rev-parse --show-toplevel` must
  succeed) when you launch `claude`; outside a repo the function
  deliberately clears the attribute rather than sending a stale value.
- **Scripts/tasks/CI invoking `claude` directly never get tagged** — this is
  a known, accepted limitation (see the constraints note in
  [fleet-config#310](https://github.com/ferraroroberto/fleet-config/issues/310)):
  the hook only fires in interactive PowerShell sessions where `$PROFILE`
  loads. It was not extended to non-interactive invocations because there's
  no single host-level layer to intercept that keeps the "only when actually
  running `claude`" scoping this mechanism deliberately chose over a broader
  `Set-Location`/`cd` hook.
- **Something needs to change here later** — start from
  `shell/claude-otel-project.ps1` (the function itself),
  `install.ps1`'s `Install-OtelProjectProfileHook` (the wiring), and
  `uninstall.ps1`'s `Remove-OtelProjectProfileHook` (the teardown); all three
  share the same two marker strings, so grep for
  `fleet-config:claude-otel-project` to find every place this convention
  touches.
