# Merge verification: the coverage cost of a fresh checkout

Loaded on demand from the `/chief` skill (`SKILL.md`, "Merge verification runs
from a fresh checkout — and must state what it cost"), which keeps the
fresh-detached-checkout rule itself.

**It also costs coverage, and the cost is invisible unless you report it.** A
fresh checkout has none of the repo's gitignored runtime files and none of the
host state keyed to a known path, so the tests that need either **skip** rather
than fail, and pytest prints the same green as a run that covered more. On the
2026-09-12 app-launcher round the skips went **17 → 19**, the whole delta being
the `#444` real-agent pin. Its residual cause is **agent folder trust**: a
never-opened directory paints Claude Code's trust prompt instead of the
composer, and `--dangerously-skip-permissions` does not clear it
(`app-launcher#932`, PR #937). It is not the gitignored
`config/webapp_config.json` registry. A skip is not a pass.

So a merge-verification report is only complete when it names that delta.
Capture the baseline from the checkout that *has* the runtime files, then
compare the fresh-checkout run against it — always with `-rs`, or the skips
come back unnamed:

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/skip_delta.py capture <primary-run.txt> --label "primary checkout" --out <baseline.json>
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/skip_delta.py compare <fresh-run.txt> --baseline <baseline.json>
```

Report-only, always exits 0. Relay `STATUS` (the count fact) **and** `SET` (the
set fact) — they fail independently, and each has its own unknown: `UNKNOWN`
means no count was established, `UNCONFIRMED` means the skips were never named
so a same-count-different-set loss can't be ruled out. Neither may be folded
into a green. `STATUS=INCREASED` or `SET=CHANGED` means the gate covered less
than the baseline; every `NEW=` line names a test that stopped running, and
those belong in what you relay to Roberto verbatim.

**Never close the gap by copying a live config into a scratch checkout.** That
puts real credentials in a throwaway tree, which is exactly what
`app-launcher#907`/PR #911 exist to prevent. It would not close this delta
anyway, and neither may a gate answer the trust prompt or write the user's
global `~/.claude.json`. Accepting reduced coverage and saying so is the
correct behaviour.
